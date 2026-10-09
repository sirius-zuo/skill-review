import json
import os
import shutil
import subprocess
import sys
import unittest

import _helpers  # noqa: F401  (sets sys.path)

from common import validate_against
from export_kit import export_plugin_eval, js_escape

FIX = os.path.join(_helpers.FIXTURES_DIR, "kit-export")
EXPECTED = os.path.join(FIX, "expected")
SCRIPT = os.path.join(_helpers.SCRIPTS_DIR, "export_kit.py")


def _tree(root):
    out = {}
    for d, _, files in os.walk(root):
        for f in files:
            full = os.path.join(d, f)
            with open(full, "rb") as fh:
                out[os.path.relpath(full, root).replace(os.sep, "/")] = fh.read()
    return out


def _stage(tmp):
    kit_dir = os.path.join(tmp, "kit", "doc-fixer")
    shutil.copytree(FIX, kit_dir, ignore=shutil.ignore_patterns("expected"))
    return kit_dir


class JsEscapeTests(unittest.TestCase):
    def test_escapes_specials(self):
        self.assertEqual(js_escape("a.b*c+d?(e)[f]{g}|h/i-j\\k^l$"),
                         "a\\.b\\*c\\+d\\?\\(e\\)\\[f\\]\\{g\\}\\|h\\/i\\-j\\\\k\\^l\\$")

    def test_plain_unchanged(self):
        self.assertEqual(js_escape("abc DEF_123"), "abc DEF_123")


class GoldenTests(unittest.TestCase):
    def _export(self, tmp):
        kit_dir = _stage(tmp)
        with open(os.path.join(kit_dir, "kit.json"), encoding="utf-8") as f:
            kit = json.load(f)
        self.assertEqual(validate_against(kit, "kit"), [])
        files = export_plugin_eval(kit, kit_dir, "doc-fixer")
        return kit_dir, files

    def test_golden_tree(self):
        with _helpers.tempdir() as tmp:
            kit_dir, files = self._export(tmp)
            actual = _tree(os.path.join(kit_dir, "evals"))
            self.assertEqual(sorted("evals/" + k for k in actual), files)
            if os.environ.get("UPDATE_GOLDEN") == "1":
                shutil.rmtree(EXPECTED, ignore_errors=True)
                for rel, data in actual.items():
                    p = os.path.join(EXPECTED, rel)
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    with open(p, "wb") as f:
                        f.write(data)
            expected = _tree(EXPECTED)
            self.assertEqual(sorted(actual), sorted(expected))
            for rel in sorted(expected):
                self.assertEqual(actual[rel].decode("utf-8"), expected[rel].decode("utf-8"), rel)

    def test_rejects_unsafe_ids_and_fixtures(self):
        with _helpers.tempdir() as tmp:
            kit_dir = _stage(tmp)
            with open(os.path.join(kit_dir, "kit.json"), encoding="utf-8") as f:
                kit = json.load(f)
            kit["task_cases"][0]["id"] = "../x"
            with self.assertRaises(ValueError):
                export_plugin_eval(kit, kit_dir, "doc-fixer")

    def test_refuses_symlinked_fixture(self):
        with _helpers.tempdir() as tmp:
            kit_dir = _stage(tmp)
            target = os.path.join(kit_dir, "fixtures", "report.md")
            os.unlink(target)
            os.symlink(os.path.join(tmp, "outside"), target)
            with open(os.path.join(kit_dir, "kit.json"), encoding="utf-8") as f:
                kit = json.load(f)
            with self.assertRaises(ValueError):
                export_plugin_eval(kit, kit_dir, "doc-fixer")


class CliTests(unittest.TestCase):
    def test_cli(self):
        with _helpers.tempdir() as tmp:
            kit_root = os.path.join(tmp, "kit")
            shutil.copytree(FIX, os.path.join(kit_root, "doc-fixer"),
                            ignore=shutil.ignore_patterns("expected"))
            work = os.path.join(tmp, "work")
            os.makedirs(os.path.join(work, "doc-fixer"))
            with open(os.path.join(work, "run.json"), "w") as f:
                json.dump({"kit_dir": kit_root}, f)
            r = subprocess.run([sys.executable, SCRIPT, "--work-dir", work, "--skill",
                                "doc-fixer", "--format", "claude-plugin-eval"],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True)
            self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
            self.assertTrue(json.loads(r.stdout)["ok"])
            self.assertTrue(os.path.isfile(os.path.join(
                kit_root, "doc-fixer", "evals", "t01", "graders", "skill-fired.md")))

    def test_bad_format(self):
        r = subprocess.run([sys.executable, SCRIPT, "--work-dir", "/nonexistent", "--skill",
                            "a", "--format", "nope"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(r.returncode, 2)


if __name__ == "__main__":
    unittest.main()
