from __future__ import annotations

import copy
import json
import logging
import mimetypes
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from .ai import AIClient, AIProviderError, AIRequest
from .contracts import GeminiReceiptExtraction

logger = logging.getLogger(__name__)

UNKNOWN_ACCOUNT_ID = "__unknown__"
CATEGORY_GUIDANCE_PATH = Path(__file__).resolve().parent / "resources" / "category_guidance.json"
UNSUPPORTED_GEMINI_SCHEMA_KEYS = frozenset({"additionalProperties", "additional_properties"})


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
  "transaction_date_raw": "string (literal date text seen, e.g. \"5/12\")",
  "date_confidence": "high | low",
  "date_note": "string (short explanation when low confidence)",
  "transaction_time": "HH:MM | null",
  "memo": "string",
  "card_last_four": "string | null",
  "total_amount": number,
  "transaction_kind": "purchase | refund",
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
6. Write memo as a high-signal purchase summary:
   - Describe what was purchased, not where.
   - Do NOT repeat payee/store names or phrases like "at <store>".
   - Use 1-3 thematic buckets with concrete items.
   - Preferred format: "Bucket: item, item; Bucket: item".
   - Avoid vague-only output like "Groceries" without item detail.
   - Keep it concise (ideally <= 180 characters) while preserving useful detail.
7. Date handling:
   - Set transaction_date to a full YYYY-MM-DD ONLY when confident AND the year is printed or unambiguous.
   - Always copy the literal date text you see into transaction_date_raw (e.g. "5/12" or "5/12/2026"). Use the date that best represents when the transaction occurred; if several dates appear, put the best match first.
   - Set date_confidence to "low" when the year is missing, multiple dates appear, or the date is handwritten/unclear; otherwise "high".
   - When the year is missing, leave transaction_date null and rely on transaction_date_raw (the year is completed downstream). Briefly explain in date_note.
8. If time is unclear or unavailable, set transaction_time to null.
9. If any line item could map to multiple categories with confidence >= 0.70, include it in category_ambiguity_flags.
10. category_ambiguity_flags should be [] when there are no qualifying ambiguous items.
11. If the document is clearly a refund, return, or credit (negative running total, REFUND/RETURN/CREDIT headers, parenthesized/negative line totals throughout), set transaction_kind to "refund"; otherwise "purchase". Report all amounts (total and line totals) as POSITIVE magnitudes — do not negate them; the kind carries direction. If the receipt mixes purchases and refunds, treat it as "purchase" (mixed receipts are not yet supported).
12. Extract card_last_four: the last 4 digits of the card used for payment, as a 4-character digit string. For a masked PAN (e.g. **** **** **** 5830, XXXXXXXXXXXX1108) use its trailing 4 digits. For Apple Pay / Google Pay / digital wallets use the device account number's last 4 if printed (stable per device-card). Set to null for cash, gift cards with no number, or when no card digits are printed. Never invent digits.

{reference_context}

Input receipt is provided as an attached file in this request.
""".strip()


def build_unified_prompt(
    user_prompt: str,
    categories: list[Any],
    accounts: list[dict[str, Any]],
    payees: list[str],
    category_hints: str = "",
) -> str:
    reference_context = _build_reference_context(categories, accounts, payees)
    if category_hints.strip():
        reference_context = (
            f"{reference_context}\n\n"
            "Learned categorization habits (the user's past payee→category choices). "
            "Prefer these when the receipt's payee matches, and use them to ground "
            "candidate_arrangements:\n"
            f"{category_hints}"
        )

    return f"""
You are extracting one unified JSON object from a receipt for both receipt-reality and YNAB draft use.

User instruction: {user_prompt}

Return STRICT JSON ONLY. No markdown. No prose.

Unified schema:
{{
  "store_name": "string",
  "store_address": "string",
  "transaction_date": "YYYY-MM-DD | null",
  "transaction_date_raw": "string (literal date text seen, e.g. \"5/12\")",
  "date_confidence": "high | low",
  "date_note": "string (short explanation when low confidence)",
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
  "card_last_four": "string | null",
  "receipt_language": "string",
  "payee_name": "string",
  "account_id": "string",
  "memo": "string",
  "transaction_kind": "purchase | refund",
  "category_id": "string | null",
  "splits": [{{ "category_id": "string", "category_name": "string", "amount": number, "memo": "string" }}],
  "category_ambiguity_flags": [
    {{
      "line_item": "string",
      "candidate_category_ids": ["string"],
      "confidence": number,
      "note": "string"
    }}
  ],
  "candidate_arrangements": [
    {{
      "label": "string (short, e.g. \"Groceries\" or \"Groceries + Household\")",
      "rationale": "string (one line; cite the learned habit if relevant)",
      "confidence": number,
      "category_id": "string | null",
      "splits": [{{ "category_id": "string", "amount": number, "memo": "string" }}]
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
9. Date handling:
   - Set transaction_date to a full YYYY-MM-DD ONLY when confident AND the year is printed or unambiguous.
   - Always copy the literal date text you see into transaction_date_raw (e.g. "5/12" or "5/12/2026"). Use the date that best represents when the transaction occurred; if several dates appear, put the best match first.
   - Set date_confidence to "low" when the year is missing, multiple dates appear, or the date is handwritten/unclear; otherwise "high".
   - When the year is missing, leave transaction_date null and rely on transaction_date_raw (the year is completed downstream). Explain briefly in date_note (e.g. "Two dates detected; 'Date In' 5/12 best matches; year not printed").
10. If time is unclear or unavailable, set transaction_time to null.
11. Write memo as a high-signal purchase summary:
   - Describe what was purchased, not where.
   - Do NOT repeat payee/store names or phrases like "at <store>".
   - Use 1-3 thematic buckets with concrete items.
   - Preferred format: "Bucket: item, item; Bucket: item".
   - Avoid vague-only output like "Groceries" without item detail.
   - Keep it concise (ideally <= 180 characters) while preserving useful detail.
12. If category confidence is ambiguous (>= 0.70 across candidates), include category_ambiguity_flags.
13. When category confidence is low (you populated category_ambiguity_flags), ALSO populate candidate_arrangements with up to 3 DISTINCT whole-receipt options, most likely first. Each is a complete choice: either a single category_id (splits=[]) or 2+ splits whose amounts sum to total_amount. Make them genuinely different (e.g. all-Groceries vs a Groceries+Household split), set a one-line rationale and a 0..1 confidence, and ground them in the learned categorization habits when the payee matches. Leave candidate_arrangements=[] when the single category is obvious.
14. If the document is clearly a refund, return, or credit (negative running total, REFUND/RETURN/CREDIT headers, parenthesized/negative line totals throughout), set transaction_kind to "refund"; otherwise "purchase". Report all amounts (total and line totals) as POSITIVE magnitudes — do not negate them; the kind carries direction. If the receipt mixes purchases and refunds, treat it as "purchase" (mixed receipts are not yet supported).
15. Extract card_last_four: the last 4 digits of the card used for payment, as a 4-character digit string. For a masked PAN (e.g. **** **** **** 5830, XXXXXXXXXXXX1108) use its trailing 4 digits. For Apple Pay / Google Pay / digital wallets use the device account number's last 4 if printed (stable per device-card). Set to null for cash, gift cards with no number, or when no card digits are printed. Never invent digits.

{reference_context}

Input receipt is provided as an attached file in this request.
""".strip()


def build_organize_prompt(
    instruction: str,
    current_payload: dict[str, Any],
    line_items: list[dict[str, Any]],
    categories: list[Any],
) -> str:
    """Prompt for type-to-organize: rework the current category/splits to satisfy a
    plain-English instruction, returning 1-3 complete proposals."""
    category_lines = "\n".join(
        f"- id={category.id} | group={category.group_name} | name={category.name}" for category in categories
    )
    item_lines = "\n".join(
        f"- {item.get('raw_text') or item.get('translated_text') or ''}"
        f" (type={item.get('item_type', 'product')}, total={item.get('line_total')})"
        for item in line_items
    ) or "- (no itemized lines available)"
    total_amount = current_payload.get("total_amount")
    current_category = current_payload.get("category_id")
    current_splits = current_payload.get("splits") or []

    return f"""
You reorganize a receipt's YNAB categories/splits to match a user's instruction.

User instruction: {instruction}

Current total_amount: {total_amount}
Current category_id: {current_category}
Current splits: {current_splits}

Receipt line items:
{item_lines}

Available YNAB categories:
{category_lines}

Return STRICT JSON ONLY. No markdown. No prose. Schema:
{{
  "proposals": [
    {{
      "label": "string (short, e.g. \"Gifts\" or \"Dining Out + IOU\")",
      "rationale": "string (one line explaining the split)",
      "confidence": number,
      "category_id": "string | null",
      "splits": [{{ "category_id": "string", "amount": number, "memo": "string" }}]
    }}
  ]
}}

Rules:
1. Return 1 to 3 proposals, most likely first, that satisfy the instruction.
2. Each proposal is a COMPLETE choice: either a single category_id (splits=[]) or 2+ splits whose amounts sum to total_amount.
3. Use category_id values ONLY from the category list above.
4. When the instruction implies a fraction (e.g. "split with two friends" → divide by the number of people), reflect it in the split amounts; amounts must sum to total_amount.
5. If the instruction is ambiguous, offer the 2 most likely interpretations as separate proposals.
6. Return {{"proposals": []}} only if the instruction can't be satisfied with the available categories.
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
  "transaction_date_raw": "string (literal date text seen, e.g. \"5/12\")",
  "date_confidence": "high | low",
  "date_note": "string (short explanation when low confidence)",
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
  "card_last_four": "string | null",
  "receipt_language": "string"
}}

Rules:
1. Preserve each line item's raw_text exactly as printed.
2. translated_text is optional and must be non-hallucinatory.
3. item_type must use the allowed taxonomy.
4. Ignore non-transaction artifacts unless financially relevant.
5. Date handling: set transaction_date to a full YYYY-MM-DD only when confident the year is known; always copy the literal date text into transaction_date_raw; set date_confidence to "low" (with a short date_note) when the year is missing or the date is unclear, leaving transaction_date null so the year is completed downstream.
6. If time is unclear or unavailable, set transaction_time to null.
7. Include line_items when possible. Keep uncertain numeric fields as null.
8. Extract card_last_four: the last 4 digits of the card used for payment, as a 4-character digit string. For a masked PAN (e.g. **** **** **** 5830, XXXXXXXXXXXX1108) use its trailing 4 digits. For Apple Pay / Google Pay / digital wallets use the device account number's last 4 if printed (stable per device-card). Set to null for cash, gift cards with no number, or when no card digits are printed. Never invent digits.

Input receipt is provided as an attached file in this request.
""".strip()


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an exception is retryable (transient error).

    Checks exception type first, then falls back to HTTP status code extraction.
    Avoids substring matching on the full error string to prevent false positives
    (e.g. a non-retryable error whose message happens to contain "500").
    """
    try:
        import google.api_core.exceptions as _gax

        _RETRYABLE_TYPES = (
            _gax.ServiceUnavailable,
            _gax.DeadlineExceeded,
            _gax.ResourceExhausted,
            _gax.InternalServerError,
            _gax.BadGateway,
            _gax.GatewayTimeout,
        )
        if isinstance(exc, _RETRYABLE_TYPES):
            return True
        # Non-retryable google API exception — don't fall through to string matching.
        if isinstance(exc, _gax.GoogleAPICallError):
            return False
    except ImportError:
        pass

    # Fallback: extract HTTP status code from the exception, not substring match.
    retryable_status_codes = {429, 500, 502, 503, 504}
    status_code: int | None = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code in retryable_status_codes

    retryable_messages = [
        "service unavailable",
        "temporarily unavailable",
        "timeout",
        "deadline exceeded",
        "resource exhausted",
    ]
    error_str = str(exc).lower()
    return any(pattern in error_str for pattern in retryable_messages)


def _default_thinking_config_for_model(model_name: str) -> dict[str, Any]:
    normalized = model_name.strip().lower()
    if "gemini-2.5" in normalized:
        # Gemini 2.5 uses thinking_budget; -1 enables dynamic thinking.
        return {"thinking_budget": -1}
    if "gemini-3" in normalized:
        return {"thinking_level": "high"}
    return {}


def _is_unsupported_thinking_config_error(exc: Exception) -> bool:
    error_str = str(exc).lower()
    if "thinking" not in error_str:
        return False
    # API-side rejection for models without thinking support, or pydantic
    # extra_forbidden from an SDK whose ThinkingConfig predates a field.
    return (
        "not supported" in error_str
        or "extra inputs are not permitted" in error_str
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
    def __init__(
        self,
        api_key: str,
        model: str,
        max_retries: int = 3,
        *,
        model_registry_path: Path | None = None,
        limits_config_path: Path | None = None,
        usage_db_url: str | None = None,
        ai_client: AIClient | None = None,
    ):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.model = model
        self.max_retries = max_retries
        self.model_registry_path = model_registry_path
        self.limits_config_path = limits_config_path
        self.usage_db_url = usage_db_url
        self._ai_client = ai_client

    def _client(self) -> AIClient:
        if self._ai_client is None:
            self._ai_client = AIClient(
                api_key=self.api_key,
                max_retries=self.max_retries,
                registry_path=self.model_registry_path,
                limits_path=self.limits_config_path,
                usage_db_url=self.usage_db_url,
            )
        return self._ai_client

    def analyze_file(
        self,
        file_path: Path,
        prompt_text: str,
        mime_type: str | None = None,
        response_schema: type[BaseModel] | None = None,
        *,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        limit_behavior: str = "hard_fail",
    ) -> GeminiAnalysisResult:
        if not file_path.exists():
            raise FileNotFoundError(f"Receipt file not found: {file_path}")

        inferred_mime = mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"

        request = AIRequest(
            model_id=self.model,
            prompt_text=prompt_text,
            file_path=file_path,
            mime_type=inferred_mime,
            response_schema=response_schema,
            route=route,
            metadata=metadata or {},
            request_id=request_id,
            correlation_id=correlation_id,
            limit_behavior="soft_fail" if limit_behavior == "soft_fail" else "hard_fail",
        )

        try:
            response = self._client().generate_structured(request, schema=response_schema)
        except AIProviderError as exc:
            raise RuntimeError(str(exc)) from exc

        return self._build_result(response, response_schema)

    def analyze_text(
        self,
        prompt_text: str,
        response_schema: type[BaseModel],
        *,
        route: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
        limit_behavior: str = "hard_fail",
    ) -> GeminiAnalysisResult:
        """Text-only structured call (no receipt file). Used by type-to-organize,
        which reasons over already-extracted line items + the user's instruction."""
        request = AIRequest(
            model_id=self.model,
            prompt_text=prompt_text,
            file_path=None,
            mime_type=None,
            response_schema=response_schema,
            route=route,
            metadata=metadata or {},
            request_id=request_id,
            correlation_id=correlation_id,
            limit_behavior="soft_fail" if limit_behavior == "soft_fail" else "hard_fail",
        )
        try:
            response = self._client().generate_structured(request, schema=response_schema)
        except AIProviderError as exc:
            raise RuntimeError(str(exc)) from exc

        return self._build_result(response, response_schema)

    def _build_result(self, response: Any, response_schema: type[BaseModel] | None) -> GeminiAnalysisResult:
        if response.status == "limit_rejected":
            message = response.error.message if response.error else "AI request rejected by configured limits"
            return GeminiAnalysisResult(
                raw_output="",
                parsed_json=None,
                schema_valid=False,
                schema_errors=[message],
                duration_ms=response.duration_ms,
                parse_source=None,
                structured_output_available=False,
            )

        raw_output = response.text or ""
        duration_ms = response.duration_ms

        parsed_json = _to_dict_from_structured(response.parsed)
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
