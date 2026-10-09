"""YAML-subset frontmatter parser for SKILL.md files (Python 3.7 stdlib only).

parse_skill_file(text) never raises; failures are reported via the error field.
"""
import re
from collections import namedtuple

ParsedSkill = namedtuple("ParsedSkill", "meta body body_start_line error")

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(.*)$")
_NESTED_KEY_RE = re.compile(r"^[ \t]+([A-Za-z0-9_-]+):(.*)$")
_BLOCK_RE = re.compile(r"^([>|])([+-]?)\s*(?:\s#.*)?$")
_ESCAPES = {'"': '"', "\\": "\\", "n": "\n"}


class _Err(Exception):
    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = reason


def _strip_comment(s):
    m = re.search(r"\s#", " " + s)
    return (s[:m.start()] if m else s).strip()


def _after_quoted(rest):
    rest = rest.strip()
    if rest and not rest.startswith("#"):
        raise _Err("unexpected text after quoted value")


def _read_quoted(s, start):
    """Parse a quoted scalar beginning at s[start]; return (value, end_index)."""
    q = s[start]
    out = []
    i = start + 1
    while i < len(s):
        c = s[i]
        if q == '"' and c == "\\":
            if i + 1 >= len(s) or s[i + 1] not in _ESCAPES:
                raise _Err("unsupported escape sequence")
            out.append(_ESCAPES[s[i + 1]])
            i += 2
            continue
        if q == "'" and c == "'" and s[i + 1:i + 2] == "'":
            out.append("'")
            i += 2
            continue
        if c == q:
            return "".join(out), i + 1
        out.append(c)
        i += 1
    raise _Err("unterminated quoted string")


def _parse_list(s):
    i = 1
    items = []
    n = len(s)
    while True:
        while i < n and s[i] in " \t":
            i += 1
        if i >= n:
            raise _Err("unterminated inline list")
        if s[i] == "]":
            i += 1
            break
        if s[i] in "\"'":
            val, i = _read_quoted(s, i)
            while i < n and s[i] in " \t":
                i += 1
        else:
            j = i
            while j < n and s[j] not in ",]":
                j += 1
            if j >= n:
                raise _Err("unterminated inline list")
            val = s[i:j].strip()
            if val[:1] in "[{&*":
                raise _Err("nested or unsupported item in inline list")
            i = j
        items.append(val)
        if i < n and s[i] == ",":
            i += 1
        elif i < n and s[i] == "]":
            i += 1
            break
        else:
            raise _Err("unterminated inline list" if i >= n else "expected ',' in inline list")
    _after_quoted(s[i:])
    return items


def _parse_scalar(raw):
    s = raw.strip()
    if not s or s.startswith("#"):
        return ""
    c = s[0]
    if c in "\"'":
        val, end = _read_quoted(s, 0)
        _after_quoted(s[end:])
        return val
    if c == "[":
        return _parse_list(s)
    if c in "&*!":
        raise _Err("anchors, aliases and tags are not supported")
    if c in "{|>":
        raise _Err("unsupported value syntax")
    return _strip_comment(s)


def _indent(line):
    return len(line) - len(line.lstrip(" \t"))


def _collect_indented(lines, i):
    """Return (index after block, lines belonging to an indented block)."""
    j = i
    while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > 0):
        j += 1
    return j, lines[i:j]


def _block_scalar(kind, chomp, block):
    body = list(block)
    trailing = 0
    while body and not body[-1].strip():
        body.pop()
        trailing += 1
    first = next((ln for ln in body if ln.strip()), None)
    if first is None:
        return ""
    ind = _indent(first)
    body = [ln[ind:] if ln.strip() else "" for ln in body]
    if kind == "|":
        text = "\n".join(body)
    else:
        parts = []
        for k, ln in enumerate(body):
            if k:
                if not ln:
                    parts.append("\n")
                elif body[k - 1]:
                    parts.append(" ")
                elif parts and parts[-1] != "\n":
                    parts.append("\n")
            parts.append(ln)
        text = "".join(parts)
    if chomp == "-":
        return text
    if chomp == "+":
        return text + "\n" * (1 + trailing)
    return text + "\n"


def _parse_block(lines, base):
    """lines: frontmatter lines (CR stripped). base: file line number of lines[0]."""
    meta = {}
    i = 0
    while i < len(lines):
        ln = lines[i]
        lineno = base + i
        try:
            if not ln.strip() or ln.lstrip().startswith("#"):
                i += 1
                continue
            if ln[0] in " \t":
                raise _Err("unexpected indentation")
            m = _KEY_RE.match(ln)
            if not m:
                raise _Err("expected 'key: value'")
            key, rest = m.group(1), m.group(2).strip()
            if key in meta:
                raise _Err("duplicate key '%s'" % key)
            bm = _BLOCK_RE.match(rest)
            if bm:
                j, block = _collect_indented(lines, i + 1)
                meta[key] = _block_scalar(bm.group(1), bm.group(2), block)
                i = j
                continue
            if not rest or rest.startswith("#"):
                j = i + 1
                while j < len(lines) and (not lines[j].strip() or lines[j].lstrip().startswith("#")) \
                        and (not lines[j].strip() or _indent(lines[j]) > 0):
                    j += 1
                if j < len(lines) and _indent(lines[j]) > 0:
                    j, block = _collect_indented(lines, i + 1)
                    meta[key] = _parse_map(block, base + i + 1)
                    i = j
                    continue
                meta[key] = ""
                i += 1
                continue
            meta[key] = _parse_scalar(rest)
            i += 1
        except _Err as e:
            raise _Err("line %d: %s" % (lineno, e.reason))
    return meta


def _parse_map(block, base):
    out = {}
    for k, ln in enumerate(block):
        lineno = base + k
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        try:
            if ln.lstrip().startswith("- "):
                raise _Err("block lists are not supported")
            m = _NESTED_KEY_RE.match(ln)
            if not m:
                raise _Err("expected 'key: value' in nested map")
            key, rest = m.group(1), m.group(2).strip()
            if key in out:
                raise _Err("duplicate key '%s'" % key)
            if not rest or rest.startswith("#"):
                raise _Err("multi-level nesting is not supported")
            if _BLOCK_RE.match(rest):
                raise _Err("block scalars are not supported in nested maps")
            out[key] = _parse_scalar(rest)
        except _Err as e:
            raise _Err("line %d: %s" % (lineno, e.reason))
    return out


def _parse(text):
    if text.startswith("﻿"):
        text = text[1:]
    raw = text.split("\n")
    if raw[0].rstrip() != "---":
        return ParsedSkill(None, text, 1, "missing frontmatter")
    lines = [ln.rstrip("\r") for ln in raw]
    end = None
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == "---":
            end = idx
            break
    if end is None:
        return ParsedSkill(None, text, 1, "line 1: frontmatter is never closed")
    body = "\n".join(raw[end + 1:])
    try:
        meta = _parse_block(lines[1:end], 2)
    except _Err as e:
        return ParsedSkill(None, body, end + 2, e.reason)
    return ParsedSkill(meta, body, end + 2, None)


def parse_skill_file(text):
    """Parse SKILL.md text into ParsedSkill(meta, body, body_start_line, error). Never raises."""
    try:
        if isinstance(text, bytes):
            text = text.decode("utf-8", errors="replace")
        return _parse(text)
    except Exception as e:  # defensive: the contract is "never raises"
        return ParsedSkill(None, text if isinstance(text, str) else "", 1, "line 1: %s" % e)
