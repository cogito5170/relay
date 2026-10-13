import copy
import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

from relay import config, hooks, supervisor


def transcript(path: Path, contexts: list[int]) -> Path:
    rows = []
    for i, ctx in enumerate(contexts):
        rows.append({"type": "assistant", "requestId": f"r{i}", "message": {
            "id": f"m{i}", "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash",
                                        "input": {"command": f"cat file{i}.py"}}],
            "usage": {"input_tokens": 0, "cache_creation_input_tokens": 0,
                      "cache_read_input_tokens": ctx, "output_tokens": 20}}})
        rows.append({"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": f"t{i}", "content": "x" * 400}]}})
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return path


def stub(path: Path, body: str) -> list[str]:
    path.write_text(body)
    return [sys.executable, str(path), "{prompt}"]


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".relay").mkdir()
        self.conf = copy.deepcopy(config.DEFAULTS)
        self.transcript = transcript(self.root / "s.jsonl", [20_000, 24_000, 30_000, 45_000])

    def tearDown(self):
        self._tmp.cleanup()

    # ---------------------------------------------------------------- 스냅샷
    def test_snapshot_is_small_and_quantified(self):
        text = supervisor.build_snapshot(self.transcript, self.conf)
        self.assertIn("requests: 4", text)
        self.assertIn("last request context: 45000", text)
        self.assertIn("context re-read total:", text)
        self.assertIn("cost by cause:", text)
        self.assertLess(len(text) // 4, 1_500, "스냅샷이 커지면 감시가 비용이 된다")
        tools = [l for l in text.splitlines() if l.startswith("  Bash ")]
        self.assertEqual(len(tools), len(set(tools)), "호출+결과 중복이 남아 있다")

    # ---------------------------------------------------------------- 판정 파싱
    def test_parse_verdict_accepts_decorated_output(self):
        text = "**VERDICT: CLEAR**\nREASON: 67 requests re-read 2.6M\nACTION: /handoff 003 then /clear"
        self.assertEqual(supervisor.parse_verdict(text),
                         ("CLEAR", "67 requests re-read 2.6M", "/handoff 003 then /clear"))
        self.assertEqual(supervisor.parse_verdict("no verdict here")[0], "")
        for v in supervisor.VERDICTS:
            self.assertEqual(supervisor.parse_verdict(f"VERDICT: {v}")[0], v)

    # ---------------------------------------------------------------- 러너 정책
    def test_supervisor_never_runs_on_claude(self):
        with self.assertRaises(ValueError):
            supervisor.assert_not_claude([["claude", "-p", "{prompt}"]])
        runners = config.DEFAULTS["supervisor"]["runners"]
        self.assertEqual([c[0] for c in runners], ["codex", "agy"])
        supervisor.assert_not_claude(runners)

    def test_run_records_verdict_from_first_runner(self):
        self.conf["supervisor"]["runners"] = [stub(self.root / "ok.py", (
            "import sys\n"
            "assert 'Session snapshot' in sys.argv[1]\n"
            "print('VERDICT: DELEGATE')\nprint('REASON: exploration belongs to agy')\n"
            "print('ACTION: relay handoff --to antigravity')\n"))]
        result = supervisor.run(self.root, self.transcript, self.conf)
        self.assertEqual(result["verdict"], "DELEGATE")
        self.assertEqual(result["action"], "relay handoff --to antigravity")
        supervisor.write_cache(self.root, result)
        self.assertEqual(supervisor.read_cache(self.root)["verdict"], "DELEGATE")

    def test_run_falls_back_when_first_runner_is_out_of_quota(self):
        limited = stub(self.root / "limited.py",
                       "print('You have exhausted your quota on this model.')\n")
        good = stub(self.root / "good.py", "print('VERDICT: HANDOFF')\nprint('REASON: long session')\n")
        self.conf["supervisor"]["runners"] = [limited, good]
        result = supervisor.run(self.root, self.transcript, self.conf)
        self.assertEqual(result["verdict"], "HANDOFF")

    def test_run_reports_failure_when_no_runner_answers(self):
        self.conf["supervisor"]["runners"] = [stub(self.root / "mute.py", "print('thinking...')\n")]
        result = supervisor.run(self.root, self.transcript, self.conf)
        self.assertEqual(result["verdict"], "")
        self.assertIn("결론 없는 출력", result["detail"])


class SupervisorHookTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / ".relay").mkdir()
        self.transcript = transcript(self.root / "s.jsonl", [20_000, 45_000])
        self.refreshed = []
        self._real_refresh = supervisor.refresh_in_background
        supervisor.refresh_in_background = lambda root, session=None: self.refreshed.append(root)
        self.addCleanup(lambda: setattr(supervisor, "refresh_in_background", self._real_refresh))

    def tearDown(self):
        self._tmp.cleanup()

    def cache(self, verdict: str, age: int = 0) -> None:
        supervisor.write_cache(self.root, {"ts": time.time() - age, "verdict": verdict,
                                           "reason": "re-read 2.6M vs 65k new input",
                                           "action": "/handoff 003 → /clear", "runner": "codex"})

    def run_hook(self, prompt: str = "계속해") -> dict | None:
        out = io.StringIO()
        data = {"cwd": str(self.root), "prompt": prompt, "transcript_path": str(self.transcript)}
        self.assertEqual(hooks.main("prompt", io.StringIO(json.dumps(data)), out), 0)
        return json.loads(out.getvalue()) if out.getvalue() else None

    def test_clear_verdict_blocks_the_message(self):
        self.cache("CLEAR")
        result = self.run_hook()
        self.assertEqual(result["decision"], "block")
        self.assertIn("/clear", result["reason"])
        self.assertIn("re-read 2.6M", result["reason"])

    def test_handoff_verdict_only_advises(self):
        self.cache("HANDOFF")
        result = self.run_hook()
        out = result["hookSpecificOutput"]
        self.assertEqual(out["hookEventName"], "UserPromptSubmit")
        self.assertIn("HANDOFF", out["additionalContext"])
        self.assertNotIn("decision", result)

    def test_continue_and_force_stay_silent(self):
        self.cache("CONTINUE")
        self.assertIsNone(self.run_hook())
        self.cache("CLEAR")
        self.assertIsNone(self.run_hook("계속해 [force]"))

    def test_stale_verdict_triggers_background_refresh_without_blocking(self):
        self.cache("CONTINUE", age=10_000)        # max_age 900 보다 오래됨
        self.assertIsNone(self.run_hook())        # 이번 메시지는 막지 않는다
        self.assertEqual([Path(r).resolve() for r in self.refreshed], [self.root.resolve()])

    def test_project_without_relay_dir_is_left_alone(self):
        """.relay 가 없으면 판정을 캐시할 수 없다. 매 프롬프트마다 감시 모델을 부르지 않는다."""
        import shutil
        self.cache("CLEAR")
        shutil.rmtree(self.root / ".relay")
        self.assertIsNone(self.run_hook())
        self.assertEqual(self.refreshed, [])

    def test_no_refresh_below_soft_context(self):
        small = transcript(self.root / "small.jsonl", [5_000, 9_000])
        out = io.StringIO()
        data = {"cwd": str(self.root), "prompt": "x", "transcript_path": str(small)}
        hooks.main("prompt", io.StringIO(json.dumps(data)), out)
        self.assertEqual(self.refreshed, [])


if __name__ == "__main__":
    unittest.main()
