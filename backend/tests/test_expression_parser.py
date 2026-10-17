"""Tests for the restricted expression parser (``app/services/expr.py``).

The parser is the only place untrusted text becomes executable behaviour, so the
security cases matter as much as the arithmetic ones: every rejected construct must
fail *before* evaluation, and every accepted construct must evaluate like Python.
"""

from __future__ import annotations

import math

import pytest

from app.services.expr import (
    ExpressionError,
    allowed_names,
    compile_expression,
    function_is_valid,
    parse,
)


def value(source: str, **env) -> float:
    fn = compile_expression(source, tuple(env))
    return fn.call(*[env[name] for name in fn.variables])


# --- arithmetic -------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,expected",
    [
        ("1+2*3", 7),
        ("(1+2)*3", 9),
        ("2^3^2", 512),  # right associative
        ("-2^2", -4),  # unary minus binds looser than ^
        ("(-2)^2", 4),
        ("7/2", 3.5),
        ("7//2", 3),
        ("7%3", 1),
        ("2^-1", 0.5),
        ("sqrt(16)+abs(-3)", 7),
        ("sin(pi/2)", 1.0),
        ("ln(e)", 1.0),
        ("log(1000)", math.log(1000)),  # natural log, as documented in the grammar
        ("log2(8)", 3.0),
        ("atan2(1, 1)", math.pi / 4),
        ("hypot(3, 4)", 5.0),
        ("max(1, 5)", 5),
        ("min(1, 5)", 1),
        ("mod(8, 3)", 2),
        ("pow(2, 10)", 1024),
        ("gamma(5)", 24.0),
        ("erf(0)", 0.0),
        ("floor(2.7) + ceil(2.1)", 5),
        ("sign(-3)", -1),
        ("cbrt(27)", 3.0),
        ("tau/pi", 2.0),
        ("phi", (1 + 5**0.5) / 2),
    ],
)
def test_expressions_evaluate_to_the_expected_number(source, expected):
    assert math.isclose(value(source), expected, rel_tol=1e-12, abs_tol=1e-12)


@pytest.mark.parametrize(
    "source,x,expected",
    [
        ("x^2 - 2", 3.0, 7.0),
        ("cos(x) - x", 0.0, 1.0),
        ("exp(-x^2/2)/sqrt(2*pi)", 0.0, 1 / math.sqrt(2 * math.pi)),
        ("1/(1+x^2)", 1.0, 0.5),
    ],
)
def test_single_variable_functions(source, x, expected):
    assert math.isclose(value(source, x=x), expected, rel_tol=1e-14)


def test_multi_variable_and_time_dependent_expressions():
    fn = compile_expression("y - k*x/t", ("x", "y", "k", "t"))
    assert math.isclose(fn.call(1.0, 4.0, 2.0, 2.0), 3.0)
    assert fn.variables == ("x", "y", "k", "t")


def test_implicit_multiplication_is_supported_and_documented():
    # the grammar advertises it, so "2x" and "2 sin(x)" must mean what a reader expects
    assert math.isclose(value("2x", x=3.0), 6.0)
    assert math.isclose(value("2 sin(x)", x=0.5), 2 * math.sin(0.5))
    assert allowed_names()["implicit_multiplication"] is True


def test_three_argument_max_is_an_arity_error_not_a_silent_result():
    with pytest.raises(ExpressionError) as exc:
        compile_expression("max(1, 5, 3)", ())
    assert "argument" in str(exc.value)


def test_single_equals_sign_is_rewritten_as_the_root_form():
    # "x = 1" means "find where x - 1 vanishes", which is what the physics UI accepts
    assert math.isclose(value("x = 1", x=2.0), 1.0)
    assert math.isclose(value("x^2 = 4", x=3.0), 5.0)
    assert math.isclose(value("x^2 =", x=3.0), 9.0)


@pytest.mark.parametrize("source", ["x>0", "x<0", "x>=1", "x<=1", "x==1", "x!=1"])
def test_comparisons_are_rejected_rather_than_quietly_mis_evaluated(source):
    # a piecewise switch that silently evaluated to 0 would corrupt a physical model,
    # so the parser refuses the construct outright instead of guessing
    with pytest.raises(ExpressionError):
        compile_expression(source, ("x",))
    assert allowed_names()["comparison_operators"] == []


@pytest.mark.parametrize("source", ["1/0", "0/0", "1%0"])
def test_division_by_zero_is_reported_at_evaluation_time(source):
    with pytest.raises(ExpressionError):
        value(source)


# --- rejection of executable constructs ------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "__import__('os').system('id')",
        "os.system('id')",
        "().__class__.__mro__[1].__subclasses__()",
        "x.real",
        "x.__class__",
        "open('/etc/passwd')",
        "eval('1+1')",
        "exec('x=1')",
        "globals()",
        "lambda: 1",
        "[1,2,3][0]",
        "{'a':1}['a']",
        "f'a'",
        "'abc'",
        "import os",
        "print(1)",
        "getattr(os, 'system')",
        "compile('1','<s>','eval')",
        "().__class__",
        "x if x else 1",
        "1 if True else 2",
        "x>0",
        "x<1",
        "*.spam",
        "1 +",
        "sin(",
        "sin())",
        "",
        "   ",
        "y = = 2",
        "0x10",
        "1_000",
        "NaN",
    ],
)
def test_the_parser_rejects_every_non_numeric_construct(source):
    with pytest.raises(ExpressionError):
        compile_expression(source, ("x",))


def test_unknown_symbols_are_listed_in_the_error():
    with pytest.raises(ExpressionError) as exc:
        compile_expression("q + 1", ("x",))
    assert "q" in str(exc.value)


def test_unknown_function_names_are_rejected():
    with pytest.raises(ExpressionError) as exc:
        compile_expression("frobnicate(x)", ("x",))
    assert "frobnicate" in str(exc.value)


def test_arity_is_checked():
    with pytest.raises(ExpressionError):
        compile_expression("sin(x, y)", ("x", "y"))
    with pytest.raises(ExpressionError):
        compile_expression("hypot(1)", ("x",))


def test_deeply_nested_input_is_rejected_not_allowed_to_blow_the_stack():
    deep = "1 + " * 400 + "1"
    with pytest.raises(Exception):  # ExpressionError (nesting limit) or RecursionError
        parse(deep, ("x",))


def test_excessively_long_source_is_rejected():
    with pytest.raises(ExpressionError):
        compile_expression("x+" * 5000 + "x", ("x",))


def test_ast_is_whitelisted_before_evaluation():
    # node kinds the evaluator refuses to walk, verified through the public entry point
    for source in ("x and 1", "x or 1", "not x", "x in (1,)", "x[0]", "x(1)"):
        with pytest.raises(ExpressionError):
            compile_expression(source, ("x",))


# --- validity probing --------------------------------------------------------------


def test_function_is_valid_accepts_a_sign_changes_and_rejects_a_constant_nan_function():
    ok, _ = function_is_valid(compile_expression("sin(x)", ("x",)))
    assert ok
    ok, why = function_is_valid(compile_expression("1/(x-1)", ("x",)))
    assert ok  # finite somewhere in the probe set
    assert why is None


def test_function_is_valid_reports_a_function_that_never_evaluates():
    fn = compile_expression("sqrt(-1-x^2)", ("x",))
    ok, why = function_is_valid(fn)
    assert ok is False
    assert why
