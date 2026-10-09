"""applies_if boolean expression parser (recursive descent, no eval).

Grammar:
    or_expr  := and_expr ('or' and_expr)*
    and_expr := not_expr ('and' not_expr)*
    not_expr := 'not' not_expr | atom
    atom     := 'true' | IDENT | '(' or_expr ')'
"""
import re

_TOKEN = re.compile(r"\s*(?:([a-z_][a-z0-9_]*)|(\()|(\)))")
_KEYWORDS = ("and", "or", "not", "true")


class ExprError(Exception):
    pass


def _tokenize(expr):
    toks = []
    pos = 0
    n = len(expr)
    while True:
        while pos < n and expr[pos].isspace():
            pos += 1
        if pos >= n:
            return toks
        m = _TOKEN.match(expr, pos)
        if not m:
            raise ExprError("unexpected character %r at position %d" % (expr[pos], pos))
        if m.group(1) is not None:
            toks.append(("id", m.group(1)))
        elif m.group(2):
            toks.append(("(", "("))
        else:
            toks.append((")", ")"))
        pos = m.end()


class _Parser(object):
    def __init__(self, toks):
        self.toks = toks
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else None

    def _is_kw(self, word):
        t = self._peek()
        return t is not None and t == ("id", word)

    def parse(self):
        node = self._or()
        if self._peek() is not None:
            raise ExprError("unexpected token %r" % (self._peek()[1],))
        return node

    def _or(self):
        node = self._and()
        while self._is_kw("or"):
            self.i += 1
            node = ("or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._is_kw("and"):
            self.i += 1
            node = ("and", node, self._not())
        return node

    def _not(self):
        if self._is_kw("not"):
            self.i += 1
            return ("not", self._not())
        return self._atom()

    def _atom(self):
        t = self._peek()
        if t is None:
            raise ExprError("unexpected end of expression")
        if t[0] == "(":
            self.i += 1
            node = self._or()
            if self._peek() is None or self._peek()[0] != ")":
                raise ExprError("missing closing parenthesis")
            self.i += 1
            return node
        if t[0] == "id":
            if t[1] == "true":
                self.i += 1
                return ("true",)
            if t[1] in _KEYWORDS:
                raise ExprError("unexpected keyword %r" % (t[1],))
            self.i += 1
            return ("flag", t[1])
        raise ExprError("unexpected token %r" % (t[1],))


def parse(expr):
    toks = _tokenize(expr)
    if not toks:
        return ("true",)
    return _Parser(toks).parse()


def _collect(node, out):
    if node[0] == "flag":
        out.add(node[1])
    else:
        for child in node[1:]:
            _collect(child, out)


def names(expr):
    out = set()
    _collect(parse(expr), out)
    return out


def _eval(node, facts):
    kind = node[0]
    if kind == "true":
        return True
    if kind == "flag":
        if node[1] not in facts:
            raise ExprError("unknown name %r" % (node[1],))
        return bool(facts[node[1]])
    if kind == "not":
        return not _eval(node[1], facts)
    if kind == "and":
        return _eval(node[1], facts) and _eval(node[2], facts)
    return _eval(node[1], facts) or _eval(node[2], facts)


def evaluate(expr, facts):
    node = parse(expr)
    missing = names(expr) - set(facts)
    if missing:
        raise ExprError("unknown name %r" % (sorted(missing)[0],))
    return _eval(node, facts)
