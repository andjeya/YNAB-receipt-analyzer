from __future__ import annotations

from pathlib import Path


def _tiktoken_count(text: str) -> int | None:
    try:
        import tiktoken  # type: ignore

        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))
    except Exception:
        return None


def estimate_tokens_for_text(text: str) -> int:
    count = _tiktoken_count(text)
    if count is not None:
        return count
    # Fallback approximation: ~4 characters/token for mixed English/JSON text.
    return max(len(text) // 4, 1) if text else 0


def estimate_tokens_for_file(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        size = path.stat().st_size
    except Exception:
        return 0
    # Binary OCR/document payloads are highly variable; we reserve conservatively.
    return max(size // 16, 1)
