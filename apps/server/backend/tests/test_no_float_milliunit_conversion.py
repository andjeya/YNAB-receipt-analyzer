"""Guard test: no ad-hoc float-to-milliunit conversions outside receipt_shared/money.py.

Catches the anti-pattern  int(float(<amount-expr>) * 1000)  which truncates on
sub-cent values instead of rounding.  All dollars→milliunits conversions must
go through receipt_shared.money.dollars_to_milliunits.

This test uses AST walking instead of regex so it correctly handles:
  - Nested parens:   int(float(payload.get("total_amount", 0)) * 1000)
  - round() wrapper: int(round(float(x) * 1000))
  - 1000.0 constant: int(float(x) * 1000.0)

The canonical conversion path (money.py) uses Decimal with ROUND_HALF_UP and
is explicitly allowed.  Non-money uses of * 1000 (timestamps, durations, etc.)
do not involve float() and are not matched.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------


def _has_float_call(node: ast.AST) -> bool:
    """Return True if the AST subtree contains a Call to the bare name `float`."""
    for child in ast.walk(node):
        if (
            isinstance(child, ast.Call)
            and isinstance(child.func, ast.Name)
            and child.func.id == "float"
        ):
            return True
    return False


def _is_thousand_constant(node: ast.AST) -> bool:
    """Return True if node is the numeric literal 1000 or 1000.0."""
    if isinstance(node, ast.Constant):
        return node.value in (1000, 1000.0)
    return False


def _subtree_multiplies_float_by_thousand(node: ast.AST) -> bool:
    """Return True if the subtree contains a BinOp `<expr> * 1000[.0]` whose
    left-hand side (or right-hand side) subtree contains a float() call."""
    for child in ast.walk(node):
        if not isinstance(child, ast.BinOp):
            continue
        if not isinstance(child.op, ast.Mult):
            continue
        left, right = child.left, child.right
        # Either operand can be the constant; the other must contain float().
        if _is_thousand_constant(right) and _has_float_call(left):
            return True
        if _is_thousand_constant(left) and _has_float_call(right):
            return True
    return False


def _is_ad_hoc_milliunit_call(node: ast.Call) -> bool:
    """Return True if *node* is a Call whose name is `int` or `round` and whose
    argument subtree contains BOTH a float() call and a * 1000[.0] multiply."""
    if not isinstance(node.func, ast.Name):
        return False
    if node.func.id not in ("int", "round"):
        return False
    # Walk the entire argument list subtree.
    for arg in node.args:
        if _subtree_multiplies_float_by_thousand(arg):
            return True
    return False


def find_ad_hoc_milliunit_lines(source: str, filename: str = "<string>") -> list[str]:
    """Parse *source* and return one string per offending line.

    Deduplicates by line number so that ``int(round(float(x)*1000))`` is
    reported only once even though both the ``int`` and the ``round`` nodes
    each match individually.
    """
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines = source.splitlines()
    seen_linenos: set[int] = set()
    bad: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_ad_hoc_milliunit_call(node):
            lineno = node.lineno
            if lineno in seen_linenos:
                continue
            seen_linenos.add(lineno)
            line_text = lines[lineno - 1].strip() if lineno <= len(lines) else ""
            bad.append(f"{filename}:{lineno}: {line_text}")
    return bad


# ---------------------------------------------------------------------------
# Self-check: verify the detector catches all three variant patterns
# ---------------------------------------------------------------------------


_SAMPLE_SHOULD_MATCH = textwrap.dedent("""\
    # Variant 1: nested parens, payload.get() inside float()
    x1 = int(float(payload.get("total_amount", 0)) * 1000)

    # Variant 2: round() wrapper around float()*1000
    x2 = int(round(float(x) * 1000))

    # Variant 3: 1000.0 instead of 1000
    x3 = int(float(amount) * 1000.0)
""")

_SAMPLE_SHOULD_NOT_MATCH = textwrap.dedent("""\
    # Canonical path — Decimal, no float() * 1000 pattern
    from receipt_shared.money import dollars_to_milliunits
    result = dollars_to_milliunits(amount, outflow=True)

    # Timestamps — int * 1000 but no float()
    ms = int(seconds * 1000)

    # Decimal arithmetic without float()
    from decimal import Decimal
    mu = int((Decimal(str(amount)) * 1000).to_integral_value())
""")


def test_detector_catches_all_three_variants():
    """Self-check: the AST detector must flag all three ad-hoc patterns."""
    hits = find_ad_hoc_milliunit_lines(_SAMPLE_SHOULD_MATCH, filename="<sample>")
    assert len(hits) == 3, (
        f"Expected 3 matches in sample source, got {len(hits)}:\n" + "\n".join(hits)
    )


def test_detector_ignores_canonical_and_non_money_uses():
    """Self-check: the AST detector must NOT flag canonical or non-money uses."""
    hits = find_ad_hoc_milliunit_lines(_SAMPLE_SHOULD_NOT_MATCH, filename="<sample>")
    assert hits == [], (
        f"False positives in canonical/non-money sample:\n" + "\n".join(hits)
    )


# ---------------------------------------------------------------------------
# Real codebase scan
# ---------------------------------------------------------------------------

# The one file that is *allowed* to use raw * 1000 as part of the canonical
# implementation.
_CANONICAL_FILE = Path("shared/receipt_shared/money.py")


def test_no_float_milliunit_conversion_in_app_code():
    repo_root = Path(__file__).resolve().parents[3]  # …/apps/server
    server_root = repo_root  # apps/server

    backend_files = list((server_root / "backend").rglob("*.py"))
    shared_files = list((server_root / "shared").rglob("*.py"))

    bad_lines: list[str] = []

    for file_path in backend_files + shared_files:
        if "tests" in file_path.parts or "__pycache__" in file_path.parts:
            continue
        # Allow the canonical money module itself
        try:
            relative = file_path.relative_to(server_root)
        except ValueError:
            relative = file_path
        if relative == _CANONICAL_FILE:
            continue

        source = file_path.read_text(encoding="utf-8")
        offenders = find_ad_hoc_milliunit_lines(source, filename=str(file_path.relative_to(server_root)))
        bad_lines.extend(offenders)

    assert bad_lines == [], (
        "Found ad-hoc float→milliunit conversions outside receipt_shared/money.py.\n"
        "Replace int(float(x) * 1000) with dollars_to_milliunits(x, outflow=False) "
        "(or outflow=True for YNAB POST payloads).\n"
        "Offending lines:\n  " + "\n  ".join(bad_lines)
    )
