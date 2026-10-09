import copy
import os
import unittest

import _helpers
from _helpers import tempdir
from common import ValidationFailed, skill_work_dir, validate_against, write_json
from judgment import (completeness_errors, load_judgments, merge_trials, trial_agreement,
                      verify_judgment, verify_quote)

IDX = {"SKILL.md": ["# T", "Always ask before deploying to production.", "x", "y", "z",
                    "Run   it  now"]}


def ev(line, quote, file="SKILL.md"):
    return {"file": file, "line": line, "quote": quote}


def J(trial=1, gates=None, inventory=None, triage=None, undisclosed=None, dismissals=None):
    return {"schema_version": 1, "engine": "script", "skill": "s", "trial": trial,
            "inventory": inventory or {}, "scan_triage": triage or [],
            "lint_dismissals": dismissals or [],
            "undisclosed_capabilities": undisclosed or [], "gates": gates or {}}


def G(answer, evidence=None, reason="r", fix=None):
    d = {"answer": answer, "evidence": evidence or [], "reason": reason}
    if fix is not None:
        d["fix"] = fix
    return d


def T(hid, label, code=None):
    return {"hit_id": hid, "label": label, "reason_code": code, "reason": "r"}


GOOD = ev(2, "ask before deploying")


class QuoteTests(unittest.TestCase):
    def test_quote_exact_window_short_fabricated(self):
        self.assertTrue(verify_quote(IDX, ev(4, "ask before deploying"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(6, "ask before deploying"), 12, 2))
        self.assertTrue(verify_quote(IDX, ev(6, "Run it now"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(2, "deploying"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(2, "never deploy anything"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(2, "ask before deploying", "OTHER.md"), 12, 2))

    def test_edges(self):
        self.assertFalse(verify_quote(IDX, ev(99, "ask before deploying"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(2, ""), 12, 2))
        self.assertTrue(verify_quote(IDX, ev(1, "# T"), 12, 2))
        self.assertFalse(verify_quote(IDX, ev(-5, "# T"), 12, 2))


class VerifyTests(unittest.TestCase):
    def test_yes_downgraded(self):
        j = J(gates={"SAF-CG1": G("yes", [ev(2, "never deploy anything")])})
        out, total, failed = verify_judgment(j, IDX)
        self.assertEqual((total, failed), (1, 1))
        self.assertEqual(out["gates"]["SAF-CG1"]["answer"], "insufficient_evidence")
        self.assertEqual(j["gates"]["SAF-CG1"]["answer"], "yes")

    def test_yes_kept_with_partial(self):
        j = J(gates={"SAF-CG1": G("yes", [GOOD, ev(2, "never deploy anything")])})
        out, total, failed = verify_judgment(j, IDX)
        self.assertEqual((total, failed), (2, 1))
        self.assertEqual(out["gates"]["SAF-CG1"]["answer"], "yes")
        self.assertEqual(out["gates"]["SAF-CG1"]["evidence"], [GOOD])

    def test_inventory_and_undisclosed(self):
        bad = ev(2, "never deploy anything")
        j = J(inventory={"file_write": {"value": True, "evidence": [bad], "reason": "r"},
                         "shell": {"value": True, "evidence": [GOOD], "reason": "r"}},
              undisclosed=[{"capability": "a", "claim": GOOD, "evidence": [bad]},
                           {"capability": "b", "claim": GOOD, "evidence": [GOOD]}])
        out, total, failed = verify_judgment(j, IDX)
        self.assertFalse(out["inventory"]["file_write"]["value"])
        self.assertTrue(out["inventory"]["shell"]["value"])
        self.assertEqual([u["capability"] for u in out["undisclosed_capabilities"]], ["b"])
        self.assertEqual((total, failed), (4, 2))


class MergeTests(unittest.TestCase):
    def test_merge_ties(self):
        a = J(1, gates={"G": G("yes", [GOOD])}, inventory={"f": {"value": True, "evidence": [GOOD]}},
              triage=[T("H1", "confirmed")])
        b = J(2, gates={"G": G("no", fix="x")}, inventory={"f": {"value": False, "evidence": []}},
              triage=[T("H1", "benign", "not_executed")])
        m = merge_trials([a, b])
        self.assertEqual(m["gates"]["G"]["answer"], "no")
        self.assertTrue(m["inventory"]["f"]["value"])
        self.assertEqual(m["scan_triage"][0]["label"], "unclear")
        self.assertIsNone(m["scan_triage"][0]["reason_code"])
        self.assertEqual(validate_against(m, "judgment"), [])

    def test_majorities(self):
        js = [J(i + 1, gates={"G": G(a, [GOOD] if a == "yes" else [])},
                triage=[T("H1", lab, "purpose_consistent" if lab == "benign" else None)],
                undisclosed=([{"capability": "c", "claim": GOOD, "evidence": [GOOD]}] if i < 2 else []),
                dismissals=([{"rule_id": "L1", "reason": "r"}] if i == 0 else []))
              for i, (a, lab) in enumerate([("yes", "benign"), ("yes", "benign"), ("no", "confirmed")])]
        m = merge_trials(js)
        self.assertEqual(m["gates"]["G"]["answer"], "yes")
        self.assertEqual(m["scan_triage"][0]["label"], "benign")
        self.assertEqual(m["scan_triage"][0]["reason_code"], "purpose_consistent")
        self.assertEqual(len(m["undisclosed_capabilities"]), 1)
        self.assertEqual(m["lint_dismissals"], [])
        # exactly half does not keep an undisclosed capability
        m2 = merge_trials(js[:1] + [J(2)])
        self.assertEqual(m2["undisclosed_capabilities"], [])

    def test_single_trial_passthrough(self):
        a = J(1, gates={"G": G("no", fix="f")})
        self.assertEqual(merge_trials([a])["gates"], a["gates"])

    def test_three_way_gate_split(self):
        js = [J(1, gates={"G": G("yes", [GOOD])}), J(2, gates={"G": G("no")}),
              J(3, gates={"G": G("insufficient_evidence")})]
        self.assertEqual(merge_trials(js)["gates"]["G"]["answer"], "no")


class AgreementTests(unittest.TestCase):
    def test_trial_agreement(self):
        gs = ["A", "B", "C", "D"]
        mk = lambda n, d: J(n, gates={g: G(d.get(g, "no")) for g in gs})
        js = [mk(1, {"D": "yes"}), mk(2, {"D": "no"}), mk(3, {"D": "no"})]
        self.assertEqual(trial_agreement(js, gs), 0.75)
        self.assertIsNone(trial_agreement(js[:1], gs))
        self.assertIsNone(trial_agreement([], gs))


class CompletenessTests(unittest.TestCase):
    def test_completeness(self):
        j = J(gates={"SAF-CG1": G("no"), "TRG-B1": G("yes", [GOOD]), "TRG-CG1": G("no", fix="f")},
              triage=[T("H002", "benign", None)])
        errs = completeness_errors(j, {"SAF-CG1", "SAF-CG2", "TRG-B1", "TRG-CG1"},
                                   {"H002", "H003"}, {"file_write"})
        self.assertIn("gate SAF-CG2 not answered", errs)
        self.assertIn("hit H003 not triaged", errs)
        self.assertIn("flag file_write not resolved", errs)
        self.assertIn("gate SAF-CG1 answered no without fix", errs)
        self.assertIn("gate TRG-B1 answered yes (blocker) without fix", errs)
        self.assertIn("hit H002 is benign without reason_code", errs)
        self.assertNotIn("gate TRG-CG1 answered no without fix", errs)

    def test_clean(self):
        j = J(gates={"TRG-CG1": G("no", fix="f")}, inventory={"x": {"value": False}})
        self.assertEqual(completeness_errors(j, {"TRG-CG1"}, set(), set()), [])


class SchemaTests(unittest.TestCase):
    def test_schema_rejects_benign_without_code(self):
        self.assertEqual(validate_against(J(triage=[T("H1", "benign", "not_executed")]), "judgment"), [])
        self.assertTrue(validate_against(J(triage=[T("H1", "benign", None)]), "judgment"))
        self.assertTrue(validate_against(J(triage=[T("H1", "benign", "other")]), "judgment"))
        self.assertEqual(validate_against(J(triage=[T("H1", "unclear")]), "judgment"), [])

    def test_schema_gates(self):
        self.assertTrue(validate_against(J(gates={"G": G("yes")}), "judgment"))
        self.assertTrue(validate_against(J(gates={"G": G("no", reason="")}), "judgment"))
        self.assertTrue(validate_against(J(gates={"G": G("maybe", [GOOD])}), "judgment"))
        self.assertEqual(validate_against(J(gates={"G": G("insufficient_evidence")}), "judgment"), [])
        self.assertTrue(validate_against(
            J(inventory={"f": {"value": True, "evidence": []}}), "judgment"))
        self.assertEqual(validate_against(
            J(dismissals=[{"rule_id": "L-DESC-05", "reason": "r"}]), "judgment"), [])


class LoadTests(unittest.TestCase):
    def test_load_sorted_and_invalid(self):
        with tempdir() as d:
            sd = skill_work_dir(d, "k")
            write_json(os.path.join(sd, "judgment-10.json"), J(10))
            write_json(os.path.join(sd, "judgment-2.json"), J(2))
            self.assertEqual([x["trial"] for x in load_judgments(d, "k")], [2, 10])
            with open(os.path.join(sd, "judgment-3.json"), "w") as f:
                f.write('{"schema_version": 1}')
            with self.assertRaises(ValidationFailed):
                load_judgments(d, "k")
            os.unlink(os.path.join(sd, "judgment-3.json"))
            with open(os.path.join(sd, "judgment-4.json"), "w") as f:
                f.write("not json")
            with self.assertRaises(ValidationFailed):
                load_judgments(d, "k")

    def test_single_judgment_json(self):
        with tempdir() as d:
            write_json(os.path.join(skill_work_dir(d, "k"), "judgment.json"), J(1))
            self.assertEqual(len(load_judgments(d, "k")), 1)

    def test_none(self):
        with tempdir() as d:
            with self.assertRaises(ValidationFailed):
                load_judgments(d, "k")


if __name__ == "__main__":
    unittest.main()
