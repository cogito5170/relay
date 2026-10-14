"""Cloud-safe relay job validation and artifact generation.

Phase 1 intentionally performs no model call.  It proves that a remote runner can
accept a bounded request, package it, and leave auditable artifacts without
depending on a developer laptop or local OAuth session.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


AGENTS = ("claude", "codex", "agy")
MODES = ("advisory",)
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_INSTRUCTION = 4_000


@dataclass(frozen=True)
class CloudRequest:
    task_id: str
    agent: str
    mode: str
    instruction: str


def validate(request: CloudRequest) -> None:
    if not TASK_ID.fullmatch(request.task_id):
        raise ValueError("task-id must be 1-64 lowercase letters, digits, '.', '_' or '-'")
    if request.agent not in AGENTS:
        raise ValueError(f"agent must be one of: {', '.join(AGENTS)}")
    if request.mode not in MODES:
        raise ValueError(f"mode must be one of: {', '.join(MODES)}")
    if not request.instruction.strip():
        raise ValueError("instruction must not be empty")
    if len(request.instruction) > MAX_INSTRUCTION:
        raise ValueError(f"instruction must be at most {MAX_INSTRUCTION} characters")


def run_dry(request: CloudRequest, output_dir: Path, now: datetime | None = None) -> dict:
    """Validate a request and create the Phase 1 audit bundle."""
    validate(request)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    payload = {"version": 1, **asdict(request)}
    report = {
        "version": 1,
        "status": "dry_run",
        "created_at": timestamp,
        "request": payload,
        "execution": {
            "runner": "github-hosted-container",
            "model_called": False,
            "next_boundary": "provider API adapter with repository secret",
        },
    }
    (output_dir / "request.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "result.md").write_text(
        "\n".join([
            f"# Cloud relay: {request.task_id}",
            "",
            "- Status: `dry_run`",
            f"- Agent: `{request.agent}`",
            f"- Mode: `{request.mode}`",
            "- Model called: no",
            "",
            "The GitHub-hosted container accepted and validated this request.",
            "A provider API adapter is deliberately outside Phase 1.",
            "",
        ]),
        encoding="utf-8",
    )
    return report
