"""Deterministic lint rules for SKILL.md files (Python 3.7 stdlib only).

Thresholds come from scoring()["lint"]; rule metadata from rules/lint-rules.json.
Files are read only through discover._read_regular, so symlinks and non-regular
files are never read and nothing outside the root is touched.
"""
import argparse
import os
import posixpath
import re

from common import (EXIT_OK, load_manifest, load_rules, reviewable_skills,
                    run_main, sanitize_name, scoring, skill_work_dir, write_json)
from discover import _read_regular, extract_references
from frontmatter import parse_skill_file

NAME_OK_RE = re.compile(r"^[a-z0-9-]+$")
XML_TAG_RE = re.compile(r"</?[A-Za-z][^>]*>")
PERSON_RE = re.compile(r"\b(I can|I will|you can|help me)\b", re.I)
WHEN_RE = re.compile(r"use when|when the user|whenever|use this|use for", re.I)
LIST_ITEM_RE = re.compile(r"^\s*([-*]|\d+\.)\s+\S")
PATH_RE = re.compile(r"[A-Za-z0-9_.-]+\\[A-Za-z0-9_.-]+(\\[A-Za-z0-9_.-]+)*\.[A-Za-z0-9]{1,5}\b")
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
TIME_RES = [
    re.compile(r"\bas of (19|20)\d{2}\b", re.I),
    re.compile(r"\b(before|after|until|since) (%s) (19|20)\d{2}\b" % MONTHS, re.I),
    re.compile(r"\bcurrently\b[^.\n]{0,40}\bv?\d+\.\d+", re.I),
]
TOOL_RE = re.compile(r"[A-Za-z_][\w.-]*(?:\([^)]*\))?")
VAGUE_NAMES = ("helper", "utils", "tools", "documents", "data", "files")
NAME_CONFLICT_RE = re.compile(r"^name '(.*)' used by: (.*)$", re.S)

_RULES = None


def rules_by_id():
    global _RULES
    if _RULES is None:
        _RULES = {r["id"]: r for r in load_rules("lint-rules")["rules"]}
    return _RULES


def _split_lines(text):
    """Lines as an editor numbers them: split on LF (CRLF-safe), no phantom final line."""
    lines = [ln[:-1] if ln.endswith("\r") else ln for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _str(v):
    return v if isinstance(v, str) else ""


def _bash_unrestricted(value):
    items = value if isinstance(value, list) else [value]
    for item in items:
        if not isinstance(item, str):
            continue
        for tok in TOOL_RE.findall(item):
            if tok == "Bash":
                return True
            m = re.match(r"^Bash\(\s*(.*?)\s*\)$", tok)
            if m and m.group(1) in ("*", "* *"):
                return True
    return False


def _name_conflicts(manifest, skill):
    mine = sanitize_name(skill["name"])
    for w in manifest.get("warnings", []):
        if w.get("code") != "NAME_CONFLICT":
            continue
        m = NAME_CONFLICT_RE.match(w.get("detail", ""))
        if m and m.group(1) == mine and skill["dir"] in m.group(2).split(", "):
            return True
    return False


def lint_skill(manifest, skill, root):
    th = scoring()["lint"]
    limits = scoring()["limits"]
    rules = rules_by_id()
    real_root = os.path.realpath(root)
    findings = []

    def add(rule_id, file, line, detail=None):
        r = rules[rule_id]
        if r.get("stage", "lint") != "lint":
            return
        msg = r["message"] + (" (%s)" % detail if detail else "")
        findings.append({"rule_id": rule_id, "severity": r["severity"], "category": r["category"],
                         "heuristic": r["heuristic"], "platform": r["platform"], "file": file,
                         "line": max(1, line), "message": msg, "fix": r["fix"], "why": r["why"]})

    sf = skill["skill_file"]
    if posixpath.basename(sf) == "skill.md":
        add("L-FILE-01", sf, 1)
    if skill["kind"] == "skill" and _name_conflicts(manifest, skill):
        add("L-UNIQ-01", sf, 1, "name '%s'" % skill["name"])

    text = _read_regular(real_root, sf, limits["ingest_max_bytes"])
    if text is None:
        add("L-FM-01", sf, 1, "SKILL.md is not a readable regular file")
        return {"findings": findings, "meta": {}}
    parsed = parse_skill_file(text)
    meta = parsed.meta if isinstance(parsed.meta, dict) else {}

    if parsed.error is not None or parsed.meta is None:
        add("L-FM-01", sf, 1, parsed.error)
    else:
        _frontmatter_rules(add, sf, meta, skill, real_root, th)

    body_lines = _split_lines(parsed.body)
    start = parsed.body_start_line
    if len(body_lines) > th["body_max_lines"]:
        add("L-SIZE-01", sf, start, "%d lines, limit %d" % (len(body_lines), th["body_max_lines"]))
    if len(parsed.body) / 4 > th["body_max_tokens"]:
        add("L-SIZE-02", sf, start, "about %d tokens, limit %d"
            % (len(parsed.body) // 4, th["body_max_tokens"]))
    path_hit = False
    time_hit = False
    for i, line in enumerate(body_lines):
        if not path_hit and PATH_RE.search(line):
            path_hit = True
            add("L-PATH-01", sf, start + i, PATH_RE.search(line).group(0))
        if not time_hit:
            for rx in TIME_RES:
                m = rx.search(line)
                if m:
                    time_hit = True
                    add("L-TIME-01", sf, start + i, m.group(0))
                    break

    _reference_rules(add, skill, real_root, text, th, limits)
    return {"findings": findings, "meta": meta}


def _frontmatter_rules(add, sf, meta, skill, real_root, th):
    name = meta.get("name")
    if not isinstance(name, str) or not name.strip():
        add("L-NAME-01", sf, 1)
    else:
        name = name.strip()
        if (len(name) > th["name_max"] or not NAME_OK_RE.match(name)
                or name.startswith("-") or name.endswith("-") or "--" in name):
            add("L-NAME-02", sf, 1, name)
        folder = posixpath.basename(skill["dir"]) if skill["dir"] != "." \
            else os.path.basename(real_root)
        if name != folder:
            add("L-NAME-03", sf, 1, "name '%s', folder '%s'" % (name, folder))
        low = name.lower()
        if "anthropic" in low or "claude" in low:
            add("L-NAME-04", sf, 1, name)
        if low in VAGUE_NAMES:
            add("L-NAME-05", sf, 1, name)
        if XML_TAG_RE.search(name):
            add("L-DESC-04", sf, 1, "in name")
    if "allowed-tools" in meta and _bash_unrestricted(meta["allowed-tools"]):
        add("L-CC-01", sf, 1)
    desc = _str(meta.get("description")).strip()
    if not desc:
        add("L-DESC-01", sf, 1)
        return
    if len(desc) > th["desc_max"]:
        add("L-DESC-02", sf, 1, "%d characters, limit %d" % (len(desc), th["desc_max"]))
    combined = len(desc) + len(_str(meta.get("when_to_use")))
    if combined > th["desc_combined_max"]:
        add("L-DESC-03", sf, 1, "%d characters, limit %d" % (combined, th["desc_combined_max"]))
    if XML_TAG_RE.search(desc):
        add("L-DESC-04", sf, 1, "in description")
    if PERSON_RE.search(desc):
        add("L-DESC-05", sf, 1, PERSON_RE.search(desc).group(0))
    if len(desc) < th["desc_min"]:
        add("L-DESC-06", sf, 1, "%d characters, minimum %d" % (len(desc), th["desc_min"]))
    if not WHEN_RE.search(desc):
        add("L-DESC-07", sf, 1)


def _ref_line(from_text, kind, target):
    for i, line in enumerate(_split_lines(from_text), 1):
        for k, t, _ in extract_references(line):
            if k == kind and t == target:
                return i
    return 1


def _reference_rules(add, skill, real_root, skill_text, th, limits):
    texts = {skill["skill_file"]: skill_text}

    def text_of(rel):
        if rel not in texts:
            texts[rel] = _read_regular(real_root, rel, limits["ingest_max_bytes"])
        return texts[rel]

    seen03 = set()
    seen04 = set()
    for ref in skill["references"]:
        src = ref["from"]
        line = _ref_line(text_of(src) or "", ref["kind"], ref["target"])
        if not ref["inside_root"]:
            add("L-REF-02", src, line, ref["target"])
            continue
        if not ref["exists"]:
            add("L-REF-01", src, line, ref["target"])
            continue
        res = ref["resolved"]
        if ref["depth"] >= 2 and res not in seen03:
            seen03.add(res)
            add("L-REF-03", src, line, res)
        if res not in seen04:
            seen04.add(res)
            body = text_of(res)
            if body is None:
                continue
            body_lines = _split_lines(body)
            if len(body_lines) > th["ref_toc_lines"]:
                window = body_lines[:th["toc_window_lines"]]
                if sum(1 for ln in window if LIST_ITEM_RE.match(ln)) < 3:
                    add("L-REF-04", res, 1, "%d lines" % len(body_lines))


def main(argv):
    ap = argparse.ArgumentParser(prog="lint.py")
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.work_dir)
    for skill in reviewable_skills(manifest):
        res = lint_skill(manifest, skill, manifest["root"])
        out = {"schema_version": 1, "engine": "script",
               "skill": {k: skill[k] for k in ("key", "name", "dir", "skill_file")},
               "meta": res["meta"], "findings": res["findings"]}
        write_json(os.path.join(skill_work_dir(args.work_dir, skill["key"]), "lint.json"),
                   out, "lint")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
