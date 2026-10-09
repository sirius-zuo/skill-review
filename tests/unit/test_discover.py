import os
import unittest

import _helpers
from _helpers import make_tree, tempdir
from common import validate_against
from discover import build_manifest

SK = "---\nname: %s\ndescription: d\n---\nbody\n"


def skill(name):
    return SK % name


class DiscoverTests(unittest.TestCase):
    def test_nested_fixture_hidden_git(self):
        with tempdir() as root:
            make_tree(root, {
                "a/SKILL.md": skill("a"), "a/tests/fx/SKILL.md": skill("fx"),
                "a/.hidden/x.sh": "echo", "a/support/s.md": "s",
                ".git/config": "x", "b/sub/SKILL.md": skill("sub")})
            m = build_manifest(root)
            kinds = {s["dir"]: s["kind"] for s in m["skills"]}
            self.assertEqual(kinds, {"a": "skill", "a/tests/fx": "fixture", "b/sub": "skill"})
            a = [s for s in m["skills"] if s["dir"] == "a"][0]
            paths = {f["path"] for f in a["files"]}
            self.assertTrue({"a/.hidden/x.sh", "a/support/s.md", "a/tests/fx/SKILL.md"} <= paths)
            self.assertFalse(any(p.startswith(".git/") for p in paths))
            hidden = [f for f in a["files"] if f["path"] == "a/.hidden/x.sh"][0]
            self.assertTrue(hidden["hidden"])

    def test_other_md_with_frontmatter_is_not_a_skill(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a"), "a/agents/r.md": skill("r")})
            m = build_manifest(root)
            self.assertEqual([s["dir"] for s in m["skills"]], ["a"])

    def test_binary_large_limit(self):
        with tempdir() as root:
            spec = {"a/SKILL.md": skill("a"), "a/bin.dat": b"ab\x00cd",
                    "a/big.txt": "x" * 262145}
            for i in range(500):
                spec["a/f/%03d.txt" % i] = "x"
            make_tree(root, spec)
            m = build_manifest(root)
            a = m["skills"][0]
            by = {f["path"]: f for f in a["files"]}
            self.assertTrue(by["a/bin.dat"]["binary"])
            self.assertEqual(by["a/bin.dat"]["skip_reason"], "binary")
            self.assertEqual(by["a/big.txt"]["skip_reason"], "too_large")
            self.assertFalse(by["a/big.txt"]["bundled"])
            self.assertIn("FILE_LIMIT", [w["code"] for w in m["warnings"]])
            self.assertEqual(sum(1 for f in a["files"] if f["skip_reason"] == "file_limit"), 3)
            self.assertEqual(validate_against(m, "manifest"), [])

    def test_non_utf8(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a"),
                             "a/l1.txt": "caf\xe9".encode("latin-1"),
                             "a/u16.txt": "﻿hello".encode("utf-16")})
            m = build_manifest(root)
            by = {f["path"]: f for f in m["skills"][0]["files"]}
            self.assertTrue(by["a/l1.txt"]["bundled"])
            self.assertFalse(by["a/l1.txt"]["binary"])
            self.assertTrue(by["a/u16.txt"]["binary"])

    def test_symlink_loop_terminates(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a")})
            os.symlink(".", os.path.join(root, "a", "loop"))
            m = build_manifest(root)
            by = {f["path"]: f for f in m["skills"][0]["files"]}
            self.assertEqual(by["a/loop"]["symlink"], ".")
            self.assertEqual(by["a/loop"]["skip_reason"], "symlink")
            self.assertFalse(by["a/loop"]["bundled"])

    def test_name_conflict_and_keys(self):
        with tempdir() as root:
            make_tree(root, {"x/SKILL.md": skill("dup"), "y/SKILL.md": skill("dup"),
                             "z/SKILL.md": skill("My Skill"), "w/SKILL.md": skill("my-skill")})
            m = build_manifest(root)
            self.assertIn("NAME_CONFLICT", [w["code"] for w in m["warnings"]])
            self.assertEqual(len({s["key"] for s in m["skills"]}), len(m["skills"]))
            self.assertEqual(m["skills"][0]["key"], "00-my-skill")

    def test_name_fallback_and_root_skill(self):
        with tempdir() as root:
            make_tree(root, {"SKILL.md": "no frontmatter\n", "f/SKILL.md": "---\nname: \n---\n"})
            m = build_manifest(root)
            names = {s["dir"]: s["name"] for s in m["skills"]}
            self.assertEqual(names["f"], "f")
            self.assertEqual(names["."], os.path.basename(root))

    def test_self_review(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a")})
            self.assertTrue(build_manifest(root, self_dir=root)["self_review"])
            self.assertFalse(build_manifest(root)["self_review"])

    def test_lowercase_skill_md(self):
        with tempdir() as root:
            make_tree(root, {"a/skill.md": skill("a")})
            m = build_manifest(root)
            self.assertEqual(m["skills"][0]["skill_file"], "a/skill.md")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "no mkfifo")
    def test_fifo_does_not_hang(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a")})
            os.mkfifo(os.path.join(root, "a", "pipe"))
            m = build_manifest(root)
            by = {f["path"]: f for f in m["skills"][0]["files"]}
            self.assertEqual(by["a/pipe"]["skip_reason"], "special")
            self.assertFalse(by["a/pipe"]["bundled"])
            self.assertEqual(validate_against(m, "manifest"), [])

    @unittest.skipIf(hasattr(os, "geteuid") and os.geteuid() == 0, "root reads anything")
    def test_unreadable_file(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a"), "a/secret.txt": "x"})
            p = os.path.join(root, "a", "secret.txt")
            os.chmod(p, 0)
            try:
                m = build_manifest(root)
            finally:
                os.chmod(p, 0o600)
            by = {f["path"]: f for f in m["skills"][0]["files"]}
            self.assertEqual(by["a/secret.txt"]["skip_reason"], "unreadable")
            self.assertIn("UNREADABLE_FILE", [w["code"] for w in m["warnings"]])
            self.assertEqual(validate_against(m, "manifest"), [])

    def test_repo_files(self):
        with tempdir() as root:
            make_tree(root, {"README.md": "r", "a/SKILL.md": skill("a")})
            m = build_manifest(root)
            self.assertEqual([f["path"] for f in m["repo_files"]], ["README.md"])

    def test_manifest_validates(self):
        with tempdir() as root:
            make_tree(root, {"a/SKILL.md": skill("a"), "a/x.txt": "x"})
            m = build_manifest(root, source={"type": "github", "url": "u", "clone_dir": "/c"})
            self.assertEqual(validate_against(m, "manifest"), [])
            self.assertEqual(m["source"]["type"], "github")


if __name__ == "__main__":
    unittest.main()
