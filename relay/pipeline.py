"""토큰 절감 파이프라인 (`relay run`) 과 lean 모드 (`relay lean`).

    explore (agy) → pack (relay, 0 tok) → implement (Codex) → verify (테스트) → review (Claude lean)
                                                     ↑__________ CHANGES 이면 리뷰 노트를 들고 재구현 ___|

Claude 토큰을 아끼는 장치
- Claude 는 review 단계에서만, 매번 **새 세션**으로 짧게 실행된다 → 세션 길이의 이차 항이 없다.
- review 세션은 도구를 Read/Grep/Glob 로 제한하고 짧은 system prompt 를 쓴다.
  실측(claude -p, Haiku): 기본 35.1k → 도구 제한 6.1k → + 짧은 system prompt 4.0k (-89%).
- review 입력은 brief + git diff + 테스트 결과 꼬리만. 저장소 탐색은 agy 가 이미 했다.
- 각 단계 러너는 config 로 바꿀 수 있고, 설치되지 않은 러너는 다음 후보로 넘어간다.
"""

import json
import os
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from relay import events
from relay import pack as packmod
from relay.tokens import estimate, human

STAGES = ("explore", "pack", "implement", "verify", "review")

REVIEW_SYSTEM_PROMPT = (
    "You are the final reviewer in a token-budgeted pipeline. The brief, diff and test results in the "
    "user message are your context; read files only if essential, with narrow line ranges. "
    "Every assistant turn re-sends the whole conversation, so each extra tool call costs as much as the "
    "context again: decide from the user message if you can, and batch any reads you truly need into one turn. "
    "Do not run shell commands and do not edit files — the diff and test output in the user message are "
    "authoritative, and a command that needs approval will leave this run with no verdict at all. "
    "Output: first line exactly 'VERDICT: APPROVE' or 'VERDICT: CHANGES'. If CHANGES, a numbered list of "
    "concrete fixes (path:line, what to change), max 200 words. No praise, no summary.")

EXPLORE_PROMPT = """You are exploring a repository for task {task_id}. Do not modify files and do not run
shell commands (a command that needs approval ends this run with no report); read the files directly.
Read whatever you need (large context is fine here), then print ONLY this markdown report:

## Summary
(max 5 lines)
## Key locations
- path:start-end — why it matters   (max 15 items, narrow ranges, paths relative to repo root)
## Risks
(fragile spots, hidden dependencies)

# Brief
{brief}
"""

IMPLEMENT_PROMPT = """Implement task {task_id} in this repository. Follow AGENTS.md.
Editing files is what matters: a run that changes nothing is a failure. Run the relevant tests if you can,
but if a shell command is denied, make the edits anyway and say so — the pipeline runs its own verify command.
Finish with a summary (max 150 words): files changed (path:line), checks run, open issues.

# Brief
{brief}
{review_notes}
# Context pack
{pack}
"""

REVIEW_PROMPT = """Review task {task_id}.

# Brief
{brief}

# Test result
{verify}

# Changes
{diff}
"""


@dataclass
class StageResult:
    stage: str
    status: str            # ok | failed | skipped
    seconds: float = 0.0
    runner: str = ""
    detail: str = ""
    claude: dict = field(default_factory=dict)   # review 단계의 Claude 사용량


@dataclass
class RunReport:
    task: str
    iterations: int = 0
    verdict: str = ""
    stages: list[StageResult] = field(default_factory=list)

    @property
    def claude_tokens(self) -> dict:
        total: dict[str, int] = {}
        for s in self.stages:
            for k, v in s.claude.items():
                if isinstance(v, int):
                    total[k] = total.get(k, 0) + v
        return total


class RunnerMissing(Exception):
    pass


def _fill(template: list[str], values: dict) -> list[str]:
    out = []
    for arg in template:
        for key, value in values.items():
            arg = arg.replace("{" + key + "}", str(value))
        out.append(arg)
    return out


def installed_runners(candidates: list[list[str]]) -> list[list[str]]:
    """설치된 러너 후보를 설정 순서대로 모두 반환한다 (앞 후보가 실패하면 다음 후보로 넘어간다)."""
    found = [cmd for cmd in candidates if cmd and shutil.which(cmd[0])]
    if found:
        return found
    names = ", ".join(c[0] for c in candidates if c) or "(none)"
    raise RunnerMissing(f"러너가 설치되어 있지 않습니다: {names}")


def pick_runner(candidates: list[list[str]]) -> list[str]:
    return installed_runners(candidates)[0]


def run_command(template: list[str], prompt: str, values: dict, cwd: Path, timeout: int) -> tuple[int, str, str]:
    """{prompt} 자리표시자가 있으면 인자로, 없으면 stdin 으로 프롬프트를 넘긴다."""
    uses_arg = any("{prompt}" in a for a in template)
    argv = _fill(template, {**values, "prompt": prompt})
    proc = subprocess.run(argv, cwd=cwd, input=None if uses_arg else prompt, capture_output=True,
                          text=True, timeout=timeout, stdin=subprocess.DEVNULL if uses_arg else None)
    return proc.returncode, proc.stdout, proc.stderr


def parse_claude_json(stdout: str) -> tuple[str, dict]:
    """claude -p --output-format json 결과 → (본문, 사용량). JSON 이 아니면 원문 그대로."""
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        return stdout.strip(), {}
    usage = data.get("usage") or {}
    keep = {k: int(usage.get(k) or 0) for k in
            ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")}
    keep["turns"] = int(data.get("num_turns") or 0)
    return str(data.get("result") or "").strip(), keep


# agy 는 헤드리스에서 셸 명령 권한을 물을 수 없어 자동 거부하고, 그 run 은 출력 없이 끝난다
# (`jetski: no output produced — a tool required the "command" permission`).
# 그래서 agy 를 쓰는 후보에는 "명령 대신 파일 도구만" 을 프롬프트 앞에 붙인다.
NO_SHELL_NOTE = ("Do not run shell commands: this run is headless and a command permission request is "
                 "auto-denied, which ends the run with no output at all. Use file read/write tools only. "
                 "The pipeline runs the test command itself afterwards.")

# 한도 소진은 보통 종료 코드 0 + 안내 문구로 온다 (claude 의 "usage limit reached",
# agy 의 "You have exhausted your quota on this model."). 그래야 다음 후보로 넘길 수 있다.
QUOTA_MARKERS = ("exhausted your quota", "usage limit reached", "quota exceeded", "resource_exhausted",
                 "rate limit exceeded", "429 too many requests")


def quota_exhausted(out: str) -> str:
    low = out.lower()
    for marker in QUOTA_MARKERS:
        if marker in low:
            return marker
    return ""


def verdict_of(text: str) -> str:
    """첫 줄의 `VERDICT: ...` 를 읽는다. 모델이 `**VERDICT: APPROVE**` 처럼 꾸며도 받아들인다."""
    for line in text.splitlines():
        line = line.strip().strip("*#`_ ").upper()
        if "VERDICT:" in line:
            return "APPROVE" if "APPROVE" in line.split("VERDICT:", 1)[1] else "CHANGES"
    return "UNKNOWN"


def workspace_fingerprint(root: Path, base_ref: str) -> str:
    """작업 트리의 상태 요약. implement 가 실제로 무엇을 바꿨는지 비교할 때 쓴다 (`.relay/` 제외)."""
    out = []
    for argv in (["git", "status", "--porcelain", "--untracked-files=all"],
                 ["git", "diff", "--stat", base_ref, "--", ".", ":(exclude).relay"]):
        proc = subprocess.run(argv, cwd=root, capture_output=True, text=True, check=False)
        out += [l for l in proc.stdout.splitlines() if ".relay/" not in l]
    return "\n".join(out)


def untracked_files(root: Path) -> list[str]:
    proc = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=root,
                          capture_output=True, text=True, check=False)
    return [p for p in proc.stdout.splitlines() if p and not p.startswith(".relay/")]


class Pipeline:
    def __init__(self, root: Path, task: Path, conf: dict, log=print):
        self.root, self.task, self.conf, self.log = root, task, conf, log
        self.p = conf["pipeline"]
        self.runners = conf["runners"]
        self.report = RunReport(task.name)
        self.values = {"review_model": self.p["review_model"], "review_effort": self.p["review_effort"],
                       "review_max_turns": self.p["review_max_turns"],
                       "review_fallback_model": self.p["review_fallback_model"],
                       "implement_fallback_model": self.p["implement_fallback_model"],
                       "no_shell": NO_SHELL_NOTE,
                       "system_prompt": REVIEW_SYSTEM_PROMPT}

    # ------------------------------------------------------------ helpers

    def _read(self, name: str, empty: str = "") -> str:
        path = self.task / name
        return path.read_text(encoding="utf-8").strip() if path.is_file() else empty

    def _write(self, name: str, text: str) -> None:
        (self.task / name).write_text(text.rstrip() + "\n", encoding="utf-8")

    def _record(self, result: StageResult) -> StageResult:
        self.report.stages.append(result)
        events.append(self.root, client="pipeline", profile="-", tool=result.stage, task=self.task.name,
                      ok=result.status != "failed", tokens_out=0, runner=result.runner,
                      seconds=round(result.seconds, 1))
        extra = ""
        if result.claude:
            c = result.claude
            ctx = c["input_tokens"] + c["cache_creation_input_tokens"] + c["cache_read_input_tokens"]
            extra = f" · Claude {c['turns']}턴 ctx {human(ctx)} out {human(c['output_tokens'])}"
        self.log(f"  [{result.status:^7}] {result.stage:<9} {result.seconds:5.1f}s {result.runner} {result.detail}{extra}")
        return result

    def _agent(self, stage: str, prompt: str, accept=None) -> tuple[StageResult, str]:
        """설치된 러너 후보를 순서대로 시도한다. 실패하면 다음 후보로 넘어간다.

        탐색 CLI 는 과부하(503)나 내부 오류로 비대화형 실행이 통째로 죽는 일이 있다.
        그때 explore 를 건너뛰면 pack 이 비어서 다음 단계 품질이 떨어지므로 다음 후보(codex read-only)로 넘긴다.

        `accept(out)` 를 주면 종료 코드가 0 이어도 출력이 쓸 수 없으면 다음 후보로 넘어간다
        (한도 소진 · max-turns 로 잘린 리뷰처럼 "성공했지만 결론이 없는" 경우).
        """
        started = time.monotonic()
        try:
            candidates = installed_runners(self.runners[stage])
        except RunnerMissing as e:
            return StageResult(stage, "skipped", detail=str(e)), ""
        result, out = StageResult(stage, "failed", runner="-"), ""
        for index, template in enumerate(candidates):
            rc, out, err = run_command(template, prompt, self.values, self.root, self.p["timeout"])
            result = StageResult(stage, "ok" if rc == 0 else "failed", time.monotonic() - started, template[0])
            reason = ""
            if rc != 0:
                tail = (err or out).strip().splitlines()
                reason = tail[-1][:120] if tail else f"exit {rc}"
            elif quota_exhausted(out + err):
                result.status = "failed"
                reason = f"한도 소진 ('{quota_exhausted(out + err)}')"
            elif accept and not accept(out):
                result.status, reason = "failed", "결론 없는 출력 (한도 소진 · 턴 잘림 가능)"
            if not reason:
                break
            result.detail = reason
            if index + 1 < len(candidates):
                self.log(f"  [{'retry':^7}] {stage:<9} {template[0]} 실패: {reason} → {candidates[index + 1][0]} 로 재시도")
        return result, out

    # ------------------------------------------------------------ stages

    def explore(self) -> StageResult:
        if self._read("explore.md"):
            return self._record(StageResult("explore", "skipped", detail="explore.md 있음 (--redo explore 로 재실행)"))
        prompt = EXPLORE_PROMPT.format(task_id=self.task.name, brief=self._read("brief.md", "(no brief)"))
        result, out = self._agent("explore", prompt)
        if result.status == "ok":
            self._write("explore.md", out)
            result.detail = f"explore.md {len(packmod.parse_refs(out))} refs"
        return self._record(result)

    def pack(self) -> StageResult:
        started = time.monotonic()
        built = packmod.build(self.root, refs_text=self._read("explore.md"), outlines=self.p["outline"],
                              budget=self.conf["budget"]["pack_tokens"])
        self._write("pack.md", built.render(packmod.header(self.task.name, built)))
        return self._record(StageResult("pack", "ok", time.monotonic() - started, "relay",
                                        f"{len(built.sections)} sections ~{human(built.tokens)} tok"))

    def implement(self, review_notes: str = "") -> StageResult:
        notes = f"\n# Reviewer requested changes (fix these first)\n{review_notes}\n" if review_notes else ""
        prompt = IMPLEMENT_PROMPT.format(task_id=self.task.name, brief=self._read("brief.md", "(no brief)"),
                                         review_notes=notes, pack=self._read("pack.md", "(no pack)"))
        before = workspace_fingerprint(self.root, self.p["base_ref"])
        result, out = self._agent("implement", prompt)
        if out.strip():
            self._write("out-codex.md", out)
        if result.status == "ok" and workspace_fingerprint(self.root, self.p["base_ref"]) == before:
            # codex 는 사이드카(codex-code-mode-host)가 없으면 "ok" 로 끝나면서 파일을 하나도 못 고친다.
            # 리뷰까지 보내면 조용한 실패를 토큰을 써서 발견하게 되므로 여기서 멈춘다.
            result.status = "failed"
            result.detail = "변경 0 — 러너가 파일을 고치지 못했습니다 (codex 사이드카 누락 · 권한 거부 확인)"
        return self._record(result)

    def verify(self) -> StageResult:
        command = self.p["verify"]
        if not command:
            self._write("verify.md", "(no verify command configured)")
            return self._record(StageResult("verify", "skipped", detail="pipeline.verify 미설정"))
        started = time.monotonic()
        proc = subprocess.run(command, shell=True, cwd=self.root, capture_output=True, text=True,
                              timeout=self.p["timeout"])
        tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-self.p["verify_tail_lines"]:])
        status = "PASS" if proc.returncode == 0 else f"FAIL (exit {proc.returncode})"
        self._write("verify.md", f"$ {command}\n{status}\n\n{tail}")
        return self._record(StageResult("verify", "ok" if proc.returncode == 0 else "failed",
                                        time.monotonic() - started, "shell", status))

    def review(self) -> StageResult:
        diff = packmod.git_diff(self.root, self.p["base_ref"])
        built = packmod.build(self.root, full=untracked_files(self.root), budget=self.p["review_budget"])
        diff_text = (diff or "(no tracked changes)") + ("\n\n# New files\n" + built.render() if built.sections else "")
        if estimate(diff_text) > self.p["review_budget"]:
            diff_text = diff_text[: self.p["review_budget"] * 4] + "\n…(truncated to review_budget)"
        prompt = REVIEW_PROMPT.format(task_id=self.task.name, brief=self._read("brief.md", "(no brief)"),
                                      verify=self._read("verify.md", "(not run)"), diff=diff_text)
        result, out = self._agent("review", prompt, accept=lambda o: verdict_of(parse_claude_json(o)[0]) != "UNKNOWN")
        text, usage = parse_claude_json(out)
        result.claude = usage
        if text:
            self._write("out-claude.md", text)
        if result.status == "ok":
            self.report.verdict = verdict_of(text)
            result.detail = f"VERDICT {self.report.verdict}"
        return self._record(result)

    # ------------------------------------------------------------ orchestration

    def run(self, stages: list[str], max_iterations: int) -> RunReport:
        if "explore" in stages:
            self.explore()
        if "pack" in stages:
            self.pack()
        notes = ""
        for iteration in range(1, max_iterations + 1):
            self.report.iterations = iteration
            self.log(f"  — iteration {iteration}")
            if "implement" in stages:
                r = self.implement(notes)
                if r.status != "ok":
                    self.log("  구현 단계 실패/건너뜀 → 중단. 수동: relay handoff --to codex")
                    break
            if "verify" in stages:
                self.verify()
            if "review" not in stages:
                break
            r = self.review()
            if r.status != "ok" or self.report.verdict != "CHANGES":
                break
            notes = self._read("out-claude.md")
        self._write("run.json", json.dumps({**asdict(self.report), "claude_tokens": self.report.claude_tokens},
                                           ensure_ascii=False, indent=2))
        return self.report


# ---------------------------------------------------------------- lean interactive mode

def lean_argv(conf: dict, root: Path, extra: list[str]) -> list[str]:
    """도구를 줄인 대화형 claude 실행 인자. 실측: 기본 35.1k → Read,Edit,Write,Bash,Grep,Glob 7.9k."""
    lean = conf["lean"]
    argv = ["claude", "--tools", lean["tools"]]
    if lean["strict_mcp"]:
        argv.append("--strict-mcp-config")
        if (root / ".mcp.json").is_file():
            argv += ["--mcp-config", str(root / ".mcp.json")]
    if lean["effort"]:
        argv += ["--effort", lean["effort"]]
    return argv + extra


def exec_lean(conf: dict, root: Path, extra: list[str]) -> int:
    argv = lean_argv(conf, root, extra)
    if not shutil.which(argv[0]):
        raise RuntimeError("claude CLI 가 설치되어 있지 않습니다")
    os.execvp(argv[0], argv)
    return 0  # pragma: no cover
