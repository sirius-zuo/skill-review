#!/usr/bin/env python3
"""Validate and materialize an eval kit (Python 3.7 stdlib only)."""
import argparse
import copy
import hashlib
import json
import os
import re
import secrets
import sys

from common import (MARK_FAILED_REASONS, ROOT_DIR, UsageError, ValidationFailed,
                    load_manifest, load_run, read_json, read_text, run_main, scoring,
                    skill_work_dir, validate_against, write_json)

TEMPLATES_DIR = os.path.join(ROOT_DIR, "kit-templates")
KIT_FAILED = "kit_failed"  # kit status names (spec 10.1, 12): "ok" or "kit_failed"
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_CANARY_REF = re.compile(r"\{\{canary:([^}]*)\}\}")

# check type -> (required params, optional params)
CHECK_PARAMS = {
    "file_exists": (["path"], []),
    "file_contains": (["path", "text"], []),
    "output_contains": (["text"], []),
    "output_not_contains": (["text"], ["scope"]),
    "tool_called": (["tool"], ["input_match"]),
    "tool_not_called": (["tool"], ["input_match"]),
    "judge": (["rubric"], []),
}
SAFETY_FLAGS = [("ingests_untrusted", "injection"), ("irreversible", "no_confirmation"),
                ("credentials", "canary")]


# ---------------------------------------------------------------- helpers

def _is_str(v):
    return isinstance(v, str) and v.strip() != ""


def _all_checks(kit):
    for group in ("task_cases", "safety_cases"):
        for case in kit.get(group) or []:
            for chk in case.get("checks") or []:
                yield case, chk


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            for s in _walk_strings(v):
                yield s
    elif isinstance(value, list):
        for v in value:
            for s in _walk_strings(v):
                yield s


def safe_fixture_path(path):
    return (isinstance(path, str) and path.startswith("fixtures/") and ".." not in path
            and "\\" not in path and "\x00" not in path and not path.endswith("/"))


# ---------------------------------------------------------------- validation

def validate_kit(kit, ctx, req):
    errs = []
    skill_name = ctx.get("skill_name") or ""
    inv = ctx.get("inventory") or {}
    siblings = list(ctx.get("sibling_names") or [])
    templates_dir = ctx.get("templates_dir") or TEMPLATES_DIR

    if kit.get("skill") != skill_name:
        errs.append("kit skill must be %s" % skill_name)

    # ids must be unique across the whole kit
    seen = set()
    all_ids = []
    for group in ("trigger_cases", "task_cases", "safety_cases", "fixtures"):
        all_ids.extend(x.get("id") for x in kit.get(group) or [])
    for case, chk in _all_checks(kit):
        all_ids.append(chk.get("id"))
    for i in all_ids:
        if i in seen:
            errs.append("duplicate id %s" % i)
        seen.add(i)

    # trigger cases
    trig = kit.get("trigger_cases") or []
    pos = [c for c in trig if c.get("should_trigger") is True]
    neg = [c for c in trig if c.get("should_trigger") is False]
    pmin, pmax = req["trigger_pos_min"], req["trigger_pos_max"]
    nmin, nmax = req["trigger_neg_min"], req["trigger_neg_max"]
    if not pmin <= len(pos) <= pmax:
        errs.append("need %d-%d should_trigger cases, got %d" % (pmin, pmax, len(pos)))
    if not nmin <= len(neg) <= nmax:
        errs.append("need %d-%d should_not_trigger cases, got %d" % (nmin, nmax, len(neg)))
    name_l = skill_name.lower()
    unnamed = [c for c in pos if not name_l or name_l not in str(c.get("prompt", "")).lower()]
    if pos and len(unnamed) * 2 < len(pos):
        errs.append("at least half of positive prompts must not name the skill")
    if siblings:
        sib = [c for c in neg if c.get("near_miss_of") in siblings]
        if len(sib) < req["sibling_neg_min"]:
            errs.append("need at least %d near misses for sibling skills" % req["sibling_neg_min"])

    # fixtures
    fixtures = kit.get("fixtures") or []
    fix_ids = set(f.get("id") for f in fixtures)
    paths = set()
    for f in fixtures:
        fid = f.get("id")
        if not safe_fixture_path(f.get("path")):
            errs.append("fixture %s: fixture path must start with fixtures/ and not contain .."
                        % fid)
        elif f["path"] in paths:
            errs.append("fixture %s: duplicate path %s" % (fid, f["path"]))
        else:
            paths.add(f["path"])
        has_c, has_t = "content" in f, "template" in f
        if has_c == has_t:
            errs.append("fixture %s: must have exactly one of content or template" % fid)
        if has_t:
            t = f["template"]
            ok = (isinstance(t, str) and t and "/" not in t and "\\" not in t and ".." not in t
                  and os.path.isfile(os.path.join(templates_dir, t))
                  and not os.path.islink(os.path.join(templates_dir, t)))
            if not ok:
                errs.append("fixture %s: template not found: %s" % (fid, t))

    def check_fixture_refs(case):
        for ref in case.get("fixtures") or []:
            if ref not in fix_ids:
                errs.append("case %s: unknown fixture %s" % (case.get("id"), ref))

    # task cases
    tasks = kit.get("task_cases") or []
    if len(tasks) < req["task_min"]:
        errs.append("need at least %d task_cases" % req["task_min"])
    for case in tasks:
        check_fixture_refs(case)
        claim = case.get("claim") or {}
        line = claim.get("line")
        if (not _is_str(claim.get("file")) or not isinstance(line, int)
                or isinstance(line, bool) or line < 1):
            errs.append("task case %s: claim must cite a SKILL.md line" % case.get("id"))

    # safety cases
    safety = kit.get("safety_cases") or []
    kinds = set(c.get("kind") for c in safety)
    for flag, kind in SAFETY_FLAGS:
        if inv.get(flag) is True and kind not in kinds:
            errs.append("missing safety case: %s" % kind)
    for case in safety:
        check_fixture_refs(case)
        kind, cid = case.get("kind"), case.get("id")
        checks = case.get("checks") or []
        if kind == "injection" and not case.get("fixtures"):
            errs.append("safety case %s: injection case must reference a fixture" % cid)
        if kind == "no_confirmation" and not any(c.get("type") == "tool_not_called"
                                                 for c in checks):
            errs.append("safety case %s: no_confirmation case must assert tool_not_called" % cid)
        if kind == "canary":
            if not any(c.get("type") == "output_not_contains"
                       and "{{canary:" in str((c.get("params") or {}).get("text", ""))
                       for c in checks):
                errs.append("safety case %s: canary case must assert output_not_contains the "
                            "canary" % cid)

    # checks
    for case, chk in _all_checks(kit):
        cid, ctype = chk.get("id"), chk.get("type")
        params = chk.get("params")
        if not isinstance(params, dict):
            errs.append("check %s: params must be an object" % cid)
            continue
        required, optional = CHECK_PARAMS.get(ctype, ([], []))
        for p in required:
            if not _is_str(params.get(p)):
                errs.append("check %s: missing param %s" % (cid, p))
        for p in params:
            if p not in required and p not in optional:
                errs.append("check %s: unexpected param %s" % (cid, p))
        if "scope" in params and params["scope"] not in ("reply", "trace"):
            errs.append("check %s: scope must be reply or trace" % cid)
        if "input_match" in params and not isinstance(params["input_match"], str):
            errs.append("check %s: input_match must be a string" % cid)
        if ctype == "judge":
            r = params.get("rubric")
            if isinstance(r, str) and not ("PASS if" in r and "FAIL if" in r):
                errs.append("check %s: judge rubric must contain PASS if and FAIL if" % cid)
    for case in tasks + safety:
        for s in _walk_strings([case.get("prompt"), case.get("checks")]):
            for ref in _CANARY_REF.findall(s):
                if ref not in fix_ids:
                    errs.append("case %s: unknown fixture %s in {{canary:%s}}"
                                % (case.get("id"), ref, ref))
    return errs


# ---------------------------------------------------------------- ids

def kit_id(kit):
    body = dict((k, v) for k, v in kit.items() if k != "kit_id")
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- materialize

def _subst_canary_refs(value, canaries):
    if isinstance(value, str):
        return _CANARY_REF.sub(lambda m: canaries.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return dict((k, _subst_canary_refs(v, canaries)) for k, v in value.items())
    if isinstance(value, list):
        return [_subst_canary_refs(v, canaries) for v in value]
    return value


def _fill(text, canary, params, is_json):
    ptext = str(params.get("text", ""))
    if is_json:
        ptext = json.dumps(ptext, ensure_ascii=False)[1:-1]
    ptext = ptext.replace("{{CANARY}}", canary)
    return text.replace("{{TEXT}}", ptext).replace("{{CANARY}}", canary)


def _write_new(base, rel, text):
    parts = rel.split("/")
    cur = base
    for p in parts[:-1]:
        cur = os.path.join(cur, p)
        if os.path.islink(cur):
            raise ValueError("refusing to write through symlink: %s" % rel)
        os.makedirs(cur, exist_ok=True)
    target = os.path.join(cur, parts[-1])
    if os.path.islink(target):
        raise ValueError("refusing to write through symlink: %s" % rel)
    with open(target, "w", encoding="utf-8") as f:
        f.write(text)


def materialize(kit, kit_dir, templates_dir, token=secrets.token_hex):
    kit = copy.deepcopy(kit)
    fixtures = kit.get("fixtures") or []
    for f in fixtures:
        if not safe_fixture_path(f.get("path")):
            raise ValueError("unsafe fixture path: %r" % (f.get("path"),))
    canaries = {}
    used = set()
    for f in fixtures:
        c = "CANARY-" + token(12)
        if c in used:
            raise ValueError("canary token collision for fixture %s" % f.get("id"))
        used.add(c)
        canaries[f["id"]] = c

    os.makedirs(kit_dir, exist_ok=True)
    for f in fixtures:
        canary = canaries[f["id"]]
        params = f.get("params") or {}
        if "template" in f:
            tname = f["template"]
            if "/" in tname or "\\" in tname or ".." in tname:
                raise ValueError("unsafe template name: %r" % tname)
            tpath = os.path.join(templates_dir, tname)
            if os.path.islink(tpath) or not os.path.isfile(tpath):
                raise ValueError("template not found: %s" % tname)
            text = _fill(read_text(tpath), canary, params, tname.endswith(".json"))
        else:
            text = f.get("content", "").replace("{{CANARY}}", canary)
        _write_new(kit_dir, f["path"], text)

    for group in ("trigger_cases", "task_cases", "safety_cases"):
        kit[group] = _subst_canary_refs(kit.get(group) or [], canaries)
    kit["kit_id"] = kit_id(kit)

    readme = read_text(os.path.join(templates_dir, "kit-readme.md"))
    vals = {"skill": kit["skill"], "kit_id": kit["kit_id"],
            "n_trigger": len(kit["trigger_cases"]), "n_task": len(kit["task_cases"]),
            "n_safety": len(kit["safety_cases"])}
    for k, v in vals.items():
        readme = readme.replace("{{%s}}" % k, str(v))
    _write_new(kit_dir, "README.md", readme)
    write_json(os.path.join(kit_dir, "kit.json"), kit)
    return kit


# ---------------------------------------------------------------- CLI

def _status(work_dir, key, skill, status, errors):
    doc = {"schema_version": 1, "engine": "script", "skill": skill, "status": status,
           "errors": list(errors)}
    write_json(os.path.join(skill_work_dir(work_dir, key), "kit-status.json"), doc,
               "kit-status")


def _skill_name(work_dir, key):
    try:
        return read_json(os.path.join(skill_work_dir(work_dir, key), "result.json"))["skill"]
    except (OSError, ValueError, KeyError):
        return key


def main(argv):
    ap = argparse.ArgumentParser(prog="build_kit.py")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--mark-failed", default=None, choices=MARK_FAILED_REASONS)
    args = ap.parse_args(argv)
    key = args.skill
    if not _KEY_RE.match(key) or ".." in key:
        raise UsageError("invalid skill key %r" % key)
    w = args.work_dir

    if args.mark_failed is not None:
        _status(w, key, _skill_name(w, key), KIT_FAILED, [args.mark_failed])
        sys.stdout.write(json.dumps({"ok": True, "status": KIT_FAILED}) + "\n")
        return 0

    try:
        result = read_json(os.path.join(skill_work_dir(w, key), "result.json"))
        manifest = load_manifest(w)
        kit_root = load_run(w)["kit_dir"]
    except (OSError, ValueError, KeyError) as e:
        raise ValidationFailed(["cannot read inputs: %s" % e], {"retryable": False})
    name = result.get("skill") or key
    siblings = [s["name"] for s in manifest.get("skills", [])
                if s.get("kind") == "skill" and s.get("key") != key and s.get("name") != name]
    kit_dir = os.path.join(kit_root, key)
    kit_path = os.path.join(kit_dir, "kit.json")
    try:
        kit = read_json(kit_path)
    except (OSError, ValueError) as e:
        errors = ["cannot read kit.json: %s" % e]
    else:
        errors = validate_against(kit, "kit")
        if not errors:
            ctx = {"skill_name": name, "inventory": result.get("inventory") or {},
                   "sibling_names": siblings, "templates_dir": TEMPLATES_DIR}
            errors = validate_kit(kit, ctx, scoring()["kit"])
    if errors:
        _status(w, key, name, KIT_FAILED, errors)
        raise ValidationFailed(errors, {"retryable": True})
    kit = materialize(kit, kit_dir, TEMPLATES_DIR)
    _status(w, key, name, "ok", [])
    sys.stdout.write(json.dumps({"ok": True, "path": kit_path, "kit_id": kit["kit_id"]}) + "\n")
    return 0


if __name__ == "__main__":
    run_main(main)
