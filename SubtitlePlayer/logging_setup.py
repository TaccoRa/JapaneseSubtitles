"""Application logging setup."""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler


def setup_logging(config=None, log_dir: str = "logs") -> str:
    """Configure console and rotating-file logging once."""
    level_name = (os.environ.get("SUBTITLEPLAYER_LOG_LEVEL") or "").strip().upper()
    if not level_name:
        try:
            level_name = "DEBUG" if bool(config.get("DEBUGGING")) else "INFO"
        except Exception:
            level_name = "INFO"
    level = getattr(logging, level_name, logging.INFO)

    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "subtitleplayer.log")

    root = logging.getLogger()
    root.setLevel(level)

    for handler in list(root.handlers):
        if getattr(handler, "_subtitleplayer_handler", False):
            root.removeHandler(handler)
            try:
                handler.close()
            except Exception:
                pass

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console._subtitleplayer_handler = True
    root.addHandler(console)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=1_000_000,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    file_handler._subtitleplayer_handler = True
    root.addHandler(file_handler)

    logging.captureWarnings(True)
    return log_path


def set_debug_logging(enabled: bool) -> None:
    """Switch active SubtitlePlayer handlers between INFO and DEBUG at runtime."""
    level = logging.DEBUG if bool(enabled) else logging.INFO
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        if not getattr(handler, "_subtitleplayer_handler", False):
            continue
        if isinstance(handler, RotatingFileHandler):
            handler.setLevel(logging.DEBUG)
        else:
            handler.setLevel(level)
