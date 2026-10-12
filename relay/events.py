"""감사 로그: MCP 도구 호출을 `.relay/events.jsonl` 에 남긴다.

누가(client/profile) 어떤 작업에 무엇을 했고, 응답으로 몇 토큰을 돌려줬는지 기록한다.
"relay 를 통해 에이전트에게 공급된 컨텍스트"를 측정하는 관측 지점이다.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


def path_for(root: Path) -> Path:
    return root / ".relay" / "events.jsonl"


def append(root: Path, **fields) -> None:
    if not (root / ".relay").is_dir():
        return
    record = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **fields}
    with path_for(root).open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read(root: Path) -> list[dict]:
    path = path_for(root)
    if not path.is_file():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def summarize(records: list[dict]) -> dict[tuple[str, str], dict]:
    """(client, tool) → {calls, errors, tokens_out}"""
    table: dict[tuple[str, str], dict] = defaultdict(lambda: {"calls": 0, "errors": 0, "tokens_out": 0})
    for r in records:
        row = table[(r.get("client", "?"), r.get("tool", "?"))]
        row["calls"] += 1
        row["errors"] += 0 if r.get("ok", True) else 1
        row["tokens_out"] += int(r.get("tokens_out") or 0)
    return dict(sorted(table.items()))
