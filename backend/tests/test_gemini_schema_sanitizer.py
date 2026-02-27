from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from receipt_shared.contracts import GeminiReceiptExtraction, ReceiptTwinExtraction, UnifiedReceiptExtraction
from receipt_shared.gemini import GeminiAnalyzer, build_gemini_response_json_schema
import receipt_shared.gemini as gemini_module


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


class _FakeThinkingConfig:
    def __init__(self, **_kwargs):
        pass


class _FakePart:
    @staticmethod
    def from_uri(file_uri: str, mime_type: str) -> dict[str, str]:
        return {"file_uri": file_uri, "mime_type": mime_type}


class _FakeGenerateContentConfig:
    model_fields = {"response_json_schema": object()}

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeGenerateContentConfigNoJsonSchema:
    model_fields: dict[str, object] = {}

    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeFiles:
    def upload(self, *, file: str):  # noqa: A002
        return SimpleNamespace(uri=f"gs://fake/{Path(file).name}", mime_type="application/pdf")


class _FakeModels:
    def __init__(self, captured: dict[str, Any]):
        self._captured = captured

    def generate_content(self, **kwargs):
        self._captured.update(kwargs)
        return SimpleNamespace(
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
        )


class _FakeClient:
    def __init__(self, captured: dict[str, Any]):
        self.files = _FakeFiles()
        self.models = _FakeModels(captured)


def test_analyze_file_uses_sanitized_response_json_schema(monkeypatch, tmp_path: Path):
    receipt_path = tmp_path / "receipt.pdf"
    receipt_path.write_bytes(b"test")
    captured: dict[str, Any] = {}

    fake_genai = SimpleNamespace(Client=lambda api_key: _FakeClient(captured))  # noqa: ARG005
    fake_types = SimpleNamespace(
        ThinkingConfig=_FakeThinkingConfig,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfig,
    )

    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    monkeypatch.setattr(gemini_module, "types", fake_types)

    analyzer = GeminiAnalyzer(api_key="test-key", model="test-model", max_retries=1)
    result = analyzer.analyze_file(
        receipt_path,
        prompt_text="Analyze this receipt",
        mime_type="application/pdf",
        response_schema=GeminiReceiptExtraction,
    )

    assert result.schema_valid is True
    config_kwargs = captured["config"].kwargs
    assert "response_json_schema" in config_kwargs
    assert "response_schema" not in config_kwargs

    keys = set(_iter_schema_keys(config_kwargs["response_json_schema"]))
    assert "additionalProperties" not in keys
    assert "additional_properties" not in keys


def test_analyze_file_falls_back_to_response_schema_when_response_json_schema_unsupported(monkeypatch, tmp_path: Path):
    receipt_path = tmp_path / "receipt.pdf"
    receipt_path.write_bytes(b"test")
    captured: dict[str, Any] = {}

    fake_genai = SimpleNamespace(Client=lambda api_key: _FakeClient(captured))  # noqa: ARG005
    fake_types = SimpleNamespace(
        ThinkingConfig=_FakeThinkingConfig,
        Part=_FakePart,
        GenerateContentConfig=_FakeGenerateContentConfigNoJsonSchema,
    )

    monkeypatch.setattr(gemini_module, "genai", fake_genai)
    monkeypatch.setattr(gemini_module, "types", fake_types)

    analyzer = GeminiAnalyzer(api_key="test-key", model="test-model", max_retries=1)
    result = analyzer.analyze_file(
        receipt_path,
        prompt_text="Analyze this receipt",
        mime_type="application/pdf",
        response_schema=GeminiReceiptExtraction,
    )

    assert result.schema_valid is True
    config_kwargs = captured["config"].kwargs
    assert "response_json_schema" not in config_kwargs
    assert "response_schema" in config_kwargs

    keys = set(_iter_schema_keys(config_kwargs["response_schema"]))
    assert "additionalProperties" not in keys
    assert "additional_properties" not in keys
