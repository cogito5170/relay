"""Cloud-safe relay job validation, provider execution, and audit artifacts."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


AGENTS = ("claude", "codex", "agy")
MODES = ("advisory",)
TASK_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
MAX_INSTRUCTION = 4_000
MAX_OUTPUT_TOKENS = 1_200
PROVIDER_TIMEOUT_SECONDS = 90
OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


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


def _response_text(payload: dict) -> str:
    parts = []
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") == "output_text" and content.get("text"):
                parts.append(content["text"])
    if not parts:
        raise RuntimeError("OpenAI response did not contain output_text")
    return "\n".join(parts).strip()


def run_openai(
    request: CloudRequest,
    output_dir: Path,
    *,
    api_key: str,
    model: str,
    now: datetime | None = None,
    opener=urllib.request.urlopen,
) -> dict:
    """Execute one bounded, tool-free advisory request through Responses API."""
    validate(request)
    if request.agent != "codex":
        raise ValueError("Phase 2 supports only the codex route")
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for a live cloud run")
    if not model.strip():
        raise ValueError("OPENAI_MODEL is required for a live cloud run")

    body = {
        "model": model,
        "input": [
            {
                "role": "developer",
                "content": (
                    "You are the read-only advisory stage of an AI workload control plane. "
                    "Answer the user's bounded request. Do not claim to inspect files, run tools, "
                    "or modify systems. State any missing context explicitly."
                ),
            },
            {"role": "user", "content": request.instruction},
        ],
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }
    http_request = urllib.request.Request(
        OPENAI_RESPONSES_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with opener(http_request, timeout=PROVIDER_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(2_000).decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API returned HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise RuntimeError(f"OpenAI API request failed: {exc}") from exc

    answer = _response_text(payload)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    request_payload = {"version": 2, **asdict(request)}
    usage = payload.get("usage", {})
    report = {
        "version": 2,
        "status": "succeeded",
        "created_at": timestamp,
        "request": request_payload,
        "execution": {
            "runner": "github-hosted-container",
            "provider": "openai",
            "model": payload.get("model", model),
            "provider_request_id": payload.get("id"),
            "model_called": True,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
        },
        "usage": {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
    }
    (output_dir / "request.json").write_text(
        json.dumps(request_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "run.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "result.md").write_text(
        "\n".join([
            f"# Cloud relay: {request.task_id}",
            "",
            "- Status: `succeeded`",
            f"- Agent route: `{request.agent}`",
            f"- Provider: `openai`",
            f"- Model: `{report['execution']['model']}`",
            "",
            "## Result",
            "",
            answer,
            "",
        ]),
        encoding="utf-8",
    )
    return report
