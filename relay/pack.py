"""Context pack 빌더.

Claude 가 직접 파일을 탐색하면 tool call 마다 전체 컨텍스트를 다시 읽는다(cache read).
대신 필요한 조각만 미리 한 파일로 묶어서 첫 요청에 넣어 주는 것이 이 모듈의 목적이다.

포함 우선순위:
  1. --refs  : 탐색 리포트(예: Antigravity 결과)에 적힌 `path:10-40` 구간만 발췌
  2. --full  : 파일 전체
  3. --outline: 시그니처만 (예산을 넘긴 --full 파일도 여기로 강등)
  4. --diff  : git diff (리뷰용)
"""

import ast
import fnmatch
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from relay.tokens import estimate

IGNORED_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build",
                ".next", ".relay", "target", ".idea", ".mypy_cache", ".pytest_cache"}

# path/to/file.ext:12  또는  path/to/file.ext:12-40  (L12, #L12-L40 형태도 허용)
REF_RE = re.compile(r"(?P<path>[\w./-]+\.\w+)(?::|#L)L?(?P<start>\d+)(?:-L?(?P<end>\d+))?")

OUTLINE_RE = re.compile(
    r"^\s*(export\s+)?(default\s+)?(async\s+)?"
    r"(def|class|function|interface|type|enum|struct|trait|impl|fn|func|const\s+\w+\s*=\s*(async\s*)?\(|"
    r"public|private|protected|module|package)\b"
)


@dataclass
class Section:
    title: str
    body: str
    lang: str = ""

    def render(self) -> str:
        return f"### {self.title}\n```{self.lang}\n{self.body.rstrip()}\n```\n"

    @property
    def tokens(self) -> int:
        return estimate(self.render())


@dataclass
class Pack:
    sections: list[Section] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def tokens(self) -> int:
        return sum(s.tokens for s in self.sections)

    def render(self, header: str = "") -> str:
        parts = [header] if header else []
        parts += [s.render() for s in self.sections]
        if self.skipped:
            parts.append("### 예산 초과로 제외됨\n" + "\n".join(f"- {s}" for s in self.skipped) + "\n")
        return "\n".join(parts)


def header(task_name: str, pack: Pack) -> str:
    return f"<!-- relay pack: {task_name} · ~{pack.tokens} tokens -->\n"


def _lang(path: Path) -> str:
    return {".py": "python", ".ts": "ts", ".tsx": "tsx", ".js": "js", ".go": "go", ".rs": "rust",
            ".java": "java", ".kt": "kotlin", ".md": "md", ".yaml": "yaml", ".yml": "yaml",
            ".toml": "toml", ".json": "json", ".sh": "bash", ".tf": "hcl"}.get(path.suffix, "")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def is_binary(path: Path) -> bool:
    with path.open("rb") as f:
        return b"\0" in f.read(8192)


def outline_python(source: str) -> str:
    tree = ast.parse(source)
    lines: list[str] = []

    def visit(nodes, indent=0):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if isinstance(node, ast.ClassDef):
                    bases = ", ".join(ast.unparse(b) for b in node.bases)
                    sig = f"class {node.name}({bases}):" if bases else f"class {node.name}:"
                else:
                    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
                    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
                    sig = f"{prefix} {node.name}({ast.unparse(node.args)}){ret}:"
                lines.append(f"{'    ' * indent}{sig}  # L{node.lineno}")
                doc = ast.get_docstring(node)
                if doc:
                    lines.append(f"{'    ' * (indent + 1)}\"\"\"{doc.strip().splitlines()[0]}\"\"\"")
                if isinstance(node, ast.ClassDef):
                    visit(node.body, indent + 1)

    visit(tree.body)
    return "\n".join(lines)


def outline(path: Path) -> str:
    source = _read(path)
    if path.suffix == ".py":
        try:
            return outline_python(source)
        except SyntaxError:
            pass
    return "\n".join(f"{line.rstrip()}  // L{i}" for i, line in enumerate(source.splitlines(), 1)
                     if OUTLINE_RE.match(line))


def excerpt(path: Path, start: int, end: int, context: int = 3) -> str:
    lines = _read(path).splitlines()
    lo, hi = max(1, start - context), min(len(lines), end + context)
    width = len(str(hi))
    return "\n".join(f"{i:>{width}} {lines[i - 1]}" for i in range(lo, hi + 1))


def parse_refs(text: str) -> list[tuple[str, int, int]]:
    """리포트 본문에서 `path:start-end` 참조를 뽑아 파일별로 겹치는 구간을 합친다."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for m in REF_RE.finditer(text):
        start = int(m["start"])
        end = int(m["end"] or start)
        spans.setdefault(m["path"], []).append((min(start, end), max(start, end)))
    merged = []
    for path, ranges in spans.items():
        ranges.sort()
        cur_s, cur_e = ranges[0]
        for s, e in ranges[1:]:
            if s <= cur_e + 8:
                cur_e = max(cur_e, e)
            else:
                merged.append((path, cur_s, cur_e))
                cur_s, cur_e = s, e
        merged.append((path, cur_s, cur_e))
    return merged


def inside(root: Path, path: Path) -> bool:
    """프로젝트 밖(`../`, 절대경로, 심볼릭 링크)을 읽지 못하게 막는다. MCP 로 노출되므로 필수."""
    return path.resolve().is_relative_to(root.resolve())


def expand_globs(root: Path, patterns: list[str]) -> list[Path]:
    found: list[Path] = []
    for pattern in patterns:
        direct = root / pattern
        if not inside(root, direct):
            continue
        if direct.is_file() and not IGNORED_DIRS & set(direct.relative_to(root).parts):
            found.append(direct)
            continue
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if (p.is_file() and not IGNORED_DIRS & set(rel.parts) and fnmatch.fnmatch(str(rel), pattern)
                    and inside(root, p)):
                found.append(p)
    seen, unique = set(), []
    for p in found:
        if p not in seen:
            seen.add(p)
            unique.append(p)
    return unique


def tree(root: Path, max_depth: int = 3, max_entries: int = 200) -> str:
    out: list[str] = []

    def walk(d: Path, depth: int):
        if depth > max_depth or len(out) >= max_entries:
            return
        for p in sorted(d.iterdir(), key=lambda x: (x.is_file(), x.name)):
            if p.name in IGNORED_DIRS or p.name.startswith(".") and p.name not in {".github", ".agent", ".claude"}:
                continue
            if len(out) >= max_entries:
                out.append("  " * depth + "…")
                return
            out.append("  " * depth + p.name + ("/" if p.is_dir() else ""))
            if p.is_dir():
                walk(p, depth + 1)

    walk(root, 0)
    return "\n".join(out)


def git_diff(root: Path, ref: str, exclude: tuple[str, ...] = (".relay",)) -> str:
    """relay 자신의 상태 파일(.relay/)은 작업 변경이 아니므로 기본으로 뺀다."""
    pathspec = ["--", "."] + [f":(exclude){e}" for e in exclude]
    result = subprocess.run(["git", "diff", "--unified=3", ref, *pathspec], cwd=root,
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "git diff 실패")
    return result.stdout


def build(root: Path, *, refs_text: str = "", full: list[str] = (), outlines: list[str] = (),
          diff_ref: str | None = None, include_tree: bool = False, budget: int = 12_000) -> Pack:
    pack = Pack()

    def add(section: Section, fallback: Section | None = None) -> None:
        if pack.tokens + section.tokens <= budget:
            pack.sections.append(section)
        elif fallback and pack.tokens + fallback.tokens <= budget:
            pack.sections.append(fallback)
        else:
            pack.skipped.append(f"{section.title} (~{section.tokens} tok)")

    if include_tree:
        add(Section("파일 트리", tree(root)))

    if diff_ref:
        add(Section(f"git diff {diff_ref}", git_diff(root, diff_ref) or "(변경 없음)", "diff"))

    for rel, start, end in parse_refs(refs_text):
        path = root / rel
        if not inside(root, path):
            pack.skipped.append(f"{rel} (프로젝트 밖 — 거부)")
        elif path.is_file():
            add(Section(f"{rel}:{start}-{end}", excerpt(path, start, end), _lang(path)))
        else:
            pack.skipped.append(f"{rel} (파일 없음)")

    for path in expand_globs(root, list(full)):
        rel = path.relative_to(root)
        if is_binary(path):
            pack.skipped.append(f"{rel} (바이너리)")
            continue
        add(Section(str(rel), _read(path), _lang(path)),
            fallback=Section(f"{rel} (outline — 예산 초과로 강등)", outline(path), _lang(path)))

    already = {s.title.split(" ")[0] for s in pack.sections}
    for path in expand_globs(root, list(outlines)):
        rel = str(path.relative_to(root))
        if rel in already:
            continue
        body = outline(path)
        if body:
            add(Section(f"{rel} (outline)", body, _lang(path)))

    return pack
