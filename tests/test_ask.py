import copy
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from relay import ask, config

ANSWER = "**한 줄 요약** 전류는 $I = V/R$\n\n$$P = I^2 R$$\n\n```mermaid\ngraph LR; A-->B\n```\n</script>"


class AskTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.conf = copy.deepcopy(config.DEFAULTS)
        self.ok = self.root / "ok.py"
        self.ok.write_text(f"import sys\nprint({ANSWER!r})\n")
        self.bad = self.root / "bad.py"
        self.bad.write_text("import sys\nsys.exit(3)\n")
        py = sys.executable
        self.conf["ask"]["easy"] = [["missing-agy-xyz", "{prompt}"], [py, str(self.bad), "{prompt}"], [py, str(self.ok), "{prompt}"]]
        self.conf["ask"]["deep"] = [[py, str(self.ok), "{prompt}"]]

    def tearDown(self):
        self._tmp.cleanup()

    def test_locate_and_context(self):
        pages = ["intro text", "Ohm's law states   that the current\nthrough a conductor is proportional", "end"]
        self.assertEqual(ask.locate(pages, "the current through a conductor"), 2)
        self.assertEqual(ask.locate(pages, "proportional conductor current law ohm's states"), 2)
        self.assertIsNone(ask.locate(pages, "completely unrelated words here today"))
        self.assertIn("current through", ask.context_window(pages, 2, "current through", 40))

    def test_wants_deep(self):
        self.assertTrue(ask.wants_deep("이 식을 유도해줘", "text"))
        self.assertTrue(ask.wants_deep("설명", "∑ a_i = ∫ f dx ≈ ∇φ"))
        self.assertFalse(ask.wants_deep("쉽게 설명해줘", "the current through a conductor"))

    def test_resolve_source(self):
        pdf = self.root / "a b.pdf"
        pdf.write_bytes(b"%PDF")
        self.assertEqual(ask.resolve_source(pdf.as_uri()), pdf)
        self.assertEqual(ask.resolve_source(str(pdf)), pdf)
        self.assertIsNone(ask.resolve_source(""))
        self.assertIsNone(ask.resolve_source(str(self.root / "missing.pdf")))

    def test_ask_falls_back_and_saves(self):
        r = ask.ask(self.root, self.conf, "the current through a conductor", "쉽게 설명해줘")
        self.assertEqual(Path(r["runner"]).name.startswith("python"), True)
        self.assertFalse(r["deep"])
        self.assertTrue(any("exit 3" in f for f in r["failures"]))
        page = r["html"].read_text()
        self.assertIn("katex", page)
        self.assertIn("쉽게 설명해줘", page)
        self.assertNotIn("</script>\"", page)          # 답변 속 </script> 가 페이지를 깨지 않음
        self.assertIn("<\\/script>", page)
        qa = r["notes"].read_text()
        self.assertIn("> the current through a conductor", qa)
        self.assertIn("$$P = I^2 R$$", qa)

    def test_deep_uses_deep_runners(self):
        self.conf["ask"]["easy"] = [[sys.executable, str(self.bad), "{prompt}"]]
        r = ask.ask(self.root, self.conf, "x", "이 식을 유도해줘")
        self.assertTrue(r["deep"])
        self.assertEqual(r["failures"], [])

    def test_no_runner_raises(self):
        self.conf["ask"]["easy"] = [["missing-1"]]
        self.conf["ask"]["deep"] = [["missing-2"]]
        with self.assertRaises(RuntimeError):
            ask.ask(self.root, self.conf, "x", "q")

    def test_install_quick_action(self):
        with mock.patch.object(Path, "home", return_value=self.root):
            script = ask.install_quick_action(self.root)
        text = script.read_text()
        self.assertIn(f'cd "{self.root}"', text)
        self.assertIn("relay ask --source", text)
        self.assertTrue(os.access(script, os.X_OK))


if __name__ == "__main__":
    unittest.main()
