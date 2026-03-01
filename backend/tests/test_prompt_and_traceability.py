from __future__ import annotations

from types import SimpleNamespace

from app.services.validation import build_initial_validation_payload
from app.services.ynab import _append_receipt_id_marker
from receipt_shared.contracts import GeminiReceiptExtraction
from receipt_shared.gemini import build_analysis_prompt, build_unified_prompt


class TestGeminiPromptAndContracts:
    def test_prompt_uses_nullable_date_and_never_today_fallback(self):
        prompt = build_analysis_prompt(
            "Map categories correctly.",
            [SimpleNamespace(id="cat-1", group_name="Essentials", name="Groceries")],
            [{"id": "acct-1", "name": "Checking"}],
            ["Trader Joe's"],
        )

        assert "transaction_date\": \"YYYY-MM-DD | null\"" in prompt
        assert "transaction_time\": \"HH:MM | null\"" in prompt
        assert "If date is unclear, set transaction_date to null." in prompt
        assert "If time is unclear or unavailable, set transaction_time to null." in prompt
        assert "If date is unclear, use today's date." not in prompt
        assert "Never suggest these categories:" in prompt
        assert "category_ambiguity_flags" in prompt
        assert "Describe what was purchased, not where." in prompt
        assert 'Preferred format: "Bucket: item, item; Bucket: item".' in prompt
        assert 'Do NOT repeat payee/store names or phrases like "at <store>".' in prompt

    def test_unified_prompt_has_high_signal_memo_rules(self):
        prompt = build_unified_prompt(
            "Map categories correctly.",
            [SimpleNamespace(id="cat-1", group_name="Essentials", name="Groceries")],
            [{"id": "acct-1", "name": "Checking"}],
            ["Trader Joe's"],
        )

        assert "If date is unclear, set transaction_date to null." in prompt
        assert "If time is unclear or unavailable, set transaction_time to null." in prompt
        assert "Describe what was purchased, not where." in prompt
        assert 'Preferred format: "Bucket: item, item; Bucket: item".' in prompt
        assert 'Do NOT repeat payee/store names or phrases like "at <store>".' in prompt

    def test_contract_allows_uncertain_payee_and_date(self):
        parsed = GeminiReceiptExtraction.model_validate(
            {
                "payee_name": "",
                "account_id": "acct-1",
                "transaction_date": None,
                "transaction_time": None,
                "memo": "",
                "total_amount": 42.11,
                "category_id": "cat-1",
                "splits": [],
                "category_ambiguity_flags": [
                    {
                        "line_item": "mulch",
                        "candidate_category_ids": ["cat-1", "cat-2"],
                        "confidence": 0.72,
                        "note": "Could be maintenance or upgrades depending on intent.",
                    }
                ],
            }
        )

        assert parsed.payee_name == ""
        assert parsed.transaction_date is None
        assert parsed.transaction_time is None
        assert len(parsed.category_ambiguity_flags) == 1


class TestValidationPayloadDefaults:
    def test_initial_payload_keeps_unknown_payee_and_date_blank(self):
        payload = build_initial_validation_payload(
            {
                "payee_name": "",
                "account_id": "acct-1",
                "transaction_date": None,
                "transaction_time": None,
                "memo": "",
                "total_amount": 19.99,
                "category_id": "cat-1",
                "splits": [],
            },
            default_account_id=None,
        )

        assert payload["payee_name"] == ""
        assert payload["transaction_date"] is None
        assert payload["transaction_time"] is None
        assert payload["memo"] == "Imported from receipt via Gemini"


class TestReceiptMemoMarker:
    def test_receipt_marker_appends_once(self):
        receipt_id = "11111111-2222-4333-8444-555555555555"
        memo = _append_receipt_id_marker("Lunch", receipt_id)

        assert memo == "Lunch [receipt_id:11111111-2222-4333-8444-555555555555]"
        assert _append_receipt_id_marker(memo, receipt_id) == memo
        assert _append_receipt_id_marker("", receipt_id) == "[receipt_id:11111111-2222-4333-8444-555555555555]"
