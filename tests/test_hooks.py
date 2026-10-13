import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

from relay import config, hooks


def transcript(path: Path, ctx: int, requests: int = 1) -> Path:
    rows = [{"type": "assistant", "requestId": f"r{i}", "message": {"id": f"m{i}", "usage": {
        "input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": ctx}}}
        for i in range(requests)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


class HooksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        (self.dir / ".relay").mkdir()
        self.conf = copy.deepcopy(config.DEFAULTS)
        (self.dir / "big.py").write_text("x = 1\n" * 1000)
        (self.dir / "small.py").write_text("x = 1\n")

    def tearDown(self):
        self._tmp.cleanup()

    def run_hook(self, event, data) -> dict | None:
        out = io.StringIO()
        self.assertEqual(hooks.main(event, io.StringIO(json.dumps({"cwd": str(self.dir), **data})), out), 0)
        return json.loads(out.getvalue()) if out.getvalue() else None

    def pretool(self, tool, **tool_input):
        return self.run_hook("pretool", {"tool_name": tool, "tool_input": tool_input})

    def test_prompt_gate(self):
        big = transcript(self.dir / "big.jsonl", 150_000)
        small = transcript(self.dir / "small.jsonl", 20_000)
        blocked = self.run_hook("prompt", {"prompt": "계속해", "transcript_path": str(big)})
        self.assertEqual(blocked["decision"], "block")
        self.assertIn("/handoff", blocked["reason"])
        self.assertIsNone(self.run_hook("prompt", {"prompt": "계속해 [force]", "transcript_path": str(big)}))
        self.assertIsNone(self.run_hook("prompt", {"prompt": "/handoff 003", "transcript_path": str(big)}))
        self.assertIsNone(self.run_hook("prompt", {"prompt": "계속해", "transcript_path": str(small)}))
        many = transcript(self.dir / "many.jsonl", 20_000, requests=80)
        self.assertEqual(self.run_hook("prompt", {"prompt": "x", "transcript_path": str(many)})["decision"], "block")

    def test_prompt_gate_judges_only_the_current_session(self):
        """/clear 하면 transcript 가 바뀐다. 이전 세션이 커도 새 세션의 첫 메시지는 통과해야 한다."""
        big = transcript(self.dir / "old.jsonl", 20_000, requests=242)
        self.assertEqual(self.run_hook("prompt", {"prompt": "x", "transcript_path": str(big)})["decision"], "block")
        fresh = self.dir / "new.jsonl"
        fresh.write_text("")                      # /clear 직후: assistant 응답이 아직 없다
        self.assertIsNone(self.run_hook("prompt", {"prompt": "x", "transcript_path": str(fresh)}))
        fresh.write_text(json.dumps({"type": "user", "message": {"content": "x"}}) + "\n")
        self.assertIsNone(self.run_hook("prompt", {"prompt": "x", "transcript_path": str(fresh)}))

    def test_read_guard(self):
        denied = self.pretool("Read", file_path=str(self.dir / "big.py"))
        out = denied["hookSpecificOutput"]
        self.assertEqual((out["hookEventName"], out["permissionDecision"]), ("PreToolUse", "deny"))
        self.assertIn("offset/limit", out["permissionDecisionReason"])
        self.assertTrue(out["permissionDecisionReason"].startswith(hooks.POLICY_PREFIX))
        self.assertIsNone(self.pretool("Read", file_path=str(self.dir / "big.py"), offset=10, limit=50))
        self.assertIsNone(self.pretool("Read", file_path="small.py"))
        self.assertIsNone(self.pretool("Read", file_path="missing.py"))

    def test_bash_guard(self):
        self.assertIsNotNone(self.pretool("Bash", command="pytest tests/"))
        self.assertIsNotNone(self.pretool("Bash", command="cd app && npm test"))
        self.assertIsNotNone(self.pretool("Bash", command="cat big.py"))
        for ok in ("pytest -q", "pytest 2>&1 | tail -n 60", "git log --oneline -5", "git log -n 3",
                   "cat small.py", "ls", "npm test > out.txt"):
            self.assertIsNone(self.pretool("Bash", command=ok), ok)

    def test_read_guard_rejects_wide_line_range(self):
        limit = config.DEFAULTS["hooks"]["read_max_range"]
        denied = self.pretool("Read", file_path="big.py", offset=1, limit=limit + 1)
        reason = denied["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(f"{limit}줄 이하로 좁혀서", reason)
        self.assertIn("Grep", reason)
        self.assertIsNone(self.pretool("Read", file_path="big.py", offset=1, limit=limit))
        self.assertIsNone(self.pretool("Read", file_path="big.py", offset=900))

    def test_bash_guard_rejects_wide_sed_and_head_ranges(self):
        limit = config.DEFAULTS["hooks"]["read_max_range"]
        for wide in (f"sed -n '1,{limit + 1}p' big.py", f'sed -n "100,{100 + limit}p" big.py',
                     f"sed -n 1,{limit + 1}p big.py", f"head -n {limit + 1} big.py",
                     f"tail -{limit + 1} big.py", f"cat big.py | head -{limit * 3}"):
            denied = self.pretool("Bash", command=wide)
            self.assertIsNotNone(denied, wide)
            self.assertIn("Grep", denied["hookSpecificOutput"]["permissionDecisionReason"])
        for ok in (f"sed -n '1,{limit}p' big.py", "sed -n '200,260p' big.py", "head -n 40 big.py",
                   "tail -20 big.py", "pytest 2>&1 | tail -n 60", "git log --oneline -500"):
            self.assertIsNone(self.pretool("Bash", command=ok), ok)

    def test_read_max_range_can_be_disabled(self):
        (self.dir / ".relay" / "config.toml").write_text(
            '[hooks]\nenabled = ["read_guard", "bash_guard"]\nread_max_range = 0\n')
        self.assertIsNone(self.pretool("Read", file_path="big.py", offset=1, limit=900))
        self.assertIsNone(self.pretool("Bash", command="sed -n '1,900p' big.py"))

    def test_write_guard_is_opt_in(self):
        content = "y" * 40_000
        self.assertIsNone(self.pretool("Write", file_path="a.md", content=content))
        (self.dir / ".relay" / "config.toml").write_text(
            '[hooks]\nenabled = ["write_guard"]\nwrite_max_tokens = 1000\n')
        self.assertIsNotNone(self.pretool("Write", file_path="a.md", content=content))
        self.assertIsNone(self.pretool("Write", file_path="a.md", content="short"))

    def test_denials_are_logged_with_avoided_tokens(self):
        self.pretool("Read", file_path="big.py")
        rows = [json.loads(l) for l in (self.dir / ".relay" / "events.jsonl").read_text().splitlines()]
        self.assertEqual(rows[-1]["tool"], "guard:Read")
        self.assertIn("big.py", rows[-1]["task"])
        self.assertGreater(rows[-1]["avoided"], 1000)

    def test_fail_open(self):
        out = io.StringIO()
        self.assertEqual(hooks.main("pretool", io.StringIO("not json"), out), 0)
        self.assertEqual(out.getvalue(), "")

    def test_install_is_idempotent(self):
        settings = {"hooks": {"PreToolUse": [{"matcher": "Edit", "hooks": [{"type": "command", "command": "fmt"}]}]}}
        hooks.install(settings)
        hooks.install(settings)
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 2)
        self.assertEqual(len(settings["hooks"]["UserPromptSubmit"]), 1)


if __name__ == "__main__":
    unittest.main()
