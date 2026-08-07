"""
Visual theme engine for BuzzInstaller.

Provides a modern, Windows-11-style palette for both light and dark
modes, plus helper functions to compute palettes and generate the Qt
Style Sheets that give our custom windows their look.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtGui import QColor


# ---------------------------------------------------------------------------
# Palette definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    name: str
    background: str          # main window bg
    surface: str             # cards / popup surface
    border: str              # subtle border
    text_primary: str
    text_secondary: str
    text_tertiary: str
    accent: str              # primary action colour (Windows blue)
    accent_hover: str
    accent_pressed: str
    progress_bg: str         # unfilled progress bar track
    progress_fill: str       # filled progress bar
    error: str
    success: str
    shadow: str              # drop-shadow colour (with alpha)
    hover_bg: str            # hover state for buttons
    pressed_bg: str


LIGHT = Palette(
    name="Light",
    background="#FFFFFF",
    surface="#FFFFFF",
    border="#E0E0E0",
    text_primary="#1F1F1F",
    text_secondary="#6B6B6B",
    text_tertiary="#9A9A9A",
    accent="#0078D4",
    accent_hover="#1082FF",
    accent_pressed="#005A9E",
    progress_bg="#E6E6E6",
    progress_fill="#0078D4",
    error="#D13438",
    success="#107C10",
    shadow="rgba(0, 0, 0, 0.15)",
    hover_bg="#F4F4F4",
    pressed_bg="#E0E0E0",
)

DARK = Palette(
    name="Dark",
    background="#1E1E1E",
    surface="#252525",
    border="#3A3A3A",
    text_primary="#E0E0E0",
    text_secondary="#A0A0A0",
    text_tertiary="#6A6A6A",
    accent="#0078D4",
    accent_hover="#1082FF",
    accent_pressed="#005A9E",
    progress_bg="#3A3A3A",
    progress_fill="#0078D4",
    error="#FF5F57",
    success="#107C10",
    shadow="rgba(0, 0, 0, 0.40)",
    hover_bg="#2A2A2A",
    pressed_bg="#323232",
)

# Map config values → Palette
PALETTES = {
    False: LIGHT,   # light
    True: DARK,     # dark
}


def get_palette(dark_mode: Optional[bool] = None) -> Palette:
    """
    Return the active palette.

    *dark_mode* of ``None`` follows the system setting (defaults to light
    on non-Windows).
    """
    if dark_mode is None:
        import sys
        if sys.platform == "win32":
            # Check Windows system theme via DWM
            try:
                import ctypes
                dwm = ctypes.windll.dwmapi
                # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win10 1809+) / 19 (older)
                for attr in (20, 19):
                    val = ctypes.c_int()
                    if dwm.DwmIsCompositionEnabled():
                        if dwm.DwmGetWindowAttribute(
                            0, attr, ctypes.byref(val), ctypes.sizeof(val)
                        ) == 0:
                            if val.value == 1:
                                return DARK
                # Fall through to system parameter check
            except Exception:
                pass
            # Fallback: use SystemParametersInfo to check dark mode
            try:
                import ctypes
                user32 = ctypes.windll.user32
                # Check if the system app (uxtheme) uses dark mode
                # Windows 10/11: check AppsUseLightTheme
                from config.config_manager import load_config
                cfg = load_config()
                return PALETTES.get(cfg.get("dark_mode"), LIGHT)
            except Exception:
                pass
        return LIGHT

    return PALETTES.get(dark_mode, LIGHT)


def get_palette_from_config() -> Palette:
    """Convenience: read dark_mode from config and return the palette."""
    from config.config_manager import load_config
    cfg = load_config()
    dm = cfg.get("dark_mode")
    if dm is None:
        return get_palette(None)
    return PALETTES.get(dm, LIGHT)


# ---------------------------------------------------------------------------
# Qt Style Sheet generation
# ---------------------------------------------------------------------------
def base_window_qss(p: Palette) -> str:
    """QSS for the rounded, shadowed main container frame."""
    return f"""
    QFrame#windowFrame {{
        background: {p.surface};
        border-radius: 14px;
        border: 1px solid {p.border};
    }}
    QFrame#windowFrame:hover {{
        border-color: {p.accent_hover};
    }}
    """


def button_qss(p: Palette, variant: str = "primary") -> str:
    """Return a QSS string for a styled push button."""
    if variant == "primary":
        return f"""
        QPushButton {{
            background: {p.accent};
            color: white;
            border: none;
            border-radius: 8px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {p.accent_hover};
        }}
        QPushButton:pressed {{
            background: {p.accent_pressed};
        }}
        QPushButton:disabled {{
            background: {p.text_tertiary};
            color: {p.surface};
        }}
        """
    elif variant == "secondary":
        return f"""
        QPushButton {{
            background: transparent;
            color: {p.text_primary};
            border: 1px solid {p.border};
            border-radius: 8px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {p.hover_bg};
        }}
        QPushButton:pressed {{
            background: {p.pressed_bg};
        }}
        QPushButton:disabled {{
            color: {p.text_tertiary};
            border-color: {p.text_tertiary};
        }}
        """
    else:  # text
        return f"""
        QPushButton {{
            background: transparent;
            color: {p.accent};
            border: none;
            border-radius: 8px;
            padding: 6px 12px;
            font-size: 13px;
            font-weight: 500;
        }}
        QPushButton:hover {{
            background: {p.hover_bg};
            color: {p.accent_hover};
        }}
        QPushButton:pressed {{
            background: {p.pressed_bg};
        }}
        """


def progress_bar_qss(p: Palette) -> str:
    """QSS for the custom progress bar."""
    pct = 0  # will be set dynamically
    return f"""
    QProgressBar {{
        border: none;
        background: {p.progress_bg};
        border-radius: 4px;
        height: 10px;
        text-align: center;
        color: {p.text_primary};
        font-size: 11px;
    }}
    QProgressBar::chunk {{
        background: {p.progress_fill};
        border-radius: 4px;
    }}
    """


def label_qss(p: Palette, size: str = "body") -> str:
    """Font-size helper for labels."""
    sizes = {
        "title": "18px",
        "subtitle": "14px",
        "body": "13px",
        "caption": "11px",
    }
    font_size = sizes.get(size, "13px")
    return f"""
    color: {p.text_primary};
    font-size: {font_size};
    """


def apply_window_shadow(widget, p: Palette):
    """Attach a QGraphicsDropShadowEffect to *widget*."""
    from PySide6.QtWidgets import QGraphicsDropShadowEffect
    shadow = QGraphicsDropShadowEffect()
    shadow.setBlurRadius(20)
    shadow.setXOffset(0)
    shadow.setYOffset(4)
    # Parse the RGBA shadow colour
    color = QColor(p.shadow)
    shadow.setColor(color)
    widget.setGraphicsEffect(shadow)
