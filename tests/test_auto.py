import copy
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relay import auto, config, hooks, tasks

CLAUDE_STUB = r'''
import json, os, sys, pathlib
args = sys.argv[1:]
task = pathlib.Path(".relay/tasks") / os.environ["RELAY_TASK"]
assert os.environ.get("RELAY_AUTO") == "1"
counter = pathlib.Path(".chunks")
if "--resume" in args:
    (task / "state.md").write_text("STATUS: DONE\n## Done\nresumed\n")
else:
    n = int(counter.read_text()) if counter.exists() else 0
    counter.write_text(str(n + 1))
    prompt = args[args.index("-p") + 1]
    assert "relay task" in prompt
    if os.environ.get("STUB_MODE") != "nostate":
        status = "DONE" if n >= 1 else "CONTINUE"
        assert n == 0 or "STATUS: CONTINUE" in prompt   # 이전 state 가 다음 세션에 전달됨
        (task / "state.md").write_text(f"STATUS: {status}\n## Next\nstep {n + 2}\n")
print(json.dumps({"result": "ok", "num_turns": 3, "session_id": "sess-1",
                  "usage": {"input_tokens": 1, "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 3000, "output_tokens": 50}}))
'''


class AutoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        tasks.scaffold(self.root)
        self.task = tasks.create(self.root, "goal", "claude")
        self.stub = self.root / "claude_stub.py"
        self.stub.write_text(CLAUDE_STUB)
        self.conf = copy.deepcopy(config.DEFAULTS)
        self.conf["auto"]["command"] = [sys.executable, str(self.stub)] + self.conf["auto"]["command"][1:]
        self._cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_rotates_sessions_until_done(self):
        logs = []
        status = auto.Supervisor(self.root, self.task, self.conf, log=logs.append).run("build it")
        self.assertEqual(status, auto.STATUS_DONE)
        report = json.loads((self.task / "auto.json").read_text())
        self.assertEqual([c["status"] for c in report["chunks"]], ["CONTINUE", "DONE"])
        self.assertEqual(report["chunks"][0]["context_read"], 5001)

    def test_resume_writes_state_when_session_forgot(self):
        with mock.patch.dict(os.environ, {"STUB_MODE": "nostate"}):
            status = auto.Supervisor(self.root, self.task, self.conf, log=lambda *_: None).run("x")
        self.assertEqual(status, auto.STATUS_DONE)
        self.assertIn("resumed", (self.task / "state.md").read_text())

    def test_current_task_resolution(self):
        other = tasks.create(self.root, "other", "claude")
        self.assertEqual(auto.current_task(self.root), other)
        auto.set_current(self.root, self.task)
        self.assertEqual(auto.current_task(self.root), self.task)
        with mock.patch.dict(os.environ, {"RELAY_TASK": other.name}):
            self.assertEqual(auto.current_task(self.root), other)

    def test_status_of(self):
        self.assertEqual(auto.status_of("STATUS: DONE\nx"), "DONE")
        self.assertEqual(auto.status_of("status: continue"), "CONTINUE")
        self.assertEqual(auto.status_of("nothing"), "")

    def test_delegate_returns_tail_and_logs(self):
        noisy = self.root / "noisy.py"
        noisy.write_text("import sys\nsys.stdin.read()\nfor i in range(100): print(f'line {i}')\n")
        self.conf["runners"]["implement"] = [[sys.executable, str(noisy)]]
        rc, text = auto.delegate(self.root, self.conf, "codex", "do it", tail=5)
        self.assertEqual(rc, 0)
        self.assertIn("line 99", text)
        self.assertNotIn("line 90\n", text)
        self.assertIn("100 lines, showing last 5", text)
        self.assertEqual(len(list((self.root / ".relay" / "delegate").glob("*-codex.log"))), 1)

    def test_check_reports_missing(self):
        with mock.patch.object(auto, "CHECKS", [("nope", ["no-such-binary-xyz", "--version"], None)]):
            self.assertEqual(auto.check(ping=False), [("nope", "✗ 미설치", "")])


class AutoHooksTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        tasks.scaffold(self.root)
        self.task = tasks.create(self.root, "goal", "claude")
        (self.task / "state.md").write_text("STATUS: CONTINUE\n## Next\nwire the API")
        rows = [{"type": "assistant", "requestId": "r", "message": {"id": "m", "usage": {
            "input_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 5000}}}]
        self.transcript = self.root / "t.jsonl"
        self.transcript.write_text(json.dumps(rows[0]) + "\n")

    def tearDown(self):
        self._tmp.cleanup()

    def hook(self, event, data):
        out = io.StringIO()
        hooks.main(event, io.StringIO(json.dumps({"cwd": str(self.root), "transcript_path": str(self.transcript), **data})), out)
        return json.loads(out.getvalue()) if out.getvalue() else None

    def test_session_start_injects_state(self):
        r = self.hook("session", {"source": "clear"})
        ctx = r["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(r["hookSpecificOutput"]["hookEventName"], "SessionStart")
        self.assertIn("wire the API", ctx)
        self.assertIn(self.task.name, ctx)
        self.assertIsNone(self.hook("session", {"source": "resume"}))

    def test_auto_wrapup_only_in_auto_mode(self):
        env = {"RELAY_AUTO": "1", "RELAY_TASK": self.task.name, "RELAY_HANDOFF_CONTEXT": "1000"}
        self.assertIsNone(self.hook("pretool", {"tool_name": "Grep", "tool_input": {"pattern": "x"}}))
        with mock.patch.dict(os.environ, env):
            denied = self.hook("pretool", {"tool_name": "Grep", "tool_input": {"pattern": "x"}})
            self.assertIn("STATUS: DONE", denied["hookSpecificOutput"]["permissionDecisionReason"])
            ok = self.hook("pretool", {"tool_name": "Write", "tool_input": {
                "file_path": str(self.task / "state.md"), "content": "STATUS: DONE"}})
            self.assertIsNone(ok)

    def test_install_adds_session_hook_and_updates_matcher(self):
        settings = {"hooks": {"PreToolUse": [{"matcher": "Read|Bash|Write",
                                              "hooks": [{"type": "command", "command": "relay hook pretool"}]}]}}
        hooks.install(settings)
        self.assertEqual(settings["hooks"]["PreToolUse"][0]["matcher"], "*")
        self.assertEqual(len(settings["hooks"]["PreToolUse"]), 1)
        self.assertEqual(settings["hooks"]["SessionStart"][0]["hooks"][0]["command"], "relay hook session")


if __name__ == "__main__":
    unittest.main()
