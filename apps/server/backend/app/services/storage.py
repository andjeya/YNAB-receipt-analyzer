from __future__ import annotations

import mimetypes
import shutil
from pathlib import Path


def sanitize_extension(filename: str) -> str:
    suffix = Path(filename).suffix.lower().lstrip(".")
    if not suffix:
        return "bin"
    if len(suffix) > 10:
        return "bin"
    return suffix


def guess_mime_type(filename: str) -> str:
    mime_type = mimetypes.guess_type(filename)[0]
    return mime_type or "application/octet-stream"


def build_storage_key(receipt_id: str, extension: str, prefix: str = "receipts") -> str:
    compact = receipt_id.replace("-", "")
    return f"{prefix}/{compact[:2]}/{compact[2:4]}/{receipt_id}.{extension}"


def storage_path(object_store_root: Path, storage_key: str) -> Path:
    return object_store_root / storage_key


def move_to_storage(source_path: Path, destination_path: Path) -> None:
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_path), str(destination_path))
