from __future__ import annotations

import copy
import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .contracts import GeminiReceiptExtraction

logger = logging.getLogger(__name__)

UNKNOWN_ACCOUNT_ID = "__unknown__"
CATEGORY_GUIDANCE_PATH = Path(__file__).resolve().parent / "resources" / "category_guidance.json"
UNSUPPORTED_GEMINI_SCHEMA_KEYS = frozenset({"additionalProperties", "additional_properties"})

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
    parse_source: str | None = None
    structured_output_available: bool = False


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
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Gemini response JSON root must be an object")
    return parsed


@lru_cache(maxsize=1)
def load_category_guidance() -> dict[str, Any]:
    if not CATEGORY_GUIDANCE_PATH.exists():
        logger.warning("Category guidance resource not found: %s", CATEGORY_GUIDANCE_PATH)
        return {}

    try:
        with CATEGORY_GUIDANCE_PATH.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except Exception as exc:  # pragma: no cover - defensive fallback for file parse errors
        logger.warning("Failed loading category guidance resource %s: %s", CATEGORY_GUIDANCE_PATH, exc)
        return {}

    if not isinstance(loaded, dict):
        logger.warning("Category guidance resource is not an object: %s", CATEGORY_GUIDANCE_PATH)
        return {}
    return loaded


def _to_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str):
            stripped = item.strip()
            if stripped:
                result.append(stripped)
    return result


def _format_category_guidance(guidance: dict[str, Any]) -> str:
    if not guidance:
        return "No extra category guidance file was loaded."

    lines: list[str] = []

    category_examples = guidance.get("category_examples", [])
    if isinstance(category_examples, list):
        lines.append("Category examples and intent hints:")
        for entry in category_examples:
            if not isinstance(entry, dict):
                continue
            category_name = str(entry.get("category", "")).strip()
            if not category_name:
                continue
            examples = ", ".join(_to_string_list(entry.get("examples")))
            notes = str(entry.get("notes", "")).strip()
            if examples and notes:
                lines.append(f"- {category_name}: {examples}. Note: {notes}")
            elif examples:
                lines.append(f"- {category_name}: {examples}")
            elif notes:
                lines.append(f"- {category_name}: {notes}")
            else:
                lines.append(f"- {category_name}")

    never_suggest = _to_string_list(guidance.get("never_suggest_categories"))
    if never_suggest:
        lines.append("")
        lines.append("Never suggest these categories:")
        for category_name in never_suggest:
            lines.append(f"- {category_name}")

    edge_case_rules = _to_string_list(guidance.get("edge_case_rules"))
    if edge_case_rules:
        lines.append("")
        lines.append("Edge-case rules:")
        for rule in edge_case_rules:
            lines.append(f"- {rule}")

    model_behavior_rules = _to_string_list(guidance.get("model_behavior_rules"))
    if model_behavior_rules:
        lines.append("")
        lines.append("Model behavior rules:")
        for rule in model_behavior_rules:
            lines.append(f"- {rule}")

    ambiguity = guidance.get("ambiguity_reporting", {})
    if isinstance(ambiguity, dict):
        threshold = ambiguity.get("probability_threshold")
        instruction = str(ambiguity.get("instruction", "")).strip()
        if threshold is not None or instruction:
            lines.append("")
            lines.append("Ambiguity reporting:")
            if threshold is not None:
                lines.append(f"- probability_threshold={threshold}")
            if instruction:
                lines.append(f"- {instruction}")

    return "\n".join(lines) if lines else "No extra category guidance file was loaded."


def _build_reference_context(categories: list[Any], accounts: list[dict[str, Any]], payees: list[str]) -> str:
    category_lines = "\n".join(
        f"- id={category.id} | group={category.group_name} | name={category.name}" for category in categories
    )
    account_lines = "\n".join(
        f"- id={account.get('id', '')} | name={account.get('name', '')}" for account in accounts
    )
    account_lines = f"{account_lines}\n- id={UNKNOWN_ACCOUNT_ID} | name=Unknown account (requires user review)"
    payee_lines = "\n".join(f"- {payee}" for payee in payees) if payees else "- (none cached)"
    category_guidance = _format_category_guidance(load_category_guidance())
    return (
        f"Available YNAB categories:\n{category_lines}\n\n"
        f"Available YNAB accounts:\n{account_lines}\n\n"
        f"Existing YNAB payees:\n{payee_lines}\n\n"
        f"Additional category guidance:\n{category_guidance}"
    )


def build_analysis_prompt(
    user_prompt: str,
    categories: list[Any],
    accounts: list[dict[str, Any]],
    payees: list[str],
) -> str:
    reference_context = _build_reference_context(categories, accounts, payees)

    return f"""
You are analyzing a purchase receipt file and mapping line items to YNAB categories.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Schema:
{{
  "payee_name": "string (can be empty if uncertain)",
  "account_id": "string",
  "transaction_date": "YYYY-MM-DD | null",
  "transaction_time": "HH:MM | null",
  "memo": "string",
  "total_amount": number,
  "category_id": "string | null",
  "splits": [{{ "category_id": "string", "category_name": "string", "amount": number, "memo": "string" }}],
  "category_ambiguity_flags": [
    {{
      "line_item": "string",
      "candidate_category_ids": ["string"],
      "confidence": number,
      "note": "string"
    }}
  ]
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
   - Use an existing YNAB payee only when receipt evidence is clear.
   - If uncertain, set payee_name to an empty string.
6. Keep memo text concise.
7. If date is unclear, set transaction_date to null.
8. If time is unclear or unavailable, set transaction_time to null.
9. If any line item could map to multiple categories with confidence >= 0.70, include it in category_ambiguity_flags.
10. category_ambiguity_flags should be [] when there are no qualifying ambiguous items.

{reference_context}

Input receipt is provided as an attached file in this request.
""".strip()


def build_unified_prompt(
    user_prompt: str,
    categories: list[Any],
    accounts: list[dict[str, Any]],
    payees: list[str],
) -> str:
    reference_context = _build_reference_context(categories, accounts, payees)

    return f"""
You are extracting one unified JSON object from a receipt for both receipt-reality and YNAB draft use.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Unified schema:
{{
  "store_name": "string",
  "store_address": "string",
  "transaction_date": "YYYY-MM-DD | null",
  "transaction_time": "HH:MM | null",
  "currency": "string",
  "line_items": [
    {{
      "index": number,
      "raw_text": "string",
      "translated_text": "string",
      "quantity": "number | null",
      "unit_price": "number | null",
      "line_total": "number | null",
      "tax_code": "string | null",
      "item_type": "product | discount | tax | fee | subtotal | total | other"
    }}
  ],
  "subtotal": "number | null",
  "tax_total": "number | null",
  "total_amount": number,
  "payment_method": "string",
  "receipt_language": "string",
  "payee_name": "string",
  "account_id": "string",
  "memo": "string",
  "category_id": "string | null",
  "splits": [{{ "category_id": "string", "category_name": "string", "amount": number, "memo": "string" }}],
  "category_ambiguity_flags": [
    {{
      "line_item": "string",
      "candidate_category_ids": ["string"],
      "confidence": number,
      "note": "string"
    }}
  ]
}}

Rules:
1. Preserve each line item's raw_text exactly as printed on the receipt.
2. translated_text is optional plain-English clarification only; do not invent information.
3. Ignore non-transaction artifacts (ads, legal boilerplate, coupon terms, barcodes, backside content) unless financially relevant.
4. Classify line item types using the allowed taxonomy.
5. Sign guidance for additive reasoning:
   - product, fee, tax are positive contributions.
   - discount is a negative contribution.
   - subtotal and total rows are labels, not additive rows.
6. Use account_id values ONLY from the account list below. If uncertain, use "{UNKNOWN_ACCOUNT_ID}".
7. Use category_id values ONLY from the category list below.
8. Choose one YNAB mode:
   - Single category mode: category_id set, splits = []
   - Split mode: category_id = null and 2+ splits summing to total_amount
9. If date is unclear, set transaction_date to null.
10. If time is unclear or unavailable, set transaction_time to null.
11. If category confidence is ambiguous (>= 0.70 across candidates), include category_ambiguity_flags.

{reference_context}

Input receipt is provided as an attached file in this request.
""".strip()


def build_twin_extraction_prompt(user_prompt: str) -> str:
    return f"""
You are extracting receipt reality data only.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Schema:
{{
  "store_name": "string",
  "store_address": "string",
  "transaction_date": "YYYY-MM-DD | null",
  "transaction_time": "HH:MM | null",
  "currency": "string",
  "line_items": [
    {{
      "index": number,
      "raw_text": "string",
      "translated_text": "string",
      "quantity": "number | null",
      "unit_price": "number | null",
      "line_total": "number | null",
      "tax_code": "string | null",
      "item_type": "product | discount | tax | fee | subtotal | total | other"
    }}
  ],
  "subtotal": "number | null",
  "tax_total": "number | null",
  "total_amount": number,
  "payment_method": "string",
  "receipt_language": "string"
}}

Rules:
1. Preserve each line item's raw_text exactly as printed.
2. translated_text is optional and must be non-hallucinatory.
3. item_type must use the allowed taxonomy.
4. Ignore non-transaction artifacts unless financially relevant.
5. If date is unclear, set transaction_date to null.
6. If time is unclear or unavailable, set transaction_time to null.
7. Include line_items when possible. Keep uncertain numeric fields as null.

Input receipt is provided as an attached file in this request.
""".strip()


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an exception is retryable (transient error)."""
    error_str = str(exc).lower()
    retryable_patterns = [
        "503",
        "service unavailable",
        "temporarily unavailable",
        "timeout",
        "deadline exceeded",
        "resource exhausted",
        "429",
        "500",
        "502",
        "504",
    ]
    return any(pattern in error_str for pattern in retryable_patterns)


def _is_unsupported_thinking_config_error(exc: Exception) -> bool:
    error_str = str(exc).lower()
    return "thinking level is not supported" in error_str or (
        "thinking" in error_str and "not supported" in error_str
    )


def _to_dict_from_structured(parsed: Any) -> dict[str, Any] | None:
    if parsed is None:
        return None
    if isinstance(parsed, dict):
        return parsed
    if isinstance(parsed, BaseModel):
        return parsed.model_dump(mode="json")
    if hasattr(parsed, "model_dump"):
        maybe_dump = parsed.model_dump(mode="json")
        if isinstance(maybe_dump, dict):
            return maybe_dump
    return None


def _sanitize_gemini_response_json_schema(node: Any) -> Any:
    if isinstance(node, dict):
        sanitized: dict[str, Any] = {}
        for key, value in node.items():
            if key in UNSUPPORTED_GEMINI_SCHEMA_KEYS:
                continue
            sanitized[key] = _sanitize_gemini_response_json_schema(value)
        return sanitized
    if isinstance(node, list):
        return [_sanitize_gemini_response_json_schema(item) for item in node]
    return node


@lru_cache(maxsize=16)
def _cached_response_json_schema(response_schema: type[BaseModel]) -> dict[str, Any]:
    return _sanitize_gemini_response_json_schema(response_schema.model_json_schema())


def build_gemini_response_json_schema(response_schema: type[BaseModel]) -> dict[str, Any]:
    """Build a Gemini-safe JSON schema dict from a Pydantic model class."""
    # google-genai mutates dict schemas in-place; return a copy per request.
    return copy.deepcopy(_cached_response_json_schema(response_schema))


class GeminiAnalyzer:
    def __init__(self, api_key: str, model: str, max_retries: int = 3):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries

    def analyze_file(
        self,
        file_path: Path,
        prompt_text: str,
        mime_type: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> GeminiAnalysisResult:
        if genai is None or types is None:
            raise RuntimeError("google-genai dependency is not installed")
        if not file_path.exists():
            raise FileNotFoundError(f"Receipt file not found: {file_path}")

        inferred_mime = mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        started = time.perf_counter()

        attempt = 0
        thinking_enabled = True
        while attempt < self.max_retries:
            try:
                client = genai.Client(api_key=self.api_key)
                uploaded_file = client.files.upload(file=str(file_path))

                config_kwargs: dict[str, Any] = {
                    "response_mime_type": "application/json",
                }
                if thinking_enabled:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_level="HIGH")
                if response_schema is not None:
                    response_json_schema = build_gemini_response_json_schema(response_schema)
                    supports_response_json_schema = "response_json_schema" in types.GenerateContentConfig.model_fields
                    if supports_response_json_schema:
                        config_kwargs["response_json_schema"] = response_json_schema
                    else:
                        config_kwargs["response_schema"] = response_json_schema

                response = client.models.generate_content(
                    model=self.model,
                    contents=[
                        prompt_text,
                        types.Part.from_uri(
                            file_uri=uploaded_file.uri,
                            mime_type=uploaded_file.mime_type or inferred_mime,
                        ),
                    ],
                    config=types.GenerateContentConfig(**config_kwargs),
                )
                break
            except Exception as exc:
                if thinking_enabled and _is_unsupported_thinking_config_error(exc):
                    logger.warning("Gemini model does not support thinking config; retrying without thinking config")
                    thinking_enabled = False
                    continue

                attempt += 1
                is_retryable = _is_retryable_error(exc)

                if not is_retryable or attempt >= self.max_retries:
                    if is_retryable:
                        error_msg = f"Gemini API failed after {attempt} attempts: {exc}"
                        logger.error(error_msg)
                        raise RuntimeError(error_msg) from exc
                    raise

                wait_seconds = 2 ** (attempt - 1)
                logger.warning(
                    "Gemini API call failed (attempt %d/%d), retrying in %ds: %s",
                    attempt,
                    self.max_retries,
                    wait_seconds,
                    exc,
                )
                time.sleep(wait_seconds)

        raw_output = response.text or ""
        duration_ms = int((time.perf_counter() - started) * 1000)

        parsed_json = _to_dict_from_structured(getattr(response, "parsed", None))
        parse_source = "response_schema" if parsed_json is not None else None
        structured_output_available = parsed_json is not None

        if parsed_json is None and raw_output:
            try:
                parsed_json = parse_json_response(raw_output)
                parse_source = "response_text"
            except ValueError as exc:
                return GeminiAnalysisResult(
                    raw_output=raw_output,
                    parsed_json=None,
                    schema_valid=False,
                    schema_errors=[str(exc)],
                    duration_ms=duration_ms,
                    parse_source=parse_source,
                    structured_output_available=structured_output_available,
                )

        if parsed_json is None:
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=None,
                schema_valid=False,
                schema_errors=["Gemini returned an empty or unparsable response"],
                duration_ms=duration_ms,
                parse_source=parse_source,
                structured_output_available=structured_output_available,
            )

        validator = response_schema or GeminiReceiptExtraction

        try:
            normalized = validator.model_validate(parsed_json)
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=normalized.model_dump(mode="json"),
                schema_valid=True,
                schema_errors=[],
                duration_ms=duration_ms,
                parse_source=parse_source,
                structured_output_available=structured_output_available,
            )
        except ValidationError as exc:
            return GeminiAnalysisResult(
                raw_output=raw_output,
                parsed_json=parsed_json,
                schema_valid=False,
                schema_errors=[err["msg"] for err in exc.errors()],
                duration_ms=duration_ms,
                parse_source=parse_source,
                structured_output_available=structured_output_available,
            )
