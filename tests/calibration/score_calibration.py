#!/usr/bin/env python3
"""Score calibration runs against corpus expectations, labels and mutation expectations.

Usage: score_calibration.py --runs DIR [DIR ...] --labels DIR --corpus corpus.json
                            --mutations mutations.json
  Each run DIR holds a results.json. A result is matched to a corpus skill name or a
  mutation id by (1) DIR/run-map.json {"<result skill name>": "<corpus name | MU-id>"},
  (2) the result's skill name, (3) for a single-skill run, the last two path parts of the
  results.json "target" (".../MU-02/good-skill" -> MU-02, "good-skill").
Prints each metric against the spec section 2 targets S1-S5 with PASS/FAIL (NO DATA when
there is nothing to measure). Exit status is 1 if any target FAILs. Python 3.7 stdlib only.
"""
import argparse
import json
import os
import sys

S3_TRIAL_AGREEMENT = 0.85
S4_KAPPA = 0.6
S5_QUOTE_FAILURE = 0.05
S2_DETERMINISTIC = 1.0
S2_JUDGE = 0.9


def percent_agreement(a, b):
    if len(a) != len(b):
        raise ValueError("label lists differ in length")
    if not a:
        raise ValueError("no labels")
    return sum(1 for x, y in zip(a, b) if x == y) / float(len(a))


def cohen_kappa(a, b):
    po = percent_agreement(a, b)
    n = float(len(a))
    pe = 0.0
    for k in set(a) | set(b):
        pe += (a.count(k) / n) * (b.count(k) / n)
    if pe >= 1.0:
        return 1.0 if po >= 1.0 else 0.0
    return (po - pe) / (1.0 - pe)


def _load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _ident(res, skill, known, run_map, single):
    name = skill.get("skill")
    if name in run_map:
        return run_map[name]
    if name in known:
        return name
    if single:
        parts = [p for p in res.get("target", "").replace("\\", "/").split("/") if p]
        for cand in (parts[-2:-1] + parts[-1:]):
            if cand in known:
                return cand
    return None


def load_runs(run_dirs, known):
    """Return {id: [skill result, ...]} (one entry per run containing it)."""
    out = {}
    for d in run_dirs:
        path = os.path.join(d, "results.json")
        if not os.path.isfile(path):
            print("warning: no results.json in %s" % d, file=sys.stderr)
            continue
        res = _load(path)
        mp_path = os.path.join(d, "run-map.json")
        run_map = _load(mp_path) if os.path.isfile(mp_path) else {}
        skills = res.get("skills", [])
        for s in skills:
            ident = _ident(res, s, known, run_map, len(skills) == 1)
            if ident:
                out.setdefault(ident, []).append(s)
    return out


def _tier(s):
    return (s.get("risk_tier") or "").lower()


def _gate_answers(s):
    out = {}
    for cat in (s.get("categories") or {}).values():
        for gid, g in (cat.get("gates") or {}).items():
            out[gid] = g.get("answer")
    return out


def _result(name, value, target, ok, fmt="%.2f"):
    if value is None:
        return (name, "NO DATA", target, "NO DATA")
    return (name, fmt % value if not isinstance(value, str) else value, target,
            "PASS" if ok else "FAIL")


def corpus_matches(corpus, runs):
    rows, s1_ok, s1_seen = [], True, False
    for e in corpus["skills"]:
        got = runs.get(e["name"])
        if not got:
            rows.append((e["name"], "no run", "", ""))
            continue
        tiers = set(_tier(s) for s in got)
        tier_ok = all(t in [x.lower() for x in e["expected_tiers"]] for t in tiers)
        bands = set(s.get("quality_band") for s in got)
        band_ok = (e["expected_band"] is None
                   or all((b or "").lower() == e["expected_band"].lower() for b in bands))
        rows.append((e["name"], "/".join(sorted(tiers)), "/".join(e["expected_tiers"]),
                     "tier ok" if tier_ok else "TIER MISMATCH",
                     "band ok" if band_ok else "BAND MISMATCH"))
        if e["source"] == "anthropic" and e["name"] in ("brand-guidelines", "internal-comms", "pdf"):
            s1_seen = True
            s1_ok = s1_ok and tier_ok
    return rows, (s1_ok if s1_seen else None)


def mutation_detection(mutations, runs, corpus):
    det_total = det_ok = jud_total = jud_ok = 0
    detail = []
    base_tiers = {}
    for e in corpus["skills"]:
        if runs.get(e["name"]):
            base_tiers[e["name"]] = set(_tier(s) for s in runs[e["name"]])
    for m in mutations:
        got = runs.get(m["id"])
        if not got:
            detail.append((m["id"], "no run"))
            continue
        exp = m["expect"]
        if exp["lint"] or exp["hits"]:
            det_total += 1
            lint_ids = set(f["rule_id"] for s in got for f in s.get("lint_findings", []))
            hit_ids = set(h["pattern_id"] for s in got for h in s.get("scan_hits", []))
            ok = all(r in lint_ids for r in exp["lint"]) and all(p in hit_ids for p in exp["hits"])
            det_ok += 1 if ok else 0
            detail.append((m["id"], "deterministic " + ("ok" if ok else "MISSING")))
        judge_checks = []
        for gid, ans in exp["gates"].items():
            judge_checks.append(all(_gate_answers(s).get(gid) == ans for s in got))
        if exp["tier"]:
            judge_checks.append(all(_tier(s) == exp["tier"].lower() for s in got))
        if exp["tier_unchanged"]:
            base = base_tiers.get(m["base"])
            judge_checks.append(bool(base) and all(_tier(s) in base for s in got))
        if judge_checks:
            jud_total += 1
            ok = all(judge_checks)
            jud_ok += 1 if ok else 0
            detail.append((m["id"], "judge " + ("ok" if ok else "NOT AS EXPECTED")))
    det = det_ok / float(det_total) if det_total else None
    jud = jud_ok / float(jud_total) if jud_total else None
    return det, jud, detail


def label_agreement(labels_dir, corpus, runs):
    a, b = [], []
    if not labels_dir or not os.path.isdir(labels_dir):
        return a, b
    for e in corpus["skills"]:
        path = os.path.join(labels_dir, e["name"] + ".json")
        got = runs.get(e["name"])
        if not os.path.isfile(path) or not got:
            continue
        labels = _load(path).get("gates", {})
        answers = _gate_answers(got[0])
        for gid in sorted(labels):
            a.append(labels[gid])
            b.append(answers.get(gid) or "missing")
    return a, b


def reliability(runs, key):
    vals = []
    for group in runs.values():
        for s in group:
            jr = s.get("judge_reliability") or {}
            if jr.get("trials", 0) > 0 and jr.get(key) is not None:
                vals.append(jr[key])
    return sum(vals) / float(len(vals)) if vals else None


def tier_stability(runs):
    seen, stable = 0, 0
    for group in runs.values():
        if len(group) >= 2:
            seen += 1
            stable += 1 if len(set(_tier(s) for s in group)) == 1 else 0
    return (stable / float(seen)) if seen else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--labels")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--mutations", required=True)
    a = ap.parse_args(argv)
    corpus = _load(a.corpus)
    mutations = _load(a.mutations)["mutations"]
    known = set(e["name"] for e in corpus["skills"]) | set(m["id"] for m in mutations)
    runs = load_runs(a.runs, known)

    rows, s1 = corpus_matches(corpus, runs)
    print("Corpus tier and band matches")
    for r in rows:
        print("  " + "  ".join(str(x) for x in r))
    det, jud, detail = mutation_detection(mutations, runs, corpus)
    print("Mutations")
    for d in detail:
        print("  %s  %s" % d)
    la, lb = label_agreement(a.labels, corpus, runs)
    kappa = cohen_kappa(la, lb) if la else None
    agree = percent_agreement(la, lb) if la else None
    tagree = reliability(runs, "trial_agreement")
    qfail = reliability(runs, "quote_failure_rate")
    stab = tier_stability(runs)

    checks = [
        ("S1 anthropic tiers as expected", "NO DATA" if s1 is None else ("yes" if s1 else "no"),
         "all match", "PASS" if s1 else ("NO DATA" if s1 is None else "FAIL")),
        _result("S2 deterministic mutations detected", det, "= 100%", det == S2_DETERMINISTIC if det is not None else False),
        _result("S2 judge-driven mutations as expected", jud, ">= 90%", jud is not None and jud >= S2_JUDGE),
        _result("S3 trial agreement (gates unanimous)", tagree, ">= 85%", tagree is not None and tagree >= S3_TRIAL_AGREEMENT),
        _result("S3 tier stability across runs", stab, "= 100%", stab == 1.0 if stab is not None else False),
        _result("S4 Cohen's kappa vs labels", kappa, ">= 0.6", kappa is not None and kappa >= S4_KAPPA),
        _result("S5 quote failure rate", qfail, "<= 5%", qfail is not None and qfail <= S5_QUOTE_FAILURE, "%.3f"),
    ]
    print("Targets")
    for name, val, target, status in checks:
        print("  %-42s %-10s target %-10s %s" % (name, val, target, status))
    if agree is not None:
        print("  (percent agreement with labels: %.3f over %d gate answers)" % (agree, len(la)))
    return 1 if any(c[3] == "FAIL" for c in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
