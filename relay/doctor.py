"""고정 부분(base) 진단 (`relay doctor`).

base 는 매 요청 다시 읽힌다. base 1k 토큰을 줄이면 (주간 요청 수 × 1k) 만큼 캐시 읽기가 준다.
측정 가능한 것(최근 세션의 첫 요청 컨텍스트, CLAUDE.md 크기, 주간 요청 수)은 숫자로,
측정할 수 없는 것(MCP 서버별 도구 정의 크기)은 목록과 확인 방법으로 보여 준다.
"""

import json
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from relay.tokens import estimate
from relay.usage import _iter_jsonl, claude_home, scan_claude, since_iso, total_of

IMPORT_RE = re.compile(r"(?:^|\s)@([~\w./-]+\.\w+)")


@dataclass
class Diagnosis:
    base_tokens: list[int] = field(default_factory=list)   # 최근 세션들의 첫 요청 컨텍스트
    weekly_requests: int = 0
    memory_files: list[tuple[str, int]] = field(default_factory=list)  # (경로, 토큰)
    mcp_servers: list[tuple[str, str, str]] = field(default_factory=list)  # (scope, name, command)

    @property
    def base_median(self) -> int:
        return int(statistics.median(self.base_tokens)) if self.base_tokens else 0

    def weekly_cost(self, tokens: int) -> int:
        return tokens * self.weekly_requests


def first_context(path: Path) -> int:
    for entry in _iter_jsonl(path):
        if entry.get("type") == "assistant" and not entry.get("isSidechain"):
            u = (entry.get("message") or {}).get("usage")
            if u:
                return (int(u.get("input_tokens") or 0) + int(u.get("cache_creation_input_tokens") or 0)
                        + int(u.get("cache_read_input_tokens") or 0))
    return 0


def memory_files(root: Path) -> list[tuple[str, int]]:
    """매 요청 base 에 들어가는 CLAUDE.md 계열 파일과 @import 대상 (1단계)."""
    home = Path.home()
    paths = [claude_home() / "CLAUDE.md"]
    for d in [root, *root.parents]:
        paths += [d / "CLAUDE.md", d / ".claude" / "CLAUDE.md"]
        if d == home or d == d.parent:
            break
    paths.append(root / "CLAUDE.local.md")
    out, seen = [], set()
    for p in paths:
        if not p.is_file() or p.resolve() in seen:
            continue
        seen.add(p.resolve())
        text = p.read_text(encoding="utf-8", errors="replace")
        out.append((str(p), estimate(text)))
        for m in IMPORT_RE.finditer(text):
            target = Path(m.group(1)).expanduser()
            target = target if target.is_absolute() else p.parent / target
            if target.is_file() and target.resolve() not in seen:
                seen.add(target.resolve())
                out.append((f"{target} (@import)", estimate(target.read_text(encoding="utf-8", errors="replace"))))
    return out


def mcp_servers(root: Path) -> list[tuple[str, str, str]]:
    out = []

    def add(scope: str, servers: dict):
        for name, conf in (servers or {}).items():
            conf = conf if isinstance(conf, dict) else {}
            cmd = conf.get("url") or " ".join([conf.get("command", ""), *map(str, conf.get("args", []))]).strip()
            out.append((scope, name, cmd[:60]))

    user_conf = Path.home() / ".claude.json"
    if user_conf.is_file():
        try:
            data = json.loads(user_conf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        add("user", data.get("mcpServers"))
        add("local", ((data.get("projects") or {}).get(str(root.resolve())) or {}).get("mcpServers"))
    project = root / ".mcp.json"
    if project.is_file():
        try:
            add("project", json.loads(project.read_text(encoding="utf-8")).get("mcpServers"))
        except json.JSONDecodeError:
            pass
    return out


def diagnose(root: Path, days: float = 14) -> Diagnosis:
    d = Diagnosis()
    since = since_iso(days)
    projects = claude_home() / "projects"
    if projects.is_dir():
        recent = sorted(projects.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:20]
        d.base_tokens = [t for t in map(first_context, recent) if t]
    d.weekly_requests = round(total_of(scan_claude(since)).requests * 7 / days)
    d.memory_files = memory_files(root)
    d.mcp_servers = mcp_servers(root)
    return d
