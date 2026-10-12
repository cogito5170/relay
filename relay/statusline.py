"""Claude Code statusline: 현재 세션의 컨텍스트 크기와 누적 cache read 를 상시 표시.

statusline 은 모델 컨텍스트에 들어가지 않으므로 토큰 비용 0 으로 "지금 /clear 할 때인가"를 알려 준다.
설정: .claude/settings.json → {"statusLine": {"type": "command", "command": "relay statusline"}}
"""

import json
import sys
from pathlib import Path

from relay import config as cfg
from relay.tokens import human
from relay.usage import Totals, _iter_jsonl


def last_context(path: Path) -> tuple[int, Totals]:
    totals = Totals()
    seen: set = set()
    last = 0
    for entry in _iter_jsonl(path):
        if entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        msg = entry.get("message") or {}
        usage = msg.get("usage")
        key = (msg.get("id"), entry.get("requestId"))
        if not usage or key in seen:
            continue
        seen.add(key)
        totals.add(usage)
        last = (int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
                + int(usage.get("cache_read_input_tokens") or 0))
    return last, totals


def bar(ratio: float, width: int = 8) -> str:
    filled = max(0, min(width, round(ratio * width)))
    return "█" * filled + "░" * (width - filled)


def render(data: dict, conf: dict) -> str:
    budget = conf["budget"]
    model = (data.get("model") or {}).get("display_name", "")
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return f"{model} · relay: 새 세션"
    ctx, totals = last_context(Path(transcript))
    ratio = ctx / budget["max_context"] if budget["max_context"] else 0
    parts = [model, f"ctx {human(ctx)} {bar(ratio)}", f"req {totals.requests}",
             f"read {human(totals.cache_read)}"]
    if ctx >= budget["max_context"] or totals.requests >= budget["warn_requests"]:
        parts.append("⛔ /handoff → /clear")
    elif ctx >= budget["warn_context"]:
        parts.append("⚠ 컨텍스트 큼")
    return " · ".join(p for p in parts if p)


def main(stdin=sys.stdin) -> int:
    try:
        data = json.load(stdin)
    except json.JSONDecodeError:
        data = {}
    conf = cfg.load(Path(data.get("cwd") or ".") if data.get("cwd") else None)
    print(render(data, conf))
    return 0
