"""VM inbox worker (`relay inbox`): run codex / agy for requests that arrive through git.

People and Claude sessions that cannot reach the VM commit
``.relay/tasks/<id>/request.json`` to a watched branch and push. Each pass this
worker fetches, finds requests that have no matching ``response.json``, runs
the installed relay CLI (never code from the branch, never a shell), commits
the results to the same branch and pushes. The VM needs only outbound git
access; nothing listens on the network.

Request shapes (no other keys are accepted)::

    {"action": "image"}
    {"action": "advise", "agent": "agy"}
    {"action": "delegate", "agent": "codex" | "agy", "instruction": "..."}

Every shape may also carry ``"id"`` so the same request can be issued again.
"""

from __future__ import annotations

import argparse
import fcntl
import fnmatch
import hashlib
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

TASKS = ".relay/tasks"
REQUEST = "request.json"
RESPONSE = "response.json"
TASK_NAME = re.compile(r"^[^./\\][^/\\]{0,127}$")
MAX_INSTRUCTION = 4_000
DEFAULT_BRANCHES = ("claude/*",)


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=False)
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def validate(request: object) -> dict:
    """Return a normalized request or raise ValueError. The request is data, never a command line."""
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    action = request.get("action")
    # "id" is free text that only makes a repeated request distinct (it changes the hash).
    allowed = {"image": {"action", "id"}, "advise": {"action", "agent", "id"},
               "delegate": {"action", "agent", "instruction", "id"}}
    if action not in allowed:
        raise ValueError(f"unsupported action: {action!r}")
    extra = set(request) - allowed[action]
    if extra:
        raise ValueError(f"unexpected keys: {', '.join(sorted(extra))}")
    if len(str(request.get("id", ""))) > 64:
        raise ValueError("id must be at most 64 characters")
    if action == "advise" and request.get("agent") != "agy":
        raise ValueError("advise supports agent 'agy' only")
    if action == "delegate":
        if request.get("agent") not in {"codex", "agy"}:
            raise ValueError("delegate agent must be 'codex' or 'agy'")
        instruction = request.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError("delegate needs a non-empty instruction")
        if len(instruction) > MAX_INSTRUCTION:
            raise ValueError(f"instruction must be at most {MAX_INSTRUCTION} characters")
    return dict(request)


def command_for(relay: list[str], task: str, request: dict) -> list[str]:
    if request["action"] == "image":
        return [*relay, "image", task]
    if request["action"] == "advise":
        return [*relay, "advise", task, "--agent", request["agent"]]
    return [*relay, "delegate", request["agent"], request["instruction"]]


def watched_branches(repo: Path, patterns: list[str]) -> list[str]:
    refs = git(repo, "for-each-ref", "--format=%(refname:strip=3)", "refs/remotes/origin/").split()
    return sorted(r for r in refs if r != "HEAD" and any(fnmatch.fnmatchcase(r, p) for p in patterns))


def pending(repo: Path, branch: str) -> list[tuple[str, bytes]]:
    """(task name, raw request bytes) for requests on origin/<branch> without a matching response."""
    ref = f"origin/{branch}"
    # -z: 한글 등 비 ASCII 경로가 따옴표·8진수로 이스케이프되지 않게 한다
    paths = [p for p in git(repo, "ls-tree", "-r", "-z", "--name-only", ref, "--", TASKS).split("\0") if p]
    found = []
    for path in paths:
        parts = path.split("/")
        if len(parts) != 4 or parts[3] != REQUEST:
            continue
        raw = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=repo, capture_output=True,
                             check=True).stdout
        digest = hashlib.sha256(raw).hexdigest()
        response = subprocess.run(["git", "show", f"{ref}:{TASKS}/{parts[2]}/{RESPONSE}"], cwd=repo,
                                  capture_output=True, check=False)
        if response.returncode == 0:
            try:
                if json.loads(response.stdout).get("request_sha256") == digest:
                    continue
            except (json.JSONDecodeError, AttributeError):
                pass
        found.append((parts[2], raw))
    return found


def run_request(repo: Path, relay: list[str], task: str, raw: bytes, timeout: int) -> dict:
    response = {"request_sha256": hashlib.sha256(raw).hexdigest(), "started": timestamp(),
                "status": "rejected", "exit_code": None, "output_tail": ""}
    try:
        if not TASK_NAME.fullmatch(task):
            raise ValueError(f"unsafe task name: {task!r}")
        request = validate(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        response.update(error=str(exc), finished=timestamp())
        return response
    response["action"] = request["action"]
    argv = command_for(relay, task, request)
    try:
        proc = subprocess.run(argv, cwd=repo, capture_output=True, text=True, timeout=timeout, check=False)
        output, response["exit_code"] = proc.stdout + proc.stderr, proc.returncode
        response["status"] = "done" if proc.returncode == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        output = f"{exc.stdout or ''}{exc.stderr or ''}"
        response.update(status="failed", error=f"timeout={timeout}")
    except FileNotFoundError as exc:
        output = ""
        response.update(status="failed", error=f"relay executable unavailable: {exc}")
    response["output_tail"] = output.strip()[-2_000:]
    response["finished"] = timestamp()
    return response


def publish(repo: Path, branch: str, task: str, response: dict, all_changes: bool) -> None:
    task_dir = repo / TASKS / task
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / RESPONSE).write_text(json.dumps(response, ensure_ascii=False, indent=2) + "\n",
                                     encoding="utf-8")
    git(repo, "add", "-A", *([] if all_changes else ["--", f"{TASKS}/{task}"]))
    git(repo, "commit", "-q", "-m", f"relay inbox: {task} {response.get('action', 'request')} "
        f"{response['status']}")
    for attempt in range(2):
        if subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"], cwd=repo,
                          capture_output=True, check=False).returncode == 0:
            return
        if attempt == 0:  # the branch moved while we worked: replay our commit on top
            git(repo, "fetch", "-q", "origin", branch)
            git(repo, "rebase", "-q", f"origin/{branch}")
    raise RuntimeError(f"push to {branch} failed twice")


def run_once(repo: Path, relay: list[str], patterns: list[str], timeout: int, log=print) -> int:
    lock_path = repo / ".git" / "relay-inbox.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        git(repo, "fetch", "-q", "--prune", "origin")
        ok, handled = True, 0
        branches = watched_branches(repo, patterns)
        for branch in branches:
            for task, raw in pending(repo, branch):
                handled += 1
                git(repo, "checkout", "-q", "-f", "-B", "relay-inbox", f"origin/{branch}")
                git(repo, "clean", "-q", "-fd")
                log(f"{timestamp()} {branch} {task}: running")
                response = run_request(repo, relay, task, raw, timeout)
                delegate = response.get("action") == "delegate"
                try:
                    publish(repo, branch, task, response, all_changes=delegate)
                except RuntimeError as exc:
                    log(f"{timestamp()} {branch} {task}: {exc}")
                    ok = False
                    continue
                log(f"{timestamp()} {branch} {task}: {response['status']}")
                ok = ok and response["status"] == "done"
        log(f"{timestamp()} 브랜치 {len(branches)}개 확인, 요청 {handled}건 처리")
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="relay inbox", description=__doc__.splitlines()[0])
    add_arguments(parser)
    return run(parser.parse_args(argv))


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo", type=Path, required=True,
                        help="이 워커 전용 클론 (작업용 체크아웃과 분리)")
    parser.add_argument("--relay", help="실행할 relay (기본: 지금 실행 중인 설치본 = python -m relay)")
    parser.add_argument("--branch", action="append", dest="branches",
                        help=f"감시할 브랜치 glob (반복 가능, 기본: {' '.join(DEFAULT_BRANCHES)})")
    parser.add_argument("--timeout", type=int, default=1_800)


def run(args) -> int:
    repo = args.repo.expanduser().resolve()
    if not (repo / ".git").is_dir():
        raise FileNotFoundError(f"git 클론이 아닙니다: {repo}")
    # 워커는 클론을 요청 브랜치로 체크아웃하므로, 클론 안의 코드가 아니라 설치된 relay 를 실행한다.
    relay = [args.relay] if args.relay else [sys.executable, "-m", "relay"]
    return run_once(repo, relay, args.branches or list(DEFAULT_BRANCHES), args.timeout)


if __name__ == "__main__":
    sys.exit(main())
