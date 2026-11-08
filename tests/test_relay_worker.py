import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "relay_worker.py"
SPEC = importlib.util.spec_from_file_location("relay_worker", SCRIPT)
worker = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(worker)


class RelayWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tasks = self.root / ".relay" / "tasks"
        self.tasks.mkdir(parents=True)
        self.fake = self.root / "relay"
        self.fake.write_text(
            "#!/bin/sh\n"
            "task=$2\n"
            "if [ \"$1\" = advise ]; then agent=$4; printf 'advice from %s\\n' \"$agent\" > .relay/tasks/$task/out-$agent.md; printf '{\"status\":\"ok\"}\\n' > .relay/tasks/$task/advisory-$agent.json; exit 0; fi\n"
            "printf '%s\\n' \"$@\"\n"
            "printf '%s\\n' '{\"verdict\":\"APPROVE\",\"stages\":[{\"stage\":\"review\",\"status\":\"ok\"}]}' "
            "> .relay/tasks/$task/run.json\n",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)

    def tearDown(self):
        self.tmp.cleanup()

    def test_review_request_runs_without_shell_and_completes(self):
        task = self.tasks / "pd-test"
        task.mkdir()
        (task / "run.request").write_text("review\n", encoding="utf-8")

        self.assertEqual(worker.run_once(self.root, self.fake, 10), 0)
        self.assertTrue((task / "run.done").is_file())
        log = (self.root / ".relay" / "runner" / "pd-test.log").read_text(encoding="utf-8")
        self.assertIn("'--only', 'review'", log)
        self.assertIn("exit_code=0", log)

    def test_unknown_request_is_rejected(self):
        task = self.tasks / "pd-test"
        task.mkdir()
        (task / "run.request").write_text("full\n", encoding="utf-8")

        self.assertEqual(worker.run_once(self.root, self.fake, 10), 1)
        status = (task / "run.failed").read_text(encoding="utf-8")
        self.assertIn("status=rejected", status)
        self.assertIn("unsupported request", status)

    def test_advisory_request_runs_named_agent_and_completes(self):
        task = self.tasks / "advice-test"
        task.mkdir()
        (task / "run.request").write_text("advisory agy\n", encoding="utf-8")

        self.assertEqual(worker.run_once(self.root, self.fake, 10), 0)
        self.assertTrue((task / "run.done").is_file())
        self.assertEqual((task / "out-agy.md").read_text().strip(), "advice from agy")
        log = (self.root / ".relay" / "runner" / "advice-test.log").read_text()
        self.assertIn("'advise', 'advice-test', '--agent', 'agy'", log)

    def test_zero_exit_with_failed_review_is_failed(self):
        self.fake.write_text(
            "#!/bin/sh\n"
            "task=$2\n"
            "printf '%s\\n' '{\"verdict\":\"\",\"stages\":[{\"stage\":\"review\",\"status\":\"failed\"}]}' "
            "> .relay/tasks/$task/run.json\n",
            encoding="utf-8",
        )
        self.fake.chmod(self.fake.stat().st_mode | stat.S_IXUSR)
        task = self.tasks / "pd-test"
        task.mkdir()
        (task / "run.request").write_text("review\n", encoding="utf-8")

        self.assertEqual(worker.run_once(self.root, self.fake, 10), 1)
        status = (task / "run.failed").read_text(encoding="utf-8")
        self.assertIn("review stage did not complete", status)


if __name__ == "__main__":
    unittest.main()
