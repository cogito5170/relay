"""컨텍스트 보관소 (`relay archive` · `relay recall` · prompt/precompact/session hook).

긴 세션의 대화는 두 가지 값을 치른다. 요청마다 전체가 다시 읽히고(토큰), 앞에서 모델이 한 추측이
뒤의 답을 끌고 간다(context biasing). 그래서 컨텍스트가 커지면 오래된 대화를 **세션 밖 파일로
옮기고**, 다음 세션에는 그 파일을 넣지 않는다. 비슷한 것을 다시 물을 때만 꺼낸다.

설계는 se_new 의 `graph/` (깃발 색인) 를 따른다 — **압축 '색인'이지 압축 '보관'이 아니다.**

    .relay/archive/chunks/<해시>.md   원문 그대로 (지우지도 줄이지도 않는다)
    .relay/archive/index.jsonl        찾는 길: {깃발, 요약, 원문 경로, 원문 해시, 종류, 상태} append-only

- 조회는 index.jsonl 만 훑는다 (코드가 읽으므로 모델 토큰 0). 폴더를 열어 파일을 하나씩 읽지 않는다.
- 모델에 들어가는 것은 걸린 기록의 **한 줄 요약**뿐이다. 원문은 `open()` 이 질의 낱말 주변 줄만,
  예산 안에서, 줄 번호를 붙여 돌려준다. 원문 해시가 색인과 다르면 그렇다고 말한다.
- 같은 원문(해시)은 두 번 적지 않는다 — 같은 transcript 를 여러 번 보관해도 색인이 안 부푼다.

기록의 종류는 gentleMonster `memory.py` 와 같은 어휘를 쓴다 (두 저장소의 기억이 섞여도 뜻이 같게).

    kind    user_stated      사용자가 한 말 (사용자 턴)
            source_fact      코드가 읽거나 잰 것 (도구 출력: Read · Bash · Grep …)
            model_inference  모델이 쓴 것 (답변 · 계획 · 모델이 쓴 파일 내용)
            summary          다른 기록의 요약. 그 자체로는 사실이 아니며 `sources` 를 가진다
    status  active · superseded (새 사용자 말이 대체) · retracted (틀린 것으로 밝혀짐)

지키는 규칙 (tests/test_archive.py 가 하나씩 붙든다)
- 아무것도 지우지 않는다. 상태 변경은 같은 id 의 새 줄이고, 조회는 id 별 마지막 줄을 본다.
- 모델 추론은 사실로 올라가지 않는다. 조회 결과에 `추론·미검증` 이 붙고, 같은 점수면 사용자 말과
  코드가 잰 것이 앞선다. 세션 요약(digest)에는 모델의 말을 넣지 않는다 — 앞선 추측이 새 세션을
  끌고 가지 않게.
- 정정이 우선한다. 정정 표현(말고 · 아니라 · instead of …)이 든 사용자 말은 겹치는 예전 기록에
  `conflicts_with` 로 이어지고, 조회 때 정정이 먼저 나오며 예전 기록에는 `이후 정정 있음` 이 붙는다.
  범위가 불분명한 정정으로 예전 기록을 자동으로 덮지는 않는다 — 덮는 것은 `supersede()` 로만.
"""

import hashlib
import json
import re
import time
from collections import Counter
from pathlib import Path

from relay.tokens import estimate
from relay.usage import _iter_jsonl

KINDS = ("user_stated", "source_fact", "model_inference", "summary")
STATUSES = ("active", "superseded", "retracted")
# 얼마나 기대도 되는가 — 어디서 왔는가로 정한다. 모델이 스스로 붙인 확신은 보지 않는다.
TRUST = {"user_stated": 3, "source_fact": 3, "summary": 1, "model_inference": 0}
LABEL = {"user_stated": "사용자", "source_fact": "도구출력", "model_inference": "추론·미검증", "summary": "요약"}

MAX_CHUNK_CHARS = 1_000_000
MAX_FLAGS = 12
SUMMARY_CHARS = 160

CORRECTION_CUE = re.compile(r"말고|아니고|아니라|대신|정정|고쳐|잘못|틀렸|instead of|->|→", re.I)
_WORD = re.compile(r"[0-9A-Za-z가-힣_][0-9A-Za-z가-힣_.\-/]*[0-9A-Za-z가-힣_]|[가-힣A-Za-z]{2,}")
# '도·이·가·만' 은 낱말 끝에도 흔해서(재시도 · 아이 · 평가) 떼지 않는다
_PARTICLE = re.compile(r"(으로|에서|이랑|까지|부터|처럼|은|는|을|를|로|의|에|과|와|랑)$")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "are", "was", "not", "you", "but", "have",
         "그", "이", "저", "것", "수", "등", "및", "좀", "더", "다시", "그리고", "그래서", "하는", "해줘",
         "있다", "없다", "합니다", "했다", "한다", "에서", "으로", "true", "false", "none", "null"}


# ---------------------------------------------------------------- 낱말 · 깃발 (모델 호출 없음)

def terms(text: str) -> list[str]:
    """검색용 낱말. 소문자 · 한국어 조사 떼기 · 경로는 마지막 조각도 함께."""
    out = []
    for w in _WORD.findall(text or ""):
        w = w.lower().strip("./-")
        if re.search(r"[가-힣]$", w) and len(w) > 2:
            w = _PARTICLE.sub("", w)
        if "/" in w:
            out.append(w.rsplit("/", 1)[-1])
        if len(w) >= 2 and not w.isdigit() and w not in _STOP:
            out.append(w)
    return out


def flags_of(text: str, kind: str) -> list[str]:
    """깃발 = 다시 찾을 때 쓸 꼬리표. 사용자 말은 짧으니 낱말 전부, 긴 기록은 잦은 낱말과 파일 이름."""
    words = terms(text)
    if kind == "user_stated":
        ranked = list(dict.fromkeys(words))
    else:
        counts = Counter(words)
        files = [w for w in dict.fromkeys(words) if re.search(r"\.[a-z]{1,5}$", w)]
        ranked = files[:4] + [w for w, c in counts.most_common(MAX_FLAGS * 2) if c >= 2]
    out: list[str] = []
    for f in ranked:
        if f not in out and len(f) <= 40:
            out.append(f)
    return out[:MAX_FLAGS]


def summary_of(text: str) -> str:
    for line in (text or "").splitlines():
        line = line.strip(" #>-*`\t")
        if len(line) >= 4:
            return " ".join(line.split())[:SUMMARY_CHARS]
    return " ".join((text or "").split())[:SUMMARY_CHARS]


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ---------------------------------------------------------------- 저장소

class Archive:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.dir = self.root / ".relay" / "archive"
        self.chunks = self.dir / "chunks"
        self.index_path = self.dir / "index.jsonl"
        self._latest: dict[str, dict] | None = None   # id → 마지막 줄. 한 번 읽고 붙일 때마다 갱신

    # 원장 -----------------------------------------------------------
    def _lines(self) -> tuple[list[dict], int]:
        if not self.index_path.is_file():
            return [], 0
        rows, broken = [], 0
        for line in self.index_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                broken += 1
                continue
            if isinstance(row, dict) and row.get("id") and row.get("kind") in KINDS:
                rows.append(row)
            else:
                broken += 1
        return rows, broken

    def records(self) -> list[dict]:
        """id 별 마지막 줄 (append-only 라 뒤가 최신)."""
        if self._latest is None:
            self._latest = {}
            for row in self._lines()[0]:
                self._latest[row["id"]] = row
        return list(self._latest.values())

    def get(self, rid: str) -> dict | None:
        for r in self.records():
            if r["id"] == rid or r["id"].startswith(rid):
                return r
        return None

    def _append(self, row: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.index_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if self._latest is not None:
            self._latest[row["id"]] = row

    def add(self, text: str, kind: str, session: str = "", turn: int = 0, sources: list[str] | None = None,
            flags: list[str] | None = None, summary: str = "") -> tuple[dict | None, bool]:
        """원문을 chunks/ 에 두고 색인 한 줄을 붙인다. (기록, 새로 적었나). 빈 원문은 (None, False)."""
        if kind not in KINDS:
            raise ValueError(f"알 수 없는 종류: {kind}")
        text = (text or "").strip()[:MAX_CHUNK_CHARS]
        if not text:
            return None, False
        if kind == "summary" and not sources:
            raise ValueError("요약은 sources 가 있어야 한다 — 출처 없는 요약은 사실처럼 읽힌다")
        h = digest(text)
        rid = f"{kind[0]}{h}"
        self.records()
        if rid in self._latest:
            return self._latest[rid], False
        fl = flags if flags is not None else flags_of(text, kind)
        if not fl:
            fl = [w for w in terms(text)[:3]] or [kind]
        self.chunks.mkdir(parents=True, exist_ok=True)
        chunk = self.chunks / f"{h}.md"
        chunk.write_text(text + "\n", encoding="utf-8")
        row = {"id": rid, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "kind": kind, "status": "active", "flags": fl, "summary": summary or summary_of(text),
               "chunk": chunk.relative_to(self.dir).as_posix(), "hash": h, "tokens": estimate(text),
               "lines": text.count("\n") + 1, "session": session, "turn": turn}
        if sources:
            row["sources"] = list(sources)
        if kind == "user_stated" and CORRECTION_CUE.search(text):
            mine = set(fl)
            older = [r for r in self.records() if r["kind"] != "summary" and r["status"] == "active"
                     and len(mine & set(r["flags"])) >= 2]
            if older:
                row["conflicts_with"] = [r["id"] for r in older]
        self._append(row)
        return row, True

    def set_status(self, rid: str, status: str, by: str = "") -> dict:
        if status not in STATUSES:
            raise ValueError(f"알 수 없는 상태: {status}")
        rec = self.get(rid)
        if not rec:
            raise LookupError(f"기록이 없다: {rid}")
        row = {**rec, "status": status, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        if by:
            row["superseded_by"] = by
        self._append(row)
        return row

    def supersede(self, old: str, new: str) -> dict:
        """새 사용자 말(또는 코드가 잰 것)이 예전 기록을 대체한다. 모델 추론으로는 대체하지 않는다."""
        new_rec = self.get(new)
        if not new_rec:
            raise LookupError(f"기록이 없다: {new}")
        if TRUST[new_rec["kind"]] < 3:
            raise ValueError("대체는 사용자 말이나 코드가 잰 것으로만 한다 — 모델 추론은 사실을 덮지 못한다")
        return self.set_status(old, "superseded", by=new_rec["id"])

    def retract(self, rid: str) -> dict:
        return self.set_status(rid, "retracted")

    # 조회 -----------------------------------------------------------
    def search(self, query: str, limit: int = 3, min_score: int = 3, exclude_session: str = "",
               include_inactive: bool = False) -> list[tuple[int, dict]]:
        """깃발 맞음 3점 · 요약 등장 1점. min_score 미만이면 '비슷한 문맥 아님' 으로 보고 버린다.
        순서: 정정(conflicts_with 가 있는 사용자 말) > 점수 > 믿음(TRUST) > 최근."""
        q = set(terms(query))
        if not q:
            return []
        scored = []
        for r in self.records():
            if r["status"] != "active" and not include_inactive:
                continue
            if exclude_session and r.get("session") == exclude_session:
                continue        # 지금 세션의 기록은 이미 컨텍스트에 있다
            flags = r["flags"]
            summ = r["summary"].lower()
            s = 0
            for t in q:
                s += 3 * sum(1 for f in flags if t == f or (len(t) >= 3 and (t in f or f in t)))
                s += 1 if t in summ else 0
            if s >= min_score:
                scored.append((s, r))
        scored.sort(key=lambda x: x[1]["ts"], reverse=True)            # 같으면 최근 것
        scored.sort(key=lambda x: (-bool(x[1].get("conflicts_with")), -x[0], -TRUST[x[1]["kind"]]))
        return scored[:limit]

    def corrected_by(self) -> dict[str, str]:
        """예전 기록 id → 그것과 겹치는 이후 정정(사용자 말) id."""
        out: dict[str, str] = {}
        for r in self.records():
            for old in r.get("conflicts_with", []):
                out.setdefault(old, r["id"])
        return out

    def line(self, r: dict, corrections: dict[str, str] | None = None) -> str:
        """모델에 보여 주는 한 줄. 원문은 넣지 않는다."""
        notes = []
        if r["status"] != "active":
            notes.append(r["status"])
        if r.get("conflicts_with"):
            notes.append("정정: " + ",".join(r["conflicts_with"][:3]))
        if corrections and r["id"] in corrections:
            notes.append(f"이후 정정 있음 {corrections[r['id']]}")
        note = f" ({'; '.join(notes)})" if notes else ""
        return (f"- {r['id']} [{LABEL[r['kind']]}]{note} {r['summary']} "
                f"<{r['lines']}줄 ~{r['tokens']}tok · {'·'.join(r['flags'][:5])}>")

    def open(self, rid: str, query: str = "", lines: tuple[int, int] | None = None, window: int = 2,
             budget: int = 800) -> str:
        """원문의 일부만. lines 가 있으면 그 범위, 없으면 질의 낱말이 있는 줄 ± window. 예산을 넘으면 자른다.
        원문 해시가 색인과 다르면 첫 줄에 경고한다 (낡은 색인이 원문 행세를 하지 않게)."""
        r = self.get(rid)
        if not r:
            raise LookupError(f"기록이 없다: {rid}")
        path = self.dir / r["chunk"]
        if not path.is_file():
            return f"! 원문이 없다 — 색인만 남았다 ({r['chunk']})"
        text = path.read_text(encoding="utf-8").rstrip("\n")
        head = [f"[{r['id']} · {LABEL[r['kind']]} · {r['status']}]"]
        if digest(text) != r["hash"]:
            head.append("! 원문이 색인 때와 다르다 (해시 어긋남) — 이 발췌를 그대로 믿지 마라")
        if r["kind"] == "model_inference":
            head.append("(모델이 쓴 것 — 사실로 쓰기 전에 확인)")
        src = text.splitlines()
        if lines:
            keep = set(range(max(1, lines[0]), min(len(src), lines[1]) + 1))
        else:
            q = set(terms(query))
            hits = [i + 1 for i, ln in enumerate(src) if q & set(terms(ln))] if q else []
            if not hits:
                hits = list(range(1, min(len(src), 2 * window + 1) + 1))
            keep = {j for i in hits for j in range(max(1, i - window), min(len(src), i + window) + 1)}
        out, used, prev = list(head), estimate("\n".join(head)), 0
        for n in sorted(keep):
            if prev and n != prev + 1:
                out.append("   …")
            row = f"{n:>5}| {src[n - 1]}"
            cost = estimate(row) + 1
            if used + cost > budget:
                out.append(f"   … (예산 {budget}tok 에서 자름 — 줄 범위를 좁혀 다시: --lines A-B)")
                break
            out.append(row)
            used += cost
            prev = n
        return "\n".join(out)

    def stats(self) -> dict:
        recs = self.records()
        by_kind = Counter(r["kind"] for r in recs)
        return {"records": len(recs), "active": sum(r["status"] == "active" for r in recs),
                "tokens": sum(r["tokens"] for r in recs), "by_kind": dict(by_kind),
                "broken_lines": self._lines()[1]}


# ---------------------------------------------------------------- transcript → 기록

def _text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return ""


def _tool_label(name: str, inp: dict) -> str:
    for key in ("command", "file_path", "path", "pattern", "url", "query"):
        if inp.get(key):
            return f"{name} {str(inp[key]).splitlines()[0][:120]}"
    return name


def turns(path: Path) -> list[dict]:
    """transcript → 턴 목록. 턴 = 사용자 입력 하나와 그 뒤의 도구 출력·모델 답.
    {"user": str, "facts": [str], "model": [str]} — thinking 은 넣지 않는다."""
    out: list[dict] = []
    tools: dict = {}
    cur = None
    for e in _iter_jsonl(path):
        if e.get("isSidechain") or e.get("type") not in ("user", "assistant"):
            continue
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user":
            blocks = content if isinstance(content, list) else []
            results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
            if results and cur is not None:
                for b in results:
                    label = tools.get(b.get("tool_use_id"), "tool")
                    body = b.get("content")
                    body = body if isinstance(body, str) else _text(body)
                    if body.strip():
                        cur["facts"].append(f"### {label}\n{body.strip()}")
                continue
            text = _text(content).strip()
            if not text or e.get("isMeta"):
                continue
            cur = {"user": text, "facts": [], "model": []}
            out.append(cur)
            continue
        if cur is None:
            continue
        for b in content if isinstance(content, list) else []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text", "").strip():
                cur["model"].append(b["text"].strip())
            elif b.get("type") == "tool_use":
                name, inp = b.get("name", "?"), b.get("input") or {}
                tools[b.get("id")] = _tool_label(name, inp)
                if name in ("Write", "Edit", "MultiEdit") and (inp.get("content") or inp.get("new_string")):
                    cur["model"].append(f"### {name} {inp.get('file_path', '')}\n"
                                        f"{inp.get('content') or inp.get('new_string')}")
    return out


def archive_transcript(root: Path, path: Path, conf: dict | None = None) -> dict:
    """transcript 전체를 보관한다. 같은 원문은 다시 적지 않으므로 여러 번 불러도 된다.
    돌려주는 값: {"turns", "added", "records", "summary_id", "tokens_archived", "digest_tokens"}."""
    ar = Archive(root)
    session = Path(path).stem
    ts = turns(Path(path))
    ids, added, archived = [], 0, 0
    user_lines, fact_labels = [], []
    for i, t in enumerate(ts, 1):
        asked = flags_of(t["user"], "user_stated")[:4]   # 같은 턴의 기록은 그 턴의 질문으로도 찾힌다
        for kind, text in (("user_stated", t["user"]), ("source_fact", "\n\n".join(t["facts"])),
                           ("model_inference", "\n\n".join(t["model"]))):
            own = flags_of(text, kind)
            fl = own if kind == "user_stated" else list(dict.fromkeys(asked + own))[:MAX_FLAGS]
            rec, new = ar.add(text, kind, session=session, turn=i, flags=fl)
            if rec:
                ids.append(rec["id"])
                added += new
                archived += rec["tokens"]
        user_lines.append(summary_of(t["user"]))
        fact_labels += [f.splitlines()[0].removeprefix("### ") for f in t["facts"]]
    summary_id = ""
    if ids:
        rec, new = ar.add(session_digest(session, user_lines, fact_labels), "summary", session=session,
                          sources=ids, summary=f"세션 {session[:8]}: 턴 {len(ts)}개 — " + " / ".join(user_lines[-3:]))
        summary_id = rec["id"]
        added += new
    return {"turns": len(ts), "added": added, "records": len(ids), "summary_id": summary_id,
            "tokens_archived": archived, "session": session}


def session_digest(session: str, user_lines: list[str], fact_labels: list[str], max_items: int = 12) -> str:
    """세션 요약. **사용자가 한 말과 코드가 건드린 대상만** 담는다 — 모델의 답은 넣지 않는다
    (새 세션이 앞 세션의 추측을 사실처럼 이어받지 않게)."""
    files = list(dict.fromkeys(l for l in fact_labels if l))[-max_items:]
    asks = user_lines[-max_items:]
    lines = [f"# 세션 {session} 요약 (요약 — 사실이 아니라 찾는 길. 원문은 sources)", "", "## 사용자 요청"]
    lines += [f"- {a}" for a in asks]
    if files:
        lines += ["", "## 도구가 다룬 대상"] + [f"- {f}" for f in files]
    return "\n".join(lines)


# ---------------------------------------------------------------- hook 에서 쓰는 조각

def recall_context(root: Path, prompt: str, session: str, conf: dict) -> str:
    """비슷한 문맥일 때만 한 줄 요약들을 돌려준다. 아니면 '' (토큰 0)."""
    a = conf["archive"]
    ar = Archive(root)
    if not ar.index_path.is_file():
        return ""
    hits, seen = [], set()
    for score, r in ar.search(prompt, limit=a["recall_max_hits"] * 3, min_score=a["recall_min_score"],
                              exclude_session=session):
        if (r["kind"], r["summary"]) not in seen:      # 같은 파일을 여러 번 읽은 기록은 한 줄만
            seen.add((r["kind"], r["summary"]))
            hits.append((score, r))
    hits = hits[:a["recall_max_hits"]]
    if not hits:
        return ""
    corr = ar.corrected_by()
    out = ["[relay recall] 보관된 이전 대화 중 이 요청과 겹치는 기록 (요약만. 필요할 때만 "
           "`relay recall --open <id> --query \"낱말\"` 로 원문 일부를 보세요. 추론·미검증은 사실이 아닙니다):"]
    used = estimate(out[0])
    for _, r in hits:
        ln = ar.line(r, corr)
        if used + estimate(ln) > a["recall_budget"]:
            break
        out.append(ln)
        used += estimate(ln)
    return "\n".join(out) if len(out) > 1 else ""


def pointer(root: Path) -> str:
    """새 세션 · /clear · 압축 직후 넣는 한 줄. 내용은 넣지 않는다."""
    ar = Archive(root)
    if not ar.index_path.is_file():
        return ""
    st = ar.stats()
    if not st["active"]:
        return ""
    return (f"[relay] 이전 대화 {st['active']}개 기록(~{st['tokens']}tok)은 .relay/archive 에 보관돼 컨텍스트에 없습니다. "
            f"비슷한 요청이면 hook 이 요약을 붙입니다. 직접 찾을 때: `relay recall \"낱말\"` (폴더를 훑지 마세요).")
