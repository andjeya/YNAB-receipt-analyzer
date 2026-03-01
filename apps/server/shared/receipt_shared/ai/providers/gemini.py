from __future__ import annotations

import copy
import logging
import mimetypes
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from receipt_shared.ai.registry import ModelDefinition
from receipt_shared.ai.tokenizer import estimate_tokens_for_file, estimate_tokens_for_text
from receipt_shared.ai.types import AIRequest, ProviderResult, TokenUsage

logger = logging.getLogger(__name__)

UNSUPPORTED_GEMINI_SCHEMA_KEYS = frozenset({"additionalProperties", "additional_properties"})

try:
    from google import genai
    from google.genai import types
except Exception:  # pragma: no cover - imported lazily for environments without dependency
    genai = None
    types = None


def _is_retryable_error(exc: Exception) -> bool:
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


def _default_thinking_config_for_model(model_name: str) -> dict[str, Any]:
    normalized = model_name.strip().lower()
    if "gemini-2.5" in normalized:
        return {"thinking_budget": -1}
    if "gemini-3" in normalized:
        return {"thinking_level": "high"}
    return {}


def _is_unsupported_thinking_config_error(exc: Exception) -> bool:
    error_str = str(exc).lower()
    return "thinking level is not supported" in error_str or (
        "thinking" in error_str and "not supported" in error_str
    )


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


def _build_gemini_response_json_schema(response_schema: type[BaseModel]) -> dict[str, Any]:
    return copy.deepcopy(_cached_response_json_schema(response_schema))


def _extract_usage_metadata(response: Any) -> TokenUsage:
    usage_metadata = getattr(response, "usage_metadata", None)
    if usage_metadata is None:
        return TokenUsage()

    def _read(name: str) -> int | None:
        if isinstance(usage_metadata, dict):
            value = usage_metadata.get(name)
        else:
            value = getattr(usage_metadata, name, None)
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    input_tokens = _read("prompt_token_count")
    if input_tokens is None:
        input_tokens = _read("input_token_count")

    output_tokens = _read("candidates_token_count")
    if output_tokens is None:
        output_tokens = _read("output_token_count")

    total_tokens = _read("total_token_count")
    cached_input_tokens = _read("cached_content_token_count")

    extra: dict[str, int] = {}
    for extra_name in ("thoughts_token_count", "tool_use_prompt_token_count", "tool_use_response_token_count"):
        value = _read(extra_name)
        if value is not None:
            extra[extra_name] = value

    usage = TokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_input_tokens=cached_input_tokens,
        total_tokens=total_tokens,
        extra_dimensions=extra,
        estimated=False,
    )
    return usage.with_total_if_missing()


class GeminiProvider:
    def __init__(self, api_key: str, max_retries: int = 3):
        if not api_key:
            raise ValueError("GEMINI_API_KEY is required")
        self.api_key = api_key
        self.max_retries = max_retries

    def estimate_usage(self, request: AIRequest, model: ModelDefinition) -> TokenUsage:  # noqa: ARG002
        input_tokens = estimate_tokens_for_text(request.prompt_text)
        if request.file_path is not None:
            input_tokens += estimate_tokens_for_file(request.file_path)

        output_tokens = model.estimated_output_tokens
        return TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            estimated=True,
        )

    def _validate_request(self, request: AIRequest) -> tuple[Path | None, str | None]:
        file_path = request.file_path
        mime_type = request.mime_type

        if file_path is not None:
            if not file_path.exists():
                raise FileNotFoundError(f"Receipt file not found: {file_path}")
            inferred_mime = mime_type or mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
            return file_path, inferred_mime

        return None, None

    def generate(self, request: AIRequest, model: ModelDefinition) -> ProviderResult:
        if genai is None or types is None:
            raise RuntimeError("google-genai dependency is not installed")

        file_path, inferred_mime = self._validate_request(request)
        started = time.perf_counter()

        attempt = 0
        thinking_enabled = True
        thinking_kwargs = _default_thinking_config_for_model(model.provider_model)

        while True:
            try:
                client = genai.Client(api_key=self.api_key)

                config_kwargs: dict[str, Any] = {
                    "response_mime_type": "application/json",
                }
                if thinking_enabled and thinking_kwargs:
                    config_kwargs["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)
                if request.response_schema is not None:
                    response_json_schema = _build_gemini_response_json_schema(request.response_schema)
                    supports_response_json_schema = "response_json_schema" in types.GenerateContentConfig.model_fields
                    if supports_response_json_schema:
                        config_kwargs["response_json_schema"] = response_json_schema
                    else:
                        config_kwargs["response_schema"] = response_json_schema

                contents: list[Any] = [request.prompt_text]
                if file_path is not None:
                    uploaded_file = client.files.upload(file=str(file_path))
                    contents.append(
                        types.Part.from_uri(
                            file_uri=uploaded_file.uri,
                            mime_type=uploaded_file.mime_type or inferred_mime or "application/octet-stream",
                        )
                    )

                response = client.models.generate_content(
                    model=model.provider_model,
                    contents=contents,
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
                if (not is_retryable) or attempt >= self.max_retries:
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
        usage = _extract_usage_metadata(response)

        return ProviderResult(
            text=raw_output,
            parsed=getattr(response, "parsed", None),
            usage=usage,
            duration_ms=duration_ms,
        )
