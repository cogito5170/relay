"""컨텍스트 보관소 — 압축·조회 전후 토큰 비교와 기억 규칙 (gentleMonster memory.py 와 같은 어휘)."""

import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from relay import archive, config, hooks
from relay.tokens import estimate

TOPICS = [
    ("결제 웹훅 재시도 로직을 추가해줘. 최대 5번, 지수 백오프로.", "relay/webhook.py",
     "def retry_webhook(event):\n    for attempt in range(5):\n        backoff = 2 ** attempt\n"),
    ("로그인 세션 만료 시간을 30분으로 바꿔줘.", "relay/session.py",
     "SESSION_TTL = 1800  # seconds\ndef refresh(token): ...\n"),
    ("pack 예산 초과 시 outline 강등 로직을 설명해줘.", "relay/pack.py",
     "def demote_to_outline(path, budget):\n    return outline(path)\n"),
    ("statusline 에 감시자 판정을 표시해줘.", "relay/statusline.py",
     "def _supervisor_verdict(cwd, conf):\n    return verdict\n"),
]


def write_transcript(path: Path, turns: list[tuple[str, str, str]], filler: int = 120, ctx: int = 80_000) -> Path:
    """turns: (사용자 말, 읽은 파일, 파일 내용). 도구 출력에 filler 줄을 붙여 실제 세션처럼 크게 만든다."""
    rows = []
    for i, (ask, file, body) in enumerate(turns):
        rows.append({"type": "user", "message": {"role": "user", "content": ask}})
        rows.append({"type": "assistant", "requestId": f"r{i}a", "message": {
            "id": f"m{i}a", "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": ctx},
            "content": [{"type": "thinking", "thinking": "", "signature": "x"},
                        {"type": "tool_use", "id": f"t{i}", "name": "Read", "input": {"file_path": file}}]}})
        noise = "\n".join(f"{n:>4}  # {file} 의 다른 부분 (턴 {i}) line {n} unrelated_code_{n % 7}()"
                          for n in range(filler))
        rows.append({"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": body + noise}]}})
        rows.append({"type": "assistant", "requestId": f"r{i}b", "message": {
            "id": f"m{i}b", "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                                      "cache_read_input_tokens": ctx},
            "content": [{"type": "text", "text": f"{file} 를 보니 이렇게 고치면 될 것 같습니다. 아마 원인은 설정값입니다."}]}})
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return path


def transcript_text(path: Path) -> str:
    """보관하지 않았다면 컨텍스트에 남았을 내용 (사용자 말 + 도구 출력 + 모델 답)."""
    out = []
    for t in archive.turns(path):
        out += [t["user"], *t["facts"], *t["model"]]
    return "\n".join(out)


class ArchiveBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / ".relay").mkdir()
        self.conf = copy.deepcopy(config.DEFAULTS)
        self.conf["supervisor"]["enabled"] = False
        self.ar = archive.Archive(self.dir)

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, event, data) -> dict | None:
        (self.dir / ".relay" / "config.toml").write_text("[supervisor]\nenabled = false\n")
        out = io.StringIO()
        self.assertEqual(hooks.main(event, io.StringIO(json.dumps({"cwd": str(self.dir), **data})), out), 0)
        return json.loads(out.getvalue()) if out.getvalue() else None


class TokenComparisonTest(ArchiveBase):
    """압축·조회 전후 토큰 수. 수치는 실패 메시지와 stdout 에 함께 남긴다."""

    def setUp(self):
        super().setUp()
        self.path = write_transcript(self.dir / "s1.jsonl", TOPICS * 5)     # 20턴
        self.info = archive.archive_transcript(self.dir, self.path)

    def test_archive_then_clear_keeps_only_a_pointer(self):
        before = estimate(transcript_text(self.path))
        after = estimate(archive.pointer(self.dir))
        print(f"\n[압축] 보관 전 컨텍스트 ~{before} tok → /clear 후 포인터 ~{after} tok ({after / before:.2%})")
        self.assertGreater(before, 10_000)
        self.assertLess(after, before * 0.02, (before, after))
        self.assertEqual(self.info["turns"], 20)

    def test_index_lookup_vs_scanning_the_folder(self):
        chunks = list((self.ar.dir / "chunks").glob("*.md"))
        scan = sum(estimate(p.read_text(encoding="utf-8")) for p in chunks)   # 폴더를 훑어 전부 읽는 길
        prompt = "웹훅 재시도 백오프 다시 보자"
        hits = self.ar.search(prompt, limit=3)
        self.assertTrue(hits)
        injected = archive.recall_context(self.dir, prompt, "other-session", self.conf)
        best = next(r for _, r in hits if r["kind"] == "source_fact")
        excerpt = self.ar.open(best["id"], query="retry_webhook backoff", budget=self.conf["archive"]["open_budget"])
        lookup = estimate(injected) + estimate(excerpt)
        print(f"\n[조회] 폴더 전체 읽기 ~{scan} tok ({len(chunks)}개 파일) → 색인 요약 ~{estimate(injected)} tok"
              f" + 원문 발췌 ~{estimate(excerpt)} tok = ~{lookup} tok ({lookup / scan:.2%})")
        self.assertIn("retry_webhook", excerpt)
        self.assertLessEqual(estimate(injected), self.conf["archive"]["recall_budget"])
        self.assertLess(lookup, scan * 0.05, (scan, lookup))

    def test_partial_open_vs_whole_chunk(self):
        rec = next(r for r in self.ar.records() if r["kind"] == "source_fact")
        whole = estimate((self.ar.dir / rec["chunk"]).read_text(encoding="utf-8"))
        part = self.ar.open(rec["id"], query="unrelated_code_3", budget=200)
        print(f"\n[부분 읽기] 원문 전체 ~{whole} tok → 발췌 ~{estimate(part)} tok")
        self.assertLessEqual(estimate(part), 200 + 30)
        self.assertIn("예산", part)
        ranged = self.ar.open(rec["id"], lines=(1, 3))
        self.assertEqual(len([l for l in ranged.splitlines() if "| " in l]), 3)

    def test_unrelated_prompt_costs_zero(self):
        self.assertEqual(archive.recall_context(self.dir, "오늘 점심 메뉴 추천", "other", self.conf), "")

    def test_rearchiving_does_not_grow_the_index(self):
        lines = self.ar.index_path.read_text().count("\n")
        again = archive.archive_transcript(self.dir, self.path)
        self.assertEqual(again["added"], 0)
        self.assertEqual(self.ar.index_path.read_text().count("\n"), lines)


class MemoryRulesTest(ArchiveBase):
    """gentleMonster memory.py 의 규칙: 사실/원자료/추론 구분 · 정정 우선 · 지우지 않음."""

    def test_turns_split_by_kind(self):
        path = write_transcript(self.dir / "s.jsonl", TOPICS[:1])
        archive.archive_transcript(self.dir, path)
        kinds = {r["kind"]: r for r in self.ar.records()}
        self.assertIn("웹훅", kinds["user_stated"]["summary"])
        self.assertIn("retry_webhook", (self.ar.dir / kinds["source_fact"]["chunk"]).read_text())
        self.assertIn("고치면", (self.ar.dir / kinds["model_inference"]["chunk"]).read_text())
        self.assertTrue(kinds["summary"]["sources"])

    def test_digest_has_no_model_text(self):
        path = write_transcript(self.dir / "s.jsonl", TOPICS)
        info = archive.archive_transcript(self.dir, path)
        text = (self.ar.dir / self.ar.get(info["summary_id"])["chunk"]).read_text()
        self.assertIn("웹훅", text)
        self.assertNotIn("될 것 같습니다", text)      # 모델의 추측이 새 세션으로 넘어가지 않는다

    def test_inference_is_labelled_and_ranked_below_facts(self):
        self.ar.add("캐시 만료가 원인일 것이다 cache expiry", "model_inference")
        self.ar.add("cache expiry 측정: ttl=1800 확인 cache", "source_fact")
        hits = self.ar.search("cache expiry", min_score=1)
        self.assertEqual(hits[0][1]["kind"], "source_fact")
        line = self.ar.line(next(r for _, r in hits if r["kind"] == "model_inference"))
        self.assertIn("추론·미검증", line)
        opened = self.ar.open(next(r["id"] for _, r in hits if r["kind"] == "model_inference"))
        self.assertIn("사실로 쓰기 전에 확인", opened)

    def test_correction_comes_first_and_marks_the_old_record(self):
        old, _ = self.ar.add("벽 재료는 콘크리트로 하자 wall concrete", "user_stated", session="a")
        new, _ = self.ar.add("벽 재료는 콘크리트 말고 나무로 wall wood", "user_stated", session="b")
        self.assertIn(old["id"], new["conflicts_with"])
        hits = self.ar.search("벽 재료", min_score=1)
        self.assertEqual(hits[0][1]["id"], new["id"])
        corr = self.ar.corrected_by()
        self.assertIn(f"이후 정정 있음 {new['id']}", self.ar.line(self.ar.get(old["id"]), corr))
        self.assertEqual(self.ar.get(old["id"])["status"], "active")   # 범위가 불분명하면 자동으로 덮지 않는다

    def test_supersede_needs_non_model_evidence_and_deletes_nothing(self):
        old, _ = self.ar.add("세션 만료 30분 session ttl", "user_stated")
        guess, _ = self.ar.add("세션 만료는 60분이 맞을 것 session ttl", "model_inference")
        with self.assertRaises(ValueError):
            self.ar.supersede(old["id"], guess["id"])
        new, _ = self.ar.add("세션 만료는 15분으로 바꾼다 session ttl", "user_stated")
        self.ar.supersede(old["id"], new["id"])
        self.assertEqual(self.ar.get(old["id"])["status"], "superseded")
        ids = [r["id"] for _, r in self.ar.search("session ttl", min_score=1)]
        self.assertNotIn(old["id"], ids)
        self.assertIn(old["id"], [r["id"] for _, r in self.ar.search("session ttl", min_score=1,
                                                                     include_inactive=True, limit=10)])
        self.assertTrue((self.ar.dir / old["chunk"]).is_file())      # 원문은 그대로

    def test_summary_requires_sources(self):
        with self.assertRaises(ValueError):
            self.ar.add("요약", "summary")

    def test_hash_mismatch_is_reported(self):
        rec, _ = self.ar.add("원래 내용 original line", "source_fact")
        (self.ar.dir / rec["chunk"]).write_text("바뀐 내용\n")
        self.assertIn("해시 어긋남", self.ar.open(rec["id"]))


class HookIntegrationTest(ArchiveBase):
    def test_large_context_archives_before_the_gate_blocks(self):
        path = write_transcript(self.dir / "big.jsonl", TOPICS, ctx=150_000)
        blocked = self.run_hook("prompt", {"prompt": "계속", "transcript_path": str(path)})
        self.assertEqual(blocked["decision"], "block")
        self.assertIn(".relay/archive", blocked["reason"])
        self.assertTrue(self.ar.records())

    def test_small_context_does_not_archive(self):
        path = write_transcript(self.dir / "small.jsonl", TOPICS[:1], ctx=10_000)
        self.run_hook("prompt", {"prompt": "계속", "transcript_path": str(path)})
        self.assertFalse(self.ar.index_path.exists())

    def test_new_session_recalls_only_similar_requests(self):
        old = write_transcript(self.dir / "old.jsonl", TOPICS, ctx=70_000)
        self.run_hook("prompt", {"prompt": "계속", "transcript_path": str(old)})        # 60k↑ → 보관
        fresh = self.dir / "new.jsonl"
        fresh.write_text("")
        hit = self.run_hook("prompt", {"prompt": "웹훅 재시도 횟수 바꾸자", "transcript_path": str(fresh)})
        ctx = hit["hookSpecificOutput"]["additionalContext"]
        self.assertIn("[relay recall]", ctx)
        self.assertIn("웹훅", ctx)
        self.assertIsNone(self.run_hook("prompt", {"prompt": "점심 뭐 먹지", "transcript_path": str(fresh)}))
        # 같은 세션의 기록은 이미 컨텍스트에 있으므로 다시 붙이지 않는다
        self.assertIsNone(self.run_hook("prompt", {"prompt": "웹훅 재시도", "transcript_path": str(old)}))

    def test_precompact_and_session_pointer(self):
        path = write_transcript(self.dir / "s.jsonl", TOPICS[:2], ctx=5_000)
        self.assertIsNone(self.run_hook("precompact", {"transcript_path": str(path), "trigger": "auto"}))
        self.assertTrue(self.ar.records())                  # 압축 직전에는 크기와 상관없이 보관
        out = self.run_hook("session", {"source": "compact"})
        self.assertIn(".relay/archive", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.run_hook("session", {"source": "startup"}))

    def test_install_registers_precompact(self):
        settings = hooks.install({})
        self.assertIn("PreCompact", settings["hooks"])


INTERIOR = [
    ("젠틀몬스터 성수 플래그십 매장, 벽면은 노출 콘크리트로 하고 무드보드 레이아웃 만들어줘", "out/gm/job.json",
     "room.wall: concrete\nroom.floor: polished_concrete\nbrand: gentle monster\n"),
    ("매장 동선 여유 0.3m 로 도면 다시 그려줘", "out/gm/layout.json", "clearance: 0.3\nstops: 3\n"),
    ("천장 조명은 어두운 갤러리로, 무드보드 레이아웃 색은 검정", "out/gm/moodboard.json", "light: dark_gallery\n"),
]
FASHION = [
    ("의류 컬렉션 룩북 무드보드 레이아웃 잡아줘. 원단은 린넨", "fashion/lookbook.json",
     "fabric: linen\nsilhouette: oversized\n"),
    ("셔츠 패턴 실루엣을 오버핏으로", "fashion/shirt.json", "pattern: oversized shirt\n"),
]


class DomainTest(ArchiveBase):
    """주제가 바뀌면 앞 주제의 기억이 섞이지 않아야 한다 (실내 디자인 → 의류 디자인)."""

    def domains(self):
        return self.conf["archive"]["domains"]

    def archive_session(self, name, turns):
        path = write_transcript(self.dir / f"{name}.jsonl", turns, filler=20)
        return archive.archive_transcript(self.dir, path, self.conf)

    def test_detect_domain(self):
        self.assertEqual(archive.detect_domain(INTERIOR[0][0], self.domains())[0], "interior")
        self.assertEqual(archive.detect_domain(FASHION[0][0], self.domains())[0], "fashion")
        self.assertEqual(archive.detect_domain("오늘 점심 뭐 먹지", self.domains())[0], archive.GENERAL)
        self.assertEqual(archive.detect_domain("무드보드 레이아웃", self.domains())[0], archive.GENERAL)
        self.assertEqual(archive.detect_domain("완벽하게 해줘", self.domains())[0], archive.GENERAL)

    def test_records_carry_the_turn_domain(self):
        self.archive_session("gm", INTERIOR)
        doms = {r["domain"] for r in self.ar.records()}
        self.assertEqual(doms, {"interior"})        # 판정이 약한 턴도 앞 턴의 도메인을 잇는다

    def test_new_domain_does_not_pull_old_domain_memory(self):
        self.archive_session("gm", INTERIOR)
        prompt = "이제 의류 디자인으로 넘어가자. 의류 컬렉션 무드보드 레이아웃 만들어줘"
        unfiltered = self.ar.search(prompt, limit=3, min_score=3)               # 도메인을 안 봤다면
        leaked = estimate("\n".join(self.ar.line(r) for _, r in unfiltered))
        self.assertTrue(unfiltered)                  # 무드보드·레이아웃이 겹쳐 실내 기록이 끌려온다
        out = archive.recall_context(self.dir, prompt, "fresh", self.conf)
        print(f"\n[도메인] 필터 없이 섞여 들어올 실내 기록 ~{leaked} tok ({len(unfiltered)}줄) → 필터 후 0줄,"
              f" 전환 안내 ~{estimate(out)} tok")
        self.assertIn("interior → fashion", out)
        for _, r in unfiltered:
            self.assertNotIn(r["id"], out)
        self.assertNotIn("젠틀몬스터", out)

    def test_each_domain_recalls_its_own(self):
        self.archive_session("gm", INTERIOR)
        self.archive_session("fa", FASHION)
        fa = archive.recall_context(self.dir, "의류 무드보드 레이아웃 다시 보자", "s2", self.conf)
        self.assertIn("린넨", fa)
        self.assertNotIn("젠틀몬스터", fa)
        gm = archive.recall_context(self.dir, "젠틀몬스터 매장 무드보드 레이아웃 다시 보자", "s3", self.conf)
        self.assertIn("젠틀몬스터", gm)
        self.assertNotIn("린넨", gm)
        self.assertIn("fashion → interior", gm)

    def test_learned_flags_skip_shared_words(self):
        self.archive_session("gm", INTERIOR)
        self.archive_session("fa", FASHION)
        learned = self.ar.learned_flags()
        self.assertNotIn("무드보드", learned["interior"])
        self.assertNotIn("무드보드", learned["fashion"])
        self.assertIn("린넨", learned["fashion"])

    def test_corrections_stay_inside_their_domain(self):
        old, _ = self.ar.add("벽면 재료 콘크리트 concrete material", "user_stated", domain="interior")
        new, _ = self.ar.add("원단 재료 콘크리트 말고 린넨 concrete material", "user_stated", domain="fashion")
        self.assertNotIn("conflicts_with", new)
        self.assertEqual(self.ar.get(old["id"])["status"], "active")

    def test_hook_same_session_shift_archives_and_warns(self):
        path = write_transcript(self.dir / "s.jsonl", INTERIOR, filler=20, ctx=5_000)
        self.assertIsNone(self.run_hook("prompt", {"prompt": INTERIOR[0][0], "transcript_path": str(path)}))
        out = self.run_hook("prompt", {"prompt": "이제 의류 컬렉션 룩북 하자", "transcript_path": str(path)})
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("interior → fashion", ctx)
        self.assertIn("/clear", ctx)
        self.assertTrue(self.ar.records())          # 작은 컨텍스트여도 전환 때는 앞 대화를 보관


if __name__ == "__main__":
    unittest.main()
