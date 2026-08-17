"""
zeno.py — Achilles Always Finishes
====================================
Metis Toolbox | Anti-Legion: ONE JOB

Job:         Evaluate Python arithmetic expressions safely, step by step.
             Named for Zeno of Elea (the paradoxes-of-infinity philosopher),
             not Zeno of Citium the Stoic.

Contract:    Exposes TOOL_DEFINITION, handle(), and format_number().
             handle(expression, ans=None) returns a dict and never raises — a
             brain tool that throws crashes Pythia's dispatcher (§2).
             No fetch() — Zeno is request-driven, not polled.
             format_number() is public on purpose: zeno_panel's ANS readout
             renders a stored result and must match the step readout exactly,
             so the two share one formatter rather than keeping two in step.

Errors:      Failure is {"expression", "error", "steps"} — the steps taken
             before the failure are kept, so the readout still shows its work.
             Identifiers are §2 slugs; the text after ": " is human detail:

               parse_error               not valid Python expression syntax
               no_previous_result        `ans` used before anything succeeded
               unknown_name: X           a name outside pi/e/tau/inf/ans
               unknown_function: X       a call outside _FUNCTIONS
               unsupported_operation: X  a node type or operator off the
                                         whitelist — X is what was rejected
               division_by_zero
               result_too_large          a magnitude guard, or _too_large()
               math_domain_error         a whitelisted function refused its
                                         argument (sqrt(-1), log(0))
               evaluation_stalled        _MAX_REDUCTIONS exhausted
               evaluation_failed: X      unforeseen; X is the exception CLASS
                                         name, never its message (§2)

Semantics:   Operands are reduced eagerly, innermost-first, so the step readout
             resolves every node. This deviates from Python in three ways, by
             design — Zeno is a step-by-step visualizer, not an interpreter:
               • `and`/`or` and chained comparisons do NOT short-circuit, e.g.
                 `False and (1/0)` reports "division by zero", not False.
               • bool flows through arithmetic as int, e.g. `True + True` -> 2.
               • `round()` uses banker's rounding, e.g. `round(2.5)` -> 2.
             Operand magnitude is bounded two ways: pre-compute guards (see
             _guard_pow / _MAX_*) stop huge `**` and factorial inputs from
             hanging, and _too_large() rejects any result too big to render as
             a string (CPython caps int->str at ~4300 digits). Both surface a
             clean "result too large" rather than hanging or an opaque error.

Upstream:    pythia.py (registration + dispatch)
Downstream:  panels/zeno_panel.py (display surface)

Requires:    stdlib only: ast, sys, math, logging, typing

V0.02:       Audited 2026-07-23 — error identifiers normalized to §2 slugs and
             routed through private exceptions so each one is a literal at the
             dict site; the AST whitelist put under test for the first time;
             format_number promoted to a public seam.
"""

import ast
import sys
import math
import logging
from typing import Any, Callable, cast

log = logging.getLogger("METIS.zeno")

# ── How failure travels ───────────────────────────────────────────────────────
# Rejections are decided deep inside a recursive walk (_validate) or mid-loop
# (_reduce), but the error IDENTIFIER is assembled in handle(), at the dict it
# returns. That split is deliberate: tests/test_tool_output_contract.py reads
# the identifiers straight out of this file's AST, so each one has to be a
# literal where the dict is built. A code threaded back through a local
# variable is invisible to it — which is exactly how six competing error styles
# grew across the toolbox before §2 existed.
#
# One class per identifier, rather than one class carrying a code string, for
# the same reason: `except _UnknownName` maps to a literal, `exc.code` does not.
# Arithmetic failures need no class at all — ZeroDivisionError, OverflowError
# and ValueError already mean the right things and propagate on their own.


class _ZenoError(Exception):
    """Base for Zeno's own rejections.

    `detail` is the human half of the §2 identifier, and it is always something
    from the user's own expression — a name, a function, an AST node type.
    Never a foreign exception's message, which §2 forbids putting on screen.
    """

    def __init__(self, detail: str = "") -> None:
        super().__init__(detail)
        self.detail = detail


class _UnknownName(_ZenoError):
    """A bare name that is not a Zeno constant or `ans`."""


class _UnknownFunction(_ZenoError):
    """A call to something outside _FUNCTIONS."""


class _Unsupported(_ZenoError):
    """A node type or operator the whitelist does not admit."""


class _Stalled(_ZenoError):
    """The reducer ran out of steps, or stopped making progress."""


# ── Whitelists ────────────────────────────────────────────────────────────────

_CONSTANTS = {
    "pi":  math.pi,
    "e":   math.e,
    "tau": math.tau,
    "inf": math.inf,
}

def _sum_args(*args: Any) -> Any:
    """Whitelist-friendly sum: sum(1, 2, 3) -> 6.

    The builtin sum() takes an iterable, but Zeno rejects list/tuple syntax,
    so the bare builtin is uncallable here. Accept positional args instead.
    """
    return sum(args)


# `pow` is intentionally omitted — `**` covers it, and math.pow always returns
# a float (pow(2, 3) -> 8.0), which would be inconsistent with the `**` operator.
_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "sin":     math.sin,   "cos":     math.cos,   "tan":     math.tan,
    "asin":    math.asin,  "acos":    math.acos,  "atan":    math.atan,
    "atan2":   math.atan2,
    "sinh":    math.sinh,  "cosh":    math.cosh,  "tanh":    math.tanh,
    "log":     math.log,   "log2":    math.log2,  "log10":   math.log10,
    "exp":     math.exp,
    "sqrt":    math.sqrt,  "floor":   math.floor, "ceil":    math.ceil,
    "factorial": math.factorial, "gcd": math.gcd,
    "degrees": math.degrees, "radians": math.radians,
    "abs":     abs, "round": round, "min": min, "max": max, "sum": _sum_args,
}

# ── Magnitude limits ──────────────────────────────────────────────────────────
# Two distinct hazards with arbitrary-precision ints, guarded separately:
#
#   1. COMPUTE hang. `2 ** 100000000` or `factorial(999999999)` don't raise
#      OverflowError — they quietly consume all memory/CPU inside a single
#      _compute call (the reducer's step cap can't help). _guard_pow and the
#      factorial cap reject these BEFORE computing, by checking integer
#      magnitude explicitly (casting to float would corrupt ints: 2 ** 10 would
#      become 1024.0).
#
#   2. DISPLAY overflow. A number can be cheap to compute yet too big to render:
#      CPython caps int->str conversion at sys.get_int_max_str_digits() (4300 by
#      default), so str(10 ** 5000) raises ValueError. _too_large() rejects such
#      results AFTER computing, so a step readout never trips that limit. This
#      also covers paths the pre-compute guards miss (giant literals, long
#      multiplication chains), giving a uniform "result too large".

_MAX_POW_EXPONENT  = 10000        # reject ** when |exponent| exceeds this
_MAX_COMPUTE_BITS  = 50000        # reject ** when the result would blow past this
_MAX_FACTORIAL     = 10000        # reject factorial(n) when n exceeds this

# Backstop for the reduction loop: one step per operand, so an expression this
# large is pathological rather than slow. Reaching it is "evaluation_stalled",
# never a partial answer presented as a whole one.
_MAX_REDUCTIONS    = 500


def _guard_pow(base: Any, exponent: Any) -> None:
    """Raise OverflowError if `base ** exponent` would be too costly to compute.

    Only integer operands are a hazard; float ** float raises OverflowError on
    its own. _reduce catches OverflowError and reports "result too large". This
    only bounds *compute* cost; _too_large() does the final display-size gate.
    """
    if not (isinstance(base, int) and isinstance(exponent, int)):
        return
    if abs(exponent) > _MAX_POW_EXPONENT:
        raise OverflowError("exponent too large")
    # Upper-bound the result size as bits(base) * exponent (base < 2**bit_length).
    # Catches a giant base with a small exponent and a moderate base with a large
    # one. Bases 0/1/-1 never grow, so skip them. Negative exponents yield a small
    # float, not a huge int, so only guard positive ones.
    if exponent > 0 and base not in (0, 1, -1):
        if base.bit_length() * exponent > _MAX_COMPUTE_BITS:
            raise OverflowError("result too large")


def _too_large(v: Any) -> bool:
    """True if v is an integer too big to convert to a string for display.

    CPython caps int<->str conversion (sys.get_int_max_str_digits, default 4300
    digits); str() on a larger int raises ValueError. We reject such values up
    front so that never surfaces as an opaque "evaluation failed: ValueError".
    """
    if not isinstance(v, int) or isinstance(v, bool):
        return False
    limit = sys.get_int_max_str_digits()
    if limit <= 0:                      # 0 means the limit is disabled
        return False
    # digits(v) ≈ bit_length / log2(10); use 3 (< 3.3219) so we trip just before
    # the real limit, leaving a margin for safety.
    return v.bit_length() >= limit * 3

# ── Number formatting ─────────────────────────────────────────────────────────
# Public (the one non-underscore name outside the §3 API block): zeno_panel's
# ANS readout formats a stored result and has to agree with the step readout
# character for character. Two copies of these rules would drift — eudoxus.py
# already keeps one, marked "kept in sync with tools/zeno.py".

def format_number(v: Any) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, float):
        # Guard non-finite values before the int(v) comparison below, which
        # raises on nan/inf (e.g. `inf - inf` -> nan, then int(nan) -> ValueError).
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
    if isinstance(v, int):
        try:
            return str(v)
        except ValueError:
            # Int beyond CPython's str-conversion cap. _validate / _reduce reject
            # these upstream, so this only guards direct callers (e.g. the panel
            # formatting a stored `ans`) — show a placeholder rather than raise.
            return f"<{v.bit_length()}-bit integer>"
    # finite float
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    # 6 significant figures: enough precision to stay useful, few enough to hide
    # float noise (0.1+0.2 -> "0.3") and repeating decimals (100°F->C -> "37.7778").
    # %g strips trailing zeros and keeps very small values (1e-8) intact.
    return f"{v:.6g}"


# ── Operator helpers for unparser ─────────────────────────────────────────────

# Precedence: higher number = binds tighter
_BINOP_PREC = {
    ast.Or:     1,
    ast.And:    2,
    # comparisons: 3
    ast.Add:    4, ast.Sub:    4,
    ast.Mult:   5, ast.Div:    5, ast.FloorDiv: 5, ast.Mod: 5,
    ast.Pow:    7,
}

_BINOP_SYM = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
}

_CMP_SYM = {
    ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=",
    ast.Eq: "==", ast.NotEq: "!=",
}

_UNARY_SYM = {
    ast.USub: "-", ast.UAdd: "+", ast.Not: "not ",
}


def _node_prec(node: ast.expr) -> int:
    """Return precedence of a node (for parenthesization decisions)."""
    if isinstance(node, ast.BinOp):
        op = type(node.op)
        return _BINOP_PREC.get(op, 0)
    if isinstance(node, ast.BoolOp):
        return _BINOP_PREC.get(type(node.op), 0)
    if isinstance(node, ast.Compare):
        return 3
    if isinstance(node, ast.UnaryOp):
        return 6  # between * and **
    return 99  # atoms bind tightest


def _unparse(node: ast.expr) -> str:
    """Custom unparser — handles whitelisted node types only."""
    if isinstance(node, ast.Constant):
        v = node.value
        if isinstance(v, (int, float, bool)):
            return format_number(v)
        return repr(v)

    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        sym = _BINOP_SYM.get(op_type, "?")
        parent_prec = _BINOP_PREC.get(op_type, 0)

        l_str = _unparse(node.left)
        r_str = _unparse(node.right)

        # Left child: parenthesize when lower precedence, plus two extra cases
        # under a Pow parent — because ** is right-associative *and* binds
        # tighter than a leading unary minus:
        #   • equal precedence: a left-nested (2**3)**4 must keep its parens,
        #     else it reads as the wrong "2 ** 3 ** 4".
        #   • a negative numeric base: a reduced -2 must show as "(-2) ** 2",
        #     not "-2 ** 2", which Python reads as -(2 ** 2).
        l_prec = _node_prec(node.left)
        l_neg_const = (op_type is ast.Pow
                       and isinstance(node.left, ast.Constant)
                       and isinstance(node.left.value, (int, float))
                       and not isinstance(node.left.value, bool)
                       and node.left.value < 0)
        if (l_prec < parent_prec
                or (l_prec == parent_prec and op_type is ast.Pow)
                or l_neg_const):
            l_str = f"({l_str})"
        # Right side: also parenthesize at equal precedence for right-assoc ** and sub/div
        r_prec = _node_prec(node.right)
        if r_prec < parent_prec or (r_prec == parent_prec and op_type in (ast.Sub, ast.Div, ast.FloorDiv, ast.Mod)):
            r_str = f"({r_str})"

        return f"{l_str} {sym} {r_str}"

    if isinstance(node, ast.UnaryOp):
        sym = _UNARY_SYM.get(type(node.op), "?")
        operand = _unparse(node.operand)
        if isinstance(node.operand, ast.BinOp):
            prec = _node_prec(node.operand)
            if prec < 6:
                operand = f"({operand})"
        return f"{sym}{operand}"

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            return "<call>"          # unreachable — _validate rejects these first
        args = ", ".join(_unparse(a) for a in node.args)
        return f"{node.func.id}({args})"

    if isinstance(node, ast.Compare):
        parts = [_unparse(node.left)]
        for op, comp in zip(node.ops, node.comparators):
            sym = _CMP_SYM.get(type(op), "?")
            parts.append(sym)
            parts.append(_unparse(comp))
        return " ".join(parts)

    if isinstance(node, ast.BoolOp):
        sym = " and " if isinstance(node.op, ast.And) else " or "
        pieces = []
        op_prec = _BINOP_PREC.get(type(node.op), 0)
        for child in node.values:
            s = _unparse(child)
            if _node_prec(child) < op_prec:
                s = f"({s})"
            pieces.append(s)
        return sym.join(pieces)

    return f"<{type(node).__name__}>"


# ── AST validation ────────────────────────────────────────────────────────────

def _validate(node: ast.AST) -> None:
    """Walk the tree and raise a _ZenoError at the first disallowed node.

    This is the sandbox. Everything reachable from a Zeno expression is on the
    lists above; anything else — attribute access, subscripts, comprehensions,
    a call to a name that is not in _FUNCTIONS — falls through to the raise at
    the bottom. The walk is a whitelist, never a blacklist: a node type nobody
    thought about is rejected by default rather than admitted by default.
    """
    if isinstance(node, ast.Expression):
        _validate(node.body)
        return
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            # The value's type, not the node's: "str" says more than "Constant".
            raise _Unsupported(type(node.value).__name__)
        # Oversized int *literals* can't reach here: CPython's parser enforces
        # the same digit cap, so ast.parse already rejected them as a SyntaxError
        # (-> "parse_error"). Computed oversized ints are caught in _reduce via
        # _too_large() instead.
        return
    if isinstance(node, ast.Name):
        if node.id not in _CONSTANTS and node.id != "ans":
            raise _UnknownName(node.id)
        return
    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div,
                                     ast.FloorDiv, ast.Mod, ast.Pow)):
            raise _Unsupported(type(node.op).__name__)
        _validate(node.left)
        _validate(node.right)
        return
    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, (ast.USub, ast.UAdd, ast.Not)):
            raise _Unsupported(type(node.op).__name__)
        _validate(node.operand)
        return
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise _Unsupported(type(node.func).__name__)
        if node.func.id not in _FUNCTIONS:
            raise _UnknownFunction(node.func.id)
        if node.keywords:
            raise _Unsupported("keyword arguments")
        for arg in node.args:
            _validate(arg)
        return
    if isinstance(node, ast.Compare):
        _validate(node.left)
        for op in node.ops:
            if not isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)):
                raise _Unsupported(type(op).__name__)
        for comp in node.comparators:
            _validate(comp)
        return
    if isinstance(node, ast.BoolOp):
        if not isinstance(node.op, (ast.And, ast.Or)):
            raise _Unsupported(type(node.op).__name__)
        for v in node.values:
            _validate(v)
        return
    raise _Unsupported(type(node).__name__)


# ── Iterative AST reducer ─────────────────────────────────────────────────────

def _snapshot(tree: ast.Expression) -> str:
    """Unparse the body of an ast.Expression."""
    return _unparse(tree.body)


def _all_const_binop(node: ast.AST) -> bool:
    """True if BinOp/UnaryOp/Compare/BoolOp has only Constant direct children."""
    if isinstance(node, ast.BinOp):
        return isinstance(node.left, ast.Constant) and isinstance(node.right, ast.Constant)
    if isinstance(node, ast.UnaryOp):
        return isinstance(node.operand, ast.Constant)
    if isinstance(node, ast.Compare):
        return (isinstance(node.left, ast.Constant)
                and all(isinstance(c, ast.Constant) for c in node.comparators))
    if isinstance(node, ast.BoolOp):
        return all(isinstance(v, ast.Constant) for v in node.values)
    if isinstance(node, ast.Call):
        return (isinstance(node.func, ast.Name)
                and node.func.id in _FUNCTIONS
                and all(isinstance(a, ast.Constant) for a in node.args)
                and not node.keywords)
    return False


def _const_value(node: ast.AST) -> Any:
    """Value of an ast.Constant that _all_const_binop already vetted. Typed Any
    on purpose: _validate() guarantees the value is numeric (int/float/bool),
    a cross-function invariant mypy cannot see — and the full Constant.value
    union (str | bytes | complex | ...) would poison every arithmetic op here."""
    return cast(ast.Constant, node).value


def _compute(node: ast.AST) -> Any:
    """Evaluate a fully-concrete arithmetic node. May raise."""
    if isinstance(node, ast.BinOp):
        l, r = _const_value(node.left), _const_value(node.right)
        op = node.op
        if isinstance(op, ast.Add):      return l + r
        if isinstance(op, ast.Sub):      return l - r
        if isinstance(op, ast.Mult):     return l * r
        if isinstance(op, ast.Div):      return l / r
        if isinstance(op, ast.FloorDiv): return l // r
        if isinstance(op, ast.Mod):      return l % r
        if isinstance(op, ast.Pow):
            _guard_pow(l, r)
            return l ** r

    if isinstance(node, ast.UnaryOp):
        v = _const_value(node.operand)
        if isinstance(node.op, ast.USub): return -v
        if isinstance(node.op, ast.UAdd): return +v
        if isinstance(node.op, ast.Not):  return not v

    if isinstance(node, ast.Compare):
        left = _const_value(node.left)
        result = True
        prev = left
        for cmp_op, comp in zip(node.ops, node.comparators):
            r = _const_value(comp)
            if isinstance(cmp_op, ast.Lt):    result = result and (prev < r)
            elif isinstance(cmp_op, ast.LtE): result = result and (prev <= r)
            elif isinstance(cmp_op, ast.Gt):  result = result and (prev > r)
            elif isinstance(cmp_op, ast.GtE): result = result and (prev >= r)
            elif isinstance(cmp_op, ast.Eq):  result = result and (prev == r)
            elif isinstance(cmp_op, ast.NotEq): result = result and (prev != r)
            prev = r
        return result

    if isinstance(node, ast.BoolOp):
        vals = [_const_value(v) for v in node.values]
        if isinstance(node.op, ast.And):
            r = vals[0]
            for v in vals[1:]:
                r = r and v
            return r
        else:
            r = vals[0]
            for v in vals[1:]:
                r = r or v
            return r

    if isinstance(node, ast.Call):
        fn = _FUNCTIONS[cast(ast.Name, node.func).id]
        args = [_const_value(a) for a in node.args]
        # factorial grows super-exponentially and won't raise on huge ints —
        # bound it like ** above. OverflowError -> _reduce -> "result too large".
        if cast(ast.Name, node.func).id == "factorial" and args and args[0] > _MAX_FACTORIAL:
            raise OverflowError("factorial argument too large")
        return fn(*args)

    # Unreachable: _all_const_binop vetted this node and _validate vetted its
    # operator. RuntimeError rather than ValueError so an invariant break is
    # reported as "evaluation_failed", not mistaken for a math domain error.
    raise RuntimeError(f"cannot compute {type(node).__name__}")


class _Replacer(ast.NodeTransformer):
    """Replace a specific node (by identity) with a new node."""
    def __init__(self, target: ast.AST, replacement: ast.AST) -> None:
        self._target = target
        self._replacement = replacement

    def generic_visit(self, node: ast.AST) -> ast.AST:
        if node is self._target:
            return self._replacement
        return super().generic_visit(node)


def _find_reducible(node: ast.AST) -> ast.AST | None:
    """
    Leftmost-innermost walk: return the first node that is all-constant
    and can be reduced. Returns None if none found.
    """
    # Post-order: children first (innermost), then self
    for child in ast.iter_child_nodes(node):
        found = _find_reducible(child)
        if found is not None:
            return found
    if _all_const_binop(node):
        return node
    return None


def _find_substitutable_name(node: ast.AST) -> ast.Name | None:
    """Leftmost Name node in _CONSTANTS or 'ans' (skips Call.func positions)."""
    if isinstance(node, ast.Name):
        return node
    if isinstance(node, ast.Call):
        # Skip node.func — it's a function name, not a substitutable constant
        for arg in node.args:
            found = _find_substitutable_name(arg)
            if found is not None:
                return found
        return None
    for child in ast.iter_child_nodes(node):
        found = _find_substitutable_name(child)
        if found is not None:
            return found
    return None


def _lookup(name: str, ans_val: float | None) -> Any:
    """Value behind a bare name. Raises _UnknownName if there isn't one.

    Unreachable from handle(): _validate has already refused any name outside
    _CONSTANTS/`ans`, and handle() refuses `ans` when no previous result was
    given. Kept because the alternative to raising is `_CONSTANTS.get()`
    returning None and a Constant(value=None) silently entering the tree.
    """
    val = ans_val if name == "ans" else _CONSTANTS.get(name)
    if val is None:
        raise _UnknownName(name)
    return val


def _reduce(tree: ast.Expression, ans_val: float | None, steps: list[str]) -> Any:
    """
    Run the iterative reduction loop and return the final value.

    Appends to steps as it goes; tree is mutated in place via NodeTransformer.
    Failures propagate as exceptions — ZeroDivisionError / OverflowError /
    ValueError straight from the arithmetic, _ZenoError from Zeno's own checks
    — and handle() turns each into its §2 identifier. Whatever steps were taken
    before the failure stay in `steps`, so a failed readout still shows its work.
    """
    for _ in range(_MAX_REDUCTIONS):
        body = tree.body

        # Done if root is a Constant
        if isinstance(body, ast.Constant):
            return body.value

        # Done if root is a substitutable Name
        if isinstance(body, ast.Name):
            new_node = ast.Constant(value=_lookup(body.id, ans_val))
            ast.copy_location(new_node, body)
            tree.body = new_node
            steps.append(_snapshot(tree))
            return new_node.value

        # Try to find and reduce an all-constant node
        target = _find_reducible(body)
        if target is not None:
            val = _compute(target)

            # Computed fine but too big to render? Reject before it reaches a
            # snapshot (where str() would raise). Covers *, factorial, etc.
            if _too_large(val):
                raise OverflowError("result too large to display")

            new_node = ast.Constant(value=val)
            ast.copy_location(new_node, target)
            tree.body = _Replacer(target, new_node).visit(tree.body)
            steps.append(_snapshot(tree))
            continue

        # Try to substitute a Name
        name_node = _find_substitutable_name(body)
        if name_node is not None:
            new_node = ast.Constant(value=_lookup(name_node.id, ans_val))
            ast.copy_location(new_node, name_node)
            tree.body = _Replacer(name_node, new_node).visit(tree.body)
            steps.append(_snapshot(tree))
            continue

        raise _Stalled("no reducible node")

    raise _Stalled("reduction limit exceeded")


# ── Public API ────────────────────────────────────────────────────────────────

TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "calculate",
        "description": (
            "Evaluate an arithmetic expression and return the result plus the "
            "reduction steps that produced it. Call whenever the user asks for a "
            "calculation, or when a number has to be worked out from values a "
            "tool returned — do not do the arithmetic yourself. "
            "Supports +, -, *, /, //, %, ** and math functions (sin, cos, sqrt, "
            "log, etc.), the constants pi, e, tau, inf, and 'ans' for the "
            "previous result."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic expression to evaluate.",
                },
                "ans": {
                    "type": "number",
                    "description": "Value to substitute for the `ans` variable if used.",
                },
            },
            "required": ["expression"],
        },
    },
}


def handle(expression: str, ans: float | None = None) -> dict[str, Any]:
    """
    Evaluate expression and return a result dict. Never raises.

    Success:
        {"expression": ..., "result": float, "display": str, "steps": [...]}
    Failure:
        {"expression": ..., "error": str, "steps": [...]}

    Every error identifier in the module is a literal in this function — see
    the "How failure travels" note at the top for why they are assembled here
    rather than where the failure is detected.
    """
    steps: list[str] = []
    try:
        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError:
            return {"expression": expression, "error": "parse_error", "steps": steps}

        # Check for ans requirement
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == "ans":
                if ans is None:
                    return {"expression": expression, "error": "no_previous_result",
                            "steps": steps}
                break

        try:
            _validate(tree)
            steps.append(_snapshot(tree))        # initial snapshot
            result_val = _reduce(tree, ans, steps)
        except _UnknownName as exc:
            return {"expression": expression, "error": f"unknown_name: {exc.detail}",
                    "steps": steps}
        except _UnknownFunction as exc:
            return {"expression": expression, "error": f"unknown_function: {exc.detail}",
                    "steps": steps}
        except _Unsupported as exc:
            return {"expression": expression, "error": f"unsupported_operation: {exc.detail}",
                    "steps": steps}
        except _Stalled:
            return {"expression": expression, "error": "evaluation_stalled", "steps": steps}
        except ZeroDivisionError:
            return {"expression": expression, "error": "division_by_zero", "steps": steps}
        except OverflowError:
            # Both magnitude hazards land here: the pre-compute guards and
            # _too_large()'s display gate. One identifier, because the caller's
            # move is the same either way — ask for a smaller number.
            return {"expression": expression, "error": "result_too_large", "steps": steps}
        except ValueError:
            # A whitelisted math function refusing its argument: sqrt(-1),
            # log(0), factorial(-1). The exception's own message is discarded
            # rather than forwarded — §2 bans raw exception text outright.
            return {"expression": expression, "error": "math_domain_error", "steps": steps}

        return {
            "expression": expression,
            "result":     result_val,
            "display":    format_number(result_val),
            "steps":      steps,
        }

    except Exception as exc:
        # The log keeps the exception's message; the returned identifier does
        # not. §2's ban on raw exception text is about what reaches the model
        # and the screen — the local log is where a human goes to debug, and
        # dropping the message there would buy nothing.
        log.error("Zeno: unexpected failure: %s: %s", type(exc).__name__, exc)
        return {"expression": expression,
                "error": f"evaluation_failed: {type(exc).__name__}", "steps": steps}


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        # ── existing baseline ──────────────────────────────────────────────
        ("2 + 2",            None),
        ("(2 + 3) * sin(pi/2)", None),
        ("0.1 + 0.2",        None),
        ("sqrt(144) + log(e)", None),
        ("ans + 1",          10.0),
        ("1 / 0",            None),
        ("undeclared_var",   None),
        # ── #1 DoS guards: must return an error promptly, not hang ──────────
        ("9**9**9**9",            None),   # -> error (exponent too large)
        ("2 ** 100000000",        None),   # -> error
        ("999999999999999 ** 999999999999999", None),  # -> error
        ("factorial(999999999)",  None),   # -> error
        ("2**10",                 None),   # still works -> 1024 (int)
        ("factorial(5)",          None),   # still works -> 120
        # ── display overflow: cheap to compute but too big to render ───────
        ("10**5000",              None),   # 5001-digit result -> "result too large"
        ("factorial(10000)",      None),   # ~35k-digit result -> "result too large"
        ("2**100",                None),   # 31-digit int, displayable -> still works
        # ── #2 non-finite formatting ───────────────────────────────────────
        ("inf - inf",             None),   # display "nan", NOT an error
        ("inf + inf",             None),   # display "inf"
        # ── #3 sum wrapper ─────────────────────────────────────────────────
        ("sum(1, 2, 3)",          None),   # -> 6
        # ── #5 unary/power unparser snapshots (inspect by eye) ─────────────
        ("-2**2",                 None),   # -(2**2) -> -4
        ("(-2)**2",               None),   # parens preserved on base -> 4
        ("2**-2",                 None),   # Pow with UnaryOp exponent -> 0.25
        ("(2**3)**4",             None),   # left-nested ** -> initial snapshot keeps parens
        # ── #4 short-circuit choice (documents eager reduction) ────────────
        ("False and (1/0)",       None),   # -> "division by zero" (no short-circuit)
    ]
    for expr, prev_ans in tests:
        r = handle(expr, ans=prev_ans)
        print(f"\n  expr : {expr!r}  (ans={prev_ans})")
        for step in r.get("steps", []):
            print(f"    {step}")
        if "error" in r:
            print(f"  ERROR: {r['error']}")
        else:
            print(f"  = {r['display']}")
