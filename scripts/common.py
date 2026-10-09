"""Shared helpers for skill-review scripts (Python 3.7 stdlib only)."""
import json
import os
import re
import sys
import tempfile
import traceback

from jsonschema_lite import validate

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(SCRIPTS_DIR)
RULES_DIR = os.path.join(ROOT_DIR, "rules")
SCHEMAS_DIR = os.path.join(RULES_DIR, "schemas")

EXIT_OK = 0
EXIT_VALIDATION = 1
EXIT_USAGE = 2
EXIT_CRASH = 3

CATEGORY_ORDER = ["trigger", "scope", "clarity", "conciseness", "workflow",
                  "scripts_tools", "safety", "output", "evaluation"]


class UsageError(Exception):
    pass


class ValidationFailed(Exception):
    def __init__(self, errors, extra=None):
        Exception.__init__(self, "; ".join(errors))
        self.errors = list(errors)
        self.extra = extra or {}


def read_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_rules(name):
    return read_json(os.path.join(RULES_DIR, name + ".json"))


_SCORING = None


def scoring():
    global _SCORING
    if _SCORING is None:
        _SCORING = load_rules("scoring")
    return _SCORING


def validate_against(data, schema_name):
    schema = read_json(os.path.join(SCHEMAS_DIR, schema_name + ".schema.json"))
    return validate(data, schema)


def write_json(path, data, schema_name=None):
    if schema_name:
        errors = validate_against(data, schema_name)
        if errors:
            raise ValidationFailed(errors)
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def read_text(path, limit_bytes=None):
    with open(path, "rb") as f:
        raw = f.read() if limit_bytes is None else f.read(limit_bytes)
    text = raw.decode("utf-8", errors="replace")
    if text.startswith("﻿"):
        text = text[1:]
    return text


def run_main(main):
    try:
        code = main(sys.argv[1:])
    except UsageError as e:
        sys.stderr.write("usage error: %s\n" % e)
        sys.exit(EXIT_USAGE)
    except ValidationFailed as e:
        out = {"ok": False, "errors": e.errors}
        out.update(e.extra)
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.exit(EXIT_VALIDATION)
    except Exception:
        traceback.print_exc()
        sys.exit(EXIT_CRASH)
    sys.exit(EXIT_OK if code is None else code)


def round_half_up(x):
    return int(x + 0.5)


def sanitize_name(s):
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return out or "skill"


def load_manifest(work_dir):
    return read_json(os.path.join(work_dir, "manifest.json"))


def load_run(work_dir):
    return read_json(os.path.join(work_dir, "run.json"))


def reviewable_skills(manifest):
    return [s for s in manifest.get("skills", []) if s.get("kind") == "skill"]


def skill_work_dir(work_dir, key):
    return os.path.join(work_dir, key)
