"""
Protocol handler — parses command-line arguments for the BZ:// URI scheme.

When Windows launches BuzzInstaller via ``BZ://code`` it passes the full
URL as a command-line argument.  This module normalises the various input
forms and also supports ``--protocol`` and ``--send`` explicit flags so the
Explorer context menu can use unambiguous argument passing.
"""

from __future__ import annotations

import argparse
import re
import sys
from typing import Optional

from core.buzzheavier_api import extract_code, is_valid_code, InvalidCodeError
from utils.logging import get_logger

# Single authoritative version source — defined in version.py.
from version import APP_VERSION

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="BuzzInstaller",
        description="Send & receive files via BuzzHeavier",
        add_help=False,
    )
    parser.add_argument("--send", nargs="*", default=None,
                        help="Upload the specified file(s)/folder(s) (context menu)")
    parser.add_argument("--protocol", nargs="?", default=None,
                        help="Handle a BZ:// URL (protocol activation)")
    parser.add_argument("--settings", action="store_true",
                        help="Show the settings window")
    parser.add_argument("--install", action="store_true",
                        help="Register protocol + context menu integration")
    parser.add_argument("--uninstall", action="store_true",
                        help="Remove all Windows integration")
    parser.add_argument("--tray", action="store_true",
                        help="Launch in system-tray mode (start-with-windows)")
    parser.add_argument("--background", action="store_true",
                        help="Run the lightweight background hotkey process")
    parser.add_argument("--version", action="version",
                        version=f"BuzzInstaller {APP_VERSION}")
    parser.add_argument("-?", "--help", action="help",
                        help="Show this help message")
    # Bare BZ:// URLs passed by Windows protocol activation also appear here
    parser.add_argument("url", nargs="?", default=None,
                        help="A BZ://code or https://buzzheavier.com/code URL")
    return parser


def parse_args(argv: list[str] | None = None):
    parser = build_parser()
    return parser.parse_args(argv)


def extract_protocol_code(args: argparse.Namespace) -> Optional[str]:
    """
    Given parsed arguments, determine the BZ:// code (if any) that should
    trigger a download.

    Looks in this order:
      1. ``--protocol <url>``
      2. positional ``url`` argument (e.g. ``BZ://code``)
      3. Any bare arg that looks like a BZ:// URL or a bare code
    """
    candidates: list[str] = []

    if args.protocol:
        candidates.append(args.protocol)
    if args.url:
        candidates.append(args.url)

    # Scan all remaining args for BZ:// patterns
    for arg in sys.argv[1:]:
        if arg.startswith("--") or arg.startswith("-"):
            continue
        if arg not in candidates:
            candidates.append(arg)

    for raw in candidates:
        try:
            code = extract_code(raw)
            if is_valid_code(code):
                logger.info("Protocol code extracted: %s", code)
                return code
        except InvalidCodeError:
            continue

    return None


def extract_send_paths(args: argparse.Namespace) -> Optional[list[str]]:
    """Return the list of file/folder paths for --send, or None."""
    if args.send is None:
        return None
    paths = [p for p in args.send if p]
    if not paths:
        return None
    return paths


# ---------------------------------------------------------------------------
# Code validation
# ---------------------------------------------------------------------------
def validate_code(code: str) -> bool:
    """True if *code* looks like a valid BuzzHeavier code."""
    return is_valid_code(code)


def code_to_url(code: str) -> str:
    """Convert a code to its full BuzzHeavier URL."""
    return f"https://buzzheavier.com/{code}"


def code_to_bz_url(code: str) -> str:
    """Convert a code to its BZ:// link."""
    return f"BZ://{code}"
