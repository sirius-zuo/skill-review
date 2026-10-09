"""Discovery: walk a root, detect skills, build the manifest (Python 3.7 stdlib only).

Symlinks are never followed; nothing outside the root is read.
"""
import argparse
import json
import os
import posixpath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime

from common import (EXIT_OK, UsageError, ValidationFailed, load_run, read_text,
                    run_main, sanitize_name, scoring, write_json, reviewable_skills)
from frontmatter import parse_skill_file

FIXTURE_DIRS = {"tests", "test", "fixtures", "fixture", "examples", "example",
                "samples", "sample"}
SKILL_FILE_NAMES = ("SKILL.md", "skill.md")
CLONE_PREFIX = "skill-review-clone-"
CLONE_MARKER = ".skill-review-clone"
WORK_MARKER = ".skill-review-work"
STDOUT_NAME_MAX = 64
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_RE = re.compile(r"`([^`\n]+)`")
CODE_PATH_RE = re.compile(r"^[A-Za-z0-9_./-]+\.[A-Za-z0-9]{1,5}$")
SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
GITHUB_RE = re.compile(r"^https://github\.com/[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9._-]+?(\.git)?/?$")
KIT_RESULT_NAMES = ("kit-results.json", "benchmark.json")


def _rel(path, root):
    return os.path.relpath(path, root).replace(os.sep, "/")


def _is_fixture(rel_dir):
    if rel_dir == ".":
        return False
    return any(p.lower() in FIXTURE_DIRS for p in rel_dir.split("/"))


def _skill_file_in(names, directory):
    for want in SKILL_FILE_NAMES:
        if want in names and os.path.isfile(os.path.join(directory, want)) \
                and not os.path.islink(os.path.join(directory, want)):
            return want
    return None


def _walk(root):
    """Return (sorted relative file paths incl. symlinks, {rel_dir: skill file name})."""
    files = []
    skill_dirs = {}
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = sorted(d for d in dirnames if d != ".git")
        rel_dir = _rel(dirpath, root)
        found = _skill_file_in(set(filenames), dirpath)
        if found:
            skill_dirs[rel_dir] = found
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                dirnames.remove(d)
                files.append(_rel(full, root))
        for n in filenames:
            files.append(_rel(os.path.join(dirpath, n), root))
    return sorted(files), skill_dirs


def _file_entry(root, rel, limits):
    full = os.path.join(root, rel.replace("/", os.sep))
    entry = {"path": rel, "size": 0, "binary": False, "symlink": None,
             "hidden": any(p.startswith(".") for p in rel.split("/")),
             "bundled": False, "skip_reason": None}
    try:
        st = os.lstat(full)
    except OSError:
        entry["skip_reason"] = "unreadable"
        return entry
    if stat.S_ISLNK(st.st_mode):
        try:
            entry["symlink"] = os.readlink(full)
        except OSError:
            entry["symlink"] = ""
        entry["skip_reason"] = "symlink"
        return entry
    if not stat.S_ISREG(st.st_mode):
        entry["skip_reason"] = "special"
        return entry
    entry["size"] = st.st_size
    try:
        with open(full, "rb") as f:
            entry["binary"] = b"\x00" in f.read(limits["binary_sniff_bytes"])
    except OSError:
        entry["skip_reason"] = "unreadable"
        return entry
    if entry["binary"]:
        entry["skip_reason"] = "binary"
    elif st.st_size > limits["bundle_max_bytes"]:
        entry["skip_reason"] = "too_large"
    entry["bundled"] = entry["skip_reason"] is None
    return entry


def _owner(rel_path, owner_dirs):
    """Deepest non-fixture skill dir containing rel_path, or None."""
    best = None
    for d in owner_dirs:
        if d == "." or rel_path.startswith(d + "/"):
            if best is None or len(d) > len(best) or best == ".":
                best = d
    return best


def extract_references(text):
    """Return [(kind, target, line)] for markdown links and backticked relative paths."""
    found = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line_refs = []
        for m in LINK_RE.finditer(line):
            target = m.group(1)
            if SCHEME_RE.match(target) or target.startswith("mailto:") or target.startswith("#"):
                continue
            target = target.split("#", 1)[0]
            if target:
                line_refs.append((m.start(), "link", target))
        for m in CODE_RE.finditer(line):
            token = m.group(1)
            if CODE_PATH_RE.match(token) and not token.startswith("-"):
                line_refs.append((m.start(), "code", token))
        for _, kind, target in sorted(line_refs):
            found.append((kind, target, lineno))
    return found


def _lstat_in_root(real_root, rel):
    """lstat of rel under root; None if missing or reached through a symlinked directory."""
    cur = real_root
    parts = [p for p in rel.split("/") if p]
    st = None
    for i, part in enumerate(parts):
        cur = os.path.join(cur, part)
        try:
            st = os.lstat(cur)
        except (OSError, ValueError):
            return None
        if stat.S_ISLNK(st.st_mode) and i < len(parts) - 1:
            return None
    return st


def _read_regular(real_root, rel, limit):
    st = _lstat_in_root(real_root, rel)
    if st is None or not stat.S_ISREG(st.st_mode):
        return None
    try:
        return read_text(os.path.join(real_root, rel.replace("/", os.sep)), limit)
    except OSError:
        return None


def _collect_references(real_root, skill, text, limits, warnings):
    refs = []
    dropped = []
    depth1 = set()
    present = {e["path"] for e in skill["files"]}

    def visit(from_rel, from_text, depth):
        descend = []
        for kind, target, _line in extract_references(from_text):
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(from_rel), target))
            if depth == 2 and (not resolved.endswith(".md") or resolved in depth1):
                continue
            inside = not (resolved == ".." or resolved.startswith("../") or resolved.startswith("/"))
            st = _lstat_in_root(real_root, resolved) if inside else None
            if kind == "code" and st is None:
                continue
            if any(r["from"] == from_rel and r["kind"] == kind and r["resolved"] == resolved
                   for r in refs):
                continue
            refs.append({"from": from_rel, "kind": kind, "target": target, "resolved": resolved,
                         "exists": st is not None, "inside_root": inside, "depth": depth})
            if depth == 1:
                depth1.add(resolved)
            if st is None or stat.S_ISDIR(st.st_mode) or resolved.startswith(".git/"):
                continue
            if resolved not in present:
                present.add(resolved)
                if len(skill["files"]) >= limits["max_files_per_skill"]:
                    dropped.append(resolved)
                    skill["files"].append({
                        "path": resolved, "size": 0, "binary": False, "symlink": None,
                        "hidden": any(p.startswith(".") for p in resolved.split("/")),
                        "bundled": False, "skip_reason": "file_limit"})
                else:
                    skill["files"].append(_file_entry(real_root, resolved, limits))
            if depth == 1 and resolved.endswith(".md"):
                descend.append(resolved)
        for rel in descend:
            body = _read_regular(real_root, rel, limits["ingest_max_bytes"])
            if body is not None:
                visit(rel, body, 2)

    visit(skill["skill_file"], text, 1)
    skill["references"] = refs
    if dropped:
        warnings.append({"code": "FILE_LIMIT",
                         "detail": "%s: %d referenced files not added (limit %d): %s"
                         % (skill["dir"], len(dropped), limits["max_files_per_skill"],
                            ", ".join(dropped[:5]))})


def _is_eval_result(real_root, entry, limits):
    base = entry["path"].rsplit("/", 1)[-1]
    if entry["skip_reason"] not in (None, "too_large") or not base.endswith(".json"):
        return False
    if base in KIT_RESULT_NAMES:
        return True
    if entry["size"] > limits["ingest_max_bytes"]:
        return False
    body = _read_regular(real_root, entry["path"], limits["ingest_max_bytes"])
    if body is None:
        return False
    try:
        data = json.loads(body)
    except (ValueError, RecursionError):  # hostile or deeply nested JSON is simply not a result
        return False
    cases = data.get("cases") if isinstance(data, dict) else None
    return isinstance(cases, list) and any(isinstance(c, dict) and "arms" in c for c in cases)


def build_manifest(root, self_dir=None, source=None):
    limits = scoring()["limits"]
    real_root = os.path.realpath(root)
    all_files, skill_dirs = _walk(real_root)
    skills = []
    warnings = []
    texts = {}
    for d in sorted(skill_dirs):
        skill_file = (d + "/" if d != "." else "") + skill_dirs[d]
        sf_path = os.path.join(real_root, skill_file.replace("/", os.sep))
        try:
            if stat.S_ISREG(os.lstat(sf_path).st_mode):
                texts[d] = read_text(sf_path, limits["ingest_max_bytes"])
                continue
        except OSError:
            pass
        warnings.append({"code": "UNREADABLE_FILE", "detail": skill_file})
    dirs = sorted(texts)
    owner_dirs = [d for d in dirs if not _is_fixture(d)]

    for d in dirs:
        fixture = _is_fixture(d)
        skill_file = (d + "/" if d != "." else "") + skill_dirs[d]
        text = texts[d]
        meta = parse_skill_file(text).meta
        name = meta.get("name") if isinstance(meta, dict) else None
        if not isinstance(name, str) or not name.strip():
            name = os.path.basename(real_root) if d == "." else os.path.basename(d)
        parent = _owner(skill_file, [o for o in owner_dirs if o != d])
        skills.append({"key": "", "name": name.strip(), "dir": d, "skill_file": skill_file,
                       "kind": "fixture" if fixture else "skill", "parent": parent,
                       "files": [], "references": [], "eval_result_files": []})

    by_dir = {s["dir"]: s for s in skills}
    repo_files = []
    per_skill = {d: [] for d in owner_dirs}
    for rel in all_files:
        owner = _owner(rel, owner_dirs)
        if owner is None:
            repo_files.append(_file_entry(real_root, rel, limits))
        else:
            per_skill[owner].append(rel)
    for d, rels in per_skill.items():
        entries = []
        for i, rel in enumerate(rels):
            if i >= limits["max_files_per_skill"]:
                e = {"path": rel, "size": 0, "binary": False, "symlink": None,
                     "hidden": any(p.startswith(".") for p in rel.split("/")),
                     "bundled": False, "skip_reason": "file_limit"}
            else:
                e = _file_entry(real_root, rel, limits)
            entries.append(e)
        by_dir[d]["files"] = entries
        for e in entries:
            if e["skip_reason"] == "unreadable":
                warnings.append({"code": "UNREADABLE_FILE", "detail": e["path"]})
        if len(rels) > limits["max_files_per_skill"]:
            warnings.append({"code": "FILE_LIMIT", "detail": "%s: %d files, only the first %d are bundled"
                             % (d, len(rels), limits["max_files_per_skill"])})

    for d in owner_dirs:
        _collect_references(real_root, by_dir[d], texts[d], limits, warnings)
        by_dir[d]["eval_result_files"] = [e["path"] for e in by_dir[d]["files"]
                                          if _is_eval_result(real_root, e, limits)]

    seen = {}
    for i, s in enumerate(skills):
        s["key"] = "%02d-%s" % (i, sanitize_name(s["name"]))
        if s["kind"] == "skill":
            seen.setdefault(sanitize_name(s["name"]), []).append(s["dir"])
    for sname in sorted(seen):
        if len(seen[sname]) > 1:
            warnings.append({"code": "NAME_CONFLICT",
                             "detail": "name '%s' used by: %s" % (sname, ", ".join(seen[sname]))})

    for e in repo_files:
        if e["skip_reason"] == "unreadable":
            warnings.append({"code": "UNREADABLE_FILE", "detail": e["path"]})

    self_review = bool(self_dir) and real_root == os.path.realpath(self_dir)
    return {"schema_version": 1, "engine": "script", "root": real_root,
            "source": source or {"type": "local", "url": None, "clone_dir": None},
            "self_review": self_review, "skills": skills,
            "repo_files": repo_files, "warnings": warnings}


# --- targets, cloning, run setup -------------------------------------------

class CloneError(Exception):
    def __init__(self, exit_code, stderr):
        Exception.__init__(self, "git clone failed (exit %s): %s" % (exit_code, stderr.strip()))
        self.exit_code = exit_code
        self.stderr = stderr


def validate_target(target):
    if GITHUB_RE.match(target) and "\n" not in target:
        return "github", target
    if os.path.isdir(target) and os.access(target, os.R_OK | os.X_OK):
        return "local", os.path.realpath(target)
    raise UsageError("The path `%s` is not a valid local path or GitHub URL. "
                     "Please provide a valid path." % target)


def clone_repo(url, tmp_parent=None):
    tmp = tempfile.mkdtemp(prefix=CLONE_PREFIX, dir=tmp_parent)
    repo = os.path.join(tmp, "repo")
    try:
        open(os.path.join(tmp, CLONE_MARKER), "w").close()
        env = dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_LFS_SKIP_SMUDGE="1")
        try:
            proc = subprocess.run(
                ["git", "clone", "--depth", "1", "--no-recurse-submodules",
                 "-c", "core.symlinks=false", "--", url, repo],
                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        except OSError as e:
            raise CloneError(127, str(e))
        if proc.returncode != 0:
            raise CloneError(proc.returncode, proc.stderr)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return tmp, repo


def _is_clone_dir(path):
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            return False
        return stat.S_ISREG(os.lstat(os.path.join(path, CLONE_MARKER)).st_mode)
    except OSError:
        return False


def cleanup_stale_clones(tmp_parent, now, max_age_hours):
    removed = []
    try:
        names = sorted(os.listdir(tmp_parent))
    except OSError:
        return removed
    for name in names:
        path = os.path.join(tmp_parent, name)
        if not name.startswith(CLONE_PREFIX) or not _is_clone_dir(path):
            continue
        try:
            age_ref = min(os.lstat(path).st_mtime, os.lstat(os.path.join(path, CLONE_MARKER)).st_mtime)
        except OSError:
            continue
        if now - age_ref > max_age_hours * 3600:
            shutil.rmtree(path, ignore_errors=True)
            removed.append(path)
    return removed


def default_run_dir(target_name, cwd, now):
    return os.path.join(cwd, "skill-reviews",
                        "%s-%s" % (sanitize_name(target_name), now.strftime("%Y%m%dT%H%M")))


def estimate_passes(n_skills, trials, no_kit, routing_will_run):
    return n_skills * trials + (0 if no_kit else n_skills) + (3 if routing_will_run else 0)


def _inside(path, root):
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _writable(path):
    cur = os.path.abspath(path)
    while not os.path.exists(cur):
        cur = os.path.dirname(cur)
    return os.path.isdir(cur) and os.access(cur, os.W_OK | os.X_OK)


def _has_work_marker(path):
    """True only for a real directory (not a symlink) holding a regular WORK_MARKER file."""
    try:
        if not stat.S_ISDIR(os.lstat(path).st_mode):
            return False
        return stat.S_ISREG(os.lstat(os.path.join(path, WORK_MARKER)).st_mode)
    except OSError:
        return False


def _stdout_name(name):
    """Skill names are untrusted frontmatter text: print only [a-z0-9-], at most 64 chars."""
    out = sanitize_name(name if isinstance(name, str) else "")[:STDOUT_NAME_MAX].strip("-")
    return out or "skill"


def _target_name(kind, value):
    if kind == "github":
        name = value.rstrip("/").rsplit("/", 1)[-1]
        return name[:-4] if name.endswith(".git") else name
    return os.path.basename(value.rstrip(os.sep)) or "skill"


def _run(args):
    limits = scoring()
    kind, value = validate_target(args.target)
    tmp_parent = args.tmp_parent or tempfile.gettempdir()
    cleanup_stale_clones(tmp_parent, time.time(), limits["limits"]["stale_clone_hours"])
    if kind == "github" and shutil.which("git") is None:
        raise UsageError("git is required to review a GitHub URL; clone it yourself and pass the local path.")
    clone_dir = None
    ok = False
    try:
        if kind == "github":
            try:
                clone_dir, root = clone_repo(value, tmp_parent)
            except CloneError as e:
                raise ValidationFailed(["git clone failed (exit %s): %s" % (e.exit_code, e.stderr.strip())])
        else:
            root = value
        name = _target_name(kind, value)
        now = datetime.now()
        run_dir = os.path.realpath(os.path.abspath(args.out)) if args.out \
            else default_run_dir(name, os.path.realpath(os.getcwd()), now)
        if _inside(run_dir, root) and not args.allow_inside_root:
            raise ValidationFailed(["The output directory `%s` is inside the reviewed root." % run_dir],
                                   {"confirm": "OUT_INSIDE_ROOT"})
        if root == os.path.realpath(args.self_dir) and not args.allow_self_review:
            raise ValidationFailed(["The target is the skill-review installation itself."],
                                   {"confirm": "SELF_REVIEW"})
        if not _writable(run_dir):
            raise ValidationFailed(["The output directory `%s` is not writable." % run_dir])
        work_dir = os.path.join(run_dir, "work")
        if os.path.lexists(work_dir):
            # Never reuse a work/ folder: cleanup deletes work/, so it must be one this run
            # created (carrying WORK_MARKER) and must not hold an earlier run's files.
            owner = ("an earlier skill-review run" if _has_work_marker(work_dir)
                     else "not created by skill-review")
            raise ValidationFailed([
                "The folder `%s` already exists (%s). skill-review will not reuse or delete it; "
                "give another `--out`." % (work_dir, owner)])
        source = {"type": kind, "url": value if kind == "github" else None, "clone_dir": clone_dir}
        manifest = build_manifest(root, self_dir=args.self_dir, source=source)
        skills = reviewable_skills(manifest)
        if not skills:
            raise ValidationFailed(["No SKILL.md found under %s." % value])
        routing = args.mode == "parallel" and not args.no_kit and not args.no_routing
        passes = estimate_passes(len(skills), args.trials, args.no_kit, routing)
        estimate = {"passes": passes, "ask": passes > limits["orchestration"]["ask_threshold_passes"]}
        try:
            os.makedirs(run_dir, exist_ok=True)
            os.mkdir(work_dir)
            fd = os.open(os.path.join(work_dir, WORK_MARKER),
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o644)
            os.close(fd)
        except OSError as e:
            raise ValidationFailed(["Cannot create `%s`: %s" % (work_dir, e)])
        write_json(os.path.join(work_dir, "manifest.json"), manifest, "manifest")
        write_json(os.path.join(work_dir, "run.json"), {
            "schema_version": 1, "engine": "script", "run_dir": run_dir, "work_dir": work_dir,
            "kit_dir": os.path.join(run_dir, "kit"), "target": value,
            "created": now.replace(microsecond=0).isoformat(), "self_dir": os.path.realpath(args.self_dir),
            "source": source,
            "args": {"mode": args.mode, "trials": args.trials, "no_kit": args.no_kit,
                     "no_routing": args.no_routing, "kit_format": args.kit_format,
                     "keep_work": args.keep_work},
            "estimate": estimate}, "run")
        sys.stdout.write(json.dumps({
            "schema_version": 1, "engine": "script", "ok": True, "run_dir": run_dir,
            "work_dir": work_dir,
            "skills": [{"key": s["key"], "name": _stdout_name(s["name"])} for s in skills],
            "estimate": estimate}, ensure_ascii=False) + "\n")
        ok = True
    finally:
        if clone_dir and not ok:
            shutil.rmtree(clone_dir, ignore_errors=True)
    return EXIT_OK


def _cleanup(args):
    try:
        run = load_run(args.work_dir)
    except (OSError, ValueError) as e:
        raise ValidationFailed(["Cannot read run.json in `%s` (%s); nothing was deleted."
                                % (args.work_dir, e)])
    removed = []
    clone_dir = (run.get("source") or {}).get("clone_dir")
    if clone_dir and os.path.basename(clone_dir).startswith(CLONE_PREFIX) and _is_clone_dir(clone_dir):
        shutil.rmtree(clone_dir, ignore_errors=True)
        removed.append(clone_dir)
    warnings = []
    if not run.get("args", {}).get("keep_work"):
        if _has_work_marker(args.work_dir):
            shutil.rmtree(args.work_dir, ignore_errors=True)
            removed.append(args.work_dir)
        else:
            warnings.append("Left `%s` in place: it has no %s marker, so skill-review did not "
                            "create it." % (args.work_dir, WORK_MARKER))
    out = {"schema_version": 1, "engine": "script", "ok": True, "removed": removed}
    if warnings:
        out["warnings"] = warnings
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    return EXIT_OK


def main(argv):
    ap = argparse.ArgumentParser(prog="discover.py")
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True
    r = sub.add_parser("run")
    r.add_argument("target")
    r.add_argument("--self-dir", required=True)
    r.add_argument("--out")
    r.add_argument("--mode", choices=["parallel", "single"], default="parallel")
    r.add_argument("--trials", type=int, default=1)
    r.add_argument("--no-kit", action="store_true")
    r.add_argument("--no-routing", action="store_true")
    r.add_argument("--kit-format", choices=["claude-plugin-eval"])
    r.add_argument("--keep-work", action="store_true")
    r.add_argument("--allow-self-review", action="store_true")
    r.add_argument("--allow-inside-root", action="store_true")
    r.add_argument("--tmp-parent")
    c = sub.add_parser("cleanup")
    c.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        if args.trials < 1:
            raise UsageError("--trials must be at least 1")
        return _run(args)
    return _cleanup(args)


if __name__ == "__main__":
    run_main(main)
