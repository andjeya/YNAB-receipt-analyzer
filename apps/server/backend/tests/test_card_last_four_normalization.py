"""Tests for normalize_card_last_four helper and contract-level normalization."""

from __future__ import annotations

import pytest

from receipt_shared.contracts import UnifiedReceiptExtraction, normalize_card_last_four


class TestNormalizeCardLastFour:
    """Unit tests for the normalize_card_last_four helper function."""

    def test_masked_pan_spaced(self):
        assert normalize_card_last_four("**** **** **** 5830") == "5830"

    def test_masked_pan_xxxx(self):
        assert normalize_card_last_four("XXXXXXXXXXXX1108") == "1108"

    def test_masked_pan_xxxx_spaced(self):
        assert normalize_card_last_four("XXXX1108") == "1108"

    def test_cash_returns_none(self):
        assert normalize_card_last_four("cash") is None

    def test_alpha_only_returns_none(self):
        assert normalize_card_last_four("abc") is None

    def test_empty_string_returns_none(self):
        assert normalize_card_last_four("") is None

    def test_none_returns_none(self):
        assert normalize_card_last_four(None) is None

    def test_whitespace_only_returns_none(self):
        assert normalize_card_last_four("   ") is None

    def test_four_digits_returns_self(self):
        assert normalize_card_last_four("5830") == "5830"

    def test_full_pan_returns_last_four(self):
        assert normalize_card_last_four("4111111111111108") == "1108"

    def test_three_digits_returns_none(self):
        assert normalize_card_last_four("123") is None

    def test_integer_input(self):
        assert normalize_card_last_four(5830) == "5830"

    def test_integer_full_pan(self):
        assert normalize_card_last_four(4111111111111108) == "1108"

    def test_float_string_drops_decimal(self):
        # Regression: "5830.0" must NOT become "8300" (decimal stripped first).
        assert normalize_card_last_four("5830.0") == "5830"

    def test_float_value_drops_decimal(self):
        assert normalize_card_last_four(5830.0) == "5830"

    def test_trailing_decimal_zeros(self):
        assert normalize_card_last_four("1108.00") == "1108"

    def test_unicode_digits_not_treated_as_ascii_key(self):
        # Arabic-Indic digits must not survive as a non-ASCII key.
        assert normalize_card_last_four("١٢٣٤") is None

    def test_leading_zeros_preserved(self):
        assert normalize_card_last_four("0042") == "0042"


class TestUnifiedExtractionCardLastFour:
    """Test that UnifiedReceiptExtraction normalizes card_last_four correctly."""

    BASE = {
        "store_name": "Test Store",
        "total_amount": 10.00,
        "account_id": "acct-1",
        "category_id": "cat-1",
    }

    def test_masked_pan_normalizes(self):
        data = {**self.BASE, "card_last_four": "**** **** **** 5830"}
        model = UnifiedReceiptExtraction.model_validate(data)
        assert model.card_last_four == "5830"

    def test_cash_normalizes_to_none(self):
        data = {**self.BASE, "card_last_four": "cash"}
        model = UnifiedReceiptExtraction.model_validate(data)
        assert model.card_last_four is None

    def test_null_stays_none(self):
        data = {**self.BASE, "card_last_four": None}
        model = UnifiedReceiptExtraction.model_validate(data)
        assert model.card_last_four is None

    def test_missing_field_defaults_to_none(self):
        model = UnifiedReceiptExtraction.model_validate(self.BASE)
        assert model.card_last_four is None

    def test_round_trip_via_model_dump(self):
        data = {**self.BASE, "card_last_four": "XXXXXXXXXXXX1108"}
        model = UnifiedReceiptExtraction.model_validate(data)
        assert model.card_last_four == "1108"
        dumped = model.model_dump()
        assert dumped["card_last_four"] == "1108"

    def test_four_digit_string_passes_through(self):
        data = {**self.BASE, "card_last_four": "5830"}
        model = UnifiedReceiptExtraction.model_validate(data)
        assert model.card_last_four == "5830"
