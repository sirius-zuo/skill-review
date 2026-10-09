"""Assemble run results (Python 3.7 stdlib only): spec section 11.1.

Merges every <work>/<key>/result.json with kit statuses, simulated routing findings and the
repo-level ingest into <run_dir>/results.json.

Routing metrics are built from the REVIEWED skills only (the skills that have a result.json):
the metrics() keys are those skills' names, never distractor names. A distractor that wins a
prompt therefore counts against recall (RT-UNDER) but can never produce RT-COLLIDE.

Banners: an input JSON whose engine is "llm-fallback" adds its step name. Steps without a
JSON output (bundle) and the assemble step itself cannot be detected, and routing-<n>.json
maps to no step name, so none of those raise a banner.
"""
import argparse
import json
import os
import re
import sys

from common import (CATEGORY_ORDER, EXIT_OK, UsageError, ValidationFailed, load_manifest,
                    load_run, read_json, reviewable_skills, run_main, scoring, write_json)
import routing

# Tier severity order, highest first. The single place tier order is defined.
TIER_ORDER = ("critical", "high", "medium", "low", "unknown")
BANDS = ("strong", "adequate", "weak")
STEP_ORDER = ("discover", "bundle", "lint", "scan", "ingest", "score", "build_kit", "assemble")
# routing findings rank with major (4) and minor (6) lint findings (spec 9.5)
ROUTING_RANK = {"major": 4, "minor": 6}
_ROUTING_RE = re.compile(r"^routing-(\d+)\.json$")


def _tier_index(tier):
    return TIER_ORDER.index(tier) if tier in TIER_ORDER else len(TIER_ORDER)


def _rec_key(rec_, index):
    """Spec 9.5 ordering: rank, category order (7.3), id, then original position."""
    cat = CATEGORY_ORDER.index(rec_["category"]) if rec_["category"] in CATEGORY_ORDER \
        else len(CATEGORY_ORDER)
    return (rec_["priority_rank"], cat, rec_["id"], index)


def _sort_recs(recs):
    order = sorted(range(len(recs)), key=lambda i: _rec_key(recs[i], i))
    return [recs[i] for i in order]


def rollup(skills):
    tier_counts = dict((t, 0) for t in TIER_ORDER)
    band_counts = dict((b, 0) for b in BANDS)
    evidence = {"reported": 0, "unverified": 0}
    scores = []
    for s in skills:
        tier_counts[s["risk_tier"]] += 1
        if s.get("quality_band") in band_counts:
            band_counts[s["quality_band"]] += 1
        if s.get("quality_overall") is not None:
            scores.append(s["quality_overall"])
        level = (s.get("evidence") or {}).get("level")
        if level in evidence:
            evidence[level] += 1
    worst = None
    for t in TIER_ORDER:  # unknown is last, so it wins only when nothing else is present
        if tier_counts[t]:
            worst = t
            break
    mean = round(sum(scores) / float(len(scores)), 1) if scores else None
    return {"worst_tier": worst, "tier_counts": tier_counts, "quality_mean": mean,
            "band_counts": band_counts, "evidence_counts": evidence}


def _failed_gates(skill_):
    out = []
    for cat in (skill_.get("categories") or {}).values():
        for gid, g in (cat.get("gates") or {}).items():
            failed = g["answer"] == "yes" if g["kind"] == "blocker" else g["answer"] != "yes"
            if failed:
                out.append(gid)
    return out


def patterns(skills, metrics_=None):
    out = []
    by_gate = {}
    for s in skills:
        for gid in set(_failed_gates(s)):
            by_gate.setdefault(gid, []).append(s["skill"])
    for gid in sorted(by_gate):
        if len(by_gate[gid]) >= 2:
            out.append({"kind": "gate", "id": gid, "skills": by_gate[gid]})
    pairs = set()
    minimum = scoring()["routing"]["collision_min"]
    for name, m in (metrics_ or {}).items():
        for winner, ids in (m.get("confusions") or {}).items():
            if winner in metrics_ and len(ids) >= minimum:
                pairs.add(tuple(sorted((name, winner))))
    for a, b in sorted(pairs):
        out.append({"kind": "routing", "skills": [a, b]})
    return out


def top_issues(skills, n):
    groups = {}
    for s in skills:
        groups.setdefault(_tier_index(s["risk_tier"]), []).append(s)
    out = []
    for idx in sorted(groups):
        members = groups[idx]
        depth = max(len(s["recommendations"]) for s in members)
        for i in range(depth):
            for s in members:
                if i < len(s["recommendations"]):
                    item = {"skill": s["skill"], "tier": s["risk_tier"]}
                    item.update(s["recommendations"][i])
                    out.append(item)
    return out[:n]


# ---------------------------------------------------------------- inputs

def _opt(path):
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        return read_json(path)
    except (OSError, ValueError):
        return None


def _routing_calls(work_dir):
    """Valid routing-<n>.json answers, in call order, plus the prompt map (or None, None)."""
    nums = []
    for name in os.listdir(work_dir):
        m = _ROUTING_RE.match(name)
        if m:
            nums.append(int(m.group(1)))
    pmap = _opt(os.path.join(work_dir, "routing-map.json"))
    if pmap is None:
        return [], None
    calls = []
    for n in sorted(nums):
        if not routing.check(work_dir, n):
            calls.append(read_json(os.path.join(work_dir, "routing-%d.json" % n))["choices"])
    return calls, pmap["prompts"]


def _routing_rec(f):
    return {"priority_rank": ROUTING_RANK[f["severity"]], "source": "routing", "id": f["id"],
            "category": f["category"], "text": f["text"], "why": f["why"], "evidence": []}


def _banners(work_dir, run, manifest, keys):
    steps = set()

    def check(doc, step):
        if isinstance(doc, dict) and doc.get("engine") == "llm-fallback":
            steps.add(step)

    check(manifest, "discover")
    check(run, "discover")
    check(_opt(os.path.join(work_dir, "repo-ingest.json")), "ingest")
    for key in keys:
        for fname, step in (("lint.json", "lint"), ("scan.json", "scan"),
                            ("ingest.json", "ingest"), ("result.json", "score"),
                            ("kit-status.json", "build_kit")):
            check(_opt(os.path.join(work_dir, key, fname)), step)
    return [s for s in STEP_ORDER if s in steps]


def assemble(work_dir):
    manifest = load_manifest(work_dir)
    run = load_run(work_dir)
    keys, skills, errors = [], [], []
    for s in reviewable_skills(manifest):
        res = _opt(os.path.join(work_dir, s["key"], "result.json"))
        if res is None:
            errors.append("missing or unreadable result.json for %s" % s["key"])
            continue
        keys.append(s["key"])
        skills.append(res)
    if errors:
        raise ValidationFailed(errors)

    metrics_ = {}
    calls, pmap = _routing_calls(work_dir)
    if calls:
        names = sorted(set(s["skill"] for s in skills))  # reviewed skills only
        metrics_ = routing.metrics(names, calls, pmap)
        found = routing.findings(metrics_, scoring()["routing"])
        for s in skills:
            extra = [_routing_rec(f) for f in found.get(s["skill"], [])]
            if extra:
                s["recommendations"] = _sort_recs(list(s["recommendations"]) + extra)

    run_dir = run["run_dir"]
    kits = {}
    for key in keys:
        st = _opt(os.path.join(work_dir, key, "kit-status.json"))
        if st is None:
            kits[key] = {"status": "skipped", "path": None}
        else:
            path = os.path.relpath(os.path.join(run["kit_dir"], key), run_dir) \
                if st["status"] == "ok" else None
            kits[key] = {"status": st["status"], "path": path}
    repo = _opt(os.path.join(work_dir, "repo-ingest.json")) or {}

    doc = {
        "schema_version": 1, "engine": "script", "target": run["target"],
        "date": run["created"], "arguments": run["args"],
        "self_review": bool(manifest.get("self_review")),
        "banners": _banners(work_dir, run, manifest, keys),
        "skills": skills, "kits": kits, "repo_results": repo.get("results") or [],
        "rollup": rollup(skills), "patterns": patterns(skills, metrics_),
        "top_issues": top_issues(skills, scoring()["report"]["top_issues_run"]),
    }
    write_json(os.path.join(run_dir, "results.json"), doc, "results")
    return doc


def main(argv):
    ap = argparse.ArgumentParser(prog="assemble.py")
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    if not os.path.isdir(args.work_dir):
        raise UsageError("--work-dir is not a directory")
    doc = assemble(args.work_dir)
    sys.stdout.write(json.dumps({"ok": True, "skills": len(doc["skills"]),
                                 "banners": doc["banners"]}) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
