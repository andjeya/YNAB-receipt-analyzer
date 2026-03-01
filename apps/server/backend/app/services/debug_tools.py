from __future__ import annotations

from app.config import Settings


def is_debug_tools_enabled(settings: Settings) -> bool:
    # Runtime toggle is driven by the flag file so terminal on/off is authoritative.
    return settings.debug_tools_flag_file.exists()
