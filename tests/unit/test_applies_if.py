import unittest

from _helpers import SCRIPTS_DIR  # noqa: F401  (puts scripts/ on sys.path)
from applies_if import ExprError, evaluate, names, parse


class AppliesIfTests(unittest.TestCase):
    def test_precedence(self):
        f = {"a": True, "b": False, "c": False}
        self.assertTrue(evaluate("a or b and c", f))
        self.assertFalse(evaluate("not a or b", f))
        self.assertTrue(evaluate("not (b or c)", f))

    def test_empty_is_true(self):
        self.assertTrue(evaluate("  ", {}))
        self.assertEqual(parse(""), ("true",))

    def test_true_keyword(self):
        self.assertEqual(parse("true"), ("true",))

    def test_ast(self):
        self.assertEqual(parse("a and not b"), ("and", ("flag", "a"), ("not", ("flag", "b"))))

    def test_names(self):
        self.assertEqual(names("x and (y or not z)"), {"x", "y", "z"})

    def test_errors(self):
        for bad in ("a and", "(a", "a b", "a && b", "__import__('os')", "a)", "A"):
            with self.assertRaises(ExprError):
                parse(bad)
        with self.assertRaises(ExprError):
            evaluate("missing", {})


if __name__ == "__main__":
    unittest.main()
