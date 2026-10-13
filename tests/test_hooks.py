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
        many = transcript(self.dir / "many.jsonl", 20_000, requests=100)
        self.assertEqual(self.run_hook("prompt", {"prompt": "x", "transcript_path": str(many)})["decision"], "block")

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

    def test_write_guard_on_by_default_for_pro(self):
        content = "y" * 40_000
        self.assertIsNotNone(self.pretool("Write", file_path="a.md", content=content))
        self.assertIsNone(self.pretool("Write", file_path="a.md", content="short"))
        (self.dir / ".relay" / "config.toml").write_text('[hooks]\nenabled = ["read_guard"]\n')
        self.assertIsNone(self.pretool("Write", file_path="a.md", content=content))

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
