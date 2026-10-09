import json
import os
import unittest

import _helpers  # noqa: F401  (sets sys.path)

from common import load_rules, read_json, validate_against, write_json
import assemble

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
            "inventory": {}, "exposure": "E0",
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

    def test_cli(self):
        with _helpers.tempdir() as tmp:
            work = make_run(tmp, [skill("a")])
            r = _helpers.run_path(SCRIPT, "--work-dir", work)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(os.path.isfile(os.path.join(tmp, "run", "results.json")))


if __name__ == "__main__":
    unittest.main()
