"""Claude Code hooks — 규칙을 '부탁'이 아니라 '강제'로 (`relay hook prompt|pretool`).

CLAUDE.md 의 규칙은 모델이 지킬 수도, 안 지킬 수도 있다. hook 은 하네스가 실행하므로 항상 적용되고,
통과할 때는 아무것도 출력하지 않아 토큰 비용이 0 이다.

- prompt  (UserPromptSubmit): 컨텍스트·요청 수가 한도를 넘으면 메시지를 막고 /handoff → /clear 를 안내.
                              막힌 메시지는 모델에 가지 않는다. `[force]` 를 넣으면 통과.
- pretool (PreToolUse):       예산에 닿으면 상태 파일 쓰기만 남기고 막아 인수인계를 받아낸 뒤(auto_wrapup),
                              큰 파일 전체 Read, 너무 넓은 줄 범위 읽기, 출력이 큰 명령의 무제한 실행,
                              (선택) 큰 Write 를 거부하고
                              짧은 이유만 Claude 에게 돌려준다. 이유 수십 토큰으로 수천 토큰의 출력을 막는다.

어떤 예외가 나도 통과시킨다(fail-open). relay 버그가 사용자의 세션을 멈추면 안 된다.
"""

import json
import os
import re
import sys
from pathlib import Path

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
_SPAN = lambda m: int(m.group(2)) - int(m.group(1)) + 1
RANGES = (
    (re.compile(r"\bsed\b[^|;&]*?-n\s*['\"]?(\d+)\s*,\s*(\d+)\s*p"), _SPAN),
    (re.compile(r"\b(?:head|tail)\b\s+(?:-n\s*|-)(\d+)\b"), lambda m: int(m.group(1))),
    # awk 'NR>=A && NR<=B' · 'NR>A && NR<B' (순서 반대도 받는다)
    (re.compile(r"\bawk\b[^|;&]*?NR\s*>=?\s*(\d+)\s*&&\s*NR\s*<=?\s*(\d+)"), _SPAN),
    (re.compile(r"\bawk\b[^|;&]*?NR\s*<=?\s*(\d+)\s*&&\s*NR\s*>=?\s*(\d+)"),
     lambda m: int(m.group(1)) - int(m.group(2)) + 1),
    # awk 'NR==A,NR==B'
    (re.compile(r"\bawk\b[^|;&]*?NR\s*==\s*(\d+)\s*,\s*NR\s*==\s*(\d+)"), _SPAN),
)
# grep 의 맥락 줄: 일치마다 N 줄이 더 붙으므로 N 이 크면 출력이 몇 배로 늘어난다.
GREP_CONTEXT = re.compile(
    r"\b(?:e?grep|rg|git\s+grep)\b[^|;&]*?"
    r"(?:-[ABC]\s*(\d+)|--(?:after-|before-)?context[=\s](\d+))")
MATCH_CAP = re.compile(r"\s(?:-m\s*\d|--max-count)")
TOKENS_PER_LINE = 12   # 범위만 보고 거부할 때의 거친 환산 (events 의 avoided 용)
NARROW_READ_ADVICE = "Grep 으로 위치를 찾고 {limit}줄 이하로 좁혀서 읽으세요."

# 다음 세션이 이어받을 상태의 형식과, task 폴더가 없는 세션(홈 디렉터리 등)에서 그것을 남길 곳.
WRAPUP_FORMAT = ("첫 줄 `STATUS: DONE` 또는 `STATUS: CONTINUE`, 그 뒤 "
                 "## Done / ## Decisions / ## Next / ## Files (250단어 이내)")
HANDOFF_FALLBACK = "HANDOFF-NEXT.md"
# 대화형 마무리를 거는 지점: gate_context 에 대한 비율. 숫자 차단 직전이어야 하고,
# 그보다 낮으면 아직 여유 있는 세션의 도구 호출까지 막는다.
WRAPUP_GATE_RATIO = 0.85


def handoff_fallback(root: Path) -> Path:
    return root / ".relay" / HANDOFF_FALLBACK


def _marked_task(root: Path) -> Path | None:
    """대화형 마무리가 건드려도 되는 task.

    auto.current_task 는 표시가 없으면 '가장 최근 task' 로 넘어가므로, 관계없는 옛 task 의
    state.md 를 덮어쓸 수 있다. 지금 이 작업이라는 표시(.relay/current · RELAY_TASK)가 있을 때만 쓴다.
    """
    if os.environ.get("RELAY_TASK") or (root / ".relay" / "current").is_file():
        return auto.current_task(root)
    return None


def wrapup_target(root: Path) -> Path:
    """상태를 남길 파일: 표시된 task 의 state.md, 표시가 없으면 .relay/HANDOFF-NEXT.md."""
    task = _marked_task(root)
    return auto.state_path(task) if task else handoff_fallback(root)


def handoff_hint(root: Path) -> str:
    """차단 메시지에 붙이는 인수인계 안내. task 가 없는 세션에도 넘길 곳이 있어야 한다."""
    task = _marked_task(root)
    if task:
        return f"→ /handoff {task.name} → /clear → /pickup {task.name}"
    return (f"→ 먼저 다음 세션에 넘길 상태를 남기세요: /handoff (또는 {handoff_fallback(root)} 에 직접)\n"
            f"  형식: {WRAPUP_FORMAT}\n"
            f"  그다음 /clear → /pickup (새 세션이 이 파일을 자동으로 읽습니다)")


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
        return {"decision": "block", "reason": (f"{notice}\n{handoff_hint(root)}\n"
                                                f"(그래도 보내려면 메시지에 {FORCE} 를 넣으세요)")}
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
    root = cfg.find_root(Path(data.get("cwd") or "."))
    _log(root, "gate:prompt", f"ctx={ctx} req={totals.requests}", ctx)
    return {"decision": "block", "reason": (
        f"relay: 컨텍스트 {human(ctx)} · 요청 {totals.requests}회. 계속하면 이후 요청마다 {human(ctx)} 이상을 다시 읽습니다.\n"
        f"{handoff_hint(root)}\n"
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
    if limit:
        return None
    path = Path(tool_input.get("file_path") or "")
    path = path if path.is_absolute() else cwd / path
    if path.suffix.lower() in BINARY_SUFFIXES or not path.is_file():
        return None
    lines, tokens = _file_stats(path)
    offset = tool_input.get("offset") or 0
    if offset:
        # limit 없는 offset 은 그 줄부터 파일 끝까지(최대 2000줄) 읽는다. offset 이 있다는 것만으로
        # '좁혀 읽었다'고 보면 한도가 무의미해진다.
        rest = max(lines - offset + 1, 0)
        if not max_range or rest <= max_range:
            return None
        return (f"relay: offset {offset} 부터 끝까지는 {rest}줄입니다 (한도 {max_range}줄). 읽은 줄은 이 세션의 "
                f"이후 모든 요청이 다시 읽습니다. limit 을 함께 주어 {max_range}줄 이하로 좁히세요."), \
               (rest - max_range) * TOKENS_PER_LINE
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


def grep_guard(command: str, conf: dict) -> tuple[str, int] | None:
    """`grep -A/-B/-C N` 의 맥락 줄이 한도를 넘으면 거부한다.

    일치 수를 모르니 줄 수로는 못 센다. 맥락이 넓으면 일치 하나가 N 배로 불어나므로 N 자체를 본다.
    `-m` · `--max-count` 로 일치 수를 제한했으면 통과시킨다.
    """
    limit = conf["hooks"]["grep_max_context"]
    if not limit or MATCH_CAP.search(command):
        return None
    for m in GREP_CONTEXT.finditer(command):
        n = int(m.group(1) or m.group(2))
        if n > limit:
            return (f"relay: `{m.group(0)[:40]}` 는 일치마다 맥락 {n}줄을 붙입니다 (한도 {limit}줄). 일치가 "
                    f"여러 개면 출력이 그만큼 불어납니다. 맥락을 좁히거나 `-m N` 으로 일치 수를 제한하세요."), \
                   (n - limit) * TOKENS_PER_LINE
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
    narrow = grep_guard(command, conf)
    if narrow:
        return narrow
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


def _wrapup_limit(conf: dict) -> int:
    """마무리를 강제할 컨텍스트. 음수면 끈다.

    relay auto 안에서는 auto.handoff_context, 대화형에서는 hooks.wrapup_context 이고
    후자가 0 이면 gate_context 의 85% 를 쓴다 — 숫자 차단 바로 앞에서만 걸려야 한다.
    budget.max_context 는 statusline 경고용이라 대화형 세션이 정상적으로 넘기므로 기준으로 쓰지 않는다.
    """
    if os.environ.get("RELAY_AUTO") == "1":
        return int(os.environ.get("RELAY_HANDOFF_CONTEXT") or conf["auto"]["handoff_context"])
    limit = int(conf["hooks"].get("wrapup_context") or 0)
    return limit if limit else int(conf["hooks"]["gate_context"] * WRAPUP_GATE_RATIO)


def _writes_wrapup(data: dict, target: Path, cwd: Path) -> bool:
    """이 도구 호출이 상태 파일을 쓰는 것인가. 마무리 중에는 이것만 통과시킨다."""
    tool_input = data.get("tool_input") or {}
    if data.get("tool_name") in ("Write", "Edit"):
        path = Path(tool_input.get("file_path") or "")
        path = path if path.is_absolute() else cwd / path
        try:
            return path.resolve() == target.resolve()
        except OSError:
            return False
    if data.get("tool_name") == "Bash":
        return target.name in (tool_input.get("command") or "")   # heredoc 으로 쓰는 세션도 있다
    return False


def auto_wrapup(data: dict, conf: dict) -> dict | None:
    """컨텍스트가 예산에 닿으면 상태 파일 쓰기 외 모든 도구를 막고 마무리를 지시한다.

    relay auto 뿐 아니라 대화형 세션에도 적용한다. prompt_gate 는 메시지를 모델에 보내지 않고 막으므로,
    차단 시점에 상태가 안 남아 있으면 다음 세션에 넘길 프롬프트가 없다. 그 전에 여기서 상태를 받아 둔다.
    """
    transcript = data.get("transcript_path")
    if not transcript or not Path(transcript).is_file():
        return None
    limit = _wrapup_limit(conf)
    if limit < 0:
        return None
    ctx, _ = last_context(Path(transcript))
    if ctx < limit:
        return None
    cwd = Path(data.get("cwd") or ".")
    root = cfg.find_root(cwd)
    in_auto = os.environ.get("RELAY_AUTO") == "1"
    task = auto.current_task(root) if in_auto else _marked_task(root)
    if in_auto and not task:
        return None          # auto 는 task 폴더가 전제다
    target = auto.state_path(task) if task else handoff_fallback(root)
    if _writes_wrapup(data, target, cwd):
        return None
    _log(root, "auto:wrapup" if in_auto else "hook:wrapup", f"ctx={ctx}", 0)
    tail = "" if in_auto else " 그다음 사용자가 /clear 하고 /pickup 으로 이어받습니다."
    return _deny(f"컨텍스트 {human(ctx)} 로 이 세션의 예산에 도달했습니다. 다른 작업을 멈추고 {target} 를 "
                 f"{WRAPUP_FORMAT} 로 덮어쓴 뒤 한 줄로 턴을 끝내세요. 다음 세션이 이어받습니다.{tail}")


def session_start(data: dict, conf: dict) -> dict | None:
    """새 세션 · /clear · 자동 압축 직후 현재 task 의 brief/state 를 컨텍스트에 자동으로 넣는다 (/pickup 불필요).

    읽는 곳은 마무리가 쓰는 곳(_marked_task)과 같아야 한다. auto.current_task 의 '가장 최근 task'
    폴백을 쓰면 표시 없는 저장소에서 방금 남긴 HANDOFF-NEXT.md 대신 관계없는 옛 task 가 들어온다.
    """
    source = data.get("source") or "startup"
    if source not in conf["hooks"]["session_inject"]:
        return None
    root = cfg.find_root(Path(data.get("cwd") or "."))
    task = _marked_task(root)
    if task:
        parts = [f"## {name}\n{(task / name).read_text(encoding='utf-8')[:3000].strip()}"
                 for name in ("brief.md", "state.md") if (task / name).is_file()]
        label, target = f"현재 작업 {task.name}", f"{task.relative_to(task.parents[2]).as_posix()}/state.md"
    else:
        # task 폴더가 없는 세션(홈 디렉터리 등)도 마무리 파일로 이어받는다.
        fallback = handoff_fallback(root)
        if not fallback.is_file():
            return None
        parts = [f"## {fallback.name}\n{fallback.read_text(encoding='utf-8')[:3000].strip()}"]
        label, target = "이전 세션 인수인계", f".relay/{HANDOFF_FALLBACK}"
    if not parts:
        return None
    text = (f"[relay] {label} ({source}). 아래 상태에서 이어서 진행하세요. "
            f"작업 단위가 끝나면 {target} 를 갱신하세요.\n\n" + "\n\n".join(parts))
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
        if event == "prompt" and "prompt_gate" in conf["hooks"]["enabled"]:
            result = prompt_gate(data, conf)
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
