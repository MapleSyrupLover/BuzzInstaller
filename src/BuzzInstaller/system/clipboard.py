"""
Cross-platform-ish clipboard helper (Windows implementation).

Uses the raw Win32 clipboard API via ctypes so there is no extra
dependency.  All functions degrade gracefully on non-Windows platforms.
"""

from __future__ import annotations

import sys
import ctypes
from ctypes import c_uint, c_wchar_p

from utils.logging import get_logger

logger = get_logger(__name__)

CF_UNICODETEXT = 13
GHND = 0x0042  # GMEM_MOVEABLE | GMEM_ZEROINIT


def _win() -> bool:
    return sys.platform == "win32"


def copy_to_clipboard(text: str) -> bool:
    """Place *text* on the system clipboard as UTF-16LE text."""
    if not _win():
        return False

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.OpenClipboard(None):
        logger.warning("OpenClipboard failed")
        return False

    try:
        user32.EmptyClipboard()

        # Allocate movable global memory for the string (+ null terminator)
        size = (len(text) + 1) * 2  # 2 bytes per wchar
        hGlobal = kernel32.GlobalAlloc(GHND, c_uint(size))
        if not hGlobal:
            logger.warning("GlobalAlloc failed")
            return False

        pGlobal = kernel32.GlobalLock(hGlobal)
        if not pGlobal:
            kernel32.GlobalFree(hGlobal)
            return False

        try:
            ctypes.memmove(pGlobal, c_wchar_p(text), size)
        finally:
            kernel32.GlobalUnlock(hGlobal)

        # SetClipboardData takes ownership of the memory handle
        result = user32.SetClipboardData(CF_UNICODETEXT, hGlobal)
        if not result:
            logger.warning("SetClipboardData failed")
            return False
        return True
    finally:
        user32.CloseClipboard()


def read_from_clipboard() -> str | None:
    """Return the current clipboard text, or None."""
    if not _win():
        return None

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    if not user32.OpenClipboard(None):
        return None

    try:
        hGlobal = user32.GetClipboardData(CF_UNICODETEXT)
        if not hGlobal:
            return None
        pGlobal = kernel32.GlobalLock(hGlobal)
        if not pGlobal:
            return None
        try:
            # Read until null terminator
            buf = c_wchar_p(pGlobal).value
            return buf
        finally:
            kernel32.GlobalUnlock(hGlobal)
    finally:
        user32.CloseClipboard()


def clear_clipboard() -> bool:
    """Empty the clipboard."""
    if not _win():
        return False
    user32 = ctypes.windll.user32
    if not user32.OpenClipboard(None):
        return False
    try:
        user32.EmptyClipboard()
        return True
    finally:
        user32.CloseClipboard()
