import itertools
import unittest

import _helpers  # noqa: F401
from applies_if import names
from common import read_json, scoring
import os
import risk
from risk import (compute_facts, evaluate, failed_review_tier, rationale, rule_items)

TH = scoring()["risk_thresholds"]
TABLE = read_json(os.path.join(_helpers.ROOT_DIR, "rules", "risk-table.json"))["rules"]
SHORT = {"SAF-CG3": "untrusted content treated as data"}


def mk(E="E0", hits=(), undisclosed=(), blockers=(), failed=(), safety=10, trigger=10, st=10):
    return {"exposure": E,
            "live_hits": [{"hit_id": h, "severity": s} for h, s in hits],
            "undisclosed": list(undisclosed), "safety_blockers": list(blockers),
            "safety_cg_failed": list(failed),
            "scores": {"trigger": trigger, "safety": safety, "scripts_tools": st}}


def tier(**kw):
    r = evaluate(compute_facts(mk(**kw), TH), TABLE)
    return r["tier"], r["id"]


class RiskTable(unittest.TestCase):
    def test_shape_and_order(self):
        self.assertEqual([r["id"] for r in TABLE],
                         ["C1", "C2", "C3", "H1", "H2", "H3", "M1", "M2", "M3", "L1"])
        self.assertEqual([r["tier"] for r in TABLE],
                         ["critical"] * 3 + ["high"] * 3 + ["medium"] * 3 + ["low"])
        facts = compute_facts(mk(), TH)
        for r in TABLE:
            self.assertLessEqual(names(r["when"]), set(facts))

    def test_totality(self):
        n = 0
        for e, und, saf, trg, st, sev, cg, blk in itertools.product(
                ["E0", "E1", "E2", "E3"], [False, True], [0, 3, 5, 7, 8, 10], [3, 5, 6, 10],
                [None, 3, 5, 8], [None, "minor", "major", "critical"], [False, True],
                [None, "SAF-B1", "SAF-B2", "SAF-B3"]):
            ctx = mk(E=e, hits=[("H1", sev)] if sev else [], undisclosed=["x"] if und else [],
                     blockers=[blk] if blk else [], failed=["SAF-CG3"] if cg else [],
                     safety=saf, trigger=trg, st=st)
            rule = evaluate(compute_facts(ctx, TH), TABLE)
            self.assertIn(rule["tier"], {"critical", "high", "medium", "low"})
            n += 1
        self.assertEqual(n, 4 * 2 * 6 * 4 * 4 * 4 * 2 * 4)

    def test_named_cases(self):
        self.assertEqual(tier(E="E0"), ("low", "L1"))
        self.assertEqual(tier(E="E2", failed=["SAF-CG3"], hits=[("H1", "minor")]), ("medium", "M1"))
        self.assertEqual(tier(E="E3", blockers=["SAF-B1"]), ("critical", "C3"))
        self.assertEqual(tier(E="E3", safety=8, trigger=9), ("low", "L1"))
        self.assertEqual(tier(E="E3", trigger=5), ("high", "H1"))
        self.assertEqual(tier(E="E1", undisclosed=["network_read"]), ("high", "H3"))
        self.assertEqual(tier(E="E0", hits=[("H1", "critical")]), ("critical", "C1"))
        self.assertEqual(tier(E="E0", hits=[("H1", "major")]), ("high", "H2"))
        self.assertEqual(tier(E="E3", undisclosed=["x"]), ("critical", "C2"))
        self.assertEqual(tier(E="E3", safety=7), ("medium", "M2"))
        self.assertEqual(tier(E="E1", st=5), ("medium", "M3"))
        self.assertEqual(tier(E="E0", st=3), ("low", "L1"))

    def test_none_scores_false(self):
        f = compute_facts(mk(E="E3", safety=None, trigger=None, st=None), TH)
        self.assertFalse(f["trigger_low"])
        self.assertFalse(f["safety_below_target"])
        self.assertFalse(f["scripts_tools_low"])

    def test_rationale_text(self):
        rule = [r for r in TABLE if r["id"] == "M1"][0]
        ctx = mk(E="E2", failed=["SAF-CG3"])
        self.assertEqual(
            rationale(rule, ctx, SHORT),
            "Medium — rule M1: exposure E2, and SAF-CG3 (untrusted content treated as data) failed.")

    def test_every_rule_renders(self):
        ctx = mk(E="E3", hits=[("H1", "critical"), ("H2", "major")], undisclosed=["network_read"],
                 blockers=["SAF-B1"], failed=["SAF-CG3"], trigger=4)
        for r in TABLE:
            text = rationale(r, ctx, SHORT)
            self.assertTrue(text.startswith(r["tier"].capitalize() + " — rule " + r["id"] + ": "))

    def test_failed_review(self):
        self.assertEqual(failed_review_tier([{"hit_id": "H1", "severity": "critical"}], TABLE),
                         ("critical", "C1"))
        self.assertEqual(failed_review_tier([{"hit_id": "H1", "severity": "major"}], TABLE),
                         ("high", "H2"))
        self.assertEqual(failed_review_tier([{"hit_id": "H1", "severity": "minor"}], TABLE),
                         ("unknown", None))
        self.assertEqual(failed_review_tier([], TABLE), ("unknown", None))

    def test_rule_items(self):
        ctx = mk(hits=[("H1", "critical"), ("H2", "major")], undisclosed=["net"],
                 blockers=["SAF-B1"], failed=["SAF-CG3"])
        self.assertEqual(rule_items("C1", ctx), {("hit", "H1")})
        self.assertEqual(rule_items("H2", ctx), {("hit", "H2")})
        self.assertEqual(rule_items("C2", ctx), {("capability", "net")})
        self.assertEqual(rule_items("H3", ctx), {("capability", "net")})
        self.assertEqual(rule_items("C3", ctx), {("gate", "SAF-B1")})
        for rid in ("H1", "M1", "M2"):
            self.assertEqual(rule_items(rid, ctx), {("gate", "SAF-CG3")})
        ctx["failed_gates"] = ["SAF-CG3", "SCT-1", "SCT-2"]
        self.assertEqual(rule_items("M3", ctx), {("gate", "SCT-1"), ("gate", "SCT-2")})
        self.assertEqual(rule_items("L1", ctx), set())


if __name__ == "__main__":
    unittest.main()
