"""End-to-end deterministic pipeline checks on the fixture skills.

For each fixture folder: copy to a temp root, run discover, bundle, lint, scan
and ingest, normalize volatile fields, and compare with tests/fixtures/expected.
UPDATE_GOLDEN=1 regenerates the golden files.
"""
import json
import os
import unittest

import _helpers
from _helpers import FIXTURES_DIR, ROOT_DIR, run_script, tempdir

EXPECTED_DIR = os.path.join(FIXTURES_DIR, "expected")
NAMES = ("bad-skill", "good-skill", "skillset")


def _copy_tree(src, dst):
    import shutil
    shutil.copytree(src, dst)


def _normalize(obj, subs):
    text = json.dumps(obj, sort_keys=True)
    for old, new in subs:
        text = text.replace(json.dumps(old)[1:-1], new)
    data = json.loads(text)

    def walk(o):
        if isinstance(o, dict):
            return {k: walk(v) for k, v in o.items() if k not in ("nonce", "created")}
        if isinstance(o, list):
            return [walk(v) for v in o]
        return o
    return walk(data)


def run_pipeline(fixture):
    """Return (normalized result dict, raw per-skill scan dicts by skill name)."""
    with tempdir() as base:
        root = os.path.join(base, fixture)
        out = os.path.join(base, "run")
        _copy_tree(os.path.join(FIXTURES_DIR, fixture), root)
        p = run_script("discover.py", "run", root, "--self-dir", ROOT_DIR, "--out", out,
                       "--no-kit", "--no-routing")
        if p.returncode != 0:
            raise AssertionError("discover failed: %s %s" % (p.stdout, p.stderr))
        work = json.loads(p.stdout)["work_dir"]
        for script in ("bundle.py", "lint.py", "scan.py", "ingest.py"):
            p = run_script(script, "--work-dir", work)
            if p.returncode != 0:
                raise AssertionError("%s failed: %s %s" % (script, p.stdout, p.stderr))
        with open(os.path.join(work, "manifest.json"), encoding="utf-8") as f:
            manifest = json.load(f)
        result = {"manifest": manifest, "skills": {}}
        scans = {}
        for sk in manifest["skills"]:
            entry = {}
            for part in ("lint", "scan", "ingest"):
                with open(os.path.join(work, sk["key"], part + ".json"), encoding="utf-8") as f:
                    entry[part] = json.load(f)
            result["skills"][sk["key"]] = entry
            scans[sk["name"]] = entry["scan"]
        subs = [(out, "<RUN>"), (root, "<ROOT>"), (base, "<TMP>")]
        return _normalize(result, subs), scans


class FixturesE2ETests(unittest.TestCase):
    def test_goldens(self):
        update = os.environ.get("UPDATE_GOLDEN") == "1"
        for name in NAMES:
            with self.subTest(fixture=name):
                result, _ = run_pipeline(name)
                path = os.path.join(EXPECTED_DIR, name + ".json")
                if update:
                    os.makedirs(EXPECTED_DIR, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(result, f, indent=2, sort_keys=True, ensure_ascii=False)
                        f.write("\n")
                self.assertTrue(os.path.exists(path), "missing golden " + path)
                with open(path, encoding="utf-8") as f:
                    expected = json.load(f)
                self.assertEqual(result, expected)

    def test_file_deployer_inventory(self):
        _, scans = run_pipeline("skillset")
        inv = scans["file-deployer"]["inventory"]
        self.assertEqual(inv["irreversible"]["value"], "suspected")
        self.assertNotEqual(inv["network_write"]["value"], "false")

    def test_good_skill_has_no_hits(self):
        _, scans = run_pipeline("good-skill")
        for scan in scans.values():
            self.assertEqual(scan["hits"], [])


if __name__ == "__main__":
    unittest.main()
