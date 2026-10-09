import unittest

from _helpers import SCRIPTS_DIR  # noqa: F401  (puts scripts/ on sys.path)
from frontmatter import parse_skill_file


class FrontmatterTests(unittest.TestCase):
    def test_basic(self):
        p = parse_skill_file("---\nname: pdf-tool\ndescription: Extracts text. Use when PDFs.\n---\n# Body\n")
        self.assertEqual(p.meta, {"name": "pdf-tool", "description": "Extracts text. Use when PDFs."})
        self.assertEqual((p.body, p.body_start_line, p.error), ("# Body\n", 5, None))

    def test_folded_and_literal(self):
        p = parse_skill_file("---\ndescription: >\n  one\n  two\nnotes: |-\n  a\n  b\n---\n")
        self.assertEqual(p.meta["description"], "one two\n")
        self.assertEqual(p.meta["notes"], "a\nb")

    def test_quotes_map_list_comment(self):
        t = '---\nname: "a \\"b\\""\nmetadata:\n  author: x # c\n  version: \'1.0\'\ntags: [a, "b c"]\n---\n'
        p = parse_skill_file(t)
        self.assertEqual(p.meta, {"name": 'a "b"', "metadata": {"author": "x", "version": "1.0"},
                                  "tags": ["a", "b c"]})

    def test_crlf_and_bom(self):  # Review Focus 2
        p = parse_skill_file("\ufeff---\r\nname: a\r\n---\r\nline\r\n")
        self.assertEqual((p.meta, p.body_start_line), ({"name": "a"}, 4))

    def test_missing_and_invalid(self):
        p = parse_skill_file("# no fm\n")
        self.assertEqual((p.error, p.meta, p.body, p.body_start_line), ("missing frontmatter", None, "# no fm\n", 1))
        self.assertTrue(parse_skill_file("---\nname: [unclosed\n---\n").error.startswith("line 2:"))
        self.assertTrue(parse_skill_file("---\nname: a\n").error.startswith("line"))  # unterminated block

    def test_extras(self):
        p = parse_skill_file("---\nempty:\nq: ''\nkeep: |+\n  a\n\n---\n")
        self.assertEqual(p.meta, {"empty": "", "q": "", "keep": "a\n\n"})
        cases = [
            ("a: &x 1", "line 2: anchors, aliases and tags are not supported"),
            ("a: *x", "line 2: anchors, aliases and tags are not supported"),
            ("a:\n  b:\n    c: 1", "line 3: multi-level nesting is not supported"),
            ("a:\n  - x", "line 3: block lists are not supported"),
            ("- x", "line 2: expected 'key: value'"),
            ("a: 1\na: 2", "line 3: duplicate key 'a'"),
            ('a: "x', "line 2: unterminated quoted string"),
            ("d: >\n    a\n  b", "line 4: block scalar line is indented less than its first line"),
            ("m:\n  a: 1\n  a: 2", "line 4: duplicate key 'a'"),
        ]
        for bad, msg in cases:
            p = parse_skill_file("---\n%s\n---\n" % bad)
            self.assertIsNone(p.meta, bad)
            self.assertEqual(p.error, msg, bad)

    def test_never_raises(self):
        for t in ("", "---", "---\n", "\x00\n---", None):
            p = parse_skill_file(t)
            self.assertIsNone(p.meta)
            self.assertTrue(p.error)


if __name__ == "__main__":
    unittest.main()
