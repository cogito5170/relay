import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relay import attribution, doctor


def line(**kw) -> str:
    return json.dumps(kw)


def assistant(mid, ctx, blocks, thinking=0, output=10):
    return line(type="assistant", requestId="r" + mid, message={
        "id": mid, "content": blocks,
        "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": ctx,
                  "output_tokens": output, "output_tokens_details": {"thinking_tokens": thinking}}})


def tool_result(tid, text):
    return line(type="user", message={"content": [{"type": "tool_result", "tool_use_id": tid, "content": text}]})


class AttributionTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, lines) -> Path:
        p = self.dir / "s.jsonl"
        p.write_text("\n".join(lines) + "\n")
        return p

    def test_costs_sum_to_total_reads(self):
        big = "x" * 4000  # ~1000 tok
        path = self.write([
            line(type="user", message={"content": "hi"}),
            assistant("a", 10_000, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat log"}}]),
            assistant("a", 10_000, [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "cat log"}}]),
            tool_result("t1", big),
            assistant("b", 11_010, [{"type": "tool_use", "id": "t2", "name": "Write",
                                     "input": {"file_path": "a.py", "content": "y" * 2000}}], thinking=300),
            tool_result("t2", "ok"),
            assistant("c", 11_900, [{"type": "text", "text": "done"}]),
            assistant("d", 12_000, []),
        ])
        r = attribution.analyze(path)
        self.assertEqual(r.requests, 4)
        self.assertAlmostEqual(sum(i.cost(r.requests) for i in r.items), r.total, delta=1)
        cats = dict(r.by_category())
        self.assertEqual(cats["base"], 10_000 * 4)
        self.assertGreater(cats["Bash output"], 2_500)  # ~1000 tok × 3회
        self.assertIn("Write/Edit input", cats)
        self.assertIn("thinking", cats)
        self.assertTrue(r.top(1)[0].label.startswith("Bash cat log"))

    def test_residual_goes_to_thinking_when_transcript_hides_it(self):
        """thinking 본문도 usage.thinking_tokens 도 없을 때 직전 응답 output 으로 역산한다."""
        path = self.write([
            assistant("a", 10_000, [{"type": "thinking", "thinking": "", "signature": "sig"},
                                    {"type": "text", "text": "ok"}], output=900),
            assistant("b", 11_000, []),
        ])
        r = attribution.analyze(path)
        cats = dict(r.by_category())
        self.assertAlmostEqual(cats["thinking"], 900 - 1, delta=5)   # "ok" 2자 ≈ 1 토큰
        self.assertAlmostEqual(cats["overhead"], 100, delta=5)       # output 으로 설명 안 되는 몫만 남는다
        self.assertAlmostEqual(sum(i.cost(r.requests) for i in r.items), r.total, delta=1)

    def test_residual_beyond_previous_output_stays_overhead(self):
        """직전 응답 output 으로 설명되지 않는 증가분은 주입·포맷 overhead 로 남긴다."""
        path = self.write([
            assistant("a", 10_000, [], output=100),
            assistant("b", 15_000, []),
        ])
        cats = dict(attribution.analyze(path).by_category())
        self.assertAlmostEqual(cats["thinking"], 100, delta=1)
        self.assertAlmostEqual(cats["overhead"], 4_900, delta=1)

    def test_compaction_ends_previous_items(self):
        path = self.write([
            assistant("a", 50_000, [{"type": "tool_use", "id": "t", "name": "Read", "input": {"file_path": "f"}}]),
            tool_result("t", "z" * 40_000),
            assistant("b", 60_000, []),
            assistant("c", 20_000, []),  # compaction
            assistant("d", 21_000, []),
        ])
        r = attribution.analyze(path)
        read = [i for i in r.items if i.category == "Read output"][0]
        self.assertEqual(read.reads(r.requests), 1)  # req1 에서 읽히고 req2 에서 compaction 으로 사라짐
        self.assertAlmostEqual(sum(i.cost(r.requests) for i in r.items), r.total, delta=1)

    def test_find_transcript(self):
        proj = self.dir / "projects" / attribution.project_dir_name(Path("/work/app"))
        proj.mkdir(parents=True)
        (proj / "abc123.jsonl").write_text("")
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.dir)}):
            self.assertEqual(attribution.find_transcript("abc").name, "abc123.jsonl")
            self.assertEqual(attribution.find_transcript(None, Path("/work/app")).name, "abc123.jsonl")
            with self.assertRaises(LookupError):
                attribution.find_transcript("zzz")


class DoctorTest(unittest.TestCase):
    def test_memory_files_and_imports(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": tmp}):
            root = Path(tmp) / "proj"
            (root / "docs").mkdir(parents=True)
            (root / "docs" / "arch.md").write_text("a" * 400)
            (root / "CLAUDE.md").write_text("rules\n@docs/arch.md\n")
            (root / ".mcp.json").write_text(json.dumps({"mcpServers": {"relay": {"command": "relay", "args": ["mcp"]}}}))
            files = doctor.memory_files(root)
            self.assertTrue(any(p.endswith("CLAUDE.md") for p, _ in files))
            self.assertIn(100, [t for p, t in files if "@import" in p])
            self.assertIn(("project", "relay", "relay mcp"), doctor.mcp_servers(root))
            d = doctor.Diagnosis(base_tokens=[10, 30, 20], weekly_requests=100)
            self.assertEqual((d.base_median, d.weekly_cost(1000)), (20, 100_000))


if __name__ == "__main__":
    unittest.main()
