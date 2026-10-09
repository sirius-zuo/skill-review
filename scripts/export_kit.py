#!/usr/bin/env python3
"""Export a materialized eval kit to the Claude Code plugin-eval layout.

Writes files only, inside the kit directory. Never runs anything, never follows
symlinks (Python 3.7 stdlib only).
"""
import argparse
import json
import os
import re
import sys

from common import (UsageError, ValidationFailed, load_run, read_json, run_main,
                    skill_work_dir, validate_against)

_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_JS_SPECIAL = set("\\^$.*+?()[]{}|/-")
MAX_TURNS = 20
ALLOWED_TOOLS = ["Read", "Glob", "Grep", "Write", "Edit", "Skill"]
FIXTURES_NOTE = "Files for this task are in the fixtures/ directory next to this case."
FORMATS = ["claude-plugin-eval"]


def js_escape(text):
    return "".join("\\" + c if c in _JS_SPECIAL else c for c in text)


def _q(value):
    """Double-quoted YAML scalar (JSON is a YAML subset)."""
    return json.dumps(value, ensure_ascii=False)


def _sq(value):
    """Single-quoted YAML scalar; control characters become regex escapes."""
    value = value.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return "'" + value.replace("'", "''") + "'"


def _safe_id(value):
    if not isinstance(value, str) or not _ID_RE.match(value) or ".." in value:
        raise ValueError("unsafe id for export: %r" % (value,))
    return value.lower()


def _write(base, rel, data):
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
    if isinstance(data, str):
        data = data.encode("utf-8")
    with open(target, "wb") as f:
        f.write(data)


def _read_fixture(kit_dir, rel):
    root = os.path.realpath(kit_dir)
    cur = kit_dir
    for p in rel.split("/"):
        cur = os.path.join(cur, p)
        if os.path.islink(cur):
            raise ValueError("fixture is a symlink: %s" % rel)
    real = os.path.realpath(cur)
    if not real.startswith(root + os.sep) or not os.path.isfile(real):
        raise ValueError("fixture not found: %s" % rel)
    with open(real, "rb") as f:
        return f.read()


def _frontmatter(pairs):
    return "---\n" + "".join("%s: %s\n" % kv for kv in pairs) + "---\n"


def _grader(chk, case_id):
    """Return (frontmatter pairs, body) for one kit check."""
    cid = chk["id"]
    typ = chk["type"]
    p = chk.get("params") or {}
    pairs = [("name", _q(cid))]
    body = ""
    if typ == "file_exists":
        pairs += [("type", "file_exists"), ("path", _q(p["path"]))]
    elif typ == "file_contains":
        pairs += [("type", "regex"), ("pattern", _sq(js_escape(p["text"]))),
                  ("target", "{source: file, path: %s}" % _q(p["path"]))]
    elif typ in ("output_contains", "output_not_contains"):
        target = "trace" if p.get("scope") == "trace" else "last_message"
        pairs += [("type", "regex"), ("pattern", _sq(js_escape(p["text"]))),
                  ("target", target)]
        if typ == "output_not_contains":
            pairs.append(("match", "not_contains"))
    elif typ in ("tool_called", "tool_not_called"):
        pairs += [("type", "tool_used"), ("tool", _q(p["tool"]))]
        if p.get("input_match"):
            pairs.append(("input_match", _sq(p["input_match"])))
        if typ == "tool_called":
            pairs.append(("min", "1"))
        else:
            pairs += [("min", "0"), ("max", "0")]
    elif typ == "judge":
        pairs.append(("type", "llm"))
        body = p["rubric"].rstrip("\n") + "\n"
    else:
        raise ValueError("case %s: unsupported check type %r" % (case_id, typ))
    return pairs, body


def _skill_fired(skill_name, should_trigger):
    pattern = '"skill"\\s*:\\s*"(?:[\\w-]+:)?%s"' % js_escape(skill_name)
    pairs = [("name", '"skill-fired"'), ("type", "tool_used"), ("tool", "Skill"),
             ("input_match", _sq(pattern))]
    if not should_trigger:
        pairs += [("min", "0"), ("max", "0")]
    return pairs, ""


def export_plugin_eval(kit, kit_dir, skill_name):
    """Write kit_dir/evals/<case id lowercased>/ for every case; return written paths."""
    fixtures = dict((f["id"], f) for f in kit.get("fixtures") or [])
    written = []

    def emit(rel, text):
        _write(kit_dir, rel, text)
        written.append(rel)

    jobs = []
    for c in kit.get("trigger_cases") or []:
        pairs, body = _skill_fired(skill_name, c.get("should_trigger") is True)
        jobs.append((c, "trigger", [], [("skill-fired", pairs, body)]))
    for c in kit.get("task_cases") or []:
        jobs.append((c, "task", c.get("fixtures") or [],
                     [(_safe_id(chk["id"]),) + _grader(chk, c["id"])
                      for chk in c.get("checks") or []]))
    for c in kit.get("safety_cases") or []:
        jobs.append((c, "safety", c.get("fixtures") or [],
                     [(_safe_id(chk["id"]),) + _grader(chk, c["id"])
                      for chk in c.get("checks") or []]))

    seen = set()
    for case, tag, fix_ids, graders in jobs:
        cid = _safe_id(case["id"])
        if cid in seen:
            raise ValueError("duplicate case id after lowercasing: %s" % cid)
        seen.add(cid)
        base = "evals/%s" % cid
        prompt = case["prompt"].rstrip("\n")
        if fix_ids:
            prompt += "\n\n" + FIXTURES_NOTE
            for fid in fix_ids:
                if fid not in fixtures:
                    raise ValueError("case %s: unknown fixture %s" % (case["id"], fid))
                rel = fixtures[fid]["path"]
                if not rel.startswith("fixtures/") or ".." in rel.split("/"):
                    raise ValueError("unsafe fixture path: %r" % rel)
                emit("%s/%s" % (base, rel), _read_fixture(kit_dir, rel))
            emit(base + "/case.yaml",
                 'schema_version: "1.1"\nname: %s\ncontext:\n  add_dirs: [fixtures]\n'
                 % _q(case["id"]))
        head = _frontmatter([("name", _q(case["id"])), ("tags", "[%s]" % tag),
                             ("max_turns", str(MAX_TURNS)),
                             ("allowed_tools", "[%s]" % ", ".join(ALLOWED_TOOLS))])
        emit(base + "/prompt.md", head + "\n" + prompt + "\n")
        for gid, pairs, body in graders:
            text = _frontmatter(pairs) + ("\n" + body if body else "")
            emit("%s/graders/%s.md" % (base, gid), text)
    return sorted(written)


def main(argv):
    ap = argparse.ArgumentParser(prog="export_kit.py")
    ap.add_argument("--work-dir", required=True)
    ap.add_argument("--skill", required=True)
    ap.add_argument("--format", required=True)
    args = ap.parse_args(argv)
    key = args.skill
    if not _KEY_RE.match(key) or ".." in key:
        raise UsageError("invalid skill key %r" % key)
    if args.format not in FORMATS:
        raise UsageError("unsupported format %r" % args.format)
    w = args.work_dir
    try:
        kit_root = load_run(w)["kit_dir"]
        kit_dir = os.path.join(kit_root, key)
        if os.path.islink(kit_dir) or os.path.islink(os.path.join(kit_dir, "kit.json")):
            raise ValueError("refusing to follow symlink")
        kit = read_json(os.path.join(kit_dir, "kit.json"))
        try:
            name = read_json(os.path.join(skill_work_dir(w, key), "result.json"))["skill"]
        except (OSError, ValueError, KeyError):
            name = kit.get("skill") or key
    except (OSError, ValueError, KeyError) as e:
        raise ValidationFailed(["cannot read inputs: %s" % e], {"retryable": False})
    errors = validate_against(kit, "kit")
    if errors:
        raise ValidationFailed(errors, {"retryable": False})
    try:
        files = export_plugin_eval(kit, kit_dir, name)
    except (OSError, ValueError, KeyError) as e:
        raise ValidationFailed(["export failed: %s" % e], {"retryable": False})
    sys.stdout.write(json.dumps({"ok": True, "format": args.format, "kit_dir": kit_dir,
                                 "files": files}) + "\n")
    return 0


if __name__ == "__main__":
    run_main(main)
