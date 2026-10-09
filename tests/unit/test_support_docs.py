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


if __name__ == "__main__":
    unittest.main()
