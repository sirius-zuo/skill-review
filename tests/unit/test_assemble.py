import json
import os
import unittest

import _helpers  # noqa: F401  (sets sys.path)

from common import ValidationFailed, load_rules, read_json, validate_against, write_json
import assemble
from scan import FLAGS

SCRIPT = os.path.join(_helpers.SCRIPTS_DIR, "assemble.py")


def rec(rank, rid, cat="clarity", source="gate"):
    return {"priority_rank": rank, "source": source, "id": rid, "category": cat,
            "text": "fix " + rid, "why": "", "evidence": []}


def skill(name, tier="low", quality=7.0, band="adequate", level="unverified", recs=None,
          failed=(), engine="script"):
    gates = {}
    for g in failed:
        gates[g] = {"kind": "quality", "answered_by": "judge", "answer": "no", "evidence": []}
    return {"schema_version": 1, "engine": engine, "skill": name, "key": "01-" + name,
            "kind": "skill", "review_failed": tier == "unknown", "review_errors": [],
            "inventory": dict((f, False) for f in FLAGS), "exposure": "E0",
            "categories": {"clarity": {"score": 5, "applicable": True, "blocker_capped": False,
                                       "gates": gates}},
            "quality_overall": quality, "quality_band": band, "risk_tier": tier,
            "risk_rule": "L1", "risk_rationale": "r", "evidence": {"level": level, "results": []},
            "scan_hits": [], "lint_findings": [], "undisclosed_capabilities": [],
            "recommendations": recs or [], "judge_reliability": {
                "quote_failure_rate": None, "trial_agreement": None, "trials": 0},
            "ignored_answers": [], "insufficient_evidence": []}


def make_run(tmp, skills, extra=None):
    """skills: list of result dicts. Writes manifest, run, results, kit-status."""
    run_dir = os.path.join(tmp, "run")
    work = os.path.join(run_dir, "work")
    kit_dir = os.path.join(run_dir, "kit")
    entries = []
    for s in skills:
        key = s["key"]
        entries.append({"key": key, "name": s["skill"], "dir": s["skill"], "kind": "skill",
                        "skill_file": s["skill"] + "/SKILL.md"})
        write_json(os.path.join(work, key, "result.json"), s)
        write_json(os.path.join(work, key, "kit-status.json"),
                   {"schema_version": 1, "engine": "script", "skill": s["skill"],
                    "status": "ok", "errors": []})
    write_json(os.path.join(work, "manifest.json"),
               {"root": tmp, "self_review": False, "skills": entries})
    write_json(os.path.join(work, "run.json"), {
        "schema_version": 1, "engine": "script", "run_dir": run_dir, "work_dir": work,
        "kit_dir": kit_dir, "target": "/t", "created": "2026-10-08T00:00:00Z",
        "args": {"mode": "single", "trials": 1, "no_kit": False, "no_routing": False,
                 "kit_format": None, "keep_work": False}})
    write_json(os.path.join(work, "repo-ingest.json"),
               {"schema_version": 1, "engine": "script", "results": [], "invalid": []})
    return work


class AssembleTests(unittest.TestCase):
    def test_rollup_unknown(self):
        r = assemble.rollup([skill("a", "unknown", None, None), skill("b", "medium", 8.0, "strong"),
                             skill("c", "low", 5.0, "weak", level="reported")])
        self.assertEqual(r["worst_tier"], "medium")
        self.assertEqual(r["tier_counts"], {"critical": 0, "high": 0, "medium": 1, "low": 1,
                                            "unknown": 1})
        self.assertEqual(r["quality_mean"], 6.5)
        self.assertEqual(r["band_counts"], {"strong": 1, "adequate": 0, "weak": 1})
        self.assertEqual(r["evidence_counts"], {"reported": 1, "unverified": 2})

    def test_rollup_all_unknown(self):
        r = assemble.rollup([skill("a", "unknown", None, None)])
        self.assertEqual(r["worst_tier"], "unknown")
        self.assertIsNone(r["quality_mean"])

    def test_tier_order_matches_risk_table(self):
        tiers = set(rule["tier"] for rule in load_rules("risk-table")["rules"])
        self.assertTrue(tiers <= set(assemble.TIER_ORDER))

    def test_gate_pattern(self):
        a = skill("a", failed=["CLR-Q1"])
        b = skill("b", failed=["CLR-Q1", "CLR-Q2"])
        c = skill("c")
        p = assemble.patterns([a, b, c], {})
        self.assertEqual(p, [{"kind": "gate", "id": "CLR-Q1", "skills": ["a", "b"]}])

    def test_top_issues_order(self):
        s1 = skill("s1", "critical", recs=[rec(1, "A1"), rec(3, "A2")])
        s2 = skill("s2", "low", recs=[rec(1, "B1")])
        s3 = skill("s3", "critical", recs=[rec(2, "C1"), rec(4, "C2")])
        top = assemble.top_issues([s1, s2, s3], 10)
        self.assertEqual([t["id"] for t in top], ["A1", "C1", "A2", "C2", "B1"])
        self.assertEqual([t["skill"] for t in top], ["s1", "s3", "s1", "s3", "s2"])
        self.assertEqual(len(assemble.top_issues([s1, s2, s3], 2)), 2)

    def test_fallback_banner(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            write_json(os.path.join(work, "01-a", "lint.json"),
                       {"schema_version": 1, "engine": "llm-fallback", "findings": []})
            out = assemble.assemble(work)
            self.assertEqual(out["banners"], ["lint"])

    def test_no_banner(self):
        with _helpers.tempdir() as tmp:
            self.assertEqual(assemble.assemble(make_run(tmp, [skill("a")]))["banners"], [])

    def _routing(self, work, winner):
        # skill a has 2 positive prompts; both won by `winner`
        pm = {"P1": {"skill": "a", "case_id": "T0", "should_trigger": True},
              "P2": {"skill": "a", "case_id": "T1", "should_trigger": True}}
        write_json(os.path.join(work, "routing-map.json"),
                   {"schema_version": 1, "engine": "script",
                    "skills": ["a", "b", "distractor-x"], "prompts": pm})
        write_json(os.path.join(work, "routing-1.json"),
                   {"schema_version": 1, "engine": "script", "call": 1,
                    "choices": {"P1": winner, "P2": winner}})

    def test_routing_finding_merged(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a", recs=[rec(5, "Q1"), rec(6, "LZ", "scope", "lint")]),
                                  skill("b")])
            self._routing(work, "b")
            out = assemble.assemble(work)
            recs = out["skills"][0]["recommendations"]
            ids = [r["id"] for r in recs]
            self.assertIn("RT-UNDER", ids)
            self.assertIn("RT-COLLIDE", ids)
            under = [r for r in recs if r["id"] == "RT-UNDER"][0]
            self.assertEqual((under["source"], under["priority_rank"], under["category"]),
                             ("routing", 4, "trigger"))
            self.assertEqual(ids, ["RT-COLLIDE", "RT-UNDER", "Q1", "LZ"])
            self.assertIn({"kind": "routing", "skills": ["a", "b"]}, out["patterns"])
            self.assertEqual(out["skills"][1]["recommendations"], [])

    def test_distractor_winner_no_collision(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a"), skill("b")])
            self._routing(work, "distractor-x")
            out = assemble.assemble(work)
            ids = [r["id"] for r in out["skills"][0]["recommendations"]]
            self.assertNotIn("RT-COLLIDE", ids)
            self.assertIn("RT-UNDER", ids)
            self.assertEqual([p for p in out["patterns"] if p["kind"] == "routing"], [])

    def _set_args(self, work, **kw):
        run = read_json(os.path.join(work, "run.json"))
        run["args"].update(kw)
        write_json(os.path.join(work, "run.json"), run)

    def _routing_calls(self, work, n_valid, n_invalid=0):
        self._routing(work, "a")
        for n in range(2, n_valid + 1):
            write_json(os.path.join(work, "routing-%d.json" % n),
                       {"schema_version": 1, "engine": "script", "call": n,
                        "choices": {"P1": "a", "P2": "a"}})
        for n in range(n_valid + 1, n_valid + 1 + n_invalid):
            write_json(os.path.join(work, "routing-%d.json" % n), {"bad": True})

    def test_routing_status_ran(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            self._set_args(work, mode="parallel")
            self._routing_calls(work, 3)
            out = assemble.assemble(work)
            self.assertEqual(out["routing"], {"status": "ran", "reason": None,
                                              "calls_valid": 3, "calls_expected": 3})
            self.assertEqual(validate_against(out, "results"), [])

    def test_routing_status_partial(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            self._set_args(work, mode="parallel")
            self._routing_calls(work, 2, n_invalid=1)
            out = assemble.assemble(work)
            self.assertEqual(out["routing"]["status"], "partial")
            self.assertEqual((out["routing"]["calls_valid"], out["routing"]["calls_expected"]),
                             (2, 3))
            self.assertIn("2 of 3", out["routing"]["reason"])
            self.assertEqual(validate_against(out, "results"), [])

    def test_routing_status_skipped(self):
        cases = [({"mode": "parallel", "no_routing": True}, "--no-routing"),
                 ({"mode": "single"}, "single mode"),
                 ({"mode": "parallel", "no_kit": True}, "--no-kit"),
                 ({"mode": "parallel"}, "no routing answers were produced")]
        for args, needle in cases:
            with _helpers.tempdir() as tmp:
                work = make_run(tmp, [skill("a")])
                self._set_args(work, **args)
                out = assemble.assemble(work)
                self.assertEqual(out["routing"]["status"], "skipped", args)
                self.assertIn(needle, out["routing"]["reason"], args)
                self.assertEqual(out["routing"]["calls_valid"], 0)
                self.assertEqual(validate_against(out, "results"), [])

    def test_routing_status_skipped_all_invalid(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            self._set_args(work, mode="parallel")
            self._routing(work, "a")
            write_json(os.path.join(work, "routing-1.json"), {"bad": True})
            out = assemble.assemble(work)
            self.assertEqual(out["routing"]["status"], "skipped")
            self.assertIn("valid", out["routing"]["reason"])

    def test_schema_accepts_llm_fallback_engine(self):
        with _helpers.tempdir() as tmp:
            out = assemble.assemble(make_run(tmp, [skill("a")]))
            out["engine"] = "llm-fallback"
            self.assertEqual(validate_against(out, "results"), [])
            out["engine"] = "other"
            self.assertNotEqual(validate_against(out, "results"), [])

    def test_kits_and_schema(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a", recs=[rec(1, "A1")])])
            out = assemble.assemble(work)
            self.assertEqual(out["kits"], {"01-a": {"status": "ok", "path": "kit/01-a"}})
            self.assertEqual(validate_against(out, "results"), [])
            saved = read_json(os.path.join(tmp, "run", "results.json"))
            self.assertEqual(saved, out)
            self.assertEqual(out["target"], "/t")
            self.assertEqual(out["engine"], "script")
            self.assertEqual(out["rollup"]["worst_tier"], "low")

    def test_quality_mean_rounds_half_up(self):
        # 7.25 is exact in binary, so round(7.25, 1) gives 7.2 (half-even); spec wants 7.3.
        r = assemble.rollup([skill("a", quality=7.0), skill("b", quality=7.5)])
        self.assertEqual(r["quality_mean"], 7.3)
        r = assemble.rollup([skill("a", quality=6.0), skill("b", quality=6.5)])
        self.assertEqual(r["quality_mean"], 6.3)

    def test_quality_mean_exact_half_up_regressions(self):
        # float sums turn exact ties into x.4999...; the mean is computed in integer tenths
        # (3.3, 3.3, 3.4) has mean 3.333..., not a tie, so exact half-up gives 3.3.
        for qs, want in (((1.2, 1.9), 1.6), ((1.1, 8.2), 4.7), ((3.3, 3.4), 3.4),
                         ((3.3, 3.3, 3.4), 3.3)):
            r = assemble.rollup([skill("s%d" % i, quality=q) for i, q in enumerate(qs)])
            self.assertEqual(r["quality_mean"], want, qs)

    def test_malformed_result_is_an_error_not_a_crash(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a"), skill("b")])
            write_json(os.path.join(work, "01-b", "result.json"), {"skill": "b"})
            with self.assertRaises(ValidationFailed) as cm:
                assemble.assemble(work)
            self.assertTrue(any("01-b" in e and "result.json" in e for e in cm.exception.errors),
                            cm.exception.errors)
            self.assertFalse(any("01-a" in e for e in cm.exception.errors))
            r = _helpers.run_path(SCRIPT, "--work-dir", work)
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertNotIn("KeyError", r.stderr)
            self.assertIn("01-b", json.loads(r.stdout)["errors"][0])

    def test_unparsable_result_is_an_error(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            with open(os.path.join(work, "01-a", "result.json"), "w") as f:
                f.write("[1, 2")
            with self.assertRaises(ValidationFailed):
                assemble.assemble(work)

    def test_kit_failed_status_passes_through(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            write_json(os.path.join(work, "01-a", "kit-status.json"),
                       {"schema_version": 1, "engine": "script", "skill": "a",
                        "status": "kit_failed", "errors": ["x"]}, "kit-status")
            out = assemble.assemble(work)
            self.assertEqual(out["kits"], {"01-a": {"status": "kit_failed", "path": None}})
            self.assertEqual(validate_against(out, "results"), [])

    def test_cli(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            r = _helpers.run_path(SCRIPT, "--work-dir", work)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "run", "results.json")))


if __name__ == "__main__":
    unittest.main()
