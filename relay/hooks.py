"""Claude Code hooks — 규칙을 '부탁'이 아니라 '강제'로 (`relay hook prompt|pretool`).

CLAUDE.md 의 규칙은 모델이 지킬 수도, 안 지킬 수도 있다. hook 은 하네스가 실행하므로 항상 적용되고,
통과할 때는 아무것도 출력하지 않아 토큰 비용이 0 이다.

- prompt  (UserPromptSubmit): 컨텍스트·요청 수가 한도를 넘으면 메시지를 막고 /handoff → /clear 를 안내.
                              막힌 메시지는 모델에 가지 않는다. `[force]` 를 넣으면 통과.
- prompt 는 그 전에 컨텍스트가 archive.trigger_context 를 넘으면 transcript 를 .relay/archive 로 보관하고,
  막지 않는 메시지가 보관된 기록과 겹치면 한 줄 요약만 붙인다 (relay/archive.py).
- precompact (PreCompact):    Claude Code 가 대화를 압축하기 직전에 원문을 보관한다 — 압축이 버린 것을 되찾을 길.
- pretool (PreToolUse):       큰 파일 전체 Read, 너무 넓은 줄 범위 읽기, 출력이 큰 명령의 무제한 실행,
                              (선택) 큰 Write 를 거부하고
                              짧은 이유만 Claude 에게 돌려준다. 이유 수십 토큰으로 수천 토큰의 출력을 막는다.

어떤 예외가 나도 통과시킨다(fail-open). relay 버그가 사용자의 세션을 멈추면 안 된다.
"""

import json
import os
import re
import sys
from pathlib import Path

from relay import archive
from relay import auto
from relay import config as cfg
from relay import events
from relay import supervisor
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


def supervisor_gate(data: dict, conf: dict, ctx: int, session: str) -> dict | None:
    """감시 모델의 캐시된 판정을 즉시 반영한다 (모델 호출 없음 = 0 토큰).

    판정은 세션별로 읽는다. /clear 하면 세션이 바뀌므로 끝난 세션의 판정은 새 세션에 쓰지 않는다.
    판정이 낡았으면 (컨텍스트가 soft 한도를 넘을 때) 갱신을 백그라운드로 띄우고 이번 메시지는 통과시킨다.
    CLEAR 는 막고, 나머지는 안내만 붙인다 — 감시자가 사용자의 작업을 임의로 끊지 않게 한다.
    """
    sup = conf["supervisor"]
    if not sup["enabled"]:
        return None
    root = cfg.find_root(Path(data.get("cwd") or "."))
    if not (root / ".relay").is_dir():
        return None          # 판정을 캐시할 곳이 없으면 매 프롬프트마다 감시 모델을 부르게 된다
    cached = supervisor.read_cache(root, session)
    if not supervisor.is_fresh(cached, sup["max_age"]):
        if ctx >= sup["soft_context"]:
            supervisor.refresh_in_background(root, session)
        return None          # 낡은 판정으로는 막지 않는다
    notice = supervisor.hook_notice(cached, conf)
    if not notice:
        return None
    if cached.get("verdict") == "CLEAR":
        _log(root, "gate:supervisor", f"CLEAR {cached.get('reason', '')}"[:80], ctx)
        return {"decision": "block", "reason": f"{notice}\n(그래도 보내려면 메시지에 {FORCE} 를 넣으세요)"}
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": notice}}


def prompt_gate(data: dict, conf: dict) -> dict | None:
    prompt = data.get("prompt") or ""
    if prompt.lstrip().startswith("/") or FORCE in prompt:
        return None
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return None
    path = Path(transcript)
    ctx, totals = last_context(path)
    if totals.requests == 0:
        return None          # /clear 직후의 새 세션: 이 transcript 에 아직 assistant 응답이 없다
    h = conf["hooks"]
    if ctx < h["gate_context"] and totals.requests < h["gate_requests"]:
        return supervisor_gate(data, conf, ctx, path.stem)
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


def auto_wrapup(data: dict, conf: dict) -> dict | None:
    """relay auto 청크 안에서만: 컨텍스트가 예산을 넘으면 state.md 쓰기 외 모든 도구를 막고 마무리를 지시."""
    if os.environ.get("RELAY_AUTO") != "1":
        return None
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return None
    ctx, _ = last_context(Path(transcript))
    if ctx < int(os.environ.get("RELAY_HANDOFF_CONTEXT") or conf["auto"]["handoff_context"]):
        return None
    cwd = Path(data.get("cwd") or ".")
    task = auto.current_task(cfg.find_root(cwd))
    if not task:
        return None
    target = auto.state_path(task)
    path = Path((data.get("tool_input") or {}).get("file_path") or "")
    path = path if path.is_absolute() else cwd / path
    if data.get("tool_name") in ("Write", "Edit") and path.resolve() == target.resolve():
        return None
    _log(cfg.find_root(cwd), "auto:wrapup", f"ctx={ctx}", 0)
    return _deny(f"컨텍스트 {human(ctx)} 로 이 세션의 예산에 도달했습니다. 다른 작업을 멈추고 {target} 를 "
                 f"`STATUS: DONE` 또는 `STATUS: CONTINUE` 첫 줄 + ## Done / ## Decisions / ## Next / ## Files 로 덮어쓴 뒤 "
                 f"한 줄로 턴을 끝내세요. 다음 세션이 이어받습니다.")


def archive_if_large(data: dict, conf: dict, force: bool = False) -> dict | None:
    """컨텍스트가 크면(또는 force) transcript 를 보관소로 옮긴다. 같은 원문은 다시 안 적으므로 매번 불러도 된다."""
    a = conf["archive"]
    transcript = data.get("transcript_path")
    if not a["enabled"] or not transcript or not Path(transcript).is_file():
        return None
    root = cfg.find_root(Path(data.get("cwd") or "."))
    if not (root / ".relay").is_dir():
        return None
    if not force:
        ctx, _ = last_context(Path(transcript))
        if ctx < a["trigger_context"]:
            return None
    info = archive.archive_transcript(root, Path(transcript), conf)
    if info["added"]:
        events.append(root, client="hook", profile="-", tool="archive", task=f"turns={info['turns']}",
                      ok=True, tokens_out=0, avoided=info["tokens_archived"])
    return info


def recall(data: dict, conf: dict) -> dict | None:
    a = conf["archive"]
    prompt = data.get("prompt") or ""
    if not (a["enabled"] and a["recall"]) or prompt.lstrip().startswith("/"):
        return None
    root = cfg.find_root(Path(data.get("cwd") or "."))
    text = archive.recall_context(root, prompt, Path(data.get("transcript_path") or "").stem, conf)
    if not text:
        return None
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}}


def prompt(data: dict, conf: dict) -> dict | None:
    """보관(필요하면) → 한도 차단 → 아니면 보관 기록 조회. 감시자 안내와 조회 결과는 하나로 합친다."""
    info = archive_if_large(data, conf)
    result = prompt_gate(data, conf) if "prompt_gate" in conf["hooks"]["enabled"] else None
    if result and result.get("decision") == "block":
        if info and info["records"]:
            result["reason"] += (f"\n(이 세션의 대화 {info['turns']}턴은 .relay/archive 에 보관했습니다. "
                                 f"/clear 뒤 비슷한 요청이면 요약이 자동으로 붙습니다.)")
        return result
    found = recall(data, conf)
    if not found:
        return result
    if result:
        extra = result["hookSpecificOutput"]["additionalContext"]
        found["hookSpecificOutput"]["additionalContext"] = extra + "\n\n" + found["hookSpecificOutput"]["additionalContext"]
    return found


def session_start(data: dict, conf: dict) -> dict | None:
    """새 세션 · /clear · 자동 압축 직후 현재 task 의 brief/state 를 컨텍스트에 자동으로 넣는다 (/pickup 불필요)."""
    source = data.get("source") or "startup"
    if source not in conf["hooks"]["session_inject"]:
        return None
    root = cfg.find_root(Path(data.get("cwd") or "."))
    note = archive.pointer(root) if conf["archive"]["enabled"] and source != "startup" else ""
    task = auto.current_task(root)
    parts = [f"## {name}\n{(task / name).read_text(encoding='utf-8')[:3000].strip()}"
             for name in ("brief.md", "state.md") if task and (task / name).is_file()]
    if not parts and not note:
        return None
    text = note
    if parts:
        text = (f"[relay] 현재 작업 {task.name} ({source}). 아래 상태에서 이어서 진행하세요. "
                f"작업 단위가 끝나면 {task.relative_to(task.parents[2]).as_posix()}/state.md 를 갱신하세요.\n\n"
                + "\n\n".join(parts) + (f"\n\n{note}" if note else ""))
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": text}}


def pre_tool(data: dict, conf: dict) -> dict | None:
    wrap = auto_wrapup(data, conf)
    if wrap:
        return wrap
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
        if event == "prompt":
            result = prompt(data, conf)
        elif event == "precompact":
            archive_if_large(data, conf, force=True)
            result = None
        elif event == "pretool":
            result = pre_tool(data, conf)
        elif event == "session":
            result = session_start(data, conf)
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
        "PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": "relay hook pretool"}]}],
        "SessionStart": [{"hooks": [{"type": "command", "command": "relay hook session"}]}],
        "PreCompact": [{"hooks": [{"type": "command", "command": "relay hook precompact"}]}],
    }


def install(settings: dict) -> dict:
    """settings dict 에 relay hook 을 병합 (이미 있으면 matcher 만 최신으로)."""
    hooks = settings.setdefault("hooks", {})
    for event, entries in settings_entries().items():
        current = hooks.setdefault(event, [])
        command = entries[0]["hooks"][0]["command"]
        existing = [e for e in current if any(h.get("command") == command for h in e.get("hooks", []))]
        if existing:
            if "matcher" in entries[0]:
                existing[0]["matcher"] = entries[0]["matcher"]
        else:
            current.extend(entries)
    return settings
