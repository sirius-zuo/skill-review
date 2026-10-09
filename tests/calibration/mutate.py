#!/usr/bin/env python3
"""Apply the calibration mutations (mutations.json) to temp copies of base skills.

Usage: mutate.py --corpus-dir D --out DIR [--mutations mutations.json] [--corpus corpus.json]
  D holds the downloaded Anthropic skills as D/<name>/ (see fetch_corpus.py). Fixture
  skills are resolved from the repository. Output goes to DIR/<MU-id>/<folder>/.
Python 3.7 stdlib only. The base is never modified; links are never followed.
"""
import argparse
import json
import os
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _write(path, text):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def _inside(root, rel):
    """Join rel under root, refusing absolute or escaping paths."""
    if os.path.isabs(rel) or ".." in rel.replace("\\", "/").split("/"):
        raise ValueError("unsafe mutation path: %r" % rel)
    return os.path.join(root, *rel.split("/"))


def _frontmatter_span(text):
    m = re.match(r"---\r?\n.*?\r?\n---\r?\n", text, re.S)
    return m.end() if m else 0


def _set_name(skill_md, name):
    text = _read(skill_md)
    end = _frontmatter_span(text)
    head, n = re.subn(r"^name:.*$", "name: " + name, text[:end], count=1, flags=re.M)
    if n != 1:
        raise ValueError("SKILL.md frontmatter has no name to align")
    _write(skill_md, head + text[end:])


def prepare_base(base_dir, mutation, out_dir):
    """Copy base_dir to out_dir as the comparison base for this mutation.

    For rename_folder the copy's frontmatter name is set to its folder name first, so the
    base never trips L-NAME-03 and only the renamed mutant can (a base whose name already
    differs from its folder, like good-skill, would make the mutation undetectable).
    """
    if os.path.lexists(out_dir):
        raise ValueError("output exists: %s" % out_dir)
    shutil.copytree(base_dir, out_dir, symlinks=True)
    if mutation["op"] == "rename_folder":
        _set_name(os.path.join(out_dir, "SKILL.md"), os.path.basename(out_dir.rstrip(os.sep)))
    return out_dir


def apply_mutation(base_dir, mutation, out_dir):
    """Copy base_dir to out_dir, apply the mutation, return the mutated skill dir."""
    op = mutation["op"]
    args = mutation.get("args", {})
    if op == "rename_folder":
        # Align name with the original folder (the prepared base), then rename the folder.
        folder = os.path.basename(out_dir.rstrip(os.sep))
        if folder == args["new_name"]:
            raise ValueError("rename_folder needs a new name that differs from the folder")
        out_dir = os.path.join(os.path.dirname(out_dir), args["new_name"])
        if os.path.lexists(out_dir):
            raise ValueError("output exists: %s" % out_dir)
        shutil.copytree(base_dir, out_dir, symlinks=True)
        _set_name(os.path.join(out_dir, "SKILL.md"), folder)
        return out_dir
    if os.path.lexists(out_dir):
        raise ValueError("output exists: %s" % out_dir)
    shutil.copytree(base_dir, out_dir, symlinks=True)
    skill_md = os.path.join(out_dir, "SKILL.md")

    if op == "edit_description":
        text = _read(skill_md)
        end = _frontmatter_span(text)
        head, rest = text[:end], text[end:]

        def sub(m):
            return re.sub(args["pattern"], args["replace"], m.group(0), count=1)
        new_head, n = re.subn(r"^description:.*$", sub, head, count=1, flags=re.M)
        if n != 1 or new_head == head:
            raise ValueError("edit_description did not change the description")
        _write(skill_md, new_head + rest)
    elif op == "append_script":
        path = _inside(out_dir, args["path"])
        old = _read(path) if os.path.isfile(path) else ""
        _write(path, old + args["text"])
    elif op == "append_skill_md":
        _write(skill_md, _read(skill_md) + args["text"])
    elif op == "insert_chars":
        lines = _read(skill_md).split("\n")
        i = args["line"] - 1
        if not 0 <= i < len(lines) or not lines[i]:
            raise ValueError("insert_chars needs a non-empty existing line")
        mid = len(lines[i]) // 2
        lines[i] = lines[i][:mid] + args["chars"] + lines[i][mid:]
        _write(skill_md, "\n".join(lines))
    elif op == "break_link":
        target = re.search(r"\]\(([^)]+)\)", args["text"]).group(1)
        if os.path.exists(_inside(out_dir, target)):
            raise ValueError("break_link target exists: " + target)
        _write(skill_md, _read(skill_md) + args["text"])
    elif op == "move_script":
        src = _inside(out_dir, args["from"])
        dst = _inside(out_dir, args["to"])
        if not os.path.isfile(src):
            _write(src, args.get("seed", ""))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.move(src, dst)
        _write(skill_md, _read(skill_md) + args.get("link", ""))
    elif op == "add_symlink":
        path = _inside(out_dir, args["path"])
        os.makedirs(os.path.dirname(path), exist_ok=True)
        os.symlink(os.path.expanduser(args["target"]), path)
    elif op == "pad_body":
        pad = "\n".join("%s (%d)" % (args["text"], i) for i in range(1, args["lines"] + 1))
        _write(skill_md, _read(skill_md).rstrip("\n") + "\n\n## Background\n\n" + pad + "\n")
    else:
        raise ValueError("unknown op: " + op)
    return out_dir


def resolve_base(base, corpus, corpus_dir):
    """Directory of the named corpus skill, or None if unavailable."""
    for s in corpus["skills"]:
        if s["name"] != base:
            continue
        if s["source"] == "fixture":
            return os.path.join(REPO, *s["path"].split("/"))
        cand = os.path.join(corpus_dir, base)
        return cand if os.path.isdir(cand) else None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--mutations", default=os.path.join(HERE, "mutations.json"))
    ap.add_argument("--corpus", default=os.path.join(HERE, "corpus.json"))
    a = ap.parse_args(argv)
    out = os.path.realpath(a.out)
    if out == REPO or out.startswith(REPO + os.sep):
        print("refusing to write inside the repository", file=sys.stderr)
        return 2
    with open(a.mutations, encoding="utf-8") as f:
        muts = json.load(f)["mutations"]
    with open(a.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    status = 0
    for m in muts:
        base = resolve_base(m["base"], corpus, a.corpus_dir)
        if base is None:
            print("%s: skipped (base %s not available)" % (m["id"], m["base"]))
            continue
        target = os.path.join(out, m["id"], os.path.basename(base.rstrip(os.sep)))
        try:
            print("%s: %s" % (m["id"], apply_mutation(base, m, target)))
        except (ValueError, OSError) as e:
            print("%s: FAILED %s" % (m["id"], e), file=sys.stderr)
            status = 1
    return status


if __name__ == "__main__":
    sys.exit(main())
