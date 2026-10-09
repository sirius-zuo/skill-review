"""Nonce-wrapped skill bundles for the review model (Python 3.7 stdlib only).

Everything inside a nonce-tagged block is untrusted data. File text is line-numbered, so
a line of file content can never equal a closing tag.
"""
import argparse
import html
import os
import re
import secrets

from common import (EXIT_OK, load_manifest, load_run, reviewable_skills, run_main,
                    scoring, write_json)
from discover import _read_regular
from frontmatter import parse_skill_file


def new_nonce(texts, token=secrets.token_hex):
    texts = list(texts)
    while True:
        nonce = token(8)
        if not any(nonce in t for t in texts):
            return nonce


def _esc_path(path):
    return html.escape(path, quote=True).replace("\r", "&#13;").replace("\n", "&#10;")


def file_block(path, text, nonce):
    lines = text.splitlines()
    width = max(4, len(str(len(lines))))
    body = ["%s| %s" % (str(i).zfill(width), line) for i, line in enumerate(lines, 1)]
    return "\n".join(['<file path="%s" nonce="%s">' % (_esc_path(path), nonce)] + body
                     + ['</file nonce="%s">' % nonce])


def wrap_untrusted(text, nonce):
    return '<untrusted nonce="%s">\n%s\n</untrusted nonce="%s">' % (nonce, text, nonce)


def _status(entry, text):
    if entry["bundled"] and text is not None:
        return "bundled"
    if entry["bundled"]:
        return "unreadable"
    if entry.get("symlink") is not None:
        return "symlink"
    return entry.get("skip_reason") or "skipped"


def _load(manifest, entries):
    """[(entry, text-or-None)] with text only for entries that are bundled and readable."""
    limit = scoring()["limits"]["bundle_max_bytes"]
    out = []
    for e in entries:
        text = _read_regular(manifest["root"], e["path"], limit) if e["bundled"] else None
        out.append((e, text))
    return out


def _ordered_files(skill):
    first = [e for e in skill["files"] if e["path"] == skill["skill_file"]]
    return first + [e for e in skill["files"] if e["path"] != skill["skill_file"]]


def bundled_texts(manifest, skill):
    entries = _ordered_files(skill) + list(manifest.get("repo_files", []))
    return [t for _, t in _load(manifest, entries) if t is not None]


def bundle_skill(manifest, skill, nonce):
    files = _load(manifest, _ordered_files(skill))
    repo = _load(manifest, manifest.get("repo_files", []))
    # Every untrusted value is escaped like a file path, so a name or path holding a newline
    # stays on its own status line and cannot forge others.
    status = ["skill: %s" % _esc_path(skill["name"]), "key: %s" % _esc_path(skill["key"]),
              "files:"]
    status += ["  %s [%s]" % (_esc_path(e["path"]), _status(e, t)) for e, t in files]
    status.append("repo-level files:")
    status += ["  %s [%s]" % (_esc_path(e["path"]), _status(e, t)) for e, t in repo]
    parts = ["# skill header", wrap_untrusted("\n".join(status), nonce), "", "# skill files"]
    parts += [file_block(e["path"], t, nonce) for e, t in files if t is not None]
    parts += ["", "# repo-level files"]
    parts += [file_block(e["path"], t, nonce) for e, t in repo if t is not None]
    return "\n".join(parts) + "\n"


def parse_bundle(text, nonce):
    open_re = re.compile(r'^<file path="(.*)" nonce="%s">$' % re.escape(nonce))
    close = '</file nonce="%s">' % nonce
    files = {}
    cur = None
    for line in text.split("\n"):
        if cur is None:
            m = open_re.match(line)
            if m:
                cur = (html.unescape(m.group(1)), [])
        elif line == close:
            files[cur[0]] = cur[1]
            cur = None
        else:
            cur[1].append(line.split("| ", 1)[1] if "| " in line else "")
    return files


def _description(manifest, skill):
    text = _read_regular(manifest["root"], skill["skill_file"],
                         scoring()["limits"]["ingest_max_bytes"]) or ""
    meta = parse_skill_file(text).meta
    desc = meta.get("description") if isinstance(meta, dict) else None
    return desc if isinstance(desc, str) else ""


def main(argv):
    ap = argparse.ArgumentParser(prog="bundle.py")
    ap.add_argument("--work-dir", required=True)
    args = ap.parse_args(argv)
    work = args.work_dir
    manifest = load_manifest(work)
    skills = reviewable_skills(manifest)
    descs = {s["key"]: _description(manifest, s) for s in skills}
    texts = list(descs.values())
    for s in skills:
        texts += bundled_texts(manifest, s)
    nonce = new_nonce(texts)
    for s in skills:
        write_text(os.path.join(work, s["key"], "bundle.txt"), bundle_skill(manifest, s, nonce))
    entries = ["name: %s\ndescription: %s" % (s["name"], descs[s["key"]]) for s in skills]
    write_text(os.path.join(work, "skills-list.txt"),
               "\n\n".join(wrap_untrusted(e, nonce) for e in entries) + "\n")
    run = load_run(work)
    run["nonce"] = nonce
    write_json(os.path.join(work, "run.json"), run, "run")
    return EXIT_OK


def write_text(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    run_main(main)
