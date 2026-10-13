"""로컬 로그로 토큰 사용량을 측정한다 — "5배 효율"을 감이 아니라 숫자로 확인하기 위함.

- Claude Code: ~/.claude/projects/<project>/<session>.jsonl  (assistant 메시지의 message.usage)
  같은 응답이 스트리밍 조각마다 여러 줄로 기록되므로 (message.id, requestId) 로 중복 제거한다.
- Codex CLI : ~/.codex/sessions/**/rollout-*.jsonl  (token_count 이벤트의 누적 total_token_usage)

핵심 지표
- cache_read 비중: 대부분 이 값이 압도적이다. 요청 1회 = 그 시점 컨텍스트 전체 재독.
- avg_context  : 요청당 평균 컨텍스트. 세션이 길어질수록 커진다.
- requests     : Claude 에 보낸 요청 수 (tool call 1번 = 요청 1번).
  ⇒ cache_read ≈ requests × avg_context. 둘 중 하나를 줄이면 그대로 줄어든다.
"""

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path


@dataclass
class Totals:
    requests: int = 0
    input: int = 0
    cache_write: int = 0
    cache_read: int = 0
    output: int = 0
    max_context: int = 0
    first: str = ""
    last: str = ""
    projects: set = field(default_factory=set)

    @property
    def context_sum(self) -> int:
        return self.input + self.cache_write + self.cache_read

    @property
    def avg_context(self) -> float:
        return self.context_sum / self.requests if self.requests else 0.0

    @property
    def raw(self) -> int:
        return self.context_sum + self.output

    @property
    def cache_read_share(self) -> float:
        return self.cache_read / self.raw if self.raw else 0.0

    def weighted(self, w: dict) -> float:
        return (self.input * w["input"] + self.cache_write * w["cache_write"]
                + self.cache_read * w["cache_read"] + self.output * w["output"])

    def add(self, usage: dict, ts: str = "", project: str = "") -> None:
        inp = int(usage.get("input_tokens") or 0)
        cw = int(usage.get("cache_creation_input_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        self.requests += 1
        self.input += inp
        self.cache_write += cw
        self.cache_read += cr
        self.output += int(usage.get("output_tokens") or 0)
        self.max_context = max(self.max_context, inp + cw + cr)
        if ts:
            self.first = min(self.first or ts, ts)
            self.last = max(self.last, ts)
        if project:
            self.projects.add(project)

    def merge(self, other: "Totals") -> None:
        for name in ("requests", "input", "cache_write", "cache_read", "output"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.max_context = max(self.max_context, other.max_context)
        self.first = min(filter(None, (self.first, other.first)), default="")
        self.last = max(self.last, other.last)
        self.projects |= other.projects

    def as_dict(self) -> dict:
        return {"requests": self.requests, "input": self.input, "cache_write": self.cache_write,
                "cache_read": self.cache_read, "output": self.output, "raw": self.raw,
                "avg_context": round(self.avg_context), "max_context": self.max_context,
                "cache_read_share": round(self.cache_read_share, 4)}


def claude_home() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))


def _iter_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
    except OSError:
        return


def read_claude_transcript(path: Path, since: str = "") -> Totals:
    """Claude Code 세션 transcript 하나를 집계. since 는 ISO 문자열(UTC) 하한."""
    totals = Totals()
    seen: set[tuple] = set()
    for entry in _iter_jsonl(path):
        if entry.get("type") != "assistant":
            continue
        msg = entry.get("message") or {}
        usage = msg.get("usage")
        if not usage:
            continue
        key = (msg.get("id"), entry.get("requestId"))
        if key != (None, None):
            if key in seen:
                continue
            seen.add(key)
        ts = entry.get("timestamp", "")
        if since and ts and ts < since:
            continue
        totals.add(usage, ts, entry.get("cwd", ""))
    return totals


def scan_claude(since: str = "", project_filter: str = "") -> dict[str, Totals]:
    """세션 id → Totals."""
    root = claude_home() / "projects"
    sessions: dict[str, Totals] = {}
    if not root.is_dir():
        return sessions
    # 서브에이전트 transcript(<session>/subagents/*.jsonl)도 같은 한도를 쓰므로 함께 집계한다.
    for path in root.rglob("*.jsonl"):
        rel = path.relative_to(root)
        if project_filter and project_filter not in rel.parts[0]:
            continue
        if since and datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat() < since:
            continue
        t = read_claude_transcript(path, since)
        if t.requests:
            sessions["/".join(rel.parts[1:])[: -len(".jsonl")]] = t
    return sessions


def scan_codex(since: str = "") -> dict[str, Totals]:
    """Codex rollout 파일 → Totals. 누적 카운터의 마지막 값을 세션 합계로 쓴다.

    Codex 의 input_tokens 는 cached_input_tokens 를 포함하므로 분리해서 기록한다.
    """
    root = codex_home() / "sessions"
    sessions: dict[str, Totals] = {}
    if not root.is_dir():
        return sessions
    for path in root.rglob("rollout-*.jsonl"):
        last, ts_first, ts_last, turns = None, "", "", 0
        for entry in _iter_jsonl(path):
            payload = entry.get("payload") or {}
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            total = info.get("total_token_usage")
            if not total:
                continue
            ts = entry.get("timestamp", "")
            turns += 1
            ts_first = ts_first or ts
            ts_last = ts or ts_last
            last = total
        if not last or (since and ts_last and ts_last < since):
            continue
        t = Totals()
        cached = int(last.get("cached_input_tokens") or 0)
        t.add({"input_tokens": max(0, int(last.get("input_tokens") or 0) - cached),
               "cache_read_input_tokens": cached,
               "output_tokens": int(last.get("output_tokens") or 0)}, ts_first)
        t.requests = turns
        t.last = ts_last
        sessions[path.stem] = t
    return sessions


def since_iso(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")


def by_day(sessions: dict[str, Totals]) -> dict[str, Totals]:
    days: dict[str, Totals] = defaultdict(Totals)
    for t in sessions.values():
        days[(t.last or t.first)[:10]].merge(t)
    return dict(sorted(days.items()))


def total_of(sessions: dict[str, Totals]) -> Totals:
    out = Totals()
    for t in sessions.values():
        out.merge(t)
    return out


# ---------------------------------------------------------------- 비용 모델

def session_cost(requests: int, base: int, growth: int) -> int:
    """한 세션의 누적 컨텍스트 재독량 근사.

    요청 i (0부터) 의 컨텍스트 ≈ base + growth·i  (base = 시스템 프롬프트+도구+CLAUDE.md)
    합계 = requests·base + growth·requests·(requests-1)/2   ← 요청 수에 대해 이차식
    """
    return requests * base + growth * requests * (requests - 1) // 2


def plan_cost(total_requests: int, sessions: int, base: int, growth: int) -> int:
    sessions = max(1, sessions)
    per, extra = divmod(total_requests, sessions)
    return sum(session_cost(per + (1 if i < extra else 0), base, growth) for i in range(sessions))
