"""Per-skill scoring, verdicts and recommendations (Python 3.7 stdlib only).

Consumes, for one skill key under the work dir: lint.json, scan.json, ingest.json,
judgment*.json and bundle.txt (spec sections 6.3-6.4, 8.4-8.6 and 9.1-9.6). Writes
result.json, validated against rules/schemas/result.schema.json. Deterministic; no model calls.

Judge input is trusted only after quote verification: every trial goes through
judgment.verify_judgment against the skill's bundle.txt, then judgment.merge_trials.
Completeness (spec 8.6) is checked on each raw trial, so a merge tie never turns into a
spurious "answered no without fix" error.

Risk context passed to risk.py (spec 9.3):
    exposure          E0..E3 from the confirmed inventory
    live_hits         scan hits triaged confirmed or unclear (benign hits never count)
    undisclosed       capabilities whose undisclosed entry kept verified evidence
    safety_blockers   applicable SAF-B* gates answered yes
    safety_cg_failed  applicable judge-answered SAF-CG* gates answered no or
                      insufficient_evidence (SAF-CG1 is script-answered and excluded)
    failed_gates      (controller ruling R-RISK-M3) every failed applicable gate whose id
                      starts with "SCT-". A critical or quality gate fails when its answer
                      is not yes (no, or insufficient_evidence, which scores as no); a
                      blocker gate fails when it is triggered (yes). risk.rule_items reads
                      this list for rule M3, so M3 names the scripts-and-tools gates that
                      made the score low.
    scores            the trigger, safety and scripts_tools category scores

Thresholds, points, the blocker cap and bands come from common.scoring() only.
"""
import argparse
import copy
import os
import re

import applies_if
import risk
from bundle import parse_bundle
from common import (CATEGORY_ORDER, EXIT_OK, UsageError, ValidationFailed, load_rules,
                    read_json, round_half_up, run_main, scoring, skill_work_dir, write_json)
from judgment import (completeness_errors, load_judgments, merge_trials, trial_agreement,
                      verify_judgment)
from scan import FLAGS

SCAN_AS_IS = ("has_siblings", "large_body", "has_references")
EXPOSURE_RULES = (("E3", ("irreversible", "network_write", "credentials")),
                  ("E2", ("file_write", "network_read", "invokes_agents")),
                  ("E1", ("scripts", "shell")))
SEV_BUCKET = {"blocker": 2, "major": 4, "minor": 6}
HIT_BUCKET = {"critical": 2, "major": 4, "minor": 6}
LIVE_LABELS = ("confirmed", "unclear")
_UNTRUSTED_NONCE = re.compile(r'^<untrusted nonce="([0-9a-f]+)">$', re.M)
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


# ---------------------------------------------------------------- inventory and exposure

def confirm_inventory(scan_inv, judgment, hits_by_id):
    """Confirmed Dict[flag, bool] (spec 6.3).

    - suspected/unknown flags take the judge's value; a judge true counts only with
      evidence (verified upstream). With no judge value, suspected resolves to true and
      unknown to false (used only for failed reviews).
    - a script-true flag stays true unless every source is a scan hit and every one of
      those hits is triaged benign with reason_code not_executed (and the judge does not
      itself assert true with evidence). A flag set by a regex rule cannot be removed.
    - the judge may raise a script-false flag to true only with evidence.
    - has_siblings, large_body and has_references come from the scan as-is.
    """
    judgment = judgment or {}
    jinv = judgment.get("inventory", {}) or {}
    triage = {}
    for t in judgment.get("scan_triage", []) or []:
        triage[t.get("hit_id")] = t
    out = {}
    for flag in FLAGS:
        entry = scan_inv.get(flag) or {"value": "unknown", "sources": []}
        value = entry.get("value")
        j = jinv.get(flag)
        has_judge = isinstance(j, dict) and isinstance(j.get("value"), bool)
        judge_true = has_judge and j["value"] is True and bool(j.get("evidence"))
        if flag in SCAN_AS_IS:
            out[flag] = value == "true"
        elif value in ("suspected", "unknown"):
            out[flag] = judge_true if has_judge else value == "suspected"
        elif value == "true":
            sources = entry.get("sources") or []
            removable = bool(sources) and all(
                s in hits_by_id
                and (triage.get(s) or {}).get("label") == "benign"
                and (triage.get(s) or {}).get("reason_code") == "not_executed"
                for s in sources)
            out[flag] = (not removable) or judge_true
        else:
            out[flag] = judge_true
    return out


def exposure_level(inv):
    """Spec 6.4: first match top to bottom."""
    for level, flags in EXPOSURE_RULES:
        if any(inv.get(f) for f in flags):
            return level
    return "E0"


# ---------------------------------------------------------------- gates and answers

def applicable_gates(gates_doc, facts):
    """Gates whose category applies_if AND own applies_if are true (spec 7.2)."""
    cat_conds = gates_doc.get("categories", {})
    cat_ok = {}
    out = []
    for g in gates_doc["gates"]:
        cat = g["category"]
        if cat not in cat_ok:
            cond = (cat_conds.get(cat) or {}).get("applies_if") or "true"
            cat_ok[cat] = applies_if.evaluate(cond, facts)
        if cat_ok[cat] and applies_if.evaluate(g.get("applies_if") or "true", facts):
            out.append(g)
    return out


def _union_evidence(items):
    seen, out = set(), []
    for u in items:
        for e in u.get("evidence") or []:
            k = (e.get("file"), e.get("line"), e.get("quote"))
            if k not in seen:
                seen.add(k)
                out.append(e)
    return out


def script_answers(applicable, ingest, judgment):
    """Answers for applicable script-answered gates (SAF-CG1, EVL-QG1, EVL-QG2)."""
    out = {}
    for g in applicable:
        if g["answered_by"] != "script":
            continue
        check = g["script_check"]
        if check == "judgment.no_undisclosed_capabilities":
            und = judgment.get("undisclosed_capabilities") or []
            if not und:
                out[g["id"]] = {"answer": "yes", "evidence": [],
                                "reason": "No undisclosed capability has verified evidence."}
            else:
                names = _unique(u["capability"] for u in und)
                out[g["id"]] = {"answer": "no", "evidence": _union_evidence(und),
                                "reason": "Undisclosed capabilities with verified evidence: %s."
                                          % ", ".join(names),
                                "fix": "Disclose these capabilities in SKILL.md, or remove the "
                                       "behavior: %s." % ", ".join(names)}
        elif check == "ingest.has_valid_result":
            if ingest.get("has_valid_result"):
                out[g["id"]] = {"answer": "yes", "evidence": [],
                                "reason": "At least one valid eval result was ingested."}
            else:
                out[g["id"]] = {"answer": "no", "evidence": [],
                                "reason": "No valid eval result was ingested for this skill.",
                                "fix": "Run the eval kit and commit kit-results.json next to "
                                       "SKILL.md."}
        elif check == "ingest.has_baseline":
            if ingest.get("has_baseline"):
                out[g["id"]] = {"answer": "yes", "evidence": [],
                                "reason": "An ingested result includes a without-skill baseline."}
            else:
                out[g["id"]] = {"answer": "no", "evidence": [],
                                "reason": "No ingested result includes a without-skill baseline.",
                                "fix": "Record without-skill baseline runs alongside the "
                                       "with-skill runs."}
        else:
            raise ValueError("unknown script_check %r for gate %s" % (check, g["id"]))
    return out


def _unique(seq):
    seen, out = set(), []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ---------------------------------------------------------------- scores

def _gate_entry(g, a):
    e = {"kind": g["kind"], "answered_by": g["answered_by"], "answer": a["answer"],
         "evidence": list(a.get("evidence") or [])}
    if a.get("reason"):
        e["reason"] = a["reason"]
    if a.get("fix"):
        e["fix"] = a["fix"]
    return e


def category_scores(applicable, answers, lint_findings, dismissed_rule_ids, scoring):
    """Spec 9.1. Each category: {score|None, applicable, blocker_capped, gates}."""
    points = scoring["points"]
    cap = scoring["blocker_cap"]
    dismissed_rule_ids = set(dismissed_rule_ids or ())
    lint_capped = set()
    for f in lint_findings:
        if f.get("severity") == "blocker" and not f.get("dismissed") \
                and f.get("rule_id") not in dismissed_rule_ids:
            lint_capped.add(f.get("category"))
    by_cat = dict((c, []) for c in CATEGORY_ORDER)
    for g in applicable:
        by_cat.setdefault(g["category"], []).append(g)
    out = {}
    for cat, gates in by_cat.items():
        earned = possible = 0
        capped = cat in lint_capped
        entries = {}
        for g in gates:
            a = answers.get(g["id"]) or {"answer": "insufficient_evidence", "evidence": [],
                                         "reason": "not answered"}
            entries[g["id"]] = _gate_entry(g, a)
            if g["kind"] == "blocker":
                if a["answer"] == "yes":
                    capped = True
                continue
            p = points[g["kind"]]
            possible += p
            if a["answer"] == "yes":
                earned += p
        score = None if possible == 0 else round_half_up(10.0 * earned / possible)
        if capped and score is not None:
            score = min(score, cap)
        out[cat] = {"score": score, "applicable": possible > 0, "blocker_capped": capped,
                    "gates": entries}
    return out


def quality(cats, scoring):
    """Mean of applicable category scores, 1 decimal (half-up); band from round_half_up(mean)."""
    vals = [c["score"] for c in cats.values() if c.get("score") is not None]
    if not vals:
        return None, None
    total, n = sum(vals), len(vals)
    mean_1dp = round_half_up(10.0 * total / n) / 10.0
    rounded = round_half_up(float(total) / n)
    bands = scoring["bands"]
    if rounded >= bands["strong_min"]:
        band = "strong"
    elif rounded >= bands["adequate_min"]:
        band = "adequate"
    else:
        band = "weak"
    return mean_1dp, band


# ---------------------------------------------------------------- recommendations

def build_recommendations(rule_items, failed_gates, blockers, lint_findings, live_hits,
                          undisclosed, gates_doc):
    """Spec 9.5 buckets:
    1 items named by the matched risk rule; 2 blockers (gate or lint); 3 failed critical
    gates; 4 major lint findings; 5 failed quality gates; 6 minor lint findings.
    Items the spec does not bucket explicitly: a live hit not named by the rule goes by
    severity (critical 2, major 4, minor 6); an undisclosed capability not named goes to 3
    (it is what fails the critical gate SAF-CG1). Ties: CATEGORY_ORDER index, then id.
    Dismissed lint findings are skipped.
    """
    rule_items = set(rule_items or ())
    meta = dict((g["id"], g) for g in gates_doc["gates"])
    cat_index = dict((c, i) for i, c in enumerate(CATEGORY_ORDER))
    recs = []

    def add(bucket, source, rid, category, text, why, evidence):
        if (source, rid) in rule_items:
            bucket = 1
        recs.append({"priority_rank": bucket, "source": source, "id": rid,
                     "category": category, "text": text, "why": why,
                     "evidence": list(evidence or [])})

    def gate_text(g):
        if g.get("fix"):
            return g["fix"]
        short = (meta.get(g["id"]) or {}).get("short", "")
        return "Address gate %s (%s)." % (g["id"], short) if short else "Address gate %s." % g["id"]

    for h in live_hits:
        add(HIT_BUCKET.get(h["severity"], 6), "hit", h["hit_id"], "safety",
            h.get("fix") or "Remove or justify %s." % h["pattern_id"], h.get("why", ""),
            [{"file": h["file"], "line": h["line"], "quote": h.get("excerpt", "")}])
    cap_why = (meta.get("SAF-CG1") or {}).get("why", "")
    for name in _unique(u["capability"] for u in undisclosed):
        ev = _union_evidence(u for u in undisclosed if u["capability"] == name)
        add(3, "capability", name, "safety",
            "Disclose the %s capability in SKILL.md, or remove the behavior." % name,
            cap_why, ev)
    for g in blockers:
        add(2, "gate", g["id"], g["category"], gate_text(g),
            (meta.get(g["id"]) or {}).get("why", ""), g.get("evidence"))
    for g in failed_gates:
        add(3 if g["kind"] == "critical" else 5, "gate", g["id"], g["category"], gate_text(g),
            (meta.get(g["id"]) or {}).get("why", ""), g.get("evidence"))
    for f in lint_findings:
        if f.get("dismissed"):
            continue
        add(SEV_BUCKET.get(f["severity"], 6), "lint", f["rule_id"], f["category"],
            f.get("fix") or f["message"], f.get("why", ""),
            [{"file": f["file"], "line": f["line"]}])
    order = list(range(len(recs)))
    order.sort(key=lambda i: (recs[i]["priority_rank"],
                              cat_index.get(recs[i]["category"], len(cat_index)),
                              recs[i]["id"], i))
    return [recs[i] for i in order]


# ---------------------------------------------------------------- inputs

def _check_key(key):
    if not _KEY_RE.match(key or "") or ".." in key:
        raise UsageError("invalid skill key %r" % (key,))


def _load(work_dir, key, name, required=True):
    path = os.path.join(skill_work_dir(work_dir, key), name)
    try:
        return read_json(path)
    except (OSError, ValueError) as e:
        if not required:
            return None
        raise ValidationFailed(["cannot read %s: %s" % (name, e)], {"retryable": False})


def _bundle_index(work_dir, key):
    path = os.path.join(skill_work_dir(work_dir, key), "bundle.txt")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        raise ValidationFailed(["cannot read bundle.txt: %s" % e], {"retryable": False})
    nonce = None
    try:
        nonce = read_json(os.path.join(work_dir, "run.json")).get("nonce")
    except (OSError, ValueError, AttributeError):
        nonce = None
    if not nonce:
        # bundle.py writes the header block first; file content lines are numbered, so the
        # first line-anchored untrusted tag is always bundle markup.
        m = _UNTRUSTED_NONCE.search(text)
        nonce = m.group(1) if m else None
    if not nonce:
        raise ValidationFailed(["bundle nonce not found (run.json or bundle header)"],
                               {"retryable": False})
    return parse_bundle(text, nonce)


def _l_cc_02(skill_file):
    r = [x for x in load_rules("lint-rules")["rules"] if x["id"] == "L-CC-02"][0]
    return {"rule_id": r["id"], "severity": r["severity"], "category": r["category"],
            "heuristic": r["heuristic"], "platform": r["platform"], "stage": "score",
            "file": skill_file, "line": 1, "message": r["message"], "fix": r["fix"],
            "why": r["why"]}


def _disables_model_invocation(meta):
    v = (meta or {}).get("disable-model-invocation")
    return v is True or (isinstance(v, str) and v.strip().lower() == "true")


def _evidence(ingest):
    ingest = ingest or {}
    return {"level": "reported" if ingest.get("has_valid_result") else "unverified",
            "results": copy.deepcopy(ingest.get("results") or [])}


def _skill_name(scan, key):
    sk = scan.get("skill") or {}
    return sk.get("name") or key


# ---------------------------------------------------------------- scoring

def score_skill_with_context(work_dir, key):
    """(result, risk ctx). score_skill returns only the result."""
    _check_key(key)
    lint = _load(work_dir, key, "lint.json")
    scan = _load(work_dir, key, "scan.json")
    ingest = _load(work_dir, key, "ingest.json")
    gates_doc = load_rules("gates")
    table = load_rules("risk-table")["rules"]
    sc = scoring()

    try:
        raw = load_judgments(work_dir, key)
    except ValidationFailed as e:
        raise ValidationFailed(e.errors, {"retryable": True})
    index = _bundle_index(work_dir, key)
    verified, quotes_total, quotes_failed = [], 0, 0
    for j in raw:
        vj, t, f = verify_judgment(j, index)
        verified.append(vj)
        quotes_total += t
        quotes_failed += f
    merged = merge_trials(verified)

    hits = scan.get("hits") or []
    hits_by_id = dict((h["hit_id"], h) for h in hits)
    inv = confirm_inventory(scan.get("inventory") or {}, merged, hits_by_id)
    exposure = exposure_level(inv)
    applicable = applicable_gates(gates_doc, inv)
    judge_ids = [g["id"] for g in applicable if g["answered_by"] == "judge"]

    unresolved = [f for f in FLAGS
                  if ((scan.get("inventory") or {}).get(f) or {}).get("value")
                  in ("suspected", "unknown")]
    errors = []
    for j in raw:
        errs = completeness_errors(j, judge_ids, list(hits_by_id), unresolved)
        if len(raw) > 1:
            errs = ["trial %d: %s" % (j["trial"], e) for e in errs]
        errors.extend(errs)
    if errors:
        raise ValidationFailed(errors, {"retryable": True})

    judge_set = set(judge_ids)
    mgates = merged.get("gates") or {}
    answers = {}
    for gid in judge_ids:
        if gid in mgates:
            answers[gid] = mgates[gid]
    answers.update(script_answers(applicable, ingest, merged))
    ignored = sorted(gid for gid in mgates if gid not in judge_set)

    # lint findings: score-stage L-CC-02, then judge dismissals (heuristic rules only)
    findings = copy.deepcopy(lint.get("findings") or [])
    meta = lint.get("meta") or {}
    if inv["irreversible"] and not _disables_model_invocation(meta):
        skill_file = (lint.get("skill") or {}).get("skill_file") or "SKILL.md"
        findings.append(_l_cc_02(skill_file))
    dismissals = {}
    for d in merged.get("lint_dismissals") or []:
        dismissals.setdefault(d["rule_id"], d["reason"])
    dismissed_ids = set()
    for f in findings:
        if f.get("heuristic") and f["rule_id"] in dismissals:
            f["dismissed"] = {"reason": dismissals[f["rule_id"]]}
            dismissed_ids.add(f["rule_id"])

    cats = category_scores(applicable, answers, findings, dismissed_ids, sc)
    q, band = quality(cats, sc)

    triage = dict((t["hit_id"], t) for t in merged.get("scan_triage") or [])
    scan_hits, live_hits = [], []
    for h in hits:
        t = triage.get(h["hit_id"])
        label = t["label"] if t else "unclear"
        live = label in LIVE_LABELS
        entry = dict(h)
        entry["triage"] = ({"label": t["label"], "reason_code": t.get("reason_code"),
                            "reason": t.get("reason", "")} if t else None)
        entry["live"] = live
        scan_hits.append(entry)
        if live:
            live_hits.append(h)

    undisclosed = merged.get("undisclosed_capabilities") or []
    gate_rows = []  # (gate, answer) for applicable gates, in gates.json order
    for g in applicable:
        a = answers.get(g["id"]) or {"answer": "insufficient_evidence", "evidence": []}
        gate_rows.append((g, a))

    def is_failed(g, a):
        return a["answer"] == "yes" if g["kind"] == "blocker" else a["answer"] != "yes"

    ctx = {
        "exposure": exposure,
        "live_hits": [{"hit_id": h["hit_id"], "severity": h["severity"]} for h in live_hits],
        "undisclosed": _unique(u["capability"] for u in undisclosed),
        "safety_blockers": [g["id"] for g, a in gate_rows
                            if g["id"].startswith("SAF-B") and a["answer"] == "yes"],
        "safety_cg_failed": [g["id"] for g, a in gate_rows
                             if g["id"].startswith("SAF-CG") and g["answered_by"] == "judge"
                             and a["answer"] != "yes"],
        "failed_gates": [g["id"] for g, a in gate_rows
                         if g["id"].startswith("SCT-") and is_failed(g, a)],
        "scores": dict((c, cats[c]["score"]) for c in ("trigger", "safety", "scripts_tools")),
    }
    rule = risk.evaluate(risk.compute_facts(ctx, sc["risk_thresholds"]), table)
    gate_short = dict((g["id"], g.get("short", "")) for g in gates_doc["gates"])
    rationale = risk.rationale(rule, ctx, gate_short)
    items = risk.rule_items(rule["id"], ctx)

    def rec_gate(g, a):
        d = {"id": g["id"], "category": g["category"], "kind": g["kind"],
             "answer": a["answer"], "evidence": list(a.get("evidence") or [])}
        if a.get("fix"):
            d["fix"] = a["fix"]
        return d

    blockers = [rec_gate(g, a) for g, a in gate_rows
                if g["kind"] == "blocker" and is_failed(g, a)]
    failed_gates = [rec_gate(g, a) for g, a in gate_rows
                    if g["kind"] != "blocker" and is_failed(g, a)]
    recs = build_recommendations(items, failed_gates, blockers, findings, live_hits,
                                 undisclosed, gates_doc)

    result = {
        "schema_version": 1,
        "engine": "script",
        "skill": _skill_name(scan, key),
        "key": key,
        "kind": "skill",
        "review_failed": False,
        "review_errors": [],
        "inventory": inv,
        "exposure": exposure,
        "categories": cats,
        "quality_overall": q,
        "quality_band": band,
        "risk_tier": rule["tier"],
        "risk_rule": rule["id"],
        "risk_rationale": rationale,
        "evidence": _evidence(ingest),
        "scan_hits": scan_hits,
        "lint_findings": findings,
        "undisclosed_capabilities": copy.deepcopy(undisclosed),
        "recommendations": recs,
        "judge_reliability": {
            "quote_failure_rate": (float(quotes_failed) / quotes_total) if quotes_total else 0.0,
            "trial_agreement": trial_agreement(verified, judge_ids),
            "trials": len(raw),
        },
        "ignored_answers": ignored,
        "insufficient_evidence": [g["id"] for g, a in gate_rows
                                  if a["answer"] == "insufficient_evidence"],
    }
    return result, ctx


def score_skill(work_dir, key):
    return score_skill_with_context(work_dir, key)[0]


def mark_failed(work_dir, key, reason):
    """review_failed result (spec 9.3): only C1 and H2 are evaluated, and every hit counts as
    live, because the failed judgment's triage is not trusted."""
    _check_key(key)
    lint = _load(work_dir, key, "lint.json")
    scan = _load(work_dir, key, "scan.json")
    ingest = _load(work_dir, key, "ingest.json", required=False) or {}
    gates_doc = load_rules("gates")
    table = load_rules("risk-table")["rules"]
    hits = scan.get("hits") or []
    inv = confirm_inventory(scan.get("inventory") or {}, {}, dict((h["hit_id"], h) for h in hits))
    exposure = exposure_level(inv)
    tier, rule_id = risk.failed_review_tier(hits, table)
    ctx = {"exposure": exposure,
           "live_hits": [{"hit_id": h["hit_id"], "severity": h["severity"]} for h in hits],
           "scores": {}}
    if rule_id:
        rule = [r for r in table if r["id"] == rule_id][0]
        rationale = risk.rationale(rule, ctx, {})
        items = risk.rule_items(rule_id, ctx)
    else:
        rationale = "Unknown — the review failed, and no critical or major scan hit was found."
        items = set()
    findings = copy.deepcopy(lint.get("findings") or [])
    scan_hits = []
    for h in hits:
        entry = dict(h)
        entry["triage"] = None
        entry["live"] = True
        scan_hits.append(entry)
    recs = build_recommendations(items, [], [], findings, hits, [], gates_doc)
    return {
        "schema_version": 1, "engine": "script", "skill": _skill_name(scan, key), "key": key,
        "kind": "skill", "review_failed": True, "review_errors": [reason],
        "inventory": inv, "exposure": exposure, "categories": {},
        "quality_overall": None, "quality_band": None,
        "risk_tier": tier, "risk_rule": rule_id, "risk_rationale": rationale,
        "evidence": _evidence(ingest), "scan_hits": scan_hits, "lint_findings": findings,
        "undisclosed_capabilities": [], "recommendations": recs,
        "judge_reliability": {"quote_failure_rate": None, "trial_agreement": None, "trials": 0},
        "ignored_answers": [], "insufficient_evidence": [],
    }


def main(argv):
    ap = argparse.ArgumentParser(prog="score.py")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--mark-failed", default=None, metavar="REASON")
    args = ap.parse_args(argv)
    if args.mark_failed is not None:
        if not args.mark_failed.strip():
            raise UsageError("--mark-failed needs a non-empty reason")
        result = mark_failed(args.work_dir, args.skill, args.mark_failed)
    else:
        result = score_skill(args.work_dir, args.skill)
    write_json(os.path.join(skill_work_dir(args.work_dir, args.skill), "result.json"),
               result, "result")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
