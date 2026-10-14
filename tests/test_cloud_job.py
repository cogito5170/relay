import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from relay.cloud_job import CloudRequest, run_dry, validate


class CloudJobTest(unittest.TestCase):
    def request(self, **changes):
        values = {"task_id": "phone-smoke", "agent": "claude",
                  "mode": "advisory", "instruction": "Review the current plan."}
        values.update(changes)
        return CloudRequest(**values)

    def test_writes_auditable_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = run_dry(self.request(), Path(tmp), datetime(2026, 1, 2, tzinfo=timezone.utc))
            self.assertEqual(result["status"], "dry_run")
            self.assertFalse(result["execution"]["model_called"])
            self.assertEqual(json.loads((Path(tmp) / "request.json").read_text())["task_id"], "phone-smoke")
            self.assertIn("Status: `dry_run`", (Path(tmp) / "result.md").read_text())

    def test_rejects_traversal_and_shell_shaped_ids(self):
        for task_id in ("../secret", "UPPER", "x;echo", "x/y"):
            with self.subTest(task_id=task_id), self.assertRaises(ValueError):
                validate(self.request(task_id=task_id))

    def test_rejects_unbounded_or_empty_instruction(self):
        for instruction in ("  ", "x" * 4_001):
            with self.subTest(length=len(instruction)), self.assertRaises(ValueError):
                validate(self.request(instruction=instruction))

    def test_rejects_unknown_execution_choices(self):
        with self.assertRaises(ValueError):
            validate(self.request(agent="shell"))
        with self.assertRaises(ValueError):
            validate(self.request(mode="write"))


if __name__ == "__main__":
    unittest.main()
