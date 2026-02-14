from __future__ import annotations

import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .contracts import GeminiReceiptExtraction

logger = logging.getLogger(__name__)

UNKNOWN_ACCOUNT_ID = "__unknown__"

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


def build_analysis_prompt(
    user_prompt: str,
    categories: list[Any],
    accounts: list[dict[str, Any]],
    payees: list[str],
) -> str:
    category_lines = "\n".join(
        f"- id={category.id} | group={category.group_name} | name={category.name}"
        for category in categories
    )
    account_lines = "\n".join(
        f"- id={account.get('id', '')} | name={account.get('name', '')}"
        for account in accounts
    )
    account_lines = f"{account_lines}\n- id={UNKNOWN_ACCOUNT_ID} | name=Unknown account (requires user review)"
    payee_lines = "\n".join(f"- {payee}" for payee in payees)

    return f"""
You are analyzing a purchase receipt file and mapping line items to YNAB categories.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Schema:
{{
  "payee_name": "string",
  "account_id": "string",
  "transaction_date": "YYYY-MM-DD",
  "memo": "string",
  "total_amount": number,
  "category_id": "string | null",
  "splits": [{{ "category_id": "string", "category_name": "string", "amount": number, "memo": "string" }}]
}}

Rules:
1. Use account_id values ONLY from the account list below.
   - If unsure which account matches, set account_id to "{UNKNOWN_ACCOUNT_ID}".
2. Use category_id values ONLY from the category list below.
3. Choose exactly one mode:
   - Single category mode: set category_id to one valid category and set splits to []
   - Split mode: set category_id to null and provide 2 or more splits whose amounts sum to total_amount
4. Prefer single category mode unless the receipt clearly maps to multiple categories.
5. For payee_name:
   - If an existing payee clearly matches, return that payee text exactly.
   - Otherwise return a new payee name from the receipt.
6. Keep memo text concise.
7. If date is unclear, use today's date.

Available YNAB categories:
{category_lines}

Available YNAB accounts:
{account_lines}

Existing YNAB payees:
{payee_lines}

Input receipt is provided as an attached file in this request.
""".strip()


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an exception is retryable (transient error)."""
    error_str = str(exc).lower()
    # Check for common transient error patterns
    retryable_patterns = [
        "503",
        "service unavailable",
        "temporarily unavailable",
        "timeout",
        "deadline exceeded",
        "resource exhausted",
        "429",  # rate limit
        "500",  # internal server error
        "502",  # bad gateway
        "504",  # gateway timeout
    ]
    return any(pattern in error_str for pattern in retryable_patterns)


class GeminiAnalyzer:
    def __init__(self, api_key: str, model: str, max_retries: int = 3):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    def analyze_file(self, file_path: Path, prompt_text: str, mime_type: str | None = None) -> GeminiAnalysisResult:
        if genai is None or types is None:
            raise RuntimeError("google-genai dependency is not installed")
        if not file_path.exists():
            raise FileNotFoundError(f"Receipt file not found: {file_path}")

        inferred_mime = mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        started = time.perf_counter()

        # Retry loop with exponential backoff
        for attempt in range(self.max_retries):
            try:
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
                # Success - break out of retry loop
                break
            except Exception as exc:
                is_retryable = _is_retryable_error(exc)

                if not is_retryable or attempt == self.max_retries - 1:
                    # Not retryable or last attempt - re-raise
                    if is_retryable:
                        error_msg = f"Gemini API failed after {self.max_retries} attempts: {exc}"
                        logger.error(error_msg)
                        raise RuntimeError(error_msg) from exc
                    else:
                        # Non-retryable error - fail immediately
                        raise

                # Wait with exponential backoff before retry
                wait_seconds = 2 ** attempt  # 1s, 2s, 4s, ...
                logger.warning(
                    "Gemini API call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1,
                    self.max_retries,
                    wait_seconds,
                    exc,
                )
                time.sleep(wait_seconds)

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
