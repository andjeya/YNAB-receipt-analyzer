from __future__ import annotations

import json
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import GeminiReceiptExtraction

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - imported lazily for environments without dependency
    genai = None
    types = None


@dataclass
class GeminiAnalysisResult:
    raw_output: str
    parsed_json: dict[str, Any] | None
    schema_valid: bool
    schema_errors: list[str]
    duration_ms: int


def parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        if lines and lines[0].strip().lower() == "json":
            lines = lines[1:]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini response was not valid JSON") from exc


def build_analysis_prompt(user_prompt: str, categories: list[Any]) -> str:
    category_lines = "\n".join(
        f"- id={category.id} | group={category.group_name} | name={category.name}"
        for category in categories
    )

    return f"""
You are analyzing a purchase receipt file and mapping line items to YNAB categories.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Schema:
{{
  "payee_name": "string",
  "transaction_date": "YYYY-MM-DD",
  "memo": "string",
  "total_amount": number,
  "splits": [
    {{
      "category_id": "string",
      "category_name": "string",
      "amount": number,
      "memo": "string"
    }}
  ]
}}

Rules:
1. Use category_id values ONLY from the category list below.
2. Ensure splits sum to total_amount.
3. Keep memo text concise.
4. If date is unclear, use today's date.

Available YNAB categories:
{category_lines}

Input receipt is provided as an attached file in this request.
""".strip()


class GeminiAnalyzer:
    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.model = model

    def analyze_file(self, file_path: Path, prompt_text: str, mime_type: str | None = None) -> GeminiAnalysisResult:
        if genai is None or types is None:
            raise RuntimeError("google-genai dependency is not installed")
        if not file_path.exists():
            raise FileNotFoundError(f"Receipt file not found: {file_path}")

        inferred_mime = mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        started = time.perf_counter()

        client = genai.Client(api_key=self.api_key)
        uploaded_file = client.files.upload(file=str(file_path))
        response = client.models.generate_content(
            model=self.model,
            contents=[
                prompt_text,
                types.Part.from_uri(
                    file_uri=uploaded_file.uri,
                    mime_type=uploaded_file.mime_type or inferred_mime,
                ),
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(thinking_level="HIGH"),
            ),
        )

        raw_output = response.text or ""
        duration_ms = int((time.perf_counter() - started) * 1000)
        if not raw_output:
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=None,
                schema_valid=False,
                schema_errors=["Gemini returned an empty response"],
                duration_ms=duration_ms,
            )

        try:
            parsed_json = parse_json_response(raw_output)
        except ValueError as exc:
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=None,
                schema_valid=False,
                schema_errors=[str(exc)],
                duration_ms=duration_ms,
            )

        try:
            normalized = GeminiReceiptExtraction.model_validate(parsed_json)
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=normalized.model_dump(mode="json"),
                schema_valid=True,
                schema_errors=[],
                duration_ms=duration_ms,
            )
        except ValidationError as exc:
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=parsed_json,
                schema_valid=False,
                schema_errors=[err["msg"] for err in exc.errors()],
                duration_ms=duration_ms,
            )
