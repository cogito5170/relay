import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from relay import config, pipeline, tasks

EXPLORE_STUB = """import sys
print("## Summary\\nentry point\\n## Key locations\\n- app.py:1-2 — handler\\n## Risks\\nnone")
"""
IMPLEMENT_STUB = """import sys, pathlib
prompt = sys.stdin.read()
p = pathlib.Path("app.py")
p.write_text(p.read_text() + ("# fixed per review\\n" if "Reviewer requested" in prompt else "# implemented\\n"))
print("changed app.py:3")
"""
REVIEW_STUB = """import sys, json, pathlib
counter = pathlib.Path(".review_count")
n = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(n + 1))
prompt = sys.argv[1]
assert "# implemented" in prompt and "PASS" in prompt, prompt
text = "VERDICT: CHANGES\\n1. app.py:3 add a comment" if n == 0 else "VERDICT: APPROVE"
print(json.dumps({"result": text, "num_turns": 1, "usage": {"input_tokens": 5,
      "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 1000, "output_tokens": 40}}))
"""
ADVISORY_STUB = """import json, sys
prompt = sys.argv[1]
assert 'Context pack' in prompt and 'Brief' in prompt
print(json.dumps({'result': 'independent advice', 'num_turns': 1,
                  'usage': {'output_tokens': 3}}))
"""


def git(root, *args):
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.bin = self.root.parent / (self.root.name + "-bin")
        self.bin.mkdir()
        self.addCleanup(lambda: [p.unlink() for p in self.bin.iterdir()] and None or self.bin.rmdir())
        for name, body in (("explore.py", EXPLORE_STUB), ("implement.py", IMPLEMENT_STUB), ("review.py", REVIEW_STUB)):
            (self.bin / name).write_text(body)
        (self.bin / "advisory.py").write_text(ADVISORY_STUB)
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "t@t")
        git(self.root, "config", "user.name", "t")
        (self.root / "app.py").write_text("def handler(event):\n    return event\n")
        (self.root / ".gitignore").write_text(".review_count\n")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-qm", "init")
        tasks.scaffold(self.root)
        self.task = tasks.create(self.root, "handler 개선", "codex")
        self.conf = copy.deepcopy(config.DEFAULTS)
        py = sys.executable
        self.conf["runners"] = {
            "explore": [["missing-explorer-xyz", "-p", "{prompt}"], [py, str(self.bin / "explore.py"), "{prompt}"]],
            "implement": [[py, str(self.bin / "implement.py")]],          # stdin 으로 프롬프트
            "review": [[py, str(self.bin / "review.py"), "{prompt}", "--model", "{review_model}"]],
            "advisory_claude": [[py, str(self.bin / "advisory.py"), "{prompt}"]],
            "advisory_agy": [[py, str(self.bin / "advisory.py"), "{prompt}"]],
        }
        self.conf["pipeline"]["verify"] = f'{py} -c "print(\'ok\')"'
        self.logs = []

    def tearDown(self):
        self._tmp.cleanup()

    def test_full_loop_with_review_feedback(self):
        report = pipeline.Pipeline(self.root, self.task, self.conf, log=self.logs.append).run(list(pipeline.STAGES), 3)
        self.assertEqual(report.verdict, "APPROVE")
        self.assertEqual(report.iterations, 2)
        self.assertIn("# fixed per review", (self.root / "app.py").read_text())
        self.assertIn("app.py:1-2", (self.task / "explore.md").read_text())
        self.assertIn("def handler", (self.task / "pack.md").read_text())
        self.assertIn("PASS", (self.task / "verify.md").read_text())
        self.assertEqual(report.claude_tokens["cache_creation_input_tokens"], 6000)
        run = json.loads((self.task / "run.json").read_text())
        self.assertEqual(run["verdict"], "APPROVE")
        explore = [s for s in report.stages if s.stage == "explore"][0]
        self.assertTrue(explore.runner.endswith("python3") or "python" in explore.runner)  # fallback 사용

    def test_read_only_advisory_writes_agent_specific_output(self):
        (self.task / "pack.md").write_text("source context")
        rc, text, report = pipeline.advise(self.root, self.task, self.conf, "claude")
        self.assertEqual(rc, 0)
        self.assertEqual(text, "independent advice")
        self.assertEqual(report["status"], "ok")
        self.assertEqual((self.task / "out-claude.md").read_text().strip(), "independent advice")

    def test_explore_skipped_when_report_exists_and_missing_runner(self):
        (self.task / "explore.md").write_text("- app.py:1-1")
        self.conf["runners"]["implement"] = [["missing-codex-xyz", "exec"]]
        report = pipeline.Pipeline(self.root, self.task, self.conf, log=self.logs.append).run(list(pipeline.STAGES), 2)
        statuses = {s.stage: s.status for s in report.stages}
        self.assertEqual(statuses["explore"], "skipped")
        self.assertEqual(statuses["implement"], "skipped")
        self.assertNotIn("review", statuses)  # 구현 실패 시 중단

    def test_helpers(self):
        self.assertEqual(pipeline.verdict_of("blah\nVERDICT: APPROVE"), "APPROVE")
        self.assertEqual(pipeline.verdict_of("verdict: changes\n1. x"), "CHANGES")
        self.assertEqual(pipeline.verdict_of("no verdict"), "UNKNOWN")
        text, usage = pipeline.parse_claude_json('{"result": "ok", "num_turns": 2, "usage": {"output_tokens": 7}}')
        self.assertEqual((text, usage["turns"], usage["output_tokens"]), ("ok", 2, 7))
        self.assertEqual(pipeline.parse_claude_json("plain")[0], "plain")
        self.assertEqual(pipeline._fill(["a{x}b", "{prompt}"], {"x": 1, "prompt": "P"}), ["a1b", "P"])

    def test_lean_argv(self):
        conf = copy.deepcopy(config.DEFAULTS)
        (self.root / ".mcp.json").write_text("{}")
        argv = pipeline.lean_argv(conf, self.root, ["--model", "sonnet"])
        self.assertEqual(argv[:3], ["claude", "--tools", "Read,Edit,Write,Bash,Grep,Glob"])
        self.assertIn("--strict-mcp-config", argv)
        self.assertIn(str(self.root / ".mcp.json"), argv)
        self.assertEqual(argv[-2:], ["--model", "sonnet"])

    def test_default_review_runner_is_lean(self):
        review = config.DEFAULTS["runners"]["review"][0]
        self.assertIn("--tools", review)
        self.assertEqual(review[review.index("--tools") + 1], "Read,Grep,Glob")
        self.assertIn("--system-prompt", review)

    def test_default_implement_runners_are_codex_then_agy(self):
        """codex 쿼터가 끝나도 구현이 멈추지 않아야 한다."""
        implement = config.DEFAULTS["runners"]["implement"]
        self.assertEqual([c[0] for c in implement], ["codex", "agy"])
        self.assertEqual(implement[0], ["codex", "exec", "--sandbox", "workspace-write", "{prompt}"])
        self.assertIn("accept-edits", implement[1])   # agy 폴백은 수정 권한이 필요하다
        self.assertIn("--sandbox", implement[1])      # 셸 명령 승인 대기로 멈추지 않도록
        pipe = pipeline.Pipeline(self.root, self.task, self.conf)
        filled = pipeline._fill(implement[1], pipe.values)
        self.assertEqual(filled[filled.index("--model") + 1],
                         config.DEFAULTS["pipeline"]["implement_fallback_model"])

    def test_quota_exhaustion_moves_to_next_runner(self):
        """한도 소진은 종료 코드 0 + 안내 문구로 온다. 그래도 다음 후보로 넘어가야 한다."""
        for message in ("Claude AI usage limit reached|1760450000",
                        "You have exhausted your quota on this model."):
            limited = self.bin / "limited.py"
            limited.write_text(f"print({message!r})\n")
            py = sys.executable
            self.conf["runners"]["explore"] = [[py, str(limited)],
                                               [py, str(self.bin / "explore.py"), "{prompt}"]]
            (self.task / "explore.md").unlink(missing_ok=True)
            self.logs.clear()
            pipe = pipeline.Pipeline(self.root, self.task, self.conf, log=self.logs.append)
            result = pipe.explore()
            self.assertEqual(result.status, "ok", message)
            self.assertTrue(any("한도 소진" in line for line in self.logs), self.logs)
        self.assertEqual(pipeline.quota_exhausted("all good"), "")

    def test_default_explore_runners_are_agy_then_codex(self):
        """Gemini API 는 쓰지 않는다. 탐색은 agy(plan 모드) 우선, 실패하면 codex read-only."""
        explore = config.DEFAULTS["runners"]["explore"]
        self.assertEqual([c[0] for c in explore], ["agy", "codex"])
        self.assertEqual(explore[0][:4], ["agy", "--mode", "plan", "--print"])
        self.assertEqual(explore[0][-2], "--print")            # --print 는 프롬프트를 바로 뒤에 받는다
        self.assertIn("{prompt}", explore[0][-1])
        self.assertIn("{no_shell}", explore[0][-1])            # 헤드리스에서 명령은 자동 거부된다
        self.assertEqual(explore[1], ["codex", "exec", "--sandbox", "read-only", "{prompt}"])

    def test_review_max_turns_comes_from_config(self):
        """비용은 턴 수가 지배하므로 --max-turns 는 설정으로 조절할 수 있어야 한다."""
        review = config.DEFAULTS["runners"]["review"][0]
        self.assertEqual(review[review.index("--max-turns") + 1], "{review_max_turns}")
        self.conf["pipeline"]["review_max_turns"] = 4
        pipe = pipeline.Pipeline(self.root, self.task, self.conf)
        filled = pipeline._fill(review, pipe.values)
        self.assertEqual(filled[filled.index("--max-turns") + 1], "4")

    def test_agent_falls_back_when_first_runner_fails(self):
        """설치돼 있지만 실패하는 러너 다음 후보로 넘어간다 (탐색 CLI 가 503 으로 죽는 경우)."""
        fail = self.bin / "fail.py"
        fail.write_text("import sys; sys.stderr.write('503 overloaded\\n'); sys.exit(1)\n")
        py = sys.executable
        self.conf["runners"]["explore"] = [[py, str(fail), "{prompt}"],
                                           [py, str(self.bin / "explore.py"), "{prompt}"]]
        logged = []
        pipe = pipeline.Pipeline(self.root, self.task, self.conf, log=logged.append)
        result = pipe.explore()
        self.assertEqual(result.status, "ok")
        self.assertIn("## Key locations", (self.task / "explore.md").read_text())
        self.assertTrue(any("재시도" in line for line in logged))

    def test_implement_with_no_change_is_a_failure(self):
        """러너가 rc 0 으로 끝나도 작업 트리가 그대로면 실패다 (codex 사이드카 누락의 조용한 실패)."""
        noop = self.bin / "noop.py"
        noop.write_text("import sys; sys.stdin.read(); print('ok, nothing to do')\n")
        self.conf["runners"]["implement"] = [[sys.executable, str(noop)]]
        pipe = pipeline.Pipeline(self.root, self.task, self.conf, log=self.logs.append)
        result = pipe.implement()
        self.assertEqual(result.status, "failed")
        self.assertIn("변경 0", result.detail)

    def test_review_falls_back_when_verdict_is_missing(self):
        """rc 0 이지만 VERDICT 가 없으면(한도 소진·턴 잘림) 다음 후보로 넘어간다."""
        mute = self.bin / "mute.py"
        mute.write_text("import json; print(json.dumps({'result': 'review was cut off', 'num_turns': 1}))\n")
        py = sys.executable
        self.conf["runners"]["review"] = [[py, str(mute)], [py, str(self.bin / "review.py"), "{prompt}"]]
        (self.root / "app.py").write_text("def handler(event):\n    return event  # implemented\n")
        pipe = pipeline.Pipeline(self.root, self.task, self.conf, log=self.logs.append)
        pipe.verify()
        result = pipe.review()
        self.assertEqual(result.status, "ok")
        self.assertEqual(pipe.report.verdict, "CHANGES")
        self.assertTrue(any("결론 없는 출력" in line for line in self.logs), self.logs)

    def test_default_review_fallback_chain_spans_three_quotas(self):
        review = config.DEFAULTS["runners"]["review"]
        self.assertEqual([c[0] for c in review], ["claude", "agy", "codex"])
        self.assertIn("{system_prompt}", review[2][-1])   # codex 도 --system-prompt 가 없다
        self.assertEqual(review[1][-2], "--print")
        self.assertIn("{system_prompt}", review[1][-1])      # agy 엔 --system-prompt 가 없다
        self.assertIn("{prompt}", review[1][-1])
        self.assertEqual(config.DEFAULTS["pipeline"]["review_fallback_model"], "claude-opus-5-5-medium")
        pipe = pipeline.Pipeline(self.root, self.task, self.conf)
        filled = pipeline._fill(review[1], pipe.values)
        self.assertEqual(filled[filled.index("--model") + 1], "claude-opus-5-5-medium")

    def test_installed_runners_skips_missing(self):
        py = sys.executable
        runners = pipeline.installed_runners([["missing-xyz-cli"], [py, "-V"]])
        self.assertEqual(runners, [[py, "-V"]])
        with self.assertRaises(pipeline.RunnerMissing):
            pipeline.installed_runners([["missing-xyz-cli"]])


if __name__ == "__main__":
    unittest.main()
