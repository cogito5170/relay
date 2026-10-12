"""`.relay/tasks/<id>-<slug>/` 작업 폴더 관리와 에이전트별 handoff 프롬프트 생성."""

import re
import shutil
from datetime import datetime
from importlib import resources
from pathlib import Path
from string import Template

AGENTS = ("chatgpt", "antigravity", "codex", "claude")

DEFAULT_ASK = {
    "chatgpt": "brief 를 검토해 빠진 요구사항·완료 조건을 채우고 구현 단계를 3~7개로 나눠줘.",
    "antigravity": "brief 와 관련된 코드를 탐색해 explore.md 를 작성해줘.",
    "codex": "state 의 Next (없으면 brief) 를 구현하고 테스트를 추가해줘.",
    "claude": "state 의 Next (없으면 brief) 중 판단이 필요한 부분을 처리해줘.",
}


def template(*parts: str) -> Template:
    return Template(resources.files("relay").joinpath("templates", *parts).read_text(encoding="utf-8"))


def tasks_dir(root: Path) -> Path:
    return root / ".relay" / "tasks"


def slugify(title: str) -> str:
    slug = re.sub(r"[^\w가-힣]+", "-", title.lower()).strip("-")
    return slug[:40] or "task"


def list_tasks(root: Path) -> list[Path]:
    d = tasks_dir(root)
    return sorted(p for p in d.iterdir() if p.is_dir()) if d.is_dir() else []


def resolve(root: Path, ref: str | None) -> Path:
    """'3', '003', '003-login' 또는 생략(가장 최근) → 작업 폴더."""
    tasks = list_tasks(root)
    if not tasks:
        raise LookupError("작업이 없습니다. `relay task new \"제목\"` 으로 만드세요.")
    if not ref:
        return tasks[-1]
    for t in tasks:
        num = t.name.split("-", 1)[0]
        if t.name == ref or t.name.startswith(ref + "-") or (ref.isdigit() and num.isdigit() and int(num) == int(ref)):
            return t
    raise LookupError(f"작업을 찾을 수 없습니다: {ref}")


def create(root: Path, title: str, route_summary: str) -> Path:
    tasks = list_tasks(root)
    next_num = max((int(t.name.split("-", 1)[0]) for t in tasks if t.name[:3].isdigit()), default=0) + 1
    task_id = f"{next_num:03d}-{slugify(title)}"
    path = tasks_dir(root) / task_id
    path.mkdir(parents=True)
    body = template("tasks", "brief.md").safe_substitute(
        title=title, task_id=task_id, route=route_summary,
        created=datetime.now().strftime("%Y-%m-%d %H:%M"))
    (path / "brief.md").write_text(body, encoding="utf-8")
    return path


def _read_optional(path: Path, empty: str) -> str:
    return path.read_text(encoding="utf-8").strip() if path.is_file() else empty


def handoff(root: Path, task: Path, agent: str, ask: str = "") -> Path:
    if agent not in AGENTS:
        raise ValueError(f"알 수 없는 에이전트: {agent} (가능: {', '.join(AGENTS)})")
    out_path = task / f"out-{agent}.md"
    text = template("prompts", f"{agent}.md").safe_substitute(
        task_id=task.name,
        ask=ask or DEFAULT_ASK[agent],
        brief=_read_optional(task / "brief.md", "(brief 없음)"),
        state=_read_optional(task / "state.md", "(아직 없음 — 첫 단계)"),
        pack=_read_optional(task / "pack.md", "(pack 없음 — `relay pack` 으로 생성)"),
        out_path=out_path.relative_to(root).as_posix(),
    )
    prompt = task / f"prompt-{agent}.md"
    prompt.write_text(text, encoding="utf-8")
    return prompt


def scaffold(root: Path, force: bool = False) -> list[tuple[str, str]]:
    """템플릿을 프로젝트에 복사. `_name` 디렉터리는 `.name` 으로 바꾼다. (경로, 상태) 목록 반환."""
    src_root = resources.files("relay").joinpath("templates", "scaffold")
    results = []

    def walk(src, rel: Path):
        for item in src.iterdir():
            name = "." + item.name[1:] if item.name.startswith("_") else item.name
            if item.is_dir():
                walk(item, rel / name)
                continue
            dest = root / rel / name
            if dest.exists() and not force:
                results.append(((rel / name).as_posix(), "skip (이미 있음)"))
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            with resources.as_file(item) as f:
                shutil.copyfile(f, dest)
            results.append(((rel / name).as_posix(), "created"))

    walk(src_root, Path())
    return sorted(results)
