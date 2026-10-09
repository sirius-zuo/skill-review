"""SKILL.md orchestrator, fallback coverage and v1 removal (Python 3.7 stdlib only)."""
import os
import re
import unittest

import _helpers  # noqa: F401  (sets sys.path)
from frontmatter import parse_skill_file

ROOT = _helpers.ROOT_DIR
SPEC = os.path.join("docs", "superpowers", "specs", "2026-10-08-skill-review-v2-design.md")

# Helper modules imported by the CLIs; they have no fallback section of their own.
NOT_CLI = {"common", "jsonschema_lite", "frontmatter", "applies_if", "judgment", "risk"}

REMOVED = ["categories", "scenarios", os.path.join("support", "discover.md"),
           os.path.join("support", "static-review.md"),
           os.path.join("support", "dynamic-review.md"), os.path.join("support", "report.md"),
           os.path.join("tests", "results.md"), os.path.join("tests", "fixtures", "skill-review")]

BODY_SECTIONS = ["Purpose and non-goals", "Forbidden actions", "Arguments", "Preflight",
                 "Phases", "Confirmation", "Sub-agent dispatch", "Retry", "Errors",
                 "Cleanup", "Delivery"]

REF_RE = re.compile(r"\b((?:scripts|support|rules|rubric|kit-templates)/[A-Za-z0-9_./*-]+"
                    r"\.(?:py|md|json|html|txt))")


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as f:
        return f.read()


def _spec_value(field):
    m = re.search(r"`%s:`? \"([^\"]+)\"" % re.escape(field), read(SPEC))
    if m is None:
        raise AssertionError("spec has no %s string" % field)
    return m.group(1)


def _sections(text):
    """Map '## heading' -> section text (up to the next '## ')."""
    out = {}
    parts = re.split(r"^## ", text, flags=re.M)
    for part in parts[1:]:
        head, _, body = part.partition("\n")
        out[head.strip()] = body
    return out


def script_names():
    return sorted(f[:-3] for f in os.listdir(os.path.join(ROOT, "scripts"))
                  if f.endswith(".py") and f[:-3] not in NOT_CLI)


class SkillMdTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_skill_file(read("SKILL.md"))
        self.assertIsNone(self.parsed.error)

    def test_frontmatter(self):
        p = self.parsed
        self.assertEqual(p.meta["name"], "skill-review")
        self.assertEqual(p.meta["description"], _spec_value("description"))
        self.assertEqual(p.meta["argument-hint"], _spec_value("argument-hint"))
        self.assertLessEqual(len(p.body.splitlines()), 200)

    def test_body_sections_in_order(self):
        heads = [h for h in re.findall(r"^## (.+)$", self.parsed.body, flags=re.M)]
        pos = []
        for want in BODY_SECTIONS:
            idx = [i for i, h in enumerate(heads) if h.startswith(want)]
            self.assertTrue(idx, "missing section %r in %r" % (want, heads))
            pos.append(idx[0])
        self.assertEqual(pos, sorted(pos), heads)

    def test_preflight_and_commands(self):
        body = self.parsed.body
        self.assertIn("python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 7) else 1)'",
                      body)
        self.assertIn("support/fallback.md", body)
        invoked = set(re.findall(r"python3 <skill_dir>/scripts/([a-z_]+)\.py", body))
        self.assertEqual(invoked, set(script_names()))
        self.assertNotRegex(body, r"python3 (?!<skill_dir>/scripts/|-c )\S+\.py")
        for needle in ["SELF_REVIEW", "OUT_INSIDE_ROOT", "estimate.ask", "retryable",
                       "batch_size", "rules/scoring.json", "support/judge.md",
                       "support/kit.md", "support/routing.md", "discover.py cleanup",
                       "--summary", "llm-fallback"]:
            self.assertIn(needle, body, needle)

    def test_mark_failed_uses_fixed_tokens(self):
        body = self.parsed.body
        used = re.findall(r"--mark-failed[ =](\S+)", body)
        self.assertTrue(used)
        allowed = {"judge_error", "judgment_invalid", "review_failed"}
        for tok in used:
            self.assertIn(tok.strip("`'\""), allowed, tok)
        self.assertNotIn("<one-line reason>", body)

    def test_routing_status_relied_on(self):
        body = self.parsed.body
        self.assertIn('"path"', body)
        self.assertNotIn("delivery message", body)
        for needle in ("`ran`", "`partial`", "`skipped`"):
            self.assertIn(needle, body)

    def test_error_table_covers_spec(self):
        body = self.parsed.body
        for needle in ["git is required to review a GitHub URL; clone it yourself and pass "
                       "the local path.", "No SKILL.md found under", "review_failed",
                       "kit_failed", "not writable", "Every judge fails"]:
            self.assertIn(needle, body, needle)

    def test_referenced_files_exist(self):
        text = read("SKILL.md")
        refs = set(REF_RE.findall(text))
        self.assertTrue(refs)
        for ref in refs:
            if "*" in ref:
                continue
            self.assertTrue(os.path.isfile(os.path.join(ROOT, ref)), ref)

    def test_old_files_removed(self):
        for rel in REMOVED:
            self.assertFalse(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_fallback_covers_every_script(self):
        doc = read("support", "fallback.md")
        sections = _sections(doc)
        for name in script_names():
            head = "%s.py" % name
            self.assertIn(head, sections, head)
            body = sections[head]
            self.assertRegex(body, r"rules/(schemas/)?[a-z-]+(\.schema)?\.json", head)
            self.assertIn("llm-fallback", body, head)

    def test_fallback_restates_no_rule_values(self):
        doc = read("support", "fallback.md")
        self.assertIsNone(re.search(r"(?<![\w.-])\d{2,}(?![\w.-])", doc),
                          "fallback.md must read numbers from rules/*.json, not restate them")


if __name__ == "__main__":
    unittest.main()
