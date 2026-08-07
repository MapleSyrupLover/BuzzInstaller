"""
Lightweight logging setup.

Logs go to %LOCALAPPDATA%\\BuzzInstaller\\logs\\buzzinstaller.log (DEBUG level)
and to stderr (INFO level).  A module-level ``get_logger`` is provided so
that other modules can simply call ``from utils.logging import get_logger``.
"""

from __future__ import annotations

import logging
import sys

from config.paths import get_log_path, ensure_dirs

_LOG_NAME = "buzzinstaller"
_CONFIGURED = False


def setup_logging() -> logging.Logger:
    """Initialise the application logger (file + stderr handlers).

    Idempotent — calling it more than once is a no-op after the first.
    Returns the root application logger.
    """
    return _make_logger()


def _make_logger() -> logging.Logger:
    global _CONFIGURED
    logger = logging.getLogger(_LOG_NAME)
    if _CONFIGURED:
        return logger
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    ensure_dirs()
    log_path = get_log_path()

    # File handler — DEBUG
    try:
        fh = logging.FileHandler(str(log_path), encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)-7s] %(name)s :: %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(fh)
    except OSError:
        pass  # logging to file is best-effort

    # Console handler — INFO
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(ch)

    _CONFIGURED = True
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return the named child logger (falls back to the root app logger).

    Child loggers are created as children of the ``buzzinstaller`` logger
    so they inherit its DEBUG level and handlers.  (Creating them under
    the root logger would give them the root's default WARNING level,
    silently dropping DEBUG/INFO diagnostic messages before they reach
    the file handler.)
    """
    _make_logger()
    if name:
        return logging.getLogger(f"{_LOG_NAME}.{name}")
    return logging.getLogger(_LOG_NAME)
