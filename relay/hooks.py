"""Claude Code hooks — 규칙을 '부탁'이 아니라 '강제'로 (`relay hook prompt|pretool`).

CLAUDE.md 의 규칙은 모델이 지킬 수도, 안 지킬 수도 있다. hook 은 하네스가 실행하므로 항상 적용되고,
통과할 때는 아무것도 출력하지 않아 토큰 비용이 0 이다.

- prompt  (UserPromptSubmit): 컨텍스트·요청 수가 한도를 넘으면 메시지를 막고 /handoff → /clear 를 안내.
                              막힌 메시지는 모델에 가지 않는다. `[force]` 를 넣으면 통과.
- pretool (PreToolUse):       큰 파일 전체 Read, 너무 넓은 줄 범위 읽기, 출력이 큰 명령의 무제한 실행,
                              (선택) 큰 Write 를 거부하고
                              짧은 이유만 Claude 에게 돌려준다. 이유 수십 토큰으로 수천 토큰의 출력을 막는다.

어떤 예외가 나도 통과시킨다(fail-open). relay 버그가 사용자의 세션을 멈추면 안 된다.
"""

import json
import re
import sys
from pathlib import Path

from relay import config as cfg
from relay import events
from relay.statusline import last_context
from relay.tokens import estimate, human

FORCE = "[force]"
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".ipynb", ".svg"}
LIMITERS = re.compile(r"\||>|\s-q\b|--quiet|--silent|\s-n\s*\d|--max-count|--tail|--stat|--shortstat|"
                      r"--name-only|--name-status|\s-\d+\b|--oneline\s+-\d")

# 범위를 지정해 읽는 명령. 범위가 read_max_range 를 넘으면 "줄 범위로 읽었다"고 보지 않는다.
RANGES = (
    (re.compile(r"\bsed\b[^|;&]*?-n\s*['\"]?(\d+)\s*,\s*(\d+)\s*p"),
     lambda m: int(m.group(2)) - int(m.group(1)) + 1),
    (re.compile(r"\b(?:head|tail)\b\s+(?:-n\s*|-)(\d+)\b"), lambda m: int(m.group(1))),
)
TOKENS_PER_LINE = 12   # 범위만 보고 거부할 때의 거친 환산 (events 의 avoided 용)
NARROW_READ_ADVICE = "Grep 으로 위치를 찾고 {limit}줄 이하로 좁혀서 읽으세요."


def _log(root: Path, rule: str, detail: str, avoided: int) -> None:
    events.append(root, client="hook", profile="-", tool=rule, task=detail[:80], ok=False, tokens_out=0,
                  avoided=avoided)


def prompt_gate(data: dict, conf: dict) -> dict | None:
    prompt = data.get("prompt") or ""
    if prompt.lstrip().startswith("/") or FORCE in prompt:
        return None
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return None
    ctx, totals = last_context(Path(transcript))
    h = conf["hooks"]
    if ctx < h["gate_context"] and totals.requests < h["gate_requests"]:
        return None
    _log(Path(data.get("cwd") or "."), "gate:prompt", f"ctx={ctx} req={totals.requests}", ctx)
    return {"decision": "block", "reason": (
        f"relay: 컨텍스트 {human(ctx)} · 요청 {totals.requests}회. 계속하면 이후 요청마다 {human(ctx)} 이상을 다시 읽습니다.\n"
        f"→ /handoff <task-id> → /clear → /pickup <task-id>\n"
        f"(그래도 보내려면 메시지에 {FORCE} 를 넣으세요)")}


POLICY_PREFIX = "relay 정책으로 차단됨 (오류 아님, 다른 방법으로 계속하세요) — "


def _deny(reason: str) -> dict:
    # Claude Code 는 거부 사유를 "PreToolUse:<Tool> hook error:" 라는 머리말과 함께 보여 준다.
    # 모델이 고장으로 오해해 같은 호출을 반복하지 않도록 의도된 정책임을 앞에 밝힌다 (실제 세션에서 확인).
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": POLICY_PREFIX + reason.removeprefix("relay: ")}}


def _file_stats(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    return data.count(b"\n") + 1, estimate(data.decode("utf-8", errors="replace"))


def read_guard(tool_input: dict, conf: dict, cwd: Path) -> tuple[str, int] | None:
    max_range = conf["hooks"]["read_max_range"]
    limit = tool_input.get("limit") or 0
    if max_range and limit > max_range:
        return (f"relay: 한 번에 {limit}줄은 너무 넓습니다 (한도 {max_range}줄). 읽은 줄은 이 세션의 이후 "
                f"모든 요청이 다시 읽습니다. " + NARROW_READ_ADVICE.format(limit=max_range)), \
               (limit - max_range) * TOKENS_PER_LINE
    if tool_input.get("offset") or limit:
        return None
    path = Path(tool_input.get("file_path") or "")
    path = path if path.is_absolute() else cwd / path
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return None
    lines, tokens = _file_stats(path)
    if lines <= conf["hooks"]["read_max_lines"]:
        return None
    return (f"relay: {path.name} 은 {lines}줄(~{human(tokens)} tok). 전체를 읽으면 이 세션의 이후 모든 요청이 "
            f"다시 읽습니다. Grep 으로 위치를 찾은 뒤 offset/limit 으로 필요한 범위만 읽으세요."), tokens


def range_guard(command: str, conf: dict) -> tuple[str, int] | None:
    """`sed -n 'A,Bp'` · `head -n N` · `tail -n N` 의 범위가 한도를 넘으면 거부한다.

    범위 플래그가 있으면 LIMITERS 는 '출력을 제한했다'고 보고 통과시키므로, 그 검사보다 먼저 본다.
    """
    max_range = conf["hooks"]["read_max_range"]
    if not max_range:
        return None
    for pattern, span_of in RANGES:
        for m in pattern.finditer(command):
            span = span_of(m)
            if span > max_range:
                return (f"relay: `{m.group(0)}` 는 {span}줄입니다 (한도 {max_range}줄). 읽은 줄은 이 세션의 "
                        f"이후 모든 요청이 다시 읽습니다. " + NARROW_READ_ADVICE.format(limit=max_range)), \
                       (span - max_range) * TOKENS_PER_LINE
    return None


def bash_guard(tool_input: dict, conf: dict, cwd: Path) -> tuple[str, int] | None:
    command = (tool_input.get("command") or "").strip()
    if not command:
        return None
    wide = range_guard(command, conf)
    if wide:
        return wide
    if LIMITERS.search(command):
        return None
    for segment in re.split(r"&&|;|\|\|", command):
        seg = segment.strip()
        m = re.match(r"(?:cat|less|more)\s+([^\s|;&]+)\s*$", seg)
        if m:
            target = Path(m.group(1))
            target = target if target.is_absolute() else cwd / target
            if target.is_file():
                lines, tokens = _file_stats(target)
                if lines > conf["hooks"]["read_max_lines"]:
                    return (f"relay: {target.name} 은 {lines}줄. `sed -n 'A,Bp'` 또는 Read(offset/limit) 로 "
                            f"필요한 범위만 보세요."), tokens
        for noisy in conf["hooks"]["noisy_commands"]:
            if seg.startswith(noisy):
                return (f"relay: `{seg[:60]}` 은 출력이 클 수 있습니다. `| tail -n 60` 이나 "
                        f"`| grep -E 'FAIL|Error'` 처럼 줄여서 다시 실행하세요."), 0
    return None


def write_guard(tool_input: dict, conf: dict, cwd: Path) -> tuple[str, int] | None:
    limit = conf["hooks"]["write_max_tokens"]
    tokens = estimate(tool_input.get("content") or "")
    if not limit or tokens <= limit:
        return None
    return (f"relay: 새로 쓰는 내용 ~{human(tokens)} tok 는 이 세션의 이후 모든 요청에 남습니다. 이 파일은 "
            f"Codex/Antigravity 에 맡기세요 (relay handoff --to codex). 꼭 필요하면 사용자에게 확인을 받으세요."), tokens


GUARDS = {"Read": ("read_guard", read_guard), "Bash": ("bash_guard", bash_guard),
          "Write": ("write_guard", write_guard)}


def pre_tool(data: dict, conf: dict) -> dict | None:
    rule = GUARDS.get(data.get("tool_name") or "")
    if not rule or rule[0] not in conf["hooks"]["enabled"]:
        return None
    cwd = Path(data.get("cwd") or ".")
    hit = rule[1](data.get("tool_input") or {}, conf, cwd)
    if not hit:
        return None
    reason, avoided = hit
    _log(cfg.find_root(cwd), f"guard:{data.get('tool_name')}", reason.removeprefix("relay: ")[:80], avoided)
    return _deny(reason)


def main(event: str, stdin=sys.stdin, stdout=sys.stdout) -> int:
    try:
        data = json.load(stdin)
        cwd = Path(data.get("cwd") or ".")
        conf = cfg.load(cwd)
        if event == "prompt" and "prompt_gate" in conf["hooks"]["enabled"]:
            result = prompt_gate(data, conf)
        elif event == "pretool":
            result = pre_tool(data, conf)
        else:
            result = None
        if result:
            stdout.write(json.dumps(result, ensure_ascii=False))
    except Exception:  # fail-open
        return 0
    return 0


def settings_entries() -> dict:
    return {
        "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "relay hook prompt"}]}],
        "PreToolUse": [{"matcher": "Read|Bash|Write", "hooks": [{"type": "command", "command": "relay hook pretool"}]}],
    }


def install(settings: dict) -> dict:
    """settings dict 에 relay hook 을 병합 (이미 있으면 그대로)."""
    hooks = settings.setdefault("hooks", {})
    for event, entries in settings_entries().items():
        current = hooks.setdefault(event, [])
        command = entries[0]["hooks"][0]["command"]
        if not any(h.get("command") == command for e in current for h in e.get("hooks", [])):
            current.extend(entries)
    return settings
