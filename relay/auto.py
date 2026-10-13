"""사용자 개입 없는 실행: `relay auto` (세션 순환 감독자), `relay delegate` (위임), `relay check` (진단).

relay auto "<목표>"
    Claude 를 headless(`claude -p`, lean 도구)로 짧은 청크 단위로 실행한다. 각 청크는 새 세션이다.
    청크가 끝날 때 Claude 가 .relay/tasks/<id>/state.md 에 `STATUS: DONE|CONTINUE` 와 다음 단계를 남기고,
    감독자가 그 state 로 다음 청크를 연다. 사람이 /clear · /pickup 을 칠 필요가 없다.
    - 컨텍스트가 auto.handoff_context 를 넘으면 PreToolUse hook 이 state.md 쓰기 외 도구를 막고 마무리를 지시한다.
    - Claude 가 state 를 못 쓰고 끝나면 감독자가 같은 세션을 한 번 resume 해서 state 만 쓰게 한다.

relay delegate codex|gemini "<지시>"
    Claude(또는 사람)가 구현은 Codex, 탐색은 Gemini 에게 맡긴다. 결과는 마지막 N줄만 돌려주고 전체는 로그 파일에 둔다.
    → 위임 결과가 Claude 컨텍스트에 쌓이는 양을 제한한다.

relay check [--ping]
    오케스트레이션이 어디서 막히는지: 설치 여부 · 버전 · (선택) 실제 1회 응답.
"""

import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from relay import events, tasks
from relay.pipeline import _fill, parse_claude_json, pick_runner

STATUS_DONE, STATUS_CONTINUE = "DONE", "CONTINUE"

CHUNK_PROMPT = """You are working autonomously on relay task {task_id}. No human will answer questions; decide and proceed.

# Goal
{goal}

# Previous state (written by the previous session)
{state}

# How to work
- Delegate bulk implementation/tests: `relay delegate codex "<precise instructions>"`. Broad repo exploration: `relay delegate gemini "<question>"`. You review and decide.
- Read files with narrow line ranges. Keep outputs short.
- When the goal is complete, or relay tells you the budget is reached, overwrite {state_path} with:
  first line `STATUS: DONE` or `STATUS: CONTINUE`, then sections ## Done, ## Decisions, ## Next, ## Files (max 250 words).
  Then end your turn with one line.
"""

RESUME_PROMPT = ("Stop working. Overwrite {state_path} now: first line `STATUS: DONE` or `STATUS: CONTINUE`, "
                 "then ## Done, ## Decisions, ## Next, ## Files (max 250 words). Then end your turn.")


def state_path(task: Path) -> Path:
    return task / "state.md"


def status_of(text: str) -> str:
    for line in text.splitlines()[:5]:
        if line.strip().upper().startswith("STATUS:"):
            return STATUS_DONE if "DONE" in line.upper() else STATUS_CONTINUE
    return ""


def set_current(root: Path, task: Path) -> None:
    (root / ".relay" / "current").write_text(task.name + "\n", encoding="utf-8")


def current_task(root: Path) -> Path | None:
    """env RELAY_TASK → .relay/current → 가장 최근 task."""
    ref = os.environ.get("RELAY_TASK")
    marker = root / ".relay" / "current"
    if not ref and marker.is_file():
        ref = marker.read_text(encoding="utf-8").strip()
    try:
        return tasks.resolve(root, ref or None)
    except LookupError:
        return None


class Supervisor:
    def __init__(self, root: Path, task: Path, conf: dict, log=print):
        self.root, self.task, self.conf, self.log = root, task, conf, log
        self.a = conf["auto"]
        self.chunks: list[dict] = []

    def _command(self, prompt: str, extra: list[str]) -> list[str]:
        values = {"prompt": prompt, "model": self.a["model"], "effort": self.a["effort"],
                  "tools": self.conf["lean"]["tools"], "max_turns": self.a["chunk_turns"],
                  "permission_mode": self.a["permission_mode"]}
        argv = _fill(self.a["command"], values)
        if self.a["allowed_tools"]:
            argv += ["--allowedTools", *self.a["allowed_tools"]]
        return argv + extra

    def _env(self) -> dict:
        return {**os.environ, "RELAY_AUTO": "1", "RELAY_TASK": self.task.name,
                "RELAY_HANDOFF_CONTEXT": str(self.a["handoff_context"])}

    def _run(self, argv: list[str]) -> tuple[int, str, dict, str]:
        proc = subprocess.run(argv, cwd=self.root, env=self._env(), capture_output=True, text=True,
                              timeout=self.a["timeout"], stdin=subprocess.DEVNULL)
        text, usage = parse_claude_json(proc.stdout)
        try:
            session = json.loads(proc.stdout).get("session_id", "")
        except (json.JSONDecodeError, AttributeError):
            session = ""
        return proc.returncode, text, usage, session

    def run(self, goal: str) -> str:
        state_file = state_path(self.task)
        status = ""
        for n in range(1, self.a["max_chunks"] + 1):
            before = state_file.stat().st_mtime if state_file.is_file() else 0
            prompt = CHUNK_PROMPT.format(task_id=self.task.name, goal=goal, state_path=state_file.relative_to(self.root),
                                         state=state_file.read_text(encoding="utf-8").strip() if state_file.is_file() else "(first session)")
            started = time.monotonic()
            rc, text, usage, session = self._run(self._command(prompt, []))
            wrote = state_file.is_file() and state_file.stat().st_mtime > before
            if not wrote and session:  # state 없이 끝남 → 같은 세션에서 state 만 쓰게 한다
                prefix = self.a["command"][: self.a["command"].index("-p")]
                self._run(prefix + ["-p", RESUME_PROMPT.format(state_path=state_file.relative_to(self.root)),
                          "--resume", session, "--tools", "Write", "--max-turns", "3", "--output-format", "json",
                          "--permission-mode", "acceptEdits"])
                wrote = state_file.is_file() and state_file.stat().st_mtime > before
            status = status_of(state_file.read_text(encoding="utf-8")) if wrote else ""
            ctx = sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
            chunk = {"chunk": n, "rc": rc, "status": status or "NO_STATE", "turns": usage.get("turns", 0),
                     "context_read": ctx, "output": usage.get("output_tokens", 0),
                     "seconds": round(time.monotonic() - started, 1)}
            self.chunks.append(chunk)
            events.append(self.root, client="auto", profile="-", tool="chunk", task=self.task.name, ok=bool(status),
                          tokens_out=0, **{k: v for k, v in chunk.items() if k != "chunk"})
            self.log(f"  chunk {n}: {chunk['status']:<9} {chunk['turns']}턴 · ctx {ctx:,} · out {chunk['output']:,} · {chunk['seconds']}s")
            if status == STATUS_DONE:
                break
            if not wrote:
                self.log("  state.md 를 얻지 못했습니다 → 중단 (로그: relay events)")
                break
        (self.task / "auto.json").write_text(json.dumps({"goal": goal, "status": status, "chunks": self.chunks},
                                                       ensure_ascii=False, indent=2), encoding="utf-8")
        return status or "STOPPED"


# ---------------------------------------------------------------- delegate

DELEGATE_STAGE = {"codex": "implement", "gemini": "explore"}


def delegate(root: Path, conf: dict, agent: str, instruction: str, tail: int) -> tuple[int, str]:
    candidates = conf["runners"][DELEGATE_STAGE[agent]]
    if agent == "gemini":  # explore 후보 중 gemini 를 우선, 없으면 다음 후보
        candidates = sorted(candidates, key=lambda c: c[0] != "gemini")
    template = pick_runner(candidates)
    uses_arg = any("{prompt}" in a for a in template)
    argv = _fill(template, {"prompt": instruction})
    started = time.monotonic()
    proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=conf["pipeline"]["timeout"],
                          input=None if uses_arg else instruction, stdin=subprocess.DEVNULL if uses_arg else None)
    logdir = root / ".relay" / "delegate"
    logdir.mkdir(parents=True, exist_ok=True)
    log = logdir / f"{datetime.now():%Y%m%d-%H%M%S}-{agent}.log"
    full = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr.strip() else "")
    log.write_text(f"$ {' '.join(argv[:3])} …\n{full}", encoding="utf-8")
    lines = full.strip().splitlines()
    short = "\n".join(lines[-tail:])
    events.append(root, client="delegate", profile="-", tool=agent, task=None, ok=proc.returncode == 0, tokens_out=0,
                  runner=template[0], seconds=round(time.monotonic() - started, 1))
    header = f"[{agent} via {template[0]} · exit {proc.returncode} · {len(lines)} lines, showing last {min(tail, len(lines))} · full: {log.relative_to(root)}]"
    return proc.returncode, header + "\n" + short


# ---------------------------------------------------------------- check

CHECKS = [("claude", ["claude", "--version"], ["claude", "-p", "Reply with exactly: OK", "--model", "haiku",
                                               "--max-turns", "1", "--tools", ""]),
          ("codex", ["codex", "--version"], ["codex", "exec", "--sandbox", "read-only", "Reply with exactly: OK"]),
          ("gemini", ["gemini", "--version"], ["gemini", "-p", "Reply with exactly: OK"]),
          ("agy", ["agy", "--help"], None)]


def check(ping: bool, timeout: int = 120) -> list[tuple[str, str, str]]:
    rows = []
    for name, version_cmd, ping_cmd in CHECKS:
        if not shutil.which(version_cmd[0]):
            rows.append((name, "✗ 미설치", ""))
            continue
        try:
            v = subprocess.run(version_cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
            version = (v.stdout or v.stderr).strip().splitlines()[0][:50] if (v.stdout or v.stderr).strip() else f"exit {v.returncode}"
        except (subprocess.TimeoutExpired, OSError) as e:
            rows.append((name, "✗ 실행 실패", str(e)[:60]))
            continue
        if not ping or not ping_cmd:
            note = "비대화형 모드 없음 → 대화형(MCP explorer)으로만" if name == "agy" else ""
            rows.append((name, "✓ 설치됨", f"{version} {note}".strip()))
            continue
        try:
            p = subprocess.run(ping_cmd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
            out = (p.stdout + p.stderr).strip()
            ok = p.returncode == 0 and "OK" in out
            rows.append((name, "✓ 응답" if ok else "✗ 응답 실패", version if ok else out.splitlines()[-1][:80] if out else f"exit {p.returncode}"))
        except subprocess.TimeoutExpired:
            rows.append((name, "✗ 시간 초과", "로그인/승인 대기일 수 있음"))
    return rows
