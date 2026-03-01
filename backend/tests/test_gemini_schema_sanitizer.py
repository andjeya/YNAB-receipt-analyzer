from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

from receipt_shared.ai.types import AIError, AIResponse, AIRequest, TokenUsage
from receipt_shared.contracts import GeminiReceiptExtraction, ReceiptTwinExtraction, UnifiedReceiptExtraction
from receipt_shared.gemini import (
    GeminiAnalyzer,
    _default_thinking_config_for_model,
    build_gemini_response_json_schema,
)


def _iter_schema_keys(node: Any):
    if isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _iter_schema_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_schema_keys(item)


def test_build_gemini_response_json_schema_removes_additional_properties_recursively():
    for model in (GeminiReceiptExtraction, UnifiedReceiptExtraction, ReceiptTwinExtraction):
        schema = build_gemini_response_json_schema(model)
        keys = set(_iter_schema_keys(schema))
        assert "additionalProperties" not in keys
        assert "additional_properties" not in keys


def test_build_gemini_response_json_schema_preserves_required_and_properties():
    schema = build_gemini_response_json_schema(UnifiedReceiptExtraction)
    required = set(schema.get("required", []))
    properties = schema.get("properties", {})

    assert {"total_amount", "account_id"}.issubset(required)
    assert "line_items" in properties
    assert "category_ambiguity_flags" in properties


def test_build_gemini_response_json_schema_preserves_nested_array_item_schema():
    schema = build_gemini_response_json_schema(UnifiedReceiptExtraction)
    line_items_items = schema["properties"]["line_items"]["items"]

    if "$ref" in line_items_items:
        ref_name = str(line_items_items["$ref"]).split("/")[-1]
        line_item_schema = schema["$defs"][ref_name]
    else:
        line_item_schema = line_items_items

    assert "properties" in line_item_schema
    assert "item_type" in line_item_schema["properties"]
    assert "line_total" in line_item_schema["properties"]


def test_build_gemini_response_json_schema_returns_isolated_copy():
    first = build_gemini_response_json_schema(GeminiReceiptExtraction)
    first["properties"].pop("payee_name", None)

    second = build_gemini_response_json_schema(GeminiReceiptExtraction)
    assert "payee_name" in second["properties"]


def test_default_thinking_config_is_model_aware():
    assert _default_thinking_config_for_model("gemini-2.5-flash") == {"thinking_budget": -1}
    assert _default_thinking_config_for_model("gemini-3-flash-preview") == {"thinking_level": "high"}
    assert _default_thinking_config_for_model("gemini-2.0-flash") == {}


class _FakeAIClient:
    def __init__(self, response: AIResponse):
        self.response = response
        self.captured_request: AIRequest | None = None
        self.captured_schema: type | None = None

    def generate_structured(self, request: AIRequest, *, schema: type | None = None) -> AIResponse:
        self.captured_request = request
        self.captured_schema = schema
        return self.response


def test_analyze_file_delegates_to_ai_client_with_schema(tmp_path: Path):
    receipt_path = tmp_path / "receipt.pdf"
    receipt_path.write_bytes(b"test")
    fake_client = _FakeAIClient(
        AIResponse(
            status="success",
            text='{"payee_name":"Store","account_id":"acct-1","transaction_date":"2026-02-15","transaction_time":"10:30","memo":"Imported","total_amount":12.50,"category_id":"cat-1","splits":[],"category_ambiguity_flags":[]}',  # noqa: E501
            parsed={
                "payee_name": "Store",
                "account_id": "acct-1",
                "transaction_date": "2026-02-15",
                "transaction_time": "10:30",
                "memo": "Imported",
                "total_amount": 12.5,
                "category_id": "cat-1",
                "splits": [],
                "category_ambiguity_flags": [],
            },
            usage=TokenUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            cost_usd=Decimal("0.0001"),
            request_id="req-1",
            duration_ms=10,
            error=None,
        )
    )

    analyzer = GeminiAnalyzer(
        api_key="test-key",
        model="gemini-3-flash-preview",
        max_retries=1,
        ai_client=fake_client,
    )
    result = analyzer.analyze_file(receipt_path, prompt_text="Analyze this receipt", response_schema=GeminiReceiptExtraction)

    assert result.schema_valid is True
    assert fake_client.captured_request is not None
    assert fake_client.captured_request.model_id == "gemini-3-flash-preview"
    assert fake_client.captured_schema is GeminiReceiptExtraction


def test_analyze_file_soft_limit_rejection_returns_structured_error(tmp_path: Path):
    receipt_path = tmp_path / "receipt.pdf"
    receipt_path.write_bytes(b"test")

    fake_client = _FakeAIClient(
        AIResponse(
            status="limit_rejected",
            text="",
            parsed=None,
            usage=TokenUsage(total_tokens=0),
            cost_usd=Decimal("0"),
            request_id="req-limit",
            duration_ms=1,
            error=AIError(code="limit_exceeded", message="Daily token cap exceeded", details={}),
        )
    )
    analyzer = GeminiAnalyzer(
        api_key="test-key",
        model="gemini-3-flash-preview",
        max_retries=1,
        ai_client=fake_client,
    )
    result = analyzer.analyze_file(
        receipt_path,
        prompt_text="Analyze this receipt",
        response_schema=GeminiReceiptExtraction,
        limit_behavior="soft_fail",
    )
    assert result.schema_valid is False
    assert result.schema_errors == ["Daily token cap exceeded"]
