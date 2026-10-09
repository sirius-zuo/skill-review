"""Security pattern scan for skills (Python 3.7 stdlib only).

Pattern text is data: files are read as UTF-8 (errors replaced) through
discover._read_regular, so symlinks and non-regular files are never read, and
nothing in the scanned tree is ever executed.
"""
import argparse
import bisect
import fnmatch
import os
import re
import unicodedata

from common import (EXIT_OK, load_manifest, load_rules, reviewable_skills,
                    run_main, scoring, skill_work_dir, write_json)
from discover import _read_regular
from frontmatter import parse_skill_file
from lint import _split_lines

FLAGS = ("scripts", "shell", "network_read", "network_write", "file_write", "irreversible",
         "credentials", "invokes_agents", "ingests_untrusted", "multi_step", "fans_out",
         "composable_output", "long_running", "has_siblings", "large_body", "has_references")
NUMBERED_RE = re.compile(r"^[ \t]{0,3}\d{1,9}[.)][ \t]+\S", re.M)
MIN_NUMBERED = 3

EXEC_EXTS = frozenset((".sh", ".bash", ".zsh", ".py", ".js", ".mjs", ".cjs", ".ts",
                       ".rb", ".ps1", ".pl"))
EXCERPT_MAX = 120
COMMENT_MAX = 2000

SYMLINK_ID = "SEC-SYMLINK-ESCAPE"
SYMLINK_FIX = "Remove the symlink, or point it at a file inside the skill directory."
SYMLINK_WHY = ("A symlink that resolves outside the skill lets the skill expose or "
               "read files such as keys and credentials that were never part of it.")

_PATTERNS = None
_CAPS = None


def load_patterns():
    global _PATTERNS
    if _PATTERNS is None:
        loaded = []
        for p in load_rules("security-patterns")["patterns"]:
            p = dict(p)
            flags = re.M | (re.S if p["multiline"] else 0) | (re.I if p["ignore_case"] else 0)
            p["_re"] = re.compile(p["regex"], flags)
            loaded.append(p)
        _PATTERNS = loaded
    return _PATTERNS


def load_capabilities():
    global _CAPS
    if _CAPS is None:
        caps = {}
        for flag, spec in load_rules("capabilities").items():
            spec = dict(spec)
            spec["rules"] = [dict(r, _re=re.compile(r["regex"], re.M | re.I)) for r in spec["rules"]]
            caps[flag] = spec
        _CAPS = caps
    return _CAPS


def is_executable(path, head):
    return os.path.splitext(path)[1].lower() in EXEC_EXTS or head.startswith("#!")


def _escape_char(ch):
    cp = ord(ch)
    if ch != "\t" and (unicodedata.category(ch) in ("Cc", "Cf") or 0xE0000 <= cp <= 0xE007F):
        return "\\u%04x" % cp if cp <= 0xFFFF else "\\U%08x" % cp
    return ch


def make_excerpt(line_text, start, end, redact, max_chars):
    end = min(end, len(line_text))
    if redact and end > start:
        span = line_text[start:end]
        line_text = line_text[:start] + span[:4] + "***" + line_text[end:]
    start = min(start, max(len(line_text) - 1, 0))
    pieces = [_escape_char(c) for c in line_text]
    if not pieces:
        return ""
    lo, hi = start, start + 1
    used = len(pieces[start])
    left_budget = max_chars // 4
    while lo > 0 and used + len(pieces[lo - 1]) <= left_budget:
        lo -= 1
        used += len(pieces[lo])
    while hi < len(pieces) and used + len(pieces[hi]) <= max_chars:
        used += len(pieces[hi])
        hi += 1
    while lo > 0 and used + len(pieces[lo - 1]) <= max_chars:
        lo -= 1
        used += len(pieces[lo])
    return "".join(pieces[lo:hi])[:max_chars]


def _comment_matches(text, regex):
    """Yield (start, end) of each closed HTML comment whose body matches regex.

    Linear: comments are located with str.find and bodies capped at COMMENT_MAX.
    """
    pos = 0
    close = -1
    while True:
        start = text.find("<!--", pos)
        if start < 0:
            return
        if close < start + 4:
            close = text.find("-->", start + 4)
            if close < 0:
                return
        if close - start - 4 <= COMMENT_MAX:
            if regex.search(text, start + 4, close):
                yield start, close + 3
            pos = close + 3
        else:
            pos = start + 4


def scan_text(rel_path, text, executable, patterns):
    base = rel_path.rsplit("/", 1)[-1]
    hits = []
    line_starts = None
    for p in patterns:
        if p["executable_only"] and not executable:
            continue
        if p["non_executable_only"] and executable:
            continue
        if p["file_globs"] and not any(fnmatch.fnmatchcase(base, g) for g in p["file_globs"]):
            continue
        seen = set()
        if p.get("html_comment_body"):
            spans = _comment_matches(text, p["_re"])
        else:
            spans = ((m.start(), m.end()) for m in p["_re"].finditer(text))
        for m_start, m_end in spans:
            if line_starts is None:
                line_starts = [0] + [i + 1 for i, c in enumerate(text) if c == "\n"]
            idx = bisect.bisect_right(line_starts, m_start) - 1
            if idx in seen:
                continue
            seen.add(idx)
            line_begin = line_starts[idx]
            line_end = text.find("\n", line_begin)
            line_text = text[line_begin:line_end if line_end >= 0 else len(text)]
            s = m_start - line_begin
            e = m_end - line_begin
            hits.append({
                "pattern_id": p["id"], "family": p["family"], "severity": p["severity"],
                "file": rel_path, "line": idx + 1,
                "excerpt": make_excerpt(line_text, s, e, p["family"] == "secrets", EXCERPT_MAX),
                "fix": p["fix"], "why": p["why"]})
    return hits


def symlink_hits(entries, root):
    root = os.path.normpath(root)
    hits = []
    for e in entries:
        target = e.get("symlink")
        if not target:
            continue
        base = os.path.dirname(os.path.join(root, e["path"].replace("/", os.sep)))
        resolved = os.path.normpath(os.path.join(base, target))
        if resolved == root or resolved.startswith(root.rstrip(os.sep) + os.sep):
            continue
        text = "-> " + target
        hits.append({
            "pattern_id": SYMLINK_ID, "family": "credential-access", "severity": "critical",
            "file": e["path"], "line": 0,
            "excerpt": make_excerpt(text, 0, len(text), False, EXCERPT_MAX),
            "fix": SYMLINK_FIX, "why": SYMLINK_WHY})
    return hits


def _scan_entries(entries, scope, real_root, limit, patterns):
    hits = []
    for e in entries:
        if e.get("symlink"):
            continue
        text = _read_regular(real_root, e["path"], limit)
        if text is None:
            continue
        executable = is_executable(e["path"], text[:2])
        for h in scan_text(e["path"], text, executable, patterns):
            h["scope"] = scope
            hits.append(h)
    return hits


def scan_skill(manifest, skill, root):
    limit = scoring()["limits"]["scan_max_bytes"]
    patterns = load_patterns()
    real_root = os.path.realpath(root)
    hits = []
    for entries, scope in ((skill["files"], "skill"), (manifest.get("repo_files", []), "repo")):
        hits.extend(_scan_entries(entries, scope, real_root, limit, patterns))
        for h in symlink_hits(entries, real_root):
            h["scope"] = scope
            hits.append(h)
    hits.sort(key=lambda h: (h["file"], h["line"], h["pattern_id"]))
    for i, h in enumerate(hits, 1):
        h["hit_id"] = "H%03d" % i
    order = ("hit_id", "pattern_id", "family", "severity", "scope", "file", "line",
             "excerpt", "fix", "why")
    return {"hits": [{k: h[k] for k in order} for h in hits]}


def _tools_mention_bash(value):
    items = value if isinstance(value, list) else [value]
    return any(isinstance(i, str) and "Bash" in i for i in items)


def inventory(manifest, skill, root, hits):
    """Map each flag to {"value", "sources"}; true beats suspected beats the default."""
    limit = scoring()["limits"]["scan_max_bytes"]
    real_root = os.path.realpath(root)
    skill_text = _read_regular(real_root, skill["skill_file"], limit)
    parsed = parse_skill_file(skill_text) if skill_text is not None else None
    body = parsed.body if parsed else ""
    meta = parsed.meta if parsed and isinstance(parsed.meta, dict) else {}
    exec_files = []
    for e in skill["files"]:
        if e.get("symlink"):
            continue
        text = skill_text if e["path"] == skill["skill_file"] else _read_regular(
            real_root, e["path"], limit)
        if text is not None and is_executable(e["path"], text[:2]):
            exec_files.append(text)
    texts = {"executable": exec_files, "skill_md": [body], "any": exec_files + [body]}
    skill_hits = [h for h in hits if h.get("scope", "skill") == "skill"]
    found = {}  # flag -> {value: [sources]}

    def add(flag, value, source):
        found.setdefault(flag, {}).setdefault(value, []).append(source)

    for flag, spec in load_capabilities().items():
        for r in spec["rules"]:
            if any(r["_re"].search(t) for t in texts[r["where"]]):
                add(flag, r["value"], r["id"])
        for h in skill_hits:
            if h["family"] in spec["families"]:
                add(flag, "true", h["hit_id"])
    if exec_files:
        add("scripts", "true", "CAP-SCRIPTS-1")
    if _tools_mention_bash(meta.get("allowed-tools")):
        add("shell", "true", "CAP-SHELL-4")
    if len(NUMBERED_RE.findall(body)) >= MIN_NUMBERED:
        add("multi_step", "suspected", "CAP-MULTI_STEP-2")
    if len(reviewable_skills(manifest)) > 1:
        add("has_siblings", "true", "CAP-HAS_SIBLINGS-1")
    if len(_split_lines(body)) > scoring()["lint"]["large_body_lines"]:
        add("large_body", "true", "CAP-LARGE_BODY-1")
    if any(r.get("exists") for r in skill.get("references", [])):
        add("has_references", "true", "CAP-HAS_REFERENCES-1")

    out = {}
    for flag in FLAGS:
        got = found.get(flag, {})
        value = next((v for v in ("true", "suspected") if v in got),
                     load_capabilities()[flag]["default"])
        out[flag] = {"value": value, "sources": got.get(value, [])}
    return out


def main(argv):
    ap = argparse.ArgumentParser(prog="scan.py")
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    manifest = load_manifest(args.work_dir)
    for skill in reviewable_skills(manifest):
        res = scan_skill(manifest, skill, manifest["root"])
        out = {"schema_version": 1, "engine": "script",
               "skill": {k: skill[k] for k in ("key", "name", "dir", "skill_file")},
               "hits": res["hits"],
               "inventory": inventory(manifest, skill, manifest["root"], res["hits"])}
        write_json(os.path.join(skill_work_dir(args.work_dir, skill["key"]), "scan.json"),
                   out, "scan")
    return EXIT_OK


if __name__ == "__main__":
    run_main(main)
