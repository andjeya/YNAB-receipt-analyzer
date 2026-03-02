from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

def _ensure_stream_handler(root_logger: logging.Logger, formatter: logging.Formatter) -> None:
    has_stream_handler = any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, RotatingFileHandler)
        for handler in root_logger.handlers
    )
    if has_stream_handler:
        return

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root_logger.addHandler(stream_handler)


def _ensure_file_handler(logger: logging.Logger, log_file_path: Path, formatter: logging.Formatter) -> None:
    absolute_target = str(log_file_path.resolve())
    for existing in list(logger.handlers):
        if isinstance(existing, RotatingFileHandler) and existing.baseFilename == absolute_target:
            if existing.stream is None:
                existing.close()
                logger.removeHandler(existing)
                break
            return

    file_handler = RotatingFileHandler(
        log_file_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def configure_logging(log_file_path: Path) -> None:
    log_file_path.parent.mkdir(parents=True, exist_ok=True)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    _ensure_stream_handler(root_logger, formatter)

    # Uvicorn/RQ may reconfigure root handlers; attach file handlers to stable logger namespaces.
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    _ensure_file_handler(app_logger, log_file_path, formatter)

    rq_logger = logging.getLogger("rq")
    rq_logger.setLevel(logging.INFO)
    _ensure_file_handler(rq_logger, log_file_path, formatter)
