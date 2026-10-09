"""Judgment loading, quote verification and trial merge (Python 3.7 stdlib only).

Deterministic: no model calls. See spec sections 8.3 to 8.6.
"""
import copy
import json
import os
import re

from common import ValidationFailed, load_rules, scoring, skill_work_dir, validate_against

_NAME_RE = re.compile(r"^judgment-(\d+)\.json$")


def load_judgments(work_dir, key):
    """Read judgment-<n>.json (or judgment.json) for one skill, sorted by trial number."""
    d = skill_work_dir(work_dir, key)
    found = []
    try:
        names = os.listdir(d)
    except OSError:
        names = []
    for name in names:
        m = _NAME_RE.match(name)
        if m:
            found.append((int(m.group(1)), name))
        elif name == "judgment.json":
            found.append((0, name))
    found.sort()
    if not found:
        raise ValidationFailed(["no judgment file found for skill %s" % key])
    out, errors = [], []
    for _n, name in found:
        try:
            with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                data = json.load(f)
        except (ValueError, OSError) as e:
            errors.append("%s: unreadable JSON (%s)" % (name, e))
            continue
        errs = validate_against(data, "judgment")
        if errs:
            errors.extend("%s: %s" % (name, e) for e in errs)
        else:
            out.append(data)
    if errors:
        raise ValidationFailed(errors)
    return out


def _norm(s):
    return " ".join(s.split())


def verify_quote(index, ev, min_chars, window):
    lines = index.get(ev.get("file"))
    quote = ev.get("quote")
    line = ev.get("line")
    if lines is None or not isinstance(quote, str) or not isinstance(line, int) \
            or isinstance(line, bool):
        return False
    q = _norm(quote)
    if not q or line < 1 or line > len(lines):
        return False
    if len(q) < min_chars:
        return _norm(lines[line - 1]) == q
    lo = max(0, line - 1 - window)
    hi = min(len(lines), line + window)
    text = _norm(" ".join(lines[lo:hi]))
    return q in text


def _filter(items, index, limits):
    kept = []
    for e in items or []:
        if verify_quote(index, e, limits[0], limits[1]):
            kept.append(e)
    return kept


def verify_judgment(j, index):
    lim = scoring()["limits"]
    limits = (lim["quote_min_chars"], lim["quote_line_window"])
    out = copy.deepcopy(j)
    total = failed = 0

    def run(items):
        nonlocal total, failed
        items = items or []
        kept = _filter(items, index, limits)
        total += len(items)
        failed += len(items) - len(kept)
        return kept

    for flag in out.get("inventory", {}).values():
        if "evidence" in flag:
            flag["evidence"] = run(flag["evidence"])
        if flag.get("value") is True and not flag.get("evidence"):
            flag["value"] = False
    for g in out.get("gates", {}).values():
        if "evidence" in g:
            g["evidence"] = run(g["evidence"])
        if g.get("answer") == "yes" and not g.get("evidence"):
            g["answer"] = "insufficient_evidence"
    kept_caps = []
    for u in out.get("undisclosed_capabilities", []):
        u["evidence"] = run(u.get("evidence"))
        if u["evidence"]:
            kept_caps.append(u)
    out["undisclosed_capabilities"] = kept_caps
    return out, total, failed


def _winner(counts, order, tie):
    """Plurality winner; any tie for first place resolves to `tie`."""
    top = max(counts.values())
    leaders = [k for k in order if counts[k] == top]
    return leaders[0] if len(leaders) == 1 else tie


def _count(values):
    counts, order = {}, []
    for v in values:
        if v not in counts:
            counts[v] = 0
            order.append(v)
        counts[v] += 1
    return counts, order


def _union_evidence(lists):
    seen, out = set(), []
    for lst in lists:
        for e in lst or []:
            k = (e.get("file"), e.get("line"), e.get("quote"))
            if k not in seen:
                seen.add(k)
                out.append(e)
    return out


def _merge_gates(js):
    ids, seen = [], set()
    for j in js:
        for gid in j.get("gates", {}):
            if gid not in seen:
                seen.add(gid)
                ids.append(gid)
    out = {}
    for gid in ids:
        entries = [j["gates"][gid] for j in js if gid in j.get("gates", {})]
        counts, order = _count(e["answer"] for e in entries)
        ans = _winner(counts, order, "no")
        same = [e for e in entries if e["answer"] == ans]
        if same:
            g = copy.deepcopy(same[0])
        else:
            g = {"answer": ans, "evidence": [], "reason": "trials disagreed; tie resolves to no"}
        if "evidence" in g:
            g["evidence"] = _union_evidence(e.get("evidence") for e in same) or g["evidence"]
        if not g.get("fix"):
            fix = next((e["fix"] for e in entries if e.get("fix")), None)
            if fix:
                g["fix"] = fix
        out[gid] = g
    return out


def _merge_inventory(js):
    names, seen = [], set()
    for j in js:
        for n in j.get("inventory", {}):
            if n not in seen:
                seen.add(n)
                names.append(n)
    out = {}
    for n in names:
        entries = [j["inventory"][n] for j in js if n in j.get("inventory", {})]
        yes = [e for e in entries if e.get("value") is True]
        value = len(yes) >= len(entries) - len(yes)  # tie resolves to true
        same = yes if value else [e for e in entries if e.get("value") is not True]
        m = copy.deepcopy(same[0])
        m["value"] = value
        m["evidence"] = _union_evidence(e.get("evidence") for e in same)
        out[n] = m
    return out


def _merge_triage(js):
    ids, groups = [], {}
    for j in js:
        for t in j.get("scan_triage", []):
            if t["hit_id"] not in groups:
                groups[t["hit_id"]] = []
                ids.append(t["hit_id"])
            groups[t["hit_id"]].append(t)
    out = []
    for hid in ids:
        entries = groups[hid]
        counts, order = _count(t["label"] for t in entries)
        label = _winner(counts, order, "unclear")
        same = [t for t in entries if t["label"] == label]
        if same:
            m = copy.deepcopy(same[0])
        else:
            m = {"hit_id": hid, "label": "unclear", "reason_code": None,
                 "reason": "trials disagreed; tie resolves to unclear"}
        if label == "benign":
            m["reason_code"] = next((t["reason_code"] for t in same if t.get("reason_code")),
                                    m.get("reason_code"))
        else:
            m["reason_code"] = None
        out.append(m)
    return out


def merge_trials(js):
    """Majority merge (spec 8.5)."""
    if not js:
        raise ValueError("no judgments to merge")
    n = len(js)
    merged = copy.deepcopy(js[0])
    merged["gates"] = _merge_gates(js)
    merged["inventory"] = _merge_inventory(js)
    merged["scan_triage"] = _merge_triage(js)
    # lint dismissals and undisclosed capabilities: kept if more than half report them
    caps, dis = {}, {}
    cap_order, dis_order = [], []
    for j in js:
        for u in j.get("undisclosed_capabilities", []):
            c = u["capability"]
            if c not in caps:
                caps[c] = []
                cap_order.append(c)
            caps[c].append((j, u))
        for d in j.get("lint_dismissals", []):
            r = d["rule_id"]
            if r not in dis:
                dis[r] = []
                dis_order.append(r)
            dis[r].append(d)
    merged["undisclosed_capabilities"] = []
    for c in cap_order:
        # one report per trial counts once
        trials = set(id(j) for j, _u in caps[c])
        if len(trials) * 2 > n:
            first = copy.deepcopy(caps[c][0][1])
            first["evidence"] = _union_evidence(u.get("evidence") for _j, u in caps[c])
            merged["undisclosed_capabilities"].append(first)
    merged["lint_dismissals"] = [copy.deepcopy(dis[r][0]) for r in dis_order
                                 if len(dis[r]) * 2 > n]
    merged["merged_trials"] = n
    return merged


def trial_agreement(js, applicable_ids):
    if len(js) < 2:
        return None
    ids = set(applicable_ids)
    if not ids:
        return None
    same = 0
    for gid in ids:
        answers = [(j.get("gates", {}).get(gid) or {}).get("answer") for j in js]
        if answers[0] is not None and all(a == answers[0] for a in answers):
            same += 1
    return same / float(len(ids))


def completeness_errors(j, judge_gate_ids, hit_ids, unresolved_flags):
    errors = []
    gates = j.get("gates", {})
    kinds = {g["id"]: g["kind"] for g in load_rules("gates")["gates"]}
    for gid in sorted(judge_gate_ids):
        g = gates.get(gid)
        if g is None:
            errors.append("gate %s not answered" % gid)
            continue
        kind = kinds.get(gid)
        if g.get("answer") == "no" and kind in ("critical", "quality") and not g.get("fix"):
            errors.append("gate %s answered no without fix" % gid)
        if g.get("answer") == "yes" and kind == "blocker" and not g.get("fix"):
            errors.append("gate %s answered yes (blocker) without fix" % gid)
    triaged = {}
    for t in j.get("scan_triage", []):
        triaged[t.get("hit_id")] = t
    for hid in sorted(hit_ids):
        t = triaged.get(hid)
        if t is None:
            errors.append("hit %s not triaged" % hid)
        elif t.get("label") == "benign" and not t.get("reason_code"):
            errors.append("hit %s is benign without reason_code" % hid)
    inv = j.get("inventory", {})
    for flag in sorted(unresolved_flags):
        if flag not in inv or not isinstance(inv[flag].get("value"), bool):
            errors.append("flag %s not resolved" % flag)
    return errors
