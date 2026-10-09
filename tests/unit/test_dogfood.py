"""Dogfood: lint and scan this repository's tracked files.

Runs on a temp copy of `git ls-files` so ignored working files never count and
nothing is written into the repository.
"""
import fnmatch
import glob
import json
import os
import shutil
import subprocess
import unittest

import _helpers
from _helpers import ROOT_DIR, run_script, tempdir

ALLOWLIST = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dogfood-allowlist.json")
ALLOWED_GLOBS = {"rules/security-patterns.json", "tests/**", "kit-templates/**"}
BAD_LINT = ("blocker", "major")
BAD_SCAN = ("critical", "major")


def _glob_match(path, pattern):
    if pattern.endswith("/**"):
        return path.startswith(pattern[:-2])
    return fnmatch.fnmatchcase(path, pattern)


def _allowed(entries, pattern_id, path):
    return any(e["pattern_id"] == pattern_id and _glob_match(path, e["path_glob"])
               for e in entries)


class DogfoodTests(unittest.TestCase):
    def test_allowlist_shape(self):
        with open(ALLOWLIST, encoding="utf-8") as f:
            entries = json.load(f)["entries"]
        self.assertTrue(entries)
        for e in entries:
            self.assertEqual(sorted(e), ["path_glob", "pattern_id", "reason"])
            self.assertIn(e["path_glob"], ALLOWED_GLOBS)
            self.assertTrue(e["reason"].strip())

    def test_repo_has_no_blocker_or_major(self):
        with open(ALLOWLIST, encoding="utf-8") as f:
            entries = json.load(f)["entries"]
        files = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT_DIR, stdout=subprocess.PIPE,
                               check=True).stdout.decode("utf-8").split("\0")
        files = [x for x in files if x]
        with tempdir() as base:
            copy = os.path.join(base, "copy")
            for rel in files:
                src = os.path.join(ROOT_DIR, rel)
                if not os.path.isfile(src) and not os.path.islink(src):
                    continue
                dst = os.path.join(copy, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if os.path.islink(src):
                    os.symlink(os.readlink(src), dst)
                else:
                    shutil.copy2(src, dst)
            out = os.path.join(base, "run")
            p = run_script("discover.py", "run", copy, "--self-dir", os.path.join(base, "none"),
                           "--out", out, "--no-kit", "--no-routing", "--allow-self-review")
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            work = json.loads(p.stdout)["work_dir"]
            for script in ("bundle.py", "lint.py", "scan.py"):
                p = run_script(script, "--work-dir", work)
                self.assertEqual(p.returncode, 0, script + p.stdout + p.stderr)
            problems = []
            for path in sorted(glob.glob(os.path.join(work, "*", "lint.json"))):
                with open(path, encoding="utf-8") as f:
                    for fnd in json.load(f)["findings"]:
                        if fnd["severity"] in BAD_LINT and not _allowed(entries, fnd["rule_id"], fnd["file"]):
                            problems.append("lint %s %s:%s %s" % (fnd["rule_id"], fnd["file"],
                                                                  fnd["line"], fnd["severity"]))
            for path in sorted(glob.glob(os.path.join(work, "*", "scan.json"))):
                with open(path, encoding="utf-8") as f:
                    for hit in json.load(f)["hits"]:
                        if hit["severity"] in BAD_SCAN and not _allowed(entries, hit["pattern_id"], hit["file"]):
                            problems.append("scan %s %s:%s %s" % (hit["pattern_id"], hit["file"],
                                                                  hit["line"], hit["severity"]))
            self.assertEqual(problems, [], "\n".join(problems))


if __name__ == "__main__":
    unittest.main()
