"""Claude 세션 감시자 (`relay watch`) — 판정은 Claude 가 아닌 모델이 한다.

hook 의 한도 차단은 숫자만 본다 (컨텍스트 N 이상, 요청 M 이상). 그래서 "지금 Claude 가 하는 일이
Claude 가 할 일인가" 는 못 본다. 그 판단을 **codex(1순위) · agy(폴백)** 에게 맡긴다. 감시 자체에
Claude 토큰을 쓰면 감시가 비용이 되므로 러너 후보에 claude 를 넣지 않는다 (`assert_not_claude`).

흐름:
    relay watch            # 스냅샷 만들고 감시 모델에게 물어 판정을 .relay/supervisor/<session>.json 에 캐시
    relay hook prompt      # 캐시된 판정을 즉시 읽어 안내·차단 (모델 호출 없이, 0 토큰)

판정은 네 가지다.
    CONTINUE  그대로 Claude 가 계속한다.
    DELEGATE  다음 작업은 다른 도구로 넘긴다 (탐색·대량 읽기·보일러플레이트).
    HANDOFF   지금 상태를 파일로 넘기고 (`relay handoff`) 세션을 정리할 준비를 한다.
    CLEAR     지금 /clear 한다. 컨텍스트 재독이 작업량보다 커진 상태다.
"""

import json
import subprocess
import time
from pathlib import Path

from relay import attribution
from relay.statusline import last_context
from relay.tokens import human

VERDICTS = ("CONTINUE", "DELEGATE", "HANDOFF", "CLEAR")
CACHE_NAME = "supervisor.json"
CACHE_DIR = "supervisor"

SUPERVISOR_PROMPT = """You supervise a Claude Code session for cost. Claude is the expensive model here;
you are the cheap one, so you decide and Claude obeys. Every Claude request re-reads the whole
conversation, so cost grows with (context size) x (requests), not with the work done.

Rules you enforce:
- Exploration, bulk file reads and mechanical edits belong to other tools (agy for reading a repo,
  codex for implementing), not to Claude.
- Long sessions are the main waste: when the re-read total dwarfs the new input, the session must end.
- A verdict of CONTINUE is correct when the session is short, or when the remaining work is exactly
  the judgement Claude is for (design decisions, debugging, security, final review).

Answer in this exact shape, nothing else:
VERDICT: CONTINUE|DELEGATE|HANDOFF|CLEAR
REASON: one line, max 20 words, cite a number from the snapshot
ACTION: one line, the concrete next command or who takes the next step

# Session snapshot
{snapshot}
"""


def build_snapshot(transcript: Path, conf: dict, recent: int = 8) -> str:
    """감시 모델에게 보낼 압축 사실들. 전사 전체가 아니라 수치와 최근 도구 호출만 (수백 토큰)."""
    report = attribution.analyze(transcript)
    ctx, totals = last_context(transcript)
    budget = conf["budget"]
    total = report.total or 1
    lines = [
        f"session: {transcript.stem[:8]}",
        f"requests: {report.requests}",
        f"last request context: {ctx} tokens (warn at {budget['warn_context']}, max {budget['max_context']})",
        f"context re-read total: {int(report.total)} tokens",
        f"new input across session: {totals.input + totals.cache_write} tokens "
        f"(cache re-reads: {totals.cache_read})",
        "cost by cause:",
    ]
    for cat, cost in report.by_category()[:6]:
        lines.append(f"  {cat}: {cost / total:.0%} ({int(cost)})")
    lines.append("most expensive items (size x later re-reads):")
    for item in report.top(5):
        lines.append(f"  req#{item.start} {int(item.tokens)} tok x {item.reads(report.requests)} = "
                     f"{int(item.cost(report.requests))} — {item.label[:70]}")
    labels = [it.label[:70] for it in report.items
              if it.category in ("Bash output", "Read output", "tool call", "Grep/Glob output")]
    tools = [l for i, l in enumerate(labels) if i == 0 or l != labels[i - 1]][-recent:]   # 호출+결과 중복 제거
    if tools:
        lines.append(f"recent tool activity (last {len(tools)}):")
        lines += [f"  {t}" for t in tools]
    return "\n".join(line for line in lines if line)


def assert_not_claude(candidates: list[list[str]]) -> None:
    """감시자는 Claude 로 돌리지 않는다. 감시가 피감시 대상의 쿼터를 쓰면 의미가 없다."""
    for cmd in candidates:
        if cmd and Path(cmd[0]).name.startswith("claude"):
            raise ValueError("supervisor 러너에 claude 를 쓸 수 없습니다 (감시 비용이 Claude 쿼터로 들어갑니다)")


def parse_verdict(text: str) -> tuple[str, str, str]:
    verdict, reason, action = "", "", ""
    for raw in text.splitlines():
        line = raw.strip().strip("*#`_ ")
        upper = line.upper()
        if not verdict and "VERDICT:" in upper:
            tail = upper.split("VERDICT:", 1)[1]
            verdict = next((v for v in VERDICTS if v in tail), "")
        elif not reason and upper.startswith("REASON:"):
            reason = line.split(":", 1)[1].strip()
        elif not action and upper.startswith("ACTION:"):
            action = line.split(":", 1)[1].strip()
    return verdict, reason, action


def legacy_cache_path(root: Path) -> Path:
    """세션 구분이 없던 옛 캐시 파일. 읽을 때만 본다."""
    return root / ".relay" / CACHE_NAME


def cache_dir(root: Path) -> Path:
    return root / ".relay" / CACHE_DIR


def cache_path(root: Path, session: str = "") -> Path:
    """판정은 세션마다 다른 파일에 저장한다.

    /clear 하면 세션 id(= transcript 파일 이름)가 바뀌므로, 세션별로 나누지 않으면 끝난 세션의
    CLEAR 판정이 새 세션의 첫 메시지를 계속 막는다.
    """
    return cache_dir(root) / f"{session}.json" if session else legacy_cache_path(root)


def _load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def read_cache(root: Path, session: str = "") -> dict | None:
    """session 을 주면 그 세션의 판정만 돌려준다. 다른 세션의 판정은 없는 것으로 본다."""
    cached = _load(cache_path(root, session))
    if cached is None and session:
        legacy = _load(legacy_cache_path(root))
        cached = legacy if legacy and legacy.get("session") == session else None
    if cached and session and cached.get("session") != session:
        return None
    return cached


def write_cache(root: Path, result: dict, keep: int = 20) -> None:
    if not (root / ".relay").is_dir():
        return
    session = result.get("session") or ""
    path = cache_path(root, session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    if session:
        legacy_cache_path(root).unlink(missing_ok=True)   # 세션 구분 없는 옛 파일은 더 읽지 않는다
        files = sorted(cache_dir(root).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for stale in files[keep:]:
            stale.unlink(missing_ok=True)


def is_fresh(cached: dict | None, max_age: int) -> bool:
    return bool(cached) and (time.time() - float(cached.get("ts") or 0)) < max_age


def run(root: Path, transcript: Path, conf: dict) -> dict:
    """감시 모델을 한 번 호출해 판정을 돌려준다. 러너가 다 실패하면 verdict 는 빈 문자열."""
    from relay.pipeline import (NO_SHELL_NOTE, RunnerMissing, installed_runners, quota_exhausted,
                                run_command)

    sup = conf["supervisor"]
    assert_not_claude(sup["runners"])
    snapshot = build_snapshot(transcript, conf)
    prompt = SUPERVISOR_PROMPT.format(snapshot=snapshot)
    result = {"ts": time.time(), "session": transcript.stem, "verdict": "", "reason": "", "action": "",
              "runner": "", "detail": "", "snapshot_tokens": len(snapshot) // 4}
    try:
        candidates = installed_runners(sup["runners"])
    except RunnerMissing as e:
        result["detail"] = str(e)
        return result
    for index, template in enumerate(candidates):
        started = time.monotonic()
        try:
            values = {"supervisor_fallback_model": sup["fallback_model"], "no_shell": NO_SHELL_NOTE}
            rc, out, err = run_command(template, prompt, values, root, sup["timeout"])
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", f"timeout {sup['timeout']}s"
        verdict, reason, action = parse_verdict(out)
        marker = quota_exhausted(out + err)
        if rc == 0 and not marker and verdict:
            result.update(verdict=verdict, reason=reason, action=action, runner=template[0],
                          seconds=round(time.monotonic() - started, 1))
            return result
        tail = (err or out).strip().splitlines()
        if marker:
            result["detail"] = f"한도 소진 ('{marker}')"
        elif rc != 0:
            result["detail"] = tail[-1][:120] if tail else f"exit {rc}"
        else:
            result["detail"] = "결론 없는 출력 (VERDICT 줄이 없습니다)"
        result["runner"] = template[0]
        if index + 1 == len(candidates):
            break
    return result


HOOK_MESSAGES = {
    "DELEGATE": "다음 작업은 다른 도구로 넘기세요",
    "HANDOFF": "작업을 파일로 넘기고 세션을 마무리하세요",
    "CLEAR": "지금 /clear 하세요",
}


def hook_notice(cached: dict | None, conf: dict) -> str:
    """hook 이 즉시 보여줄 한 줄. 모델을 호출하지 않으므로 0 토큰이다."""
    if not cached or cached.get("verdict") in ("", "CONTINUE"):
        return ""
    verdict = cached["verdict"]
    head = f"relay 감시자({cached.get('runner') or '?'}) {verdict}: {HOOK_MESSAGES.get(verdict, '')}"
    body = [f"  이유: {cached['reason']}" if cached.get("reason") else "",
            f"  조치: {cached['action']}" if cached.get("action") else ""]
    return "\n".join([head, *[b for b in body if b]])


def refresh_in_background(root: Path, session: str | None = None) -> None:
    """hook 을 막지 않도록 판정 갱신은 떼어 놓고 돌린다 (출력은 캐시 파일로만 남는다)."""
    argv = ["relay", "watch", "--quiet"]
    if session:
        argv += [session]
    try:
        subprocess.Popen(argv, cwd=root, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        pass


def render(result: dict) -> str:
    if not result.get("verdict"):
        return f"감시자 판정 실패: {result.get('detail') or '알 수 없음'} (러너: {result.get('runner') or '-'})"
    lines = [f"VERDICT: {result['verdict']}  ({result.get('runner')}, {result.get('seconds', 0)}초, "
             f"스냅샷 ~{human(result.get('snapshot_tokens', 0))} tok)"]
    if result.get("reason"):
        lines.append(f"이유: {result['reason']}")
    if result.get("action"):
        lines.append(f"조치: {result['action']}")
    return "\n".join(lines)
