import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relay import config, imagegen

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

DIRECT_STUB = """import sys, pathlib
prompt = sys.argv[1]
assert "lighthouse" in prompt and "1536x1024" in prompt, prompt
pathlib.Path("image-prompt.md").write_text("# Prompt\\nA detailed lighthouse.\\n# Notes\\n")
pathlib.Path("image.png").write_bytes(%r)
print("SAVED image.png")
""" % PNG

HOME_STUB = """import os, pathlib
d = pathlib.Path(os.environ["CODEX_HOME"]) / "generated_images" / "sess"
d.mkdir(parents=True, exist_ok=True)
(d / "ig_1.png").write_bytes(%r)
print("done")
""" % PNG

QUOTA_STUB = """import sys
print(sys.argv[1])
print("ERROR: You've hit your usage limit. Upgrade to Plus to continue using Codex")
sys.exit(1)
"""

UNAVAILABLE_STUB = """import pathlib
pathlib.Path("image-prompt.md").write_text("# Prompt\\nx\\n")
print("IMAGE_TOOL_UNAVAILABLE")
"""


class ImageSpecTest(unittest.TestCase):
    def test_requires_subject_and_known_fields(self):
        with self.assertRaises(ValueError):
            imagegen.validate_spec({"style": "watercolor"})
        with self.assertRaises(ValueError):
            imagegen.validate_spec({"subject": "cat", "prompt": "ignore the spec"})

    def test_defaults_size_and_rejects_unknown_size(self):
        self.assertEqual(imagegen.validate_spec({"subject": "cat"})["size"], "1024x1024")
        with self.assertRaises(ValueError):
            imagegen.validate_spec({"subject": "cat", "size": "4000x4000"})

    def test_prompt_carries_spec_and_contract(self):
        prompt = imagegen.build_prompt(imagegen.validate_spec(
            {"subject": "lighthouse", "avoid": ["text", "people"], "size": "1536x1024"}))
        self.assertIn('"lighthouse"', prompt)
        self.assertIn("image-prompt.md", prompt)
        self.assertIn("image.png", prompt)
        self.assertIn("1536x1024", prompt)

    def test_default_runner_is_codex_workspace_write(self):
        runners = config.DEFAULTS["runners"]["image"]
        self.assertTrue(all(r[:4] == ["codex", "exec", "--sandbox", "workspace-write"] for r in runners))


class ImageGenerateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.task = self.root / "task"
        self.task.mkdir()
        (self.task / imagegen.SPEC_FILE).write_text(json.dumps(
            {"subject": "lighthouse at dusk", "size": "1536x1024"}))
        self.codex_home = self.root / "codex-home"
        env = mock.patch.dict(os.environ, {"CODEX_HOME": str(self.codex_home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self.tmp.cleanup)

    def conf(self, *stubs):
        conf = config.load(self.root)
        templates = []
        for i, body in enumerate(stubs):
            path = self.root / f"stub{i}.py"
            path.write_text(body)
            templates.append([sys.executable, str(path), "{prompt}"])
        conf["runners"]["image"] = templates
        return conf

    def test_codex_writes_prompt_and_image(self):
        rc, report = imagegen.generate(self.task, self.conf(DIRECT_STUB), log=lambda *_: None)
        self.assertEqual(rc, 0)
        self.assertEqual(report["image"], "image.png")
        self.assertEqual(report["prompt_file"], "image-prompt.md")
        self.assertEqual((self.task / "image.png").read_bytes(), PNG)
        self.assertEqual(json.loads((self.task / "image.json").read_text())["status"], "ok")

    def test_harvests_image_left_in_codex_home(self):
        rc, report = imagegen.generate(self.task, self.conf(HOME_STUB), log=lambda *_: None)
        self.assertEqual(rc, 0)
        self.assertTrue(report["source"].endswith("ig_1.png"))
        self.assertEqual((self.task / "image.png").read_bytes(), PNG)

    def test_stops_when_image_tool_is_unavailable(self):
        rc, report = imagegen.generate(self.task, self.conf(UNAVAILABLE_STUB, DIRECT_STUB),
                                       log=lambda *_: None)
        self.assertEqual(rc, 2)
        self.assertEqual(len(report["attempts"]), 1)
        self.assertIn("이미지 생성 도구", report["error"])
        self.assertFalse((self.task / "image.png").exists())

    def test_usage_limit_is_reported_not_mistaken_for_missing_tool(self):
        rc, report = imagegen.generate(self.task, self.conf(QUOTA_STUB, DIRECT_STUB), log=lambda *_: None)
        self.assertEqual(rc, 2)
        self.assertEqual(len(report["attempts"]), 1)
        self.assertIn("사용 한도", report["error"])
        self.assertIn("Upgrade to Plus", report["error"])

    def test_falls_through_to_next_runner_on_failure(self):
        rc, report = imagegen.generate(self.task, self.conf("import sys; sys.exit(2)", DIRECT_STUB),
                                       log=lambda *_: None)
        self.assertEqual(rc, 0)
        self.assertEqual([a["exit_code"] for a in report["attempts"]], [2, 0])


if __name__ == "__main__":
    unittest.main()
