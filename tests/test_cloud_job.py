import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from relay.cloud_job import CloudRequest, run_dry, run_openai, validate


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

    def test_live_run_writes_provider_metadata_and_answer(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps({
                    "id": "resp_test",
                    "model": "test-model",
                    "output": [{"content": [{"type": "output_text", "text": "Use a queue."}]}],
                    "usage": {"input_tokens": 10, "output_tokens": 3, "total_tokens": 13},
                }).encode()

        def opener(http_request, timeout):
            self.assertNotIn("secret-value", http_request.data.decode())
            self.assertFalse(json.loads(http_request.data)["store"])
            self.assertEqual(timeout, 90)
            return Response()

        with tempfile.TemporaryDirectory() as tmp:
            report = run_openai(
                self.request(agent="codex"), Path(tmp), api_key="secret-value",
                model="test-model", opener=opener,
                now=datetime(2026, 1, 2, tzinfo=timezone.utc),
            )
            self.assertEqual(report["status"], "succeeded")
            self.assertEqual(report["execution"]["provider_request_id"], "resp_test")
            self.assertIn("Use a queue.", (Path(tmp) / "result.md").read_text())
            self.assertNotIn("secret-value", (Path(tmp) / "run.json").read_text())

    def test_live_run_rejects_unsupported_route_or_missing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                run_openai(self.request(agent="claude"), Path(tmp), api_key="key", model="model")
            with self.assertRaises(ValueError):
                run_openai(self.request(agent="codex"), Path(tmp), api_key="", model="model")


if __name__ == "__main__":
    unittest.main()
