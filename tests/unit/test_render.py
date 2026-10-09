import copy
import json
import os
import re
import unittest

import _helpers

from common import read_json
import render
from render import render_report, summary

FIX = os.path.join(_helpers.FIXTURES_DIR, "render")
TEMPLATE_PATH = os.path.join(_helpers.ROOT_DIR, "support", "report-template.html")
CSP = ('<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
       'style-src \'unsafe-inline\'; img-src data:">')


def load_fixture():
    return copy.deepcopy(read_json(os.path.join(FIX, "results.json")))


def template():
    with open(TEMPLATE_PATH, encoding="utf-8") as f:
        return f.read()


def check_golden(case, name, actual):
    path = os.path.join(FIX, name)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        with open(path, "w", encoding="utf-8") as f:
            f.write(actual)
    with open(path, encoding="utf-8") as f:
        case.assertEqual(f.read(), actual)


class RenderTests(unittest.TestCase):
    def test_untrusted_values_escaped(self):
        r = load_fixture()
        r["skills"][0]["skill"] = "<img src=x onerror=alert(1)>"
        r["skills"][0]["recommendations"][0]["text"] = "</details><script>alert(1)</script>"
        r["target"] = '"><script>x</script>'
        out = render_report(r, template())
        self.assertNotIn("<img src=x", out)
        self.assertNotIn("<script", out.lower())
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", out)
        self.assertIn("&lt;/details&gt;&lt;script&gt;alert(1)&lt;/script&gt;", out)

    def test_placeholder_text_in_values_not_substituted(self):
        r = load_fixture()
        r["target"] = "{{header}}"
        out = render_report(r, template())
        self.assertIn("{{header}}", out)

    def test_csp_present(self):
        out = render_report(load_fixture(), template())
        self.assertIn(CSP, out)
        self.assertLess(out.index(CSP), out.index("<style>"))

    def test_template_placeholders_exact(self):
        found = re.findall(r"\{\{(\w+)\}\}", template())
        self.assertEqual(found, ["title", "header", "banners", "verdict_strip", "top_issues",
                                 "dashboard", "skill_sections", "patterns", "method_notes"])
        self.assertNotIn("<script", template().lower())

    def test_no_unfilled_placeholder(self):
        self.assertNotRegex(render_report(load_fixture(), template()), r"\{\{\w+\}\}")

    def test_top5_then_collapsed(self):
        out = render_report(load_fixture(), template())
        sec = out[out.index("<summary>alpha"):out.index("<summary>beta")]
        nested = sec.index("2 more recommendations")
        self.assertEqual(sec[:nested].count('class="rec '), 5)
        self.assertEqual(sec[nested:].split("</details>")[0].count('class="rec '), 2)

    def test_routing_marked_simulated(self):
        out = render_report(load_fixture(), template())
        self.assertIn("(RT-UNDER) <span class=\"badge simulated\">simulated</span>", out)
        self.assertIn("simulated", out)
        self.assertIn("Routing results", out)

    def test_routing_status_note(self):
        for status, reason in (("skipped", "--no-routing was given"),
                               ("partial", "only 2 of 3 routing calls returned valid answers")):
            r = load_fixture()
            r["routing"] = {"status": status, "reason": reason + " <b>",
                            "calls_valid": 2 if status == "partial" else 0, "calls_expected": 3}
            out = render_report(r, template())
            self.assertIn("Routing check %s: %s &lt;b&gt; (%d of 3 calls valid)."
                          % (status, reason, r["routing"]["calls_valid"]), out)
            self.assertNotIn(reason + " <b>", out)
        r = load_fixture()
        r["routing"]["status"] = "skipped"
        r["routing"]["reason"] = "single mode"
        for s in r["skills"]:
            s["recommendations"] = [x for x in s["recommendations"] if x.get("source") != "routing"]
        out = render_report(r, template())
        self.assertNotIn("No simulated routing findings.", out)
        self.assertIn("Routing check skipped: single mode", out)

    def test_routing_status_ran_no_note(self):
        out = render_report(load_fixture(), template())
        self.assertNotIn("Routing check ran", out)

    def test_gate_evidence_marks(self):
        out = render_report(load_fixture(), template())
        self.assertIn("&#10003;", out)
        self.assertIn("&#10007;", out)

    def test_section_order(self):
        out = render_report(load_fixture(), template())
        marks = ["<header>", "Verdicts", "Top issues across the run", "Dashboard", "<h2>Skills",
                 "Cross-skill patterns", "Method notes"]
        pos = [out.index(m) for m in marks]
        self.assertEqual(pos, sorted(pos))

    def test_snapshot(self):
        check_golden(self, "expected-report.html", render_report(load_fixture(), template()))

    def test_summary_format(self):
        check_golden(self, "expected-summary.txt", summary(load_fixture(), "/runs/r1"))

    def test_summary_strips_control_chars(self):
        r = load_fixture()
        r["top_issues"][0]["text"] = "bad\x1b[31mred"
        self.assertNotIn("\x1b", summary(r, "/runs/r1"))

    def test_cli_writes_report_and_summary(self):
        with _helpers.tempdir() as d:
            with open(os.path.join(d, "results.json"), "w") as f:
                json.dump(load_fixture(), f)
            p = _helpers.run_script("render.py", "--run-dir", d)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertTrue(os.path.isfile(os.path.join(d, "report.html")))
            p = _helpers.run_script("render.py", "--run-dir", d, "--summary")
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertIn("Skill review complete", p.stdout)

    def test_cli_refuses_symlinked_report(self):
        with _helpers.tempdir() as d:
            with open(os.path.join(d, "results.json"), "w") as f:
                json.dump(load_fixture(), f)
            target = os.path.join(d, "elsewhere")
            os.symlink(target, os.path.join(d, "report.html"))
            p = _helpers.run_script("render.py", "--run-dir", d)
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
