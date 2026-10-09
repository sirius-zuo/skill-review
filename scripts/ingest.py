"""Ingest externally produced eval results (spec 10.2). Python 3.7 stdlib only."""
import argparse
import datetime
import json
import math
import os
import subprocess

from common import (EXIT_OK, load_manifest, read_json, reviewable_skills, run_main,
                    scoring, skill_work_dir, validate_against, write_json)
from discover import _is_eval_result, _read_regular

GIT_TIMEOUT = 30


def _is_num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def _r(x):
    return None if x is None else round(x, 6)


def detect_format(data, filename):
    if not isinstance(data, dict):
        return None
    if not validate_against(data, "kit-results"):
        return "kit-results"
    cases = data.get("cases")
    if (data.get("schemaVersion") == 1 and isinstance(cases, list) and cases
            and all(isinstance(c, dict) and "arms" in c for c in cases)):
        return "claude-plugin-eval"
    rs = data.get("run_summary")
    if isinstance(rs, dict) and "with_skill" in rs:
        return "skill-creator-benchmark"
    return None


def _blank(fmt, source_file):
    return {"source_file": source_file, "format": fmt, "skill_name": None, "date": None,
            "model": None, "runs_per_case": 0, "n_cases": 0, "with_pass_rate": None,
            "without_pass_rate": None, "delta": None, "trigger_precision": None,
            "trigger_recall": None, "stale_kit": False, "fresh": "unknown",
            "warnings": [], "valid": True}


def _str_or_none(x):
    return x if isinstance(x, str) and x else None


def _norm_kit(data, source_file, kit_lookup, res):
    res["skill_name"] = _str_or_none(data.get("skill"))
    res["date"] = _str_or_none(data.get("date"))
    res["model"] = _str_or_none(data.get("model"))
    kit = kit_lookup(source_file)
    matches = isinstance(kit, dict) and kit.get("kit_id") == data.get("kit_id")
    res["stale_kit"] = not matches
    if not matches:
        res["warnings"].append("stale kit" if kit else "no sibling kit.json")
    trig = {}
    if matches:
        for c in kit.get("trigger_cases") or []:
            if isinstance(c, dict) and isinstance(c.get("id"), str):
                trig[c["id"]] = bool(c.get("should_trigger"))
    arms = {"with": [], "without": []}
    per = {}
    tp = fp = fn = 0
    for run in data["runs"]:
        cid, arm, passed = run["case_id"], run["arm"], run["passed"]
        if cid in trig:
            if arm == "with":
                if trig[cid]:
                    tp += 1 if passed else 0
                    fn += 0 if passed else 1
                elif not passed:
                    fp += 1
            continue
        arms[arm].append(1 if passed else 0)
        if arm == "with":
            per[cid] = per.get(cid, 0) + 1
    res["with_pass_rate"] = _mean(arms["with"])
    res["without_pass_rate"] = _mean(arms["without"])
    res["n_cases"] = len(per)
    res["runs_per_case"] = max(per.values()) if per else 0
    if res["with_pass_rate"] is not None and res["without_pass_rate"] is not None:
        res["delta"] = res["with_pass_rate"] - res["without_pass_rate"]
    if tp + fp:
        res["trigger_precision"] = tp / (tp + fp)
    if tp + fn:
        res["trigger_recall"] = tp / (tp + fn)


def _norm_plugin(data, source_file, res):
    for k in ("skill", "skillName", "skill_name"):
        if _str_or_none(data.get(k)):
            res["skill_name"] = data[k]
            break
    res["model"] = _str_or_none(data.get("model"))
    cases = data["cases"]
    scores = []
    deltas = []
    per = 0
    for c in cases:
        agg = c.get("aggregates") if isinstance(c.get("aggregates"), dict) else {}
        if _is_num(agg.get("score")):
            scores.append(agg["score"])
        if _is_num(agg.get("delta")):
            deltas.append(agg["delta"])
        w = c["arms"].get("with") if isinstance(c.get("arms"), dict) else None
        per = max(per, len(w) if isinstance(w, list) else 0)
    top = data.get("aggregates") if isinstance(data.get("aggregates"), dict) else {}
    delta = top.get("meanDelta") if _is_num(top.get("meanDelta")) else _mean(deltas)
    res["with_pass_rate"] = _mean(scores)
    if delta is not None and res["with_pass_rate"] is not None:
        res["delta"] = delta
        res["without_pass_rate"] = res["with_pass_rate"] - delta
    res["n_cases"] = len(cases)
    res["runs_per_case"] = per
    if data.get("partial") is True:
        res["valid"] = False
        res["warnings"].append("partial suite")


def _norm_benchmark(data, source_file, res):
    md = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    rs = data["run_summary"]

    def mean_of(key):
        s = rs.get(key)
        pr = s.get("pass_rate") if isinstance(s, dict) else None
        m = pr.get("mean") if isinstance(pr, dict) else None
        return m if _is_num(m) else None

    res["skill_name"] = _str_or_none(md.get("skill_name"))
    res["date"] = _str_or_none(md.get("timestamp"))
    res["model"] = _str_or_none(md.get("executor_model"))
    rpc = md.get("runs_per_configuration")
    res["runs_per_case"] = rpc if isinstance(rpc, int) and not isinstance(rpc, bool) else 0
    ev = md.get("evals_run")
    if isinstance(ev, list):
        res["n_cases"] = len(ev)
    else:
        ids = set()
        for run in data.get("runs") or []:
            if isinstance(run, dict) and "eval_id" in run:
                ids.add(str(run["eval_id"]))
        res["n_cases"] = len(ids)
    res["with_pass_rate"] = mean_of("with_skill")
    res["without_pass_rate"] = mean_of("without_skill")
    if res["with_pass_rate"] is not None and res["without_pass_rate"] is not None:
        res["delta"] = res["with_pass_rate"] - res["without_pass_rate"]


def normalize(fmt, data, source_file, kit_lookup):
    res = _blank(fmt, source_file)
    if fmt == "kit-results":
        _norm_kit(data, source_file, kit_lookup, res)
    elif fmt == "claude-plugin-eval":
        _norm_plugin(data, source_file, res)
    elif fmt == "skill-creator-benchmark":
        _norm_benchmark(data, source_file, res)
    else:
        raise ValueError("unknown format: %s" % fmt)
    for k in ("with_pass_rate", "without_pass_rate", "delta", "trigger_precision", "trigger_recall"):
        res[k] = _r(res[k])
    problems = []
    if res["with_pass_rate"] is None:
        problems.append("no with-skill pass rate")
    for k in ("with_pass_rate", "without_pass_rate"):
        v = res[k]
        if v is not None and not (0 <= v <= 1):
            problems.append("%s out of range [0, 1]" % k)
    if res["n_cases"] < 1:
        problems.append("n_cases < 1")
    if res["runs_per_case"] < 1:
        problems.append("runs_per_case < 1")
    if problems:
        res["valid"] = False
        res["warnings"].extend(problems)
    return res


# --- freshness --------------------------------------------------------------

def _parse_date(s):
    if not isinstance(s, str) or not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        d = datetime.datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=datetime.timezone.utc)
    return d


def _git(root, *args):
    try:
        p = subprocess.run(["git", "-C", root] + list(args), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return p.stdout.strip() if p.returncode == 0 else None


def freshness(result_date, root, skill_file):
    rd = _parse_date(result_date)
    if rd is None or not skill_file:
        return "unknown"
    if _git(root, "rev-parse", "--is-shallow-repository") != "false":
        return "unknown"
    out = _git(root, "log", "-1", "--format=%cI", "--", skill_file)
    commit = _parse_date(out) if out else None
    if commit is None:
        return "unknown"
    return rd >= commit


# --- mapping and CLI --------------------------------------------------------

def _owner(manifest, rel):
    best = None
    for s in manifest["skills"]:
        d = s["dir"]
        if d == "." or rel.startswith(d + "/"):
            if best is None or (d != "." and (best["dir"] == "." or len(d) > len(best["dir"]))):
                best = s
    return best


def _candidates(manifest, real_root):
    limits = scoring()["limits"]
    seen = []
    for s in manifest["skills"]:
        for p in s.get("eval_result_files", []):
            if p not in seen:
                seen.append(p)
    for e in manifest.get("repo_files", []):
        if e["path"] not in seen and _is_eval_result(real_root, e, limits):
            seen.append(e["path"])
    return sorted(seen)


def build_ingest(manifest):
    root = manifest["root"]
    limits = scoring()["limits"]
    skills = reviewable_skills(manifest)
    by_name = {}
    for s in skills:
        by_name.setdefault(s["name"], s)
    buckets = {s["key"]: ([], []) for s in skills}
    repo = ([], [])

    def kit_lookup(source_file):
        sib = (source_file.rsplit("/", 1)[0] + "/" if "/" in source_file else "") + "kit.json"
        body = _read_regular(root, sib, limits["ingest_max_bytes"])
        if body is None:
            return None
        try:
            kit = json.loads(body)
        except (ValueError, RecursionError):
            return None
        return kit if isinstance(kit, dict) else None

    for rel in _candidates(manifest, root):
        body = _read_regular(root, rel, limits["ingest_max_bytes"])
        data = None
        reason = "unreadable file"
        res = None
        if body is not None:
            try:
                data = json.loads(body)
                reason = None
            except (ValueError, RecursionError):
                reason = "not valid JSON"
        fmt = None
        if reason is None:
            fmt = detect_format(data, rel.rsplit("/", 1)[-1])
            if fmt is None:
                reason = "unrecognized format"
        if reason is None:
            try:
                res = normalize(fmt, data, rel, kit_lookup)
            except (KeyError, TypeError, AttributeError, ValueError):
                reason = "malformed %s content" % fmt
        owner = _owner(manifest, rel)
        target = None
        if res is not None and res["skill_name"] in by_name:
            target = by_name[res["skill_name"]]
        elif owner is not None and owner.get("kind") == "skill":
            target = owner
        bucket = repo if target is None else buckets[target["key"]]
        if res is not None and res["valid"]:
            res["fresh"] = (freshness(res["date"], root, target["skill_file"])
                            if target is not None else "unknown")
            bucket[0].append(res)
        else:
            if res is not None:
                reason = "; ".join(res["warnings"]) or "invalid result"
            bucket[1].append({"source_file": rel, "format": fmt, "reason": reason})

    out_skills = {}
    for s in skills:
        results, invalid = buckets[s["key"]]
        out_skills[s["key"]] = {
            "schema_version": 1, "engine": "script",
            "skill": {k: s[k] for k in ("key", "name", "dir", "skill_file")},
            "results": results, "invalid": invalid,
            "has_valid_result": bool(results),
            "has_baseline": any(r["without_pass_rate"] is not None for r in results)}
    out_repo = {"schema_version": 1, "engine": "script", "results": repo[0], "invalid": repo[1]}
    return {"skills": out_skills, "repo": out_repo}


def main(argv):
    ap = argparse.ArgumentParser(prog="ingest.py")
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.work_dir)
    out = build_ingest(manifest)
    for key, doc in out["skills"].items():
        write_json(os.path.join(skill_work_dir(args.work_dir, key), "ingest.json"), doc, "ingest")
    write_json(os.path.join(args.work_dir, "repo-ingest.json"), out["repo"], "ingest")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
