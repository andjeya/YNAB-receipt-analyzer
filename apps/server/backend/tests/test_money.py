"""Unit tests for receipt_shared.money — pins CURRENT behavior as of M0.

All cases were live-validated against the actual implementation.
Surprises and future-work notes are in-line.
"""
from __future__ import annotations

import pytest

from receipt_shared.money import dollars_to_milliunits, milliunits_to_dollars


# ---------------------------------------------------------------------------
# dollars_to_milliunits
# ---------------------------------------------------------------------------


class TestDollarsToMilliunits:
    def test_basic_outflow(self):
        assert dollars_to_milliunits(12.34) == -12340

    def test_outflow_true_explicit(self):
        assert dollars_to_milliunits(12.34, outflow=True) == -12340

    def test_outflow_false_returns_positive(self):
        # outflow=False → inflow/positive milliunits
        assert dollars_to_milliunits(12.34, outflow=False) == 12340

    def test_live_validated_119_19(self):
        # Regression: 119.19 is a known floating-point "difficult" value
        assert dollars_to_milliunits(119.19) == -119190

    def test_round_half_up_12_345(self):
        # 12.345 → ROUND_HALF_UP → 12345 milliunits → -12345
        assert dollars_to_milliunits(12.345) == -12345

    def test_small_half_unit_0_005(self):
        # 0.005 rounds to 0.005 → 5 milliunits → -5
        assert dollars_to_milliunits(0.005) == -5

    def test_float_artifact_0_1_plus_0_2(self):
        # 0.1 + 0.2 == 0.30000000000000004 in IEEE 754
        # str() produces "0.30000000000000004"; Decimal("0.30000000000000004")
        # quantized to 0.001 with ROUND_HALF_UP → 0.300 → -300
        artifact = 0.1 + 0.2
        assert artifact == 0.30000000000000004  # confirm we have the artifact
        assert dollars_to_milliunits(artifact) == -300

    def test_near_integer_19_999999999(self):
        # 19.999999999 quantized → 20.000 → 20000 → -20000
        assert dollars_to_milliunits(19.999999999) == -20000

    def test_zero(self):
        # 0 → milliunits = 0; condition > 0 is False; returns 0 regardless of outflow
        assert dollars_to_milliunits(0) == 0
        assert dollars_to_milliunits(0, outflow=True) == 0
        assert dollars_to_milliunits(0, outflow=False) == 0

    def test_string_input(self):
        # Function signature accepts float|int|str; "12.30" is valid
        assert dollars_to_milliunits("12.30") == -12300

    def test_negative_input_outflow_true(self):
        # CURRENT BEHAVIOR (pin): negative dollar amount with outflow=True
        # str(-5.0) → Decimal("-5.0") → -5000 milliunits
        # condition: milliunits (-5000) > 0 is False → returns -5000 unchanged
        # NOTE: current behavior; refund/inflow semantics redesigned in M1
        #       (see docs/agent_loop_state.md)
        assert dollars_to_milliunits(-5.0, outflow=True) == -5000

    def test_negative_input_outflow_false(self):
        # CURRENT BEHAVIOR (pin): negative dollar with outflow=False
        # milliunits = -5000; outflow is False so the negate branch is skipped entirely;
        # returns -5000 unchanged.
        # NOTE: current behavior; refund/inflow semantics redesigned in M1
        assert dollars_to_milliunits(-5.0, outflow=False) == -5000

    def test_integer_input(self):
        assert dollars_to_milliunits(10) == -10000
        assert dollars_to_milliunits(10, outflow=False) == 10000


# ---------------------------------------------------------------------------
# milliunits_to_dollars
# ---------------------------------------------------------------------------


class TestMilliunitsToDollars:
    def test_negative_119190(self):
        assert milliunits_to_dollars(-119190) == -119.19

    def test_positive_12340(self):
        assert milliunits_to_dollars(12340) == 12.34

    def test_zero(self):
        assert milliunits_to_dollars(0) == 0.0

    def test_returns_float(self):
        # CURRENT BEHAVIOR (pin): milliunits_to_dollars returns float, not Decimal
        result = milliunits_to_dollars(12340)
        assert isinstance(result, float)

    def test_negative_single_milliunit(self):
        assert milliunits_to_dollars(-1) == -0.001

    def test_positive_single_milliunit(self):
        assert milliunits_to_dollars(1) == 0.001


# ---------------------------------------------------------------------------
# Round-trip properties
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Exact round-trip: milliunits_to_dollars(dollars_to_milliunits(v, outflow=False)) == v
    for values that are representable to three decimal places.
    """

    @pytest.mark.parametrize(
        "amount",
        [12.34, 119.19, 0.0, 1.0, 100.0, 99.99, 0.001],
    )
    def test_inflow_round_trip(self, amount: float):
        # outflow=False keeps sign positive; milliunits_to_dollars returns same sign
        mu = dollars_to_milliunits(amount, outflow=False)
        back = milliunits_to_dollars(mu)
        assert back == amount

    @pytest.mark.parametrize(
        "amount",
        [12.34, 119.19, 1.0, 100.0, 99.99, 0.001],
    )
    def test_outflow_round_trip_negate(self, amount: float):
        # outflow=True negates; milliunits_to_dollars gives back -amount
        mu = dollars_to_milliunits(amount, outflow=True)
        back = milliunits_to_dollars(mu)
        assert back == -amount

    def test_zero_round_trip(self):
        mu = dollars_to_milliunits(0.0)
        back = milliunits_to_dollars(mu)
        assert back == 0.0
