"""Restricted mathematical expression parser + evaluator.

Security requirement: user/AI supplied equations must never reach ``eval``.
This module implements a small tokenizer + Pratt parser that produces a closed
AST, then evaluates that AST with a whitelist of functions and hard numeric
limits.  Anything outside the whitelist (attribute access, subscripts,
assignments, unknown names, double underscores, string literals, huge
exponents, ...) is rejected with :class:`ExpressionError`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable, Iterable

MAX_EXPRESSION_CHARS = 400
MAX_NODES = 400
MAX_ABS_EXPONENT = 64.0


class ExpressionError(ValueError):
    """Raised when an expression is invalid, unsupported, or rejected."""


# --- whitelist -------------------------------------------------------------

CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "phi": (1 + math.sqrt(5)) / 2,
    "inf": math.inf,
}

UNARY: dict[str, Callable[[float], float]] = {
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "sinh": math.sinh,
    "cosh": math.cosh,
    "tanh": math.tanh,
    "exp": math.exp,
    "log": math.log,
    "ln": math.log,
    "log10": math.log10,
    "log2": math.log2,
    "sqrt": math.sqrt,
    "cbrt": lambda x: math.copysign(abs(x) ** (1 / 3), x),
    "abs": abs,
    "fabs": math.fabs,
    "floor": math.floor,
    "ceil": math.ceil,
    "round": round,
    "sign": lambda x: 0.0 if x == 0 else math.copysign(1.0, x),
    "erf": math.erf,
    "gamma": math.gamma,
}

BINARY: dict[str, Callable[[float, float], float]] = {
    "atan2": math.atan2,
    "pow": math.pow,
    "hypot": math.hypot,
    "min": min,
    "max": max,
    "mod": math.fmod,
    "log": math.log,  # two-argument log(x, base)
}


@dataclass(frozen=True)
class Node:
    kind: str
    value: object = None
    children: tuple["Node", ...] = ()


# --- tokenizer -------------------------------------------------------------

_OPERATORS = {
    "+",
    "-",
    "*",
    "/",
    "//",
    "%",
    "^",
    "**",
    "(",
    ")",
    ",",
}


def _tokenize(src: str) -> list[tuple[str, object]]:
    tokens: list[tuple[str, object]] = []
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch.isspace():
            i += 1
            continue
        if ch.isdigit() or (ch == "." and i + 1 < n and src[i + 1].isdigit()):
            j = i
            seen_dot = False
            while j < n and (src[j].isdigit() or (src[j] == "." and not seen_dot)):
                seen_dot = seen_dot or src[j] == "."
                j += 1
            if j < n and src[j] in "eE":
                k = j + 1
                if k < n and src[k] in "+-":
                    k += 1
                if k < n and src[k].isdigit():
                    j = k
                    while j < n and src[j].isdigit():
                        j += 1
            text = src[i:j]
            try:
                tokens.append(("num", float(text)))
            except ValueError as exc:  # pragma: no cover - guarded by regex shape
                raise ExpressionError(f"invalid numeric literal '{text}'") from exc
            i = j
            continue
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (src[j].isalnum() or src[j] == "_"):
                j += 1
            name = src[i:j]
            if "__" in name:
                raise ExpressionError("identifiers containing '__' are not allowed")
            tokens.append(("name", name))
            i = j
            continue
        two = src[i : i + 2]
        if two in ("**", "//"):
            tokens.append(("op", "**" if two == "**" else two))
            i += 2
            continue
        if ch in "+-*/%^(),":
            tokens.append(("op", ch))
            i += 1
            continue
        raise ExpressionError(f"unsupported character '{ch}' at position {i}")
    if not tokens:
        raise ExpressionError("expression is empty")
    return tokens


class _Parser:
    """Pratt parser for the closed grammar below.

    expr    := term (('+'|'-') term)*
    term    := unary (('*'|'/'|'//'|'%') unary | implicit-mult)*
    unary   := ('+'|'-') unary | power
    power   := atom ('^' unary)?          (right associative)
    atom    := NUMBER | CONST | VAR | func '(' args ')' | '(' expr ')'
    """

    def __init__(self, tokens: list[tuple[str, object]], variables: tuple[str, ...]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.variables = variables
        self.nodes = 0

    # --- token helpers ---
    def _peek(self) -> tuple[str, object]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ("end", None)

    def _next(self) -> tuple[str, object]:
        tok = self._peek()
        self.pos += 1
        return tok

    def _expect(self, value: str) -> None:
        kind, v = self._next()
        if kind != "op" or v != value:
            raise ExpressionError(f"expected '{value}' but found {v!r}")

    def _node(self, kind: str, value: object = None, children: tuple[Node, ...] = ()) -> Node:
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ExpressionError("expression is too complex (node limit exceeded)")
        return Node(kind, value, children)

    # --- grammar ---
    def parse(self) -> Node:
        node = self.expr()
        if self.pos != len(self.tokens):
            _, v = self._peek()
            raise ExpressionError(f"unexpected token {v!r}")
        return node

    def expr(self) -> Node:
        left = self.term()
        while True:
            kind, v = self._peek()
            if kind == "op" and v in ("+", "-"):
                self._next()
                left = self._node("bin", v, (left, self.term()))
            else:
                return left

    def term(self) -> Node:
        left = self.unary()
        while True:
            kind, v = self._peek()
            if kind == "op" and v in ("*", "/", "//", "%"):
                self._next()
                left = self._node("bin", "*" if v == "**" else v, (left, self.unary()))
                continue
            # implicit multiplication: 2x, 3(x+1), 2sin(x), (x+1)(x-1)
            if kind == "name" or (kind == "op" and v == "("):
                left = self._node("bin", "*", (left, self.unary()))
                continue
            return left

    def unary(self) -> Node:
        kind, v = self._peek()
        if kind == "op" and v in ("-", "+"):
            self._next()
            operand = self.unary()
            return self._node("neg", None, (operand,)) if v == "-" else operand
        return self.power()

    def power(self) -> Node:
        base = self.atom()
        kind, v = self._peek()
        if kind == "op" and v in ("^", "**"):
            self._next()
            return self._node("pow", "^", (base, self.unary()))
        return base

    def atom(self) -> Node:
        kind, v = self._next()
        if kind == "num":
            return self._node("num", float(v))
        if kind == "name":
            name = str(v)
            if self._peek() == ("op", "("):
                self._next()
                args: list[Node] = []
                if self._peek() != ("op", ")"):
                    args.append(self.expr())
                    while self._peek() == ("op", ","):
                        self._next()
                        args.append(self.expr())
                self._expect(")")
                return self._call(name, tuple(args))
            if name in CONSTANTS:
                return self._node("num", float(CONSTANTS[name]))
            if name in self.variables:
                return self._node("var", name)
            raise ExpressionError(f"unknown symbol '{name}' (allowed: {', '.join(self.variables)})")
        if kind == "op" and v == "(":
            inner = self.expr()
            self._expect(")")
            return inner
        raise ExpressionError(f"unexpected token {v!r}")

    def _call(self, name: str, args: tuple[Node, ...]) -> Node:
        if name in BINARY and len(args) == 2:
            return self._node("call2", name, args)
        if name in UNARY and len(args) == 1:
            return self._node("call1", name, args)
        raise ExpressionError(
            f"call '{name}' is not supported with {len(args)} argument(s)"
        )


def allowed_names() -> dict[str, object]:
    """Public introspection helper used by API docs and error messages."""
    return {
        "constants": sorted(CONSTANTS),
        "unary_functions": sorted(UNARY),
        "binary_functions": sorted(BINARY),
        "operators": ["+", "-", "*", "/", "//", "%", "^", "(", ")"],
        "comparison_operators": [],
        "implicit_multiplication": True,
        "equation_input": "a single '=' is accepted and rewritten as (lhs) - (rhs)",
        "rejected": [
            "attribute access", "subscripts", "literals other than numbers",
            "calls to unknown functions", "assignment", "keyword characters",
            "semicolons and backslashes",
        ],
    }


def parse(src: str, variables: object = ("x",)) -> Node:
    """Tokenize + parse ``src`` into a closed AST (no eval anywhere)."""
    if not isinstance(src, str):
        raise ExpressionError("expression must be a string")
    text = src.strip()
    if not text:
        raise ExpressionError("expression is empty")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise ExpressionError(f"expression longer than {MAX_EXPRESSION_CHARS} characters")
    lowered = text.lower()
    for bad in ("__", "import", "lambda", "eval", "exec", "open", "global", "def ",
                ";", "`", "{", "}", "[", "]", '"', "'", "\\", "&", "|", "<", ">"):
        if bad in lowered:
            raise ExpressionError(f"expression contains disallowed sequence {bad!r}")
    if "=" in text:
        lhs, _, rhs = text.partition("=")
        text = f"({lhs}) - ({rhs})" if rhs.strip() else lhs
    variables = tuple(variables) if not isinstance(variables, str) else (variables,)
    return _Parser(_tokenize(text), variables).parse()


# --- evaluation ------------------------------------------------------------


def _evaluate(node: Node, env: dict[str, float]) -> float:
    kind = node.kind
    if kind == "num":
        return float(node.value)
    if kind == "var":
        return env[str(node.value)]
    if kind == "pos":
        return _evaluate(node.children[0], env)
    if kind == "neg":
        return -_evaluate(node.children[0], env)
    if kind == "pow":
        base = _evaluate(node.children[0], env)
        exp = _evaluate(node.children[1], env)
        if abs(exp) > MAX_ABS_EXPONENT:
            raise ExpressionError("exponent magnitude above the allowed limit")
        if base == 0.0 and exp < 0:
            raise ZeroDivisionError("division by zero in expression")
        try:
            return math.pow(base, exp)
        except OverflowError:
            raise ExpressionError("numeric overflow in expression") from None
    if kind == "bin":
        left = _evaluate(node.children[0], env)
        right = _evaluate(node.children[1], env)
        op = node.value
        try:
            if op == "+":
                return left + right
            if op == "-":
                return left - right
            if op == "*":
                return left * right
            if op == "/":
                return left / right
            if op == "//":
                return left // right
            if op == "%":
                try:
                    return math.fmod(left, right)
                except ValueError:
                    raise ExpressionError("modulo by zero while evaluating '%'") from None
        except OverflowError:
            raise ExpressionError("numeric overflow in expression") from None
        except ZeroDivisionError:
            # surfaced as the module's own error type so every caller (probing,
            # iteration records, API validation) reports one clean input failure
            raise ExpressionError(
                f"division by zero while evaluating '{op}' (denominator evaluated to 0)"
            ) from None
        raise ExpressionError(f"unsupported operator {op!r}")
    if kind == "call1":
        arg = _evaluate(node.children[0], env)
        fn = UNARY[str(node.value)]
        try:
            return float(fn(arg))
        except ValueError:
            return math.nan
        except OverflowError:
            raise ExpressionError("numeric overflow in expression") from None
    if kind == "call2":
        a = _evaluate(node.children[0], env)
        b = _evaluate(node.children[1], env)
        fn2 = BINARY[str(node.value)]
        try:
            return float(fn2(a, b))
        except ValueError:
            return math.nan
        except (OverflowError, ZeroDivisionError):
            raise ExpressionError("invalid numeric domain in expression") from None
    raise ExpressionError(f"internal: unhandled node {kind}")


@dataclass
class CompiledFunction:
    source: str
    variables: tuple[str, ...]
    ast: Node

    def call(self, *values: float) -> float:
        env = dict(zip(self.variables, values))
        return _evaluate(self.ast, env)

    def unsafe_call(self, *values: float) -> float:
        """Like :meth:`call` but maps domain errors to NaN (root finders)."""
        try:
            return self.call(*values)
        except (ValueError, ZeroDivisionError):
            return math.nan


def compile_function(src: str, variables: Iterable[str] = ("x",)) -> CompiledFunction:
    """Compile ``src`` into a callable ``f(*variables)`` wrapper."""
    return compile_expression(src, variables)


def compile_expression(src: str, variables: Iterable[str] = ("x",)) -> CompiledFunction:
    variables = tuple(variables)
    ast = parse(src, variables)
    return CompiledFunction(source=src, variables=variables, ast=ast)


def function_is_valid(
    fn: CompiledFunction, probes: Iterable[tuple[float, ...]] | None = None
) -> tuple[bool, str | None]:
    """Sanity check: the expression evaluates to a real float somewhere."""
    nv = len(fn.variables)
    if probes is None:
        grids = [(0.0, 1.0, -1.0, 0.5)]
        for _ in range(nv - 1):
            grids = [g + (v,) for g in grids for v in (0.0, 1.0, -1.0, 0.5)]
        probes = grids[:24]
    finite_seen = 0
    error: str | None = None
    for point in probes:
        try:
            y = fn.call(*point)
        except (ValueError, ZeroDivisionError, OverflowError) as exc:
            error = str(exc)
            continue
        if isinstance(y, (int, float)) and math.isfinite(y):
            finite_seen += 1
    if finite_seen == 0:
        return False, (
            "expression never evaluated to a finite real number "
            + (f"({error})" if error else "")
        )
    return True, None
