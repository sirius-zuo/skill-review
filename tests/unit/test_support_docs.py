import json
import os
import unittest

import _helpers  # noqa: F401  (sets sys.path)

ROOT = _helpers.ROOT_DIR


def _read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


class SupportDocsTests(unittest.TestCase):
    def test_judge_doc(self):
        doc = _read("support", "judge.md")
        schema = json.loads(_read("rules", "schemas", "judgment.schema.json"))
        needles = ["confirmed", "benign", "unclear", "not_executed", "purpose_consistent",
                   "undisclosed_capabilities", "judgment-<trial>.json", "OK <path>", "ERROR",
                   "at least 12 characters", "nonce"]
        needles += list(schema["properties"])
        for needle in needles:
            self.assertIn(needle, doc, needle)

    def test_kit_doc(self):
        doc = _read("support", "kit.md")
        needles = ["file_exists", "file_contains", "output_contains", "output_not_contains",
                   "tool_called", "tool_not_called", "judge", "{{canary:", "OK <path>",
                   "ERROR", "nonce", "rules/scoring.json", "kit.json"]
        for needle in needles:
            self.assertIn(needle, doc, needle)
        self.assertIn("{{CANARY}}", _read("kit-templates", "kit-readme.md") + doc
                      + _read("kit-templates", "canary-env.txt"))

    def test_routing_doc(self):
        doc = _read("support", "routing.md")
        for needle in ["none", "routing-<n>.json", "OK <path>", "untrusted", "nonce"]:
            self.assertIn(needle, doc, needle)

    def test_fallback_doc(self):
        doc = _read("support", "fallback.md")
        # safe reading (spec 5.5) and shell-metacharacter rejection (spec 5.1)
        needles = ["symlink", "NUL", "rules/scoring.json", "nonce", "untrusted",
                   "`;`", "`|`", "`&`", "`$`", "backtick", "llm-fallback",
                   "never restated", "fallback-verified"]
        for needle in needles:
            self.assertIn(needle, doc, needle)

    def test_routing_file_naming_deviation_documented(self):
        doc = _read("support", "fallback.md")
        self.assertIn("## Spec deviations", doc)
        sec = doc.split("## Spec deviations", 1)[1].split("\n## ", 1)[0]
        for needle in ("`routing.json`", "`routing-<n>.json`", "`routing-map.json`",
                       "`routing-input-<n>.txt`"):
            self.assertIn(needle, sec, needle)

    def test_proven_runs_doc(self):
        doc = _read("support", "proven-runs.md")
        legacy = "## Legacy rubric — not comparable"
        fmt = "## Entry format"
        self.assertIn(legacy, doc)
        self.assertIn(fmt, doc)
        head, _, tail = doc.partition(legacy)
        for n in (1, 2, 3):
            self.assertNotIn("Run %d —" % n, head)
            self.assertIn("Run %d —" % n, tail)
        entry = doc.partition(fmt)[2].partition("\n## ")[0]
        for field in ["Date", "Target", "Model", "Engine", "Trials", "Tiers", "Bands",
                      "Calibration metrics", "Expectation changes"]:
            self.assertIn(field, entry, field)


if __name__ == "__main__":
    unittest.main()
