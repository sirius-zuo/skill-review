import os
import shutil
import subprocess
import time
import unittest
from datetime import datetime

import _helpers
from _helpers import make_tree, tempdir
from common import UsageError, validate_against
from discover import (CloneError, build_manifest, cleanup_stale_clones, clone_repo,
                      default_run_dir, estimate_passes, extract_references, validate_target)

SK = "---\nname: %s\ndescription: d\n---\n%s\n"


def skill(name, body="body"):
    return SK % (name, body)


class ReferenceTests(unittest.TestCase):
    def test_extract_references(self):
        refs = extract_references(
            "See [g](ref/guide.md#x), [w](https://a.b), `scripts/run.sh`, `-rf.sh`, `x`.\n")
        self.assertEqual(refs, [("link", "ref/guide.md", 1), ("code", "scripts/run.sh", 1)])

    def test_extract_skips_anchors_and_mailto(self):
        self.assertEqual(extract_references("[a](#top) [b](mailto:x@y.z)\n\n[c](d.md)"),
                         [("link", "d.md", 3)])

    def test_reference_depth_and_outside(self):
        with tempdir() as root:
            make_tree(root, {
                "s/SKILL.md": skill("s", "[a](a.md) [o](../../out.md) [m](missing.md) `ghost.sh`"),
                "s/a.md": "[b](b.md) [a](a.md)", "s/b.md": "[c](c.md)", "s/c.md": "c"})
            m = build_manifest(root)
            refs = {r["target"]: r for r in m["skills"][0]["references"]}
            self.assertEqual(refs["a.md"]["depth"], 1)
            self.assertEqual(refs["a.md"]["from"], "s/SKILL.md")
            self.assertEqual(refs["b.md"]["depth"], 2)
            self.assertNotIn("c.md", refs)
            self.assertFalse(refs["../../out.md"]["inside_root"])
            self.assertFalse(refs["../../out.md"]["exists"])
            self.assertFalse(refs["missing.md"]["exists"])
            self.assertNotIn("ghost.sh", refs)
            self.assertEqual(validate_against(m, "manifest"), [])

    def test_external_ref_included(self):
        with tempdir() as root:
            make_tree(root, {"s/SKILL.md": skill("s", "run [x](../shared/x.sh)"),
                             "shared/x.sh": "echo", "shared/other.sh": "echo"})
            m = build_manifest(root)
            paths = [f["path"] for f in m["skills"][0]["files"]]
            self.assertEqual(paths.count("shared/x.sh"), 1)
            self.assertNotIn("shared/other.sh", paths)

    def test_symlink_reference_not_followed(self):
        with tempdir() as root, tempdir() as outside:
            make_tree(root, {"s/SKILL.md": skill("s", "[l](lnk/x.md) [t](t.md)")})
            make_tree(outside, {"x.md": "secret"})
            os.symlink(outside, os.path.join(root, "s", "lnk"))
            os.symlink(os.path.join(outside, "x.md"), os.path.join(root, "s", "t.md"))
            m = build_manifest(root)
            s = m["skills"][0]
            refs = {r["target"]: r for r in s["references"]}
            self.assertFalse(refs["lnk/x.md"]["exists"])
            self.assertEqual([r for r in s["references"] if r["depth"] == 2], [])

    def test_fifo_reference_not_read(self):
        with tempdir() as root:
            make_tree(root, {"s/SKILL.md": skill("s", "[f](p.md)")})
            os.mkfifo(os.path.join(root, "s", "p.md"))
            m = build_manifest(root)
            f = [x for x in m["skills"][0]["files"] if x["path"] == "s/p.md"][0]
            self.assertEqual(f["skip_reason"], "special")

    def test_repo_files_and_eval_results(self):
        with tempdir() as root:
            make_tree(root, {
                ".mcp.json": "{}", "README.md": "r", "s/SKILL.md": skill("s"),
                "s/evals/kit-results.json": "not json",
                "s/r.json": '{"cases":[{"arms":{}}]}',
                "s/other.json": '{"cases":[{"x":1}]}', "s/bad.json": "{"})
            m = build_manifest(root)
            self.assertIn(".mcp.json", [f["path"] for f in m["repo_files"]])
            self.assertEqual(sorted(m["skills"][0]["eval_result_files"]),
                             ["s/evals/kit-results.json", "s/r.json"])
            self.assertEqual(validate_against(m, "manifest"), [])


class TargetTests(unittest.TestCase):
    def test_validate_target(self):
        self.assertEqual(validate_target("https://github.com/org/repo.git")[0], "github")
        self.assertEqual(validate_target("https://github.com/org/repo/")[0], "github")
        with tempdir() as d:
            self.assertEqual(validate_target(d), ("local", os.path.realpath(d)))
        for bad in ("http://github.com/o/r", "https://gitlab.com/o/r",
                    "https://github.com/-o/r", "/no/such/dir"):
            with self.assertRaises(UsageError) as cm:
                validate_target(bad)
            self.assertIn("is not a valid local path or GitHub URL", str(cm.exception))

    @unittest.skipUnless(shutil.which("git"), "git required")
    def test_clone_and_failure(self):
        with tempdir() as base, tempdir() as tmp:
            repo = os.path.join(base, "r")
            os.makedirs(repo)
            make_tree(repo, {"README": "hi"})
            env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
            for cmd in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "x"]):
                subprocess.run(["git", "-C", repo] + cmd, env=env, check=True)
            clone_tmp, clone_repo_dir = clone_repo("file://" + repo, tmp)
            self.assertTrue(os.path.exists(os.path.join(clone_tmp, ".skill-review-clone")))
            self.assertTrue(os.path.exists(os.path.join(clone_repo_dir, "README")))
            with self.assertRaises(CloneError) as cm:
                clone_repo("file:///no/such", tmp)
            self.assertNotEqual(cm.exception.exit_code, 0)
            left = [n for n in os.listdir(tmp) if n.startswith("skill-review-clone-")]
            self.assertEqual(left, [os.path.basename(clone_tmp)])

    def test_cleanup_stale_only_marked(self):
        with tempdir() as tmp:
            old = time.time() - 48 * 3600
            for name, marked in (("skill-review-clone-a", True), ("skill-review-clone-b", False)):
                d = os.path.join(tmp, name)
                os.makedirs(d)
                if marked:
                    open(os.path.join(d, ".skill-review-clone"), "w").close()
                os.utime(d, (old, old))
            fresh = os.path.join(tmp, "skill-review-clone-c")
            os.makedirs(fresh)
            open(os.path.join(fresh, ".skill-review-clone"), "w").close()
            removed = cleanup_stale_clones(tmp, time.time(), 24)
            self.assertEqual([os.path.basename(p) for p in removed], ["skill-review-clone-a"])
            self.assertTrue(os.path.isdir(os.path.join(tmp, "skill-review-clone-b")))
            self.assertTrue(os.path.isdir(fresh))

    def test_run_dir_and_estimate(self):
        self.assertEqual(default_run_dir("My Skill!", "/c", datetime(2026, 10, 8, 9, 5)),
                         "/c/skill-reviews/my-skill-20261008T0905")
        self.assertEqual(estimate_passes(11, 2, False, True), 36)
        self.assertEqual(estimate_passes(3, 1, True, False), 3)


if __name__ == "__main__":
    unittest.main()
