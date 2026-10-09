"""Simulated routing check (Python 3.7 stdlib only).

prepare writes a shuffled, nonce-wrapped skill list and prompt list for a router sub-agent;
check validates its answer; metrics and findings turn the answers into spec section 10.3 results.
"""
import argparse
import json
import os
import random
import re
import sys

from bundle import wrap_untrusted
from common import (EXIT_OK, ROOT_DIR, UsageError, ValidationFailed, load_manifest, load_run,
                    read_json, reviewable_skills, run_main, scoring, validate_against,
                    write_json)
from discover import _read_regular
from frontmatter import parse_skill_file

DISTRACTORS = os.path.join(ROOT_DIR, "kit-templates", "distractors.json")
_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _description(manifest, skill):
    limit = scoring()["limits"]["bundle_max_bytes"]
    text = _read_regular(manifest["root"], skill["skill_file"], limit)
    meta = parse_skill_file(text).meta if text is not None else None
    desc = meta.get("description") if isinstance(meta, dict) else None
    return desc if isinstance(desc, str) else ""


def _collect(work_dir):
    manifest = load_manifest(work_dir)
    run = load_run(work_dir)
    skills, prompts = [], []
    for s in reviewable_skills(manifest):
        skills.append({"name": s["name"], "description": _description(manifest, s)})
        key = s.get("key", "")
        if not _KEY_RE.match(key) or key in (".", ".."):
            continue
        kit_path = os.path.join(run["kit_dir"], key, "kit.json")
        if os.path.islink(kit_path) or not os.path.isfile(kit_path):
            continue
        try:
            kit = read_json(kit_path)
        except (OSError, ValueError):
            continue
        for c in kit.get("trigger_cases") or []:
            prompts.append({"skill": s["name"], "case_id": c.get("id"),
                            "prompt": str(c.get("prompt", "")),
                            "should_trigger": c.get("should_trigger") is True})
    return run, skills, prompts


def prepare(work_dir, call_no, seed=None):
    run, skills, prompts = _collect(work_dir)
    nonce = run["nonce"]
    distractors = read_json(DISTRACTORS)["distractors"]
    names = set(s["name"] for s in skills)
    allskills = skills + [d for d in distractors if d["name"] not in names]
    rng = random.Random(call_no if seed is None else seed)
    order = list(allskills)
    rng.shuffle(order)
    porder = list(range(len(prompts)))
    rng.shuffle(porder)

    out = ["# skill list"]
    for s in order:
        out.append(wrap_untrusted("name: %s\ndescription: %s" % (s["name"], s["description"]),
                                  nonce))
    out += ["", "# prompts"]
    # ids are stable per prompt (P1..Pn in pooled order); only display order is shuffled
    for i in porder:
        out.append("P%d: %s" % (i + 1, wrap_untrusted(prompts[i]["prompt"], nonce)))
    path = os.path.join(work_dir, "routing-input-%d.txt" % call_no)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    pmap = dict(("P%d" % (i + 1), {"skill": p["skill"], "case_id": p["case_id"],
                                    "should_trigger": p["should_trigger"]})
                for i, p in enumerate(prompts))
    write_json(os.path.join(work_dir, "routing-map.json"),
               {"schema_version": 1, "engine": "script", "skills": [s["name"] for s in allskills],
                "prompts": pmap})
    return path


def check(work_dir, call_no):
    path = os.path.join(work_dir, "routing-%d.json" % call_no)
    try:
        doc = read_json(path)
        pmap = read_json(os.path.join(work_dir, "routing-map.json"))
    except (OSError, ValueError) as e:
        return ["cannot read %s: %s" % (path, e)]
    errors = list(validate_against(doc, "routing"))
    if errors:
        return errors
    if doc["call"] != call_no:
        errors.append("call is %s, expected %d" % (doc["call"], call_no))
    known = set(pmap["skills"]) | {"none"}
    choices = doc["choices"]
    for pid in pmap["prompts"]:
        if pid not in choices:
            errors.append("prompt %s not answered" % pid)
        elif choices[pid] not in known:
            errors.append("prompt %s: unknown skill name %r" % (pid, choices[pid]))
    for pid in choices:
        if pid not in pmap["prompts"]:
            errors.append("unknown prompt id %s" % pid)
    return errors


def _majority(votes):
    counts = {}
    for v in votes:
        counts[v] = counts.get(v, 0) + 1
    top = max(counts.values())
    winners = [v for v, n in counts.items() if n == top]
    return winners[0] if len(winners) == 1 else "none"


def metrics(kits, calls, prompt_map):
    chosen = {}
    for pid in prompt_map:
        votes = [c[pid] for c in calls if pid in c]
        chosen[pid] = _majority(votes) if votes else "none"
    out = {}
    for name in sorted(kits):
        pos = sorted((p for p, m in prompt_map.items()
                      if m["skill"] == name and m["should_trigger"]), key=_pid_key)
        neg = sorted((p for p, m in prompt_map.items()
                      if m["skill"] == name and not m["should_trigger"]), key=_pid_key)
        hits = [p for p in pos if chosen[p] == name]
        false = [p for p in neg if chosen[p] == name]
        conf = {}
        for p in pos:
            w = chosen[p]
            if w != name and w != "none":
                conf.setdefault(w, []).append(p)
        out[name] = {"recall": len(hits) / float(len(pos)) if pos else None,
                     "false_trigger_rate": len(false) / float(len(neg)) if neg else None,
                     "confusions": conf}
    return out


def _pid_key(pid):
    m = re.match(r"^P(\d+)$", pid)
    return (int(m.group(1)) if m else 0, pid)


def _finding(fid, severity, text, why):
    return {"source": "routing", "id": fid, "severity": severity, "category": "trigger",
            "text": text, "why": why, "simulated": True}


def findings(metrics_, thresholds):
    out = {}
    for name in sorted(metrics_):
        m = metrics_[name]
        fs = []
        recall = m.get("recall")
        if recall is not None:
            if recall < thresholds["recall_major_below"]:
                sev = "major"
            elif recall < thresholds["recall_minor_below"]:
                sev = "minor"
            else:
                sev = None
            if sev:
                fs.append(_finding(
                    "RT-UNDER", sev,
                    "Simulated routing: the skill under-triggers (recall %.2f)." % recall,
                    "A router that sees only skill names and descriptions did not pick this "
                    "skill for many prompts it should handle; sharpen the description."))
        ftr = m.get("false_trigger_rate")
        if ftr is not None and ftr > thresholds["false_trigger_above"]:
            fs.append(_finding(
                "RT-OVER", "minor",
                "Simulated routing: the skill over-triggers (false-trigger rate %.2f)." % ftr,
                "The router picked this skill for prompts it should not handle; narrow the "
                "description."))
        for winner in sorted(m.get("confusions") or {}):
            if winner not in metrics_:  # distractor winners count against recall only
                continue
            ids = m["confusions"][winner]
            if len(ids) >= thresholds["collision_min"]:
                fs.append(_finding(
                    "RT-COLLIDE", "major",
                    "Simulated routing: the skill collides with %s (%d positive prompts "
                    "went to %s)." % (winner, len(ids), winner),
                    "Prompts meant for this skill were won by %s; make the descriptions "
                    "more distinct." % winner))
        out[name] = fs
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="routing.py")
    ap.add_argument("action", choices=["prepare", "check"])
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--call", required=True, type=int)
    args = ap.parse_args(argv)
    if args.call < 1:
        raise UsageError("--call must be >= 1")
    if args.action == "prepare":
        path = prepare(args.work_dir, args.call)
        sys.stdout.write(json.dumps({"ok": True, "path": path}) + "\n")
        return EXIT_OK
    errors = check(args.work_dir, args.call)
    if errors:
        raise ValidationFailed(errors)
    sys.stdout.write(json.dumps({"ok": True}) + "\n")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
