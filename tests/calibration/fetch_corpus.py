#!/usr/bin/env python3
"""Download the pinned anthropics/skills commit and extract the corpus skills.

Usage: fetch_corpus.py --dest DIR
  DIR must be new or empty and outside this repository. Only skills/<name>/ for corpus
  entries with source "anthropic" are extracted, to DIR/<name>/. Absolute or ".." member
  paths are rejected; symlink, hard link and device members are skipped. Python 3.7 stdlib.
"""
import argparse
import io
import json
import os
import sys
import tarfile
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
URL = "https://codeload.github.com/anthropics/skills/tar.gz/%s"
MAX_BYTES = 200 * 1024 * 1024


def check_dest(dest):
    real = os.path.realpath(dest)
    if real == REPO or real.startswith(REPO + os.sep):
        raise ValueError("destination must be outside the repository: %s" % dest)
    if os.path.exists(real) and (not os.path.isdir(real) or os.listdir(real)):
        raise ValueError("destination must be a new or empty directory: %s" % dest)
    return real


def extract_skills(fileobj, dest, names):
    """Extract skills/<name>/ trees (for the given names) from a gzip tar stream."""
    wanted = set(names)
    dest = os.path.realpath(dest)
    os.makedirs(dest, exist_ok=True)
    with tarfile.open(fileobj=fileobj, mode="r|gz") as tf:
        for member in tf:
            raw = member.name
            norm = raw.replace("\\", "/")
            parts = norm.split("/")
            if norm.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
                raise ValueError("unsafe tar member path: %r" % raw)
            if not (member.isfile() or member.isdir()):
                continue  # symlink, hard link, device, fifo: skipped
            parts = [p for p in parts if p not in ("", ".")]
            # <top>/skills/<name>/<rest...>
            if len(parts) < 3 or parts[1] != "skills" or parts[2] not in wanted:
                continue
            target = os.path.join(dest, *parts[2:])
            real = os.path.realpath(target)
            if real != dest and not real.startswith(dest + os.sep):
                raise ValueError("unsafe tar member path: %r" % raw)
            if member.isdir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            src = tf.extractfile(member)
            with open(target, "wb") as out:
                out.write(src.read())
    return dest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dest", required=True)
    ap.add_argument("--corpus", default=os.path.join(HERE, "corpus.json"))
    a = ap.parse_args(argv)
    try:
        dest = check_dest(a.dest)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    with open(a.corpus, encoding="utf-8") as f:
        corpus = json.load(f)
    names = [s["name"] for s in corpus["skills"] if s["source"] == "anthropic"]
    url = URL % corpus["anthropic_skills_sha"]
    print("downloading %s" % url)
    with urllib.request.urlopen(url, timeout=60) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        print("download too large", file=sys.stderr)
        return 1
    try:
        extract_skills(io.BytesIO(data), dest, names)
    except (ValueError, tarfile.TarError) as e:
        print("extraction failed: %s" % e, file=sys.stderr)
        return 1
    missing = [n for n in names if not os.path.isdir(os.path.join(dest, n))]
    if missing:
        print("missing from archive: %s" % ", ".join(missing), file=sys.stderr)
        return 1
    print("extracted %s to %s" % (", ".join(names), dest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
