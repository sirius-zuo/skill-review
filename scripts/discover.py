"""Discovery: walk a root, detect skills, build the manifest (Python 3.7 stdlib only).

Symlinks are never followed; nothing outside the root is read.
"""
import os
import stat

from common import read_text, sanitize_name, scoring
from frontmatter import parse_skill_file

FIXTURE_DIRS = {"tests", "test", "fixtures", "fixture", "examples", "example",
                "samples", "sample"}
SKILL_FILE_NAMES = ("SKILL.md", "skill.md")


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
