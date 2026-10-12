import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relay import cli, config, pack, router, statusline, tasks, usage
from relay.tokens import estimate, human


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def assistant(msg_id: str, req: str, read: int, write: int = 0, out: int = 10, ts="2026-10-12T10:00:00Z"):
    return {"type": "assistant", "requestId": req, "timestamp": ts, "cwd": "/p",
            "message": {"id": msg_id, "usage": {"input_tokens": 2, "cache_creation_input_tokens": write,
                                                "cache_read_input_tokens": read, "output_tokens": out}}}


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()


class TokensTest(unittest.TestCase):
    def test_estimate(self):
        self.assertEqual(estimate(""), 0)
        self.assertEqual(estimate("abcd" * 10), 10)
        self.assertEqual(estimate("한글"), 2)

    def test_human(self):
        self.assertEqual(human(1_500_000), "1.5M")
        self.assertEqual(human(2_500), "2.5k")
        self.assertEqual(human(12), "12")


class PackTest(TempDirTest):
    def test_parse_refs_merges_nearby_ranges(self):
        refs = pack.parse_refs("a/b.py:10-20 and a/b.py:25 also a/b.py:100-110, c.ts#L5-L7")
        self.assertIn(("a/b.py", 10, 25), refs)
        self.assertIn(("a/b.py", 100, 110), refs)
        self.assertIn(("c.ts", 5, 7), refs)

    def test_outline_python(self):
        src = 'class A(B):\n    """Doc line.\n\n    more"""\n    def m(self, x: int) -> str:\n        return ""\n\nasync def f():\n    pass\n'
        out = pack.outline_python(src)
        self.assertIn("class A(B):  # L1", out)
        self.assertIn('"""Doc line."""', out)
        self.assertIn("def m(self, x: int) -> str:", out)
        self.assertIn("async def f():", out)
        self.assertNotIn("return", out)

    def test_full_file_degrades_to_outline_over_budget(self):
        body = "def big():\n" + "    x = 1\n" * 2000
        (self.root / "big.py").write_text(body)
        built = pack.build(self.root, full=["big.py"], budget=500)
        self.assertEqual(len(built.sections), 1)
        self.assertIn("outline", built.sections[0].title)
        self.assertLessEqual(built.tokens, 500)

    def test_refs_excerpt_and_missing_file(self):
        (self.root / "m.py").write_text("\n".join(f"line{i}" for i in range(1, 51)))
        built = pack.build(self.root, refs_text="m.py:20-22 gone.py:1-2", budget=5000)
        self.assertIn("line20", built.render())
        self.assertNotIn("line40", built.render())
        self.assertTrue(any("gone.py" in s for s in built.skipped))

    def test_binary_and_ignored_direct_paths_skipped(self):
        (self.root / "__pycache__").mkdir()
        (self.root / "__pycache__" / "m.pyc").write_bytes(b"\0\1binary")
        (self.root / "data.bin").write_bytes(b"ab\0cd")
        built = pack.build(self.root, full=["__pycache__/m.pyc", "data.bin"], budget=5000)
        self.assertEqual(built.sections, [])
        self.assertTrue(any("바이너리" in s for s in built.skipped))
        self.assertNotIn("\0", built.render())

    def test_glob_ignores_vendor_dirs(self):
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "x.py").write_text("def a(): pass")
        (self.root / "y.py").write_text("def b(): pass")
        found = [p.name for p in pack.expand_globs(self.root, ["*.py", "**/*.py"])]
        self.assertEqual(found, ["y.py"])


class UsageTest(TempDirTest):
    def test_claude_dedupes_streamed_duplicates(self):
        write_jsonl(self.root / "projects" / "-p" / "s1.jsonl", [
            {"type": "user"},
            assistant("m1", "r1", read=1000, write=500),
            assistant("m1", "r1", read=1000, write=500),  # 같은 응답의 중복 기록
            assistant("m2", "r2", read=1600, out=40),
        ])
        write_jsonl(self.root / "projects" / "-p" / "s1" / "subagents" / "agent-a.jsonl",
                    [assistant("m3", "r3", read=300)])
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": str(self.root)}):
            sessions = usage.scan_claude()
        main = sessions["s1"]
        self.assertEqual(main.requests, 2)
        self.assertEqual(main.cache_read, 2600)
        self.assertEqual(main.cache_write, 500)
        self.assertEqual(main.max_context, 1602)
        self.assertIn("s1/subagents/agent-a", sessions)
        self.assertEqual(usage.total_of(sessions).requests, 3)

    def test_since_filter(self):
        path = self.root / "t.jsonl"
        write_jsonl(path, [assistant("a", "1", 10, ts="2026-01-01T00:00:00Z"),
                           assistant("b", "2", 20, ts="2026-10-01T00:00:00Z")])
        self.assertEqual(usage.read_claude_transcript(path, since="2026-06-01").cache_read, 20)

    def test_codex_uses_last_cumulative_counter(self):
        def tc(inp, cached, out):
            return {"timestamp": "2026-10-12T10:00:00Z", "type": "event_msg",
                    "payload": {"type": "token_count", "info": {"total_token_usage": {
                        "input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out}}}}
        write_jsonl(self.root / "sessions" / "2026" / "10" / "12" / "rollout-x.jsonl",
                    [tc(100, 0, 10), {"payload": {"type": "token_count", "info": None}}, tc(500, 300, 50)])
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root)}):
            t = usage.scan_codex()["rollout-x"]
        self.assertEqual((t.input, t.cache_read, t.output, t.requests), (200, 300, 50, 2))

    def test_cost_model_is_quadratic_in_session_length(self):
        self.assertEqual(usage.session_cost(3, 100, 10), 3 * 100 + 10 * 3)
        one = usage.plan_cost(100, 1, 20_000, 2_000)
        four = usage.plan_cost(100, 4, 20_000, 2_000)
        self.assertGreater(one / four, 2.5)
        self.assertEqual(usage.plan_cost(10, 3, 1, 0), 10)


class RouterTest(unittest.TestCase):
    def test_routes(self):
        conf = config.DEFAULTS
        self.assertEqual(router.route("결제 모듈 동시성 버그 디버깅", conf)[0].agent, "claude")
        self.assertEqual(router.route("explore where auth is handled", conf)[0].agent, "antigravity")
        self.assertEqual(router.route("CRUD boilerplate and tests", conf)[0].agent, "codex")
        self.assertEqual(router.route("???", conf)[0].agent, conf["default_agent"])


class TasksTest(TempDirTest):
    def test_create_resolve_handoff(self):
        t1 = tasks.create(self.root, "Login 버그 수정!", "claude")
        t2 = tasks.create(self.root, "second", "chatgpt")
        self.assertEqual(t1.name, "001-login-버그-수정")
        self.assertEqual(tasks.resolve(self.root, "1"), t1)
        self.assertEqual(tasks.resolve(self.root, None), t2)
        with self.assertRaises(LookupError):
            tasks.resolve(self.root, "9")
        (t1 / "pack.md").write_text("PACK $HOME ${x}")
        prompt = tasks.handoff(self.root, t1, "claude", ask="검증")
        text = prompt.read_text()
        self.assertIn("검증", text)
        self.assertIn("PACK $HOME ${x}", text)  # pack 내용의 $ 는 치환되지 않는다
        self.assertIn(".relay/tasks/001-login-버그-수정/out-claude.md", text)

    def test_scaffold_maps_underscore_dirs_and_respects_existing(self):
        (self.root / "CLAUDE.md").write_text("mine")
        results = dict(tasks.scaffold(self.root))
        self.assertTrue((self.root / ".claude" / "commands" / "handoff.md").is_file())
        self.assertTrue((self.root / ".agent" / "rules" / "relay.md").is_file())
        self.assertEqual((self.root / "CLAUDE.md").read_text(), "mine")
        self.assertTrue(results["CLAUDE.md"].startswith("skip"))


class StatuslineTest(TempDirTest):
    def test_warns_when_context_large(self):
        path = self.root / "s.jsonl"
        write_jsonl(path, [assistant("a", "1", 50_000), assistant("b", "2", 120_000)])
        line = statusline.render({"model": {"display_name": "Opus"}, "transcript_path": str(path)},
                                 config.DEFAULTS)
        self.assertIn("ctx 120.0k", line)
        self.assertIn("req 2", line)
        self.assertIn("/handoff", line)

    def test_new_session(self):
        self.assertIn("새 세션", statusline.render({}, config.DEFAULTS))


class CliTest(TempDirTest):
    def run_cli(self, *argv) -> str:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.assertEqual(cli.main(list(argv)), 0)
        return buf.getvalue()

    def test_full_flow(self):
        self.run_cli("init", "--statusline")
        settings = json.loads((self.root / ".claude" / "settings.local.json").read_text())
        self.assertEqual(settings["statusLine"]["command"], "relay statusline")
        (self.root / "app.py").write_text("def handler(event):\n    return event\n")
        self.run_cli("task", "new", "app", "구조", "파악")
        task = tasks.resolve(self.root, "1")
        (task / "explore.md").write_text("- app.py:1-2 — entry")
        self.assertIn("pack.md", self.run_cli("pack", "1", "--refs", "explore.md"))
        self.assertIn("def handler", (task / "pack.md").read_text())
        self.assertIn("relay task 001", self.run_cli("handoff", "--to", "codex", "--print"))
        self.assertIn("x", self.run_cli("estimate", "--offload", "0.5", "--after-sessions", "2"))

    def test_config_override(self):
        (self.root / ".relay").mkdir()
        (self.root / ".relay" / "config.toml").write_text("[budget]\nmax_context = 5\n")
        conf = config.load(self.root)
        self.assertEqual(conf["budget"]["max_context"], 5)
        self.assertEqual(conf["budget"]["warn_requests"], 40)


if __name__ == "__main__":
    unittest.main()
