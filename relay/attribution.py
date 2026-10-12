"""캐시 읽기 원인 분석 (`relay why`).

대화에 들어온 내용은 이후 모든 요청에서 다시 읽힌다. 그래서 내용 하나의 비용은
    (그 내용의 토큰) × (그 뒤에 이어진 요청 수)
다. transcript 에서 요청마다 실제 컨텍스트 증가량(usage)을 읽고, 그 사이에 들어온 블록
(도구 출력, Claude 가 쓴 파일 내용, 사용자 입력)에 증가량을 나눠 귀속시킨다.

블록 크기는 추정치이므로 실제 증가량에 맞춰 비례 조정하고, 남는 부분은 overhead(thinking 등)로 둔다.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from relay.tokens import estimate
from relay.usage import _iter_jsonl, claude_home

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
OUTPUT_CATEGORY = {"Bash": "Bash output", "Read": "Read output", "Grep": "Grep/Glob output",
                   "Glob": "Grep/Glob output", "WebFetch": "Web output", "WebSearch": "Web output"}

ADVICE = {
    "base": "고정 부분 — `relay doctor` 로 MCP 서버·CLAUDE.md 정리",
    "Bash output": "명령 출력 — 훅 bash guard 켜기, 출력은 `| tail -50` / `grep` 으로",
    "Read output": "파일 읽기 — 훅 read guard (큰 파일은 줄 범위로), 탐색은 Antigravity → pack",
    "Write/Edit input": "Claude 가 쓴 내용 — 큰 문서·반복 코드는 Codex/Antigravity 로 (relay handoff --to codex)",
    "Grep/Glob output": "검색 결과 — 탐색은 Antigravity 로, Grep 은 head_limit/좁은 경로로",
    "MCP output": "MCP 응답 — 응답이 큰 MCP 도구는 끄거나 페이지 크기를 줄이기",
    "thinking": "thinking — 도구 루프 안에서 다음 요청들에 계속 남음. 루틴 작업은 effort/thinking 을 낮추기",
    "overhead": "미분류 — 세션 중 주입된 시스템 메시지·도구 스키마, 추정 오차",
    "user prompt": "붙여넣은 입력 — 긴 로그·문서는 파일로 두고 경로만 전달",
}


@dataclass
class Item:
    label: str
    category: str
    tokens: float
    start: int          # 이 내용을 처음 읽은 요청 번호
    end: int = -1       # compaction 으로 사라진 요청 번호 (-1 = 끝까지)

    def reads(self, n: int) -> int:
        return (self.end if self.end >= 0 else n) - self.start

    def cost(self, n: int) -> float:
        return self.tokens * self.reads(n)


@dataclass
class Report:
    path: Path
    requests: int
    total: float
    items: list[Item]

    def by_category(self) -> list[tuple[str, float]]:
        out: dict[str, float] = defaultdict(float)
        for it in self.items:
            out[it.category] += it.cost(self.requests)
        return sorted(out.items(), key=lambda kv: kv[1], reverse=True)

    def top(self, k: int) -> list[Item]:
        return sorted((i for i in self.items if i.category != "base"),
                      key=lambda i: i.cost(self.requests), reverse=True)[:k]


def _describe(name: str, inp: dict) -> str:
    if name == "Bash":
        return str(inp.get("command", "")).splitlines()[0][:70] if inp.get("command") else ""
    for key in ("file_path", "path", "pattern", "url", "query"):
        if inp.get(key):
            return str(inp[key])[:70]
    return ""


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict))
    return ""


def _blocks(entry: dict, tools: dict) -> list[tuple[str, str, int]]:
    """transcript 한 줄 → [(category, label, 추정 토큰)]"""
    content = (entry.get("message") or {}).get("content")
    if entry.get("type") == "user" and isinstance(content, str):
        return [("user prompt", content.strip().splitlines()[0][:70] if content.strip() else "", estimate(content))]
    out = []
    for b in content if isinstance(content, list) else []:
        kind = b.get("type")
        if kind == "text":
            cat = "assistant text" if entry.get("type") == "assistant" else "user prompt"
            out.append((cat, b.get("text", "").strip()[:70], estimate(b.get("text", ""))))
        elif kind == "tool_use":
            name, inp = b.get("name", "?"), b.get("input") or {}
            tools[b.get("id")] = (name, _describe(name, inp))
            cat = "Write/Edit input" if name in WRITE_TOOLS else "tool call"
            out.append((cat, f"{name} {_describe(name, inp)}", estimate(json.dumps(inp, ensure_ascii=False))))
        elif kind == "tool_result":
            name, desc = tools.get(b.get("tool_use_id"), ("?", ""))
            cat = OUTPUT_CATEGORY.get(name) or ("MCP output" if name.startswith("mcp__") else "other tool output")
            out.append((cat, f"{name} {desc}", estimate(_text_of(b.get("content")))))
    return out  # thinking·이미지 등은 overhead 로 처리


def analyze(path: Path) -> Report:
    ctxs: list[int] = []
    items: list[Item] = []
    pending: list[tuple[str, str, int]] = []
    seen: set = set()
    tools: dict = {}
    for entry in _iter_jsonl(path):
        if entry.get("isSidechain") or entry.get("type") not in ("assistant", "user"):
            continue
        msg = entry.get("message") or {}
        usage = msg.get("usage") if entry.get("type") == "assistant" else None
        key = (msg.get("id"), entry.get("requestId"))
        if usage and key not in seen:
            seen.add(key)
            ctx = (int(usage.get("input_tokens") or 0) + int(usage.get("cache_creation_input_tokens") or 0)
                   + int(usage.get("cache_read_input_tokens") or 0))
            i = len(ctxs)
            if i == 0:
                items.append(Item("시스템 프롬프트 + 도구 정의 + CLAUDE.md + 첫 메시지", "base", ctx, 0))
            elif ctx < ctxs[-1]:  # compaction: 이전 내용이 사라지고 요약으로 대체됨
                for it in items:
                    if it.end < 0:
                        it.end = i
                items.append(Item("compaction 이후 컨텍스트", "base", ctx, i))
            else:
                delta = ctx - ctxs[-1]
                est = sum(p[2] for p in pending)
                scale = delta / est if est > delta else 1.0
                items += [Item(label, cat, tok * scale, i) for cat, label, tok in pending if tok]
                residual = delta - est * scale
                if residual > 0:
                    items.append(Item("thinking · 포맷 overhead", "overhead", residual, i))
            ctxs.append(ctx)
            pending = []
            thinking = int((usage.get("output_tokens_details") or {}).get("thinking_tokens") or 0)
            if thinking:  # 도구 루프 안에서는 thinking 블록이 다음 요청 컨텍스트에 남는다
                pending.append(("thinking", f"thinking (req#{i} 응답)", thinking))
        pending += _blocks(entry, tools)
    return Report(path, len(ctxs), float(sum(ctxs)), items)


def project_dir_name(cwd: Path) -> str:
    return "".join(c if c.isalnum() or c == "-" else "-" for c in str(cwd.resolve()))


def find_transcript(ref: str | None, cwd: Path | None = None) -> Path:
    """세션 id 접두어 → transcript. 생략하면 현재 프로젝트(없으면 전체)의 가장 최근 세션."""
    root = claude_home() / "projects"
    candidates = [p for p in root.glob("*/*.jsonl")] if root.is_dir() else []
    if ref:
        matches = [p for p in candidates if p.stem.startswith(ref)]
        if not matches:
            raise LookupError(f"세션을 찾을 수 없습니다: {ref}")
        return max(matches, key=lambda p: p.stat().st_mtime)
    local = [p for p in candidates if p.parent.name == project_dir_name(cwd or Path.cwd())]
    pool = local or candidates
    if not pool:
        raise LookupError(f"Claude Code transcript 가 없습니다 ({root})")
    return max(pool, key=lambda p: p.stat().st_mtime)
