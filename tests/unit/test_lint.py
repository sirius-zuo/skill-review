import json
import os
import unittest

import _helpers
from _helpers import make_tree, run_script, tempdir

from common import load_rules, scoring, validate_against, write_json
from discover import build_manifest
from lint import lint_skill

BASE_DESC = "Cleans CSV exports. Use when the user asks to fix CSV delimiters."


def fm(name="csv-cleaner", desc=BASE_DESC, extra=None):
    lines = ["---"]
    if name is not None:
        lines.append("name: %s" % name)
    if desc is not None:
        lines.append("description: %s" % desc)
    for k, v in (extra or {}).items():
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    return "\n".join(lines) + "\n"


def q(s):
    return '"%s"' % s


def skill_text(body="Body.\n", **kw):
    return fm(**kw) + body


def lines(n):
    return "".join("line %d\n" % i for i in range(n))


def desc_of(n):
    return q("Use when " + "x" * (n - 9))


def run_lint(spec, folder="csv-cleaner", which=None):
    """Build files from spec, return findings for the skill in `which` (default: first)."""
    with tempdir() as root:
        make_tree(root, spec)
        manifest = build_manifest(root)
        skills = [s for s in manifest["skills"] if s["kind"] == "skill"]
        skill = skills[0] if which is None else [s for s in skills if s["dir"] == which][0]
        return lint_skill(manifest, skill, root), manifest


def fires(spec, rule, **kw):
    res, _ = run_lint(spec, **kw)
    return [f for f in res["findings"] if f["rule_id"] == rule]


def one(text, folder="csv-cleaner", extra=None):
    spec = {folder + "/SKILL.md": text}
    spec.update(extra or {})
    return spec


CASES = [
    ("L-FM-01", one("# no frontmatter\n"), one(skill_text())),
    ("L-NAME-01", one(skill_text(name=None)), one(skill_text())),
    ("L-NAME-02", one(skill_text(name="PDF--Tool"), "pdf-tool"),
     one(skill_text(name="pdf-tool"), "pdf-tool")),
    ("L-NAME-03", one(skill_text(name="pdf-tool"), "pdf"),
     one(skill_text(name="pdf-tool"), "pdf-tool")),
    ("L-NAME-04", one(skill_text(name="claude-helper"), "claude-helper"),
     one(skill_text(name="csv-helper"), "csv-helper")),
    ("L-NAME-05", one(skill_text(name="utils"), "utils"),
     one(skill_text(name="csv-cleaner"))),
    ("L-DESC-01", one(skill_text(desc='""')), one(skill_text())),
    ("L-DESC-02", one(skill_text(desc=desc_of(1025))), one(skill_text(desc=desc_of(1024)))),
    ("L-DESC-03",
     one(skill_text(desc=desc_of(1000), extra={"when_to_use": q("w" * 537)})),
     one(skill_text(desc=desc_of(1000), extra={"when_to_use": q("w" * 536)}))),
    ("L-DESC-04", one(skill_text(desc="Use <b>when</b> fixing CSV delimiters in exports")),
     one(skill_text())),
    ("L-DESC-05", one(skill_text(desc="I can help you clean CSV files. Use when fixing CSV.")),
     one(skill_text())),
    ("L-DESC-06", one(skill_text(desc="x" * 39)), one(skill_text(desc="x" * 40))),
    ("L-DESC-07", one(skill_text(desc="Cleans CSV exports and repairs broken delimiters.")),
     one(skill_text())),
    ("L-SIZE-01", one(skill_text(body=lines(501))), one(skill_text(body=lines(500)))),
    ("L-SIZE-02", one(skill_text(body="a" * 20004)), one(skill_text(body="a" * 20000))),
    ("L-REF-01", one(skill_text(body="[g](ref/missing.md)\n")),
     one(skill_text(body="[g](ref/g.md)\n"), extra={"csv-cleaner/ref/g.md": "g\n"})),
    ("L-REF-02", {"root/csv-cleaner/SKILL.md": skill_text(body="[o](../../outside.md)\n"),
                  "outside.md": "o\n"},
     {"root/csv-cleaner/SKILL.md": skill_text(body="[o](../inside.md)\n"),
      "root/inside.md": "o\n"}),
    ("L-REF-03", one(skill_text(body="[a](a.md)\n"),
                     extra={"csv-cleaner/a.md": "[b](b.md)\n", "csv-cleaner/b.md": "b\n"}),
     one(skill_text(body="[a](a.md) [b](b.md)\n"),
         extra={"csv-cleaner/a.md": "[b](b.md)\n", "csv-cleaner/b.md": "b\n"})),
    ("L-REF-04", one(skill_text(body="[a](a.md)\n"), extra={"csv-cleaner/a.md": lines(101)}),
     one(skill_text(body="[a](a.md)\n"),
         extra={"csv-cleaner/a.md": "- one\n- two\n3. three\n" + lines(98)})),
    ("L-PATH-01", one(skill_text(body="Run scripts\\helper.py\n")),
     one(skill_text(body="Run scripts/helper.py\n"))),
    ("L-TIME-01", one(skill_text(body="As of 2025 the API changed.\n")),
     one(skill_text(body="The v2 API is used.\n"))),
    ("L-UNIQ-01", {"a/SKILL.md": skill_text(name="dup"), "b/SKILL.md": skill_text(name="dup")},
     {"a/SKILL.md": skill_text(name="dup-a"), "b/SKILL.md": skill_text(name="dup-b")}),
    ("L-FILE-01", one(skill_text()) and {"csv-cleaner/skill.md": skill_text()},
     one(skill_text())),
    ("L-CC-01", one(skill_text(extra={"allowed-tools": "Bash"})),
     one(skill_text(extra={"allowed-tools": "Bash(git:*)"}))),
]


class LintRuleTests(unittest.TestCase):
    def test_each_rule_positive_and_negative(self):
        for rule, pos, neg in CASES:
            with self.subTest(rule=rule, variant="positive"):
                which = "a" if rule == "L-UNIQ-01" else None
                if rule == "L-REF-02":
                    which = "root/csv-cleaner"
                    res, _ = self._lint_root(pos, "root")
                else:
                    res, _ = run_lint(pos, which=which)
                self.assertTrue([f for f in res["findings"] if f["rule_id"] == rule],
                                "%s did not fire" % rule)
            with self.subTest(rule=rule, variant="negative"):
                if rule == "L-REF-02":
                    res, _ = self._lint_root(neg, "root")
                else:
                    res, _ = run_lint(neg, which=which)
                self.assertEqual([f for f in res["findings"] if f["rule_id"] == rule], [],
                                 "%s fired on negative" % rule)

    def _lint_root(self, spec, sub):
        with tempdir() as base:
            make_tree(base, spec)
            root = os.path.join(base, sub)
            manifest = build_manifest(root)
            skill = [s for s in manifest["skills"] if s["kind"] == "skill"][0]
            return lint_skill(manifest, skill, root), manifest

    def test_baseline_is_clean(self):
        res, _ = run_lint(one(skill_text()))
        self.assertEqual(res["findings"], [])
        self.assertEqual(res["meta"]["name"], "csv-cleaner")

    def test_cc02_is_skipped_and_rules_file(self):
        rules = {r["id"]: r for r in load_rules("lint-rules")["rules"]}
        self.assertEqual(rules["L-CC-02"]["stage"], "score")
        self.assertEqual(len(rules), 25)
        for r in rules.values():
            self.assertEqual(set(r), {"id", "severity", "category", "heuristic", "platform",
                                      "stage", "message", "fix", "why"})

    def test_finding_shape_and_lines(self):
        body = "one\nAs of 2025 x\n"
        res, _ = run_lint(one(skill_text(body=body)))
        f = [f for f in res["findings"] if f["rule_id"] == "L-TIME-01"][0]
        self.assertEqual(f["line"], 6)  # 4 frontmatter lines + closing => body starts line 5
        self.assertEqual(f["file"], "csv-cleaner/SKILL.md")
        self.assertTrue(f["heuristic"])
        res, _ = run_lint(one("# x\n"))
        self.assertEqual([f["line"] for f in res["findings"] if f["rule_id"] == "L-FM-01"], [1])

    def test_crlf_line_numbers(self):
        text = ("---\r\nname: csv-cleaner\r\ndescription: " + BASE_DESC + "\r\n---\r\n"
                "a\r\nb\r\nAs of 2025 the API changed.\r\n")
        res, _ = run_lint(one(text))
        f = [f for f in res["findings"] if f["rule_id"] == "L-TIME-01"]
        self.assertEqual([x["line"] for x in f], [7])

    def test_empty_body(self):
        res, _ = run_lint(one(fm()))
        ids = {f["rule_id"] for f in res["findings"]}
        self.assertNotIn("L-SIZE-01", ids)
        self.assertNotIn("L-SIZE-02", ids)

    def test_no_frontmatter_rules_when_unparseable(self):
        res, _ = run_lint(one("---\nname: [unclosed\n---\nbody\n"))
        ids = [f["rule_id"] for f in res["findings"]]
        self.assertEqual(ids.count("L-FM-01"), 1)
        self.assertNotIn("L-NAME-01", ids)
        self.assertEqual(res["meta"], {})

    def test_allowed_tools_forms(self):
        for val, expect in [("[Bash, Read]", True), ("Read Bash(* *)", True),
                            ("Bash(*)", True), ("Read Bash(git:*)", False)]:
            with self.subTest(val=val):
                self.assertEqual(
                    bool(fires(one(skill_text(extra={"allowed-tools": val})), "L-CC-01")), expect)

    def test_symlinked_skill_file_not_read(self):
        with tempdir() as root:
            make_tree(root, {"outside.md": skill_text(), "s/SKILL.md": skill_text()})
            manifest = build_manifest(root)
            skill = [s for s in manifest["skills"]][0]
            os.unlink(os.path.join(root, "s", "SKILL.md"))
            os.symlink(os.path.join(root, "outside.md"), os.path.join(root, "s", "SKILL.md"))
            res = lint_skill(manifest, skill, root)
            self.assertEqual(res["meta"], {})

    def test_cli_writes_valid_json(self):
        with tempdir() as base:
            root = os.path.join(base, "root")
            make_tree(root, one("# nope\n"))
            work = os.path.join(base, "work")
            manifest = build_manifest(root)
            write_json(os.path.join(work, "manifest.json"), manifest, "manifest")
            r = run_script("lint.py", "--work-dir", work)
            self.assertEqual(r.returncode, 0, r.stderr)
            key = manifest["skills"][0]["key"]
            with open(os.path.join(work, key, "lint.json")) as f:
                data = json.load(f)
            self.assertEqual(validate_against(data, "lint"), [])
            self.assertEqual(data["findings"][0]["rule_id"], "L-FM-01")


if __name__ == "__main__":
    unittest.main()
