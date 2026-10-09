import glob
import json
import os
import re
import unittest
from collections import Counter

import _helpers  # noqa: F401  (sets sys.path)
import applies_if
import scan

ROOT = _helpers.ROOT_DIR
with open(os.path.join(ROOT, "rules", "gates.json"), encoding="utf-8") as _f:
    DATA = json.load(_f)
GATES = DATA["gates"]
PREFIXES = {"trigger": "TRG", "scope": "SCP", "clarity": "CLR", "conciseness": "CON",
            "workflow": "WFL", "scripts_tools": "SCT", "safety": "SAF", "output": "OUT",
            "evaluation": "EVL"}
KINDS = {"B": "blocker", "CG": "critical", "QG": "quality"}
ID_RE = re.compile(r"^### ([A-Z]{3}-(?:B|CG|QG)\d+) — ", re.M)


class GatesSyncTests(unittest.TestCase):
    def test_ids_match_rubric(self):
        ids = []
        for path in sorted(glob.glob(os.path.join(ROOT, "rubric", "*.md"))):
            with open(path, encoding="utf-8") as f:
                ids += ID_RE.findall(f.read())
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), {g["id"] for g in GATES})
        self.assertEqual(len(glob.glob(os.path.join(ROOT, "rubric", "*.md"))), 9)

    def test_counts(self):
        kinds = Counter(g["kind"] for g in GATES)
        self.assertEqual(kinds, {"blocker": 10, "critical": 23, "quality": 28})
        self.assertEqual(len({g["id"] for g in GATES}), len(GATES))

    def test_kind_matches_id(self):
        for g in GATES:
            m = re.match(r"^([A-Z]{3})-(B|CG|QG)\d+$", g["id"])
            self.assertTrue(m, g["id"])
            self.assertEqual(PREFIXES[g["category"]], m.group(1), g["id"])
            self.assertEqual(KINDS[m.group(2)], g["kind"], g["id"])

    def test_applies_if_parse_and_known_flags(self):
        for g in GATES:
            self.assertLessEqual(applies_if.names(g["applies_if"]), set(scan.FLAGS), g["id"])
        for key, c in DATA["categories"].items():
            self.assertIn(key, PREFIXES)
            self.assertLessEqual(applies_if.names(c["applies_if"]), set(scan.FLAGS), key)

    def test_script_checks(self):
        self.assertEqual(
            {g["id"]: g["script_check"] for g in GATES if g["answered_by"] == "script"},
            {"EVL-QG1": "ingest.has_valid_result", "EVL-QG2": "ingest.has_baseline",
             "SAF-CG1": "judgment.no_undisclosed_capabilities"})
        for g in GATES:
            if g["answered_by"] == "judge":
                self.assertIsNone(g["script_check"], g["id"])

    def test_short_and_why(self):
        for g in GATES:
            self.assertTrue(1 <= len(g["short"].split()) <= 8, g["id"])
            self.assertTrue(g["why"].endswith("."), g["id"])
        by_id = {g["id"]: g for g in GATES}
        self.assertEqual(by_id["SAF-CG3"]["short"], "untrusted content treated as data")

    def test_schema_version(self):
        self.assertEqual(DATA["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
