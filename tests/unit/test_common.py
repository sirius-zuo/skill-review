import json
import os
import unittest
from unittest import mock

from _helpers import tempdir, run_path, SCRIPTS_DIR
import common
from common import (ValidationFailed, round_half_up, sanitize_name, scoring)
from jsonschema_lite import validate


class CommonTests(unittest.TestCase):
    def test_validate_types(self):
        s = {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}},
             "additionalProperties": False}
        self.assertEqual(validate({"n": 1}, s), [])
        self.assertIn("$.n: expected integer", validate({"n": True}, s))
        self.assertIn("$: missing required property 'n'", validate({}, s))
        self.assertIn("$: unexpected property 'x'", validate({"n": 1, "x": 2}, s))

    def test_validate_rejects_unknown_keyword(self):
        with self.assertRaises(ValueError):
            validate(1, {"oneOf": []})

    def test_validate_array_paths(self):
        s = {"type": "object", "properties": {"skills": {"type": "array", "items": {
            "type": "object", "properties": {"name": {"type": "string"}}}}}}
        self.assertIn("$.skills[0].name: expected string", validate({"skills": [{"name": 1}]}, s))

    def test_round_half_up(self):
        self.assertEqual([round_half_up(x) for x in (2.5, 3.5, 7.49, 0.0)], [3, 4, 7, 0])

    def test_sanitize_name(self):
        self.assertEqual(sanitize_name("My Skill!"), "my-skill")
        self.assertEqual(sanitize_name("***"), "skill")

    def test_write_json_validates(self):
        with tempdir() as d, mock.patch.object(common, "SCHEMAS_DIR", d):
            with open(os.path.join(d, "t.schema.json"), "w") as f:
                json.dump({"type": "object", "required": ["n"]}, f)
            out = os.path.join(d, "o", "x.json")
            with self.assertRaises(ValidationFailed):
                common.write_json(out, {}, schema_name="t")
            self.assertFalse(os.path.exists(out))
            common.write_json(out, {"n": 1}, schema_name="t")
            self.assertEqual(common.read_json(out), {"n": 1})

    def test_read_text_strips_bom_and_replaces(self):
        with tempdir() as d:
            p = os.path.join(d, "a.txt")
            with open(p, "wb") as f:
                f.write(b"\xef\xbb\xbfh\xe9llo")
            self.assertEqual(common.read_text(p), "h�llo")

    def test_run_main_exit_codes(self):
        body = ("import sys\nsys.path.insert(0, %r)\nimport common\n"
                "def main(argv):\n    k = argv[0]\n"
                "    if k == 'v': raise common.ValidationFailed(['bad'], {'x': 1})\n"
                "    if k == 'u': raise common.UsageError('use')\n"
                "    if k == 'r': raise RuntimeError('boom')\n"
                "    return 0\ncommon.run_main(main)\n") % SCRIPTS_DIR
        with tempdir() as d:
            p = os.path.join(d, "t.py")
            with open(p, "w") as f:
                f.write(body)
            r = run_path(p, "v")
            self.assertEqual(r.returncode, 1)
            self.assertEqual(json.loads(r.stdout), {"ok": False, "errors": ["bad"], "x": 1})
            self.assertEqual(run_path(p, "u").returncode, 2)
            r = run_path(p, "r")
            self.assertEqual(r.returncode, 3)
            self.assertIn("RuntimeError", r.stderr)
            self.assertEqual(run_path(p, "ok").returncode, 0)

    def test_scoring_loads(self):
        self.assertEqual(scoring()["limits"]["quote_min_chars"], 12)


if __name__ == "__main__":
    unittest.main()
