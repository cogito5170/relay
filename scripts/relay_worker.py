#!/usr/bin/env python3
"""One-shot worker for explicit relay review and advisory requests.

The worker accepts ``review`` or ``advisory claude|agy`` markers and invokes
relay without a shell. launchd wakes it when the task directory changes and
every 30 seconds as a safety net.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def safe_task_dir(tasks_dir: Path, candidate: Path) -> bool:
    return (
        candidate.parent == tasks_dir
        and candidate.is_dir()
        and candidate.name not in {"", ".", ".."}
        and not candidate.name.startswith(".")
    )


def write_status(path: Path, status: str, detail: str = "") -> None:
    body = f"status={status}\ntime={timestamp()}\n"
    if detail:
        body += f"detail={detail.strip()}\n"
    path.write_text(body, encoding="utf-8")


def valid_review_result(task_dir: Path) -> tuple[bool, str]:
    try:
        report = json.loads((task_dir / "run.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, f"invalid run.json: {exc}"
    reviews = [stage for stage in report.get("stages", []) if stage.get("stage") == "review"]
    if not reviews or reviews[-1].get("status") != "ok":
        return False, "review stage did not complete successfully"
    verdict = report.get("verdict")
    if verdict not in {"APPROVE", "CHANGES"}:
        return False, f"invalid review verdict: {verdict!r}"
    return True, f"verdict={verdict}"


def valid_advisory_result(task_dir: Path, agent: str) -> tuple[bool, str]:
    try:
        report = json.loads((task_dir / f"advisory-{agent}.json").read_text(encoding="utf-8"))
        body = (task_dir / f"out-{agent}.md").read_text(encoding="utf-8").strip()
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        return False, f"invalid advisory result: {exc}"
    if report.get("status") != "ok" or not body:
        return False, f"advisory did not complete: {report.get('error') or 'empty output'}"
    return True, f"agent={agent}; output=out-{agent}.md"


def process_request(root: Path, relay: Path, task_dir: Path, timeout: int) -> bool:
    request = task_dir / "run.request"
    running = task_dir / "run.running"
    done = task_dir / "run.done"
    failed = task_dir / "run.failed"
    log_dir = root / ".relay" / "runner"
    log_path = log_dir / f"{task_dir.name}.log"

    try:
        mode = request.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return True

    parts = mode.split()
    is_review = parts == ["review"]
    is_advisory = len(parts) == 2 and parts[0] == "advisory" and parts[1] in {"claude", "agy"}
    if not (is_review or is_advisory):
        request.replace(failed)
        write_status(failed, "rejected", f"unsupported request: {mode!r}")
        return False

    request.replace(running)
    done.unlink(missing_ok=True)
    failed.unlink(missing_ok=True)
    command = ([str(relay), "run", task_dir.name, "--only", "review", "--review-max-turns", "4"]
               if is_review else [str(relay), "advise", task_dir.name, "--agent", parts[1]])
    started = timestamp()
    try:
        result = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = result.stdout + result.stderr
        log_path.write_text(
            f"started={started}\nfinished={timestamp()}\ncommand={command!r}\n"
            f"exit_code={result.returncode}\n\n{output}",
            encoding="utf-8",
        )
        report_ok, report_detail = (valid_review_result(task_dir) if is_review
                                    else valid_advisory_result(task_dir, parts[1]))
        succeeded = result.returncode == 0 and report_ok
        target = done if succeeded else failed
        running.replace(target)
        detail = f"exit={result.returncode}; {report_detail}"
        write_status(target, "done" if succeeded else "failed", detail)
        return succeeded
    except subprocess.TimeoutExpired as exc:
        output = (exc.stdout or "") + (exc.stderr or "")
        log_path.write_text(
            f"started={started}\nfinished={timestamp()}\ncommand={command!r}\n"
            f"timeout={timeout}\n\n{output}",
            encoding="utf-8",
        )
        running.replace(failed)
        write_status(failed, "failed", f"timeout={timeout}")
        return False


def run_once(root: Path, relay: Path, timeout: int) -> int:
    tasks_dir = root / ".relay" / "tasks"
    runner_dir = root / ".relay" / "runner"
    runner_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    with (runner_dir / "worker.lock").open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0

        ok = True
        for request in sorted(tasks_dir.glob("*/run.request")):
            task_dir = request.parent
            if safe_task_dir(tasks_dir, task_dir):
                ok = process_request(root, relay, task_dir, timeout) and ok
        return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--relay", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    relay = args.relay.expanduser().resolve()
    if not relay.is_file() or not os.access(relay, os.X_OK):
        parser.error(f"relay executable unavailable: {relay}")
    return run_once(root, relay, args.timeout)


if __name__ == "__main__":
    sys.exit(main())
