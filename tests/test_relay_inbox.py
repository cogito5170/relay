import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "relay_inbox.py"
spec = importlib.util.spec_from_file_location("relay_inbox", SCRIPT)
inbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inbox)

# 설치된 relay 대신 쓰는 가짜: `image <task>` 이면 task 폴더에 image.png 를 만든다.
FAKE_RELAY = """import pathlib, sys
args = sys.argv[1:]
assert args[0] == "image", args
pathlib.Path(".relay/tasks", args[1], "image.png").write_bytes(b"PNG")
print("[ok]")
"""


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


class ValidateTest(unittest.TestCase):
    def test_accepts_known_shapes(self):
        for request in ({"action": "image"}, {"action": "advise", "agent": "agy", "id": "2"},
                        {"action": "delegate", "agent": "codex", "instruction": "add a test"}):
            with self.subTest(request=request):
                self.assertEqual(inbox.validate(request), request)

    def test_rejects_anything_else(self):
        for request in ([], {"action": "shell"}, {"action": "image", "argv": ["rm"]},
                        {"action": "advise", "agent": "claude"},
                        {"action": "delegate", "agent": "codex", "instruction": " "},
                        {"action": "delegate", "agent": "codex", "instruction": "x" * 4_001}):
            with self.subTest(request=request), self.assertRaises(ValueError):
                inbox.validate(request)

    def test_commands_are_argument_lists(self):
        self.assertEqual(inbox.command_for(["relay"], "005", {"action": "delegate", "agent": "agy",
                                                              "instruction": "a; rm -rf /"}),
                         ["relay", "delegate", "agy", "a; rm -rf /"])


class InboxRoundTripTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        base = Path(self.tmp.name)
        self.origin = base / "origin.git"
        git(base, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.author = base / "author"
        git(base, "clone", "-q", str(self.origin), str(self.author))
        for cfg in (("user.email", "a@example.com"), ("user.name", "a")):
            git(self.author, "config", *cfg)
        task = self.author / ".relay" / "tasks" / "005-img"
        task.mkdir(parents=True)
        (task / "image-spec.json").write_text('{"subject": "cat"}')
        (task / "request.json").write_text(json.dumps({"action": "image"}))
        git(self.author, "checkout", "-q", "-b", "claude/x")
        git(self.author, "add", "-A")
        git(self.author, "commit", "-q", "-m", "request")
        git(self.author, "push", "-q", "origin", "claude/x")
        self.worker = base / "worker"
        git(base, "clone", "-q", str(self.origin), str(self.worker))
        for cfg in (("user.email", "w@example.com"), ("user.name", "w")):
            git(self.worker, "config", *cfg)
        fake = base / "fake_relay.py"
        fake.write_text(FAKE_RELAY)
        self.relay = [sys.executable, str(fake)]

    def run_worker(self):
        return inbox.run_once(self.worker, self.relay, ["claude/*"], 60, log=lambda *_: None)

    def test_runs_request_and_pushes_results_once(self):
        self.assertEqual(self.run_worker(), 0)
        git(self.author, "pull", "-q", "origin", "claude/x")
        task = self.author / ".relay" / "tasks" / "005-img"
        self.assertEqual((task / "image.png").read_bytes(), b"PNG")
        response = json.loads((task / "response.json").read_text())
        self.assertEqual(response["status"], "done")
        head = git(self.author, "rev-parse", "HEAD")
        self.assertEqual(self.run_worker(), 0)
        git(self.author, "pull", "-q", "origin", "claude/x")
        self.assertEqual(git(self.author, "rev-parse", "HEAD"), head)

    def test_rejected_request_is_answered_not_run(self):
        task = self.author / ".relay" / "tasks" / "005-img"
        (task / "request.json").write_text(json.dumps({"action": "shell", "cmd": "id"}))
        git(self.author, "commit", "-q", "-am", "bad request")
        git(self.author, "push", "-q", "origin", "claude/x")
        self.assertEqual(self.run_worker(), 1)
        git(self.author, "pull", "-q", "origin", "claude/x")
        response = json.loads((task / "response.json").read_text())
        self.assertEqual(response["status"], "rejected")
        self.assertFalse((task / "image.png").exists())

    def test_ignores_unwatched_branches(self):
        self.assertEqual(inbox.run_once(self.worker, self.relay, ["main"], 60, log=lambda *_: None), 0)
        self.assertNotIn("response", git(self.worker, "ls-tree", "-r", "--name-only", "origin/claude/x"))


if __name__ == "__main__":
    unittest.main()
