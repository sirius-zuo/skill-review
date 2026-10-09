import json
import os
import unittest

import _helpers
from _helpers import ROOT_DIR, make_tree, run_script, tempdir
from common import read_json, validate_against

SK = "---\nname: %s\ndescription: d\n---\nbody\n"


def run_json(*args):
    p = run_script("discover.py", *args)
    return p, (json.loads(p.stdout) if p.stdout.strip() else None)


class DiscoverCliTests(unittest.TestCase):
    def test_cli_confirmations(self):
        with tempdir() as root:
            make_tree(root, {"s/SKILL.md": SK % "s"})
            p, out = run_json("run", root, "--self-dir", root + "/elsewhere", "--out", root + "/out")
            self.assertEqual(p.returncode, 1)
            self.assertEqual(out["confirm"], "OUT_INSIDE_ROOT")
            self.assertFalse(os.path.exists(root + "/out"))
            with tempdir() as outd:
                p, out = run_json("run", root, "--self-dir", root, "--out", outd + "/o")
                self.assertEqual(p.returncode, 1)
                self.assertEqual(out["confirm"], "SELF_REVIEW")
                self.assertFalse(os.path.exists(outd + "/o"))
                p, out = run_json("run", root, "--self-dir", root, "--out", outd + "/o",
                                  "--allow-self-review")
                self.assertEqual(p.returncode, 0, p.stderr)

    def test_cli_estimate_and_no_skills(self):
        with tempdir() as root, tempdir() as outd:
            make_tree(root, {"s%d/SKILL.md" % i: SK % ("s%d" % i) for i in range(11)})
            out_dir = outd + "/run"
            p, out = run_json("run", root, "--self-dir", ROOT_DIR, "--out", out_dir, "--trials", "2")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertEqual(out["estimate"], {"passes": 36, "ask": True})
            self.assertEqual(len(out["skills"]), 11)
            work = os.path.join(out_dir, "work")
            run = read_json(work + "/run.json")
            self.assertEqual(validate_against(run, "run"), [])
            self.assertEqual(run["args"]["trials"], 2)
            self.assertEqual(run["kit_dir"], os.path.join(out_dir, "kit"))
            self.assertEqual(validate_against(read_json(work + "/manifest.json"), "manifest"), [])
            p, _ = run_json("run", root, "--self-dir", ROOT_DIR, "--out", out_dir + "2",
                            "--no-kit", "--mode", "single")
            self.assertEqual(json.loads(p.stdout)["estimate"]["passes"], 11)
        with tempdir() as empty, tempdir() as outd:
            p, out = run_json("run", empty, "--self-dir", ROOT_DIR, "--out", outd + "/r")
            self.assertEqual(p.returncode, 1)
            self.assertIn("No SKILL.md found under", out["errors"][0])
            self.assertFalse(os.path.exists(outd + "/r"))

    def test_cli_bad_target_and_cleanup(self):
        p = run_script("discover.py", "run", "/no/such/dir", "--self-dir", ROOT_DIR)
        self.assertEqual(p.returncode, 2)
        self.assertIn("is not a valid local path or GitHub URL", p.stderr)
        with tempdir() as root, tempdir() as outd:
            make_tree(root, {"s/SKILL.md": SK % "s"})
            p, out = run_json("run", root, "--self-dir", ROOT_DIR, "--out", outd + "/r")
            self.assertEqual(p.returncode, 0, p.stderr)
            p = run_script("discover.py", "cleanup", "--work-dir", out["work_dir"])
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertFalse(os.path.exists(out["work_dir"]))
            self.assertTrue(os.path.isdir(outd + "/r"))
            p, out = run_json("run", root, "--self-dir", ROOT_DIR, "--out", outd + "/k", "--keep-work")
            run_script("discover.py", "cleanup", "--work-dir", out["work_dir"])
            self.assertTrue(os.path.exists(out["work_dir"] + "/run.json"))


if __name__ == "__main__":
    unittest.main()
