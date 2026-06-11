from __future__ import annotations

from pathlib import Path

import pytest

from app.config import get_settings
from receipt_shared.contracts import GeminiReceiptExtraction, ReceiptTwinExtraction, UnifiedReceiptExtraction
from receipt_shared.gemini import GeminiAnalyzer, build_analysis_prompt, build_twin_extraction_prompt, build_unified_prompt
from receipt_shared.ynab_client import Category


@pytest.mark.integration
@pytest.mark.enable_socket
def test_gemini_structured_calls_do_not_raise_additional_properties_payload_error():
    settings = get_settings()
    if not settings.gemini_api_key:
        pytest.skip("GEMINI_API_KEY is not configured")

    sample_file = next(iter(sorted(Path("receipt_examples").glob("*.pdf"))), None)
    if sample_file is None:
        pytest.skip("No receipt sample files found in receipt_examples/")

    analyzer = GeminiAnalyzer(settings.gemini_api_key, settings.gemini_model, max_retries=1)
    categories = [Category(id="cat-1", name="Groceries", group_name="Everyday")]
    accounts = [{"id": "acct-1", "name": "Checking"}]
    payees = ["Store"]

    attempts = [
        ("unified", UnifiedReceiptExtraction, build_unified_prompt(settings.gemini_prompt, categories, accounts, payees)),
        ("ynab", GeminiReceiptExtraction, build_analysis_prompt(settings.gemini_prompt, categories, accounts, payees)),
        ("twin", ReceiptTwinExtraction, build_twin_extraction_prompt(settings.gemini_prompt)),
    ]

    for attempt_name, schema_model, prompt in attempts:
        try:
            result = analyzer.analyze_file(
                sample_file,
                prompt_text=prompt,
                mime_type="application/pdf",
                response_schema=schema_model,
            )
        except Exception as exc:  # pragma: no cover - integration-only network call
            message = str(exc)
            assert "additional_properties" not in message
            assert "INVALID_ARGUMENT" not in message
            raise

        assert result.parsed_json is not None, f"{attempt_name} returned no parsed JSON"
