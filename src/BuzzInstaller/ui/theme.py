"""
Visual theme engine for BuzzInstaller.

Provides a modern palette for light and dark modes plus helpers that
generate Qt style sheets for the legacy frameless dialogs and the new
dashboard window.

The dashboard (``ui.main_window``) relies on the global application
stylesheet returned by :func:`app_qss`, which styles widgets by object
name / dynamic property.  Keeping the styling in one place means a theme
switch is a single ``app.setStyleSheet(...)`` call.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from PySide6.QtGui import QColor


# ---------------------------------------------------------------------------
# Palette definitions
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Palette:
    name: str
    background: str          # main window / content bg
    surface: str             # cards / popups
    surface_alt: str         # nested cards, table header
    sidebar: str
    border: str              # subtle border
    input_bg: str
    text_primary: str
    text_secondary: str
    text_tertiary: str
    text_on_accent: str
    accent: str              # primary action colour
    accent_hover: str
    accent_pressed: str
    accent_soft: str         # translucent accent for selected nav items
    progress_bg: str         # unfilled progress bar track
    progress_fill: str       # filled progress bar
    error: str
    success: str
    warning: str
    shadow: str              # drop-shadow colour (with alpha)
    hover_bg: str            # hover state for buttons
    pressed_bg: str
    selection: str


LIGHT = Palette(
    name="Light",
    background="#F3F5F9",
    surface="#FFFFFF",
    surface_alt="#F7F9FC",
    sidebar="#FFFFFF",
    border="#E2E7EF",
    input_bg="#F8FAFD",
    text_primary="#111827",
    text_secondary="#5B6472",
    text_tertiary="#8B95A5",
    text_on_accent="#FFFFFF",
    accent="#2563EB",
    accent_hover="#3B76F6",
    accent_pressed="#1D4ED8",
    accent_soft="#E8EFFF",
    progress_bg="#E5E9F1",
    progress_fill="#2563EB",
    error="#DC2626",
    success="#15803D",
    warning="#B45309",
    shadow="rgba(15, 23, 42, 0.18)",
    hover_bg="#EEF2F8",
    pressed_bg="#E3E8F1",
    selection="#DCE6FF",
)

DARK = Palette(
    name="Dark",
    background="#0E1117",
    surface="#151A23",
    surface_alt="#1B2130",
    sidebar="#111620",
    border="#252D3D",
    input_bg="#0F141D",
    text_primary="#E8EDF5",
    text_secondary="#9BA6B7",
    text_tertiary="#6B7688",
    text_on_accent="#FFFFFF",
    accent="#4F8CFF",
    accent_hover="#6BA0FF",
    accent_pressed="#3C74E0",
    accent_soft="#1C2A47",
    progress_bg="#242C3B",
    progress_fill="#4F8CFF",
    error="#FF6B6B",
    success="#4ADE80",
    warning="#FBBF24",
    shadow="rgba(0, 0, 0, 0.55)",
    hover_bg="#1C2331",
    pressed_bg="#232B3B",
    selection="#22365E",
)

# Map config values → Palette
PALETTES = {
    False: LIGHT,   # light
    True: DARK,     # dark
}


def _system_uses_dark() -> bool:
    """Best-effort read of the Windows 'app theme' preference."""
    try:
        import winreg
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        )
        try:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        finally:
            key.Close()
    except Exception:
        return False


def get_palette(dark_mode: Optional[bool] = None) -> Palette:
    """
    Return the active palette.

    *dark_mode* of ``None`` follows the system setting (defaults to light
    when the system preference cannot be read).
    """
    if dark_mode is None:
        import sys
        if sys.platform == "win32":
            return DARK if _system_uses_dark() else LIGHT
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
# Legacy dialog style sheets
# ---------------------------------------------------------------------------
def base_window_qss(p: Palette) -> str:
    """QSS for the rounded, shadowed main container frame."""
    return f"""
    QFrame#windowFrame {{
        background: {p.surface};
        border-radius: 14px;
        border: 1px solid {p.border};
    }}
    """


def button_qss(p: Palette, variant: str = "primary") -> str:
    """Return a QSS string for a styled push button."""
    if variant == "primary":
        return f"""
        QPushButton {{
            background: {p.accent};
            color: {p.text_on_accent};
            border: none;
            border-radius: 8px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 600;
        }}
        QPushButton:hover {{ background: {p.accent_hover}; }}
        QPushButton:pressed {{ background: {p.accent_pressed}; }}
        QPushButton:disabled {{
            background: {p.text_tertiary};
            color: {p.surface};
        }}
        """
    elif variant == "secondary":
        return f"""
        QPushButton {{
            background: {p.input_bg};
            color: {p.text_primary};
            border: 1px solid {p.border};
            border-radius: 8px;
            padding: 8px 18px;
            font-size: 13px;
            font-weight: 500;
        }}
        QPushButton:hover {{ background: {p.hover_bg}; }}
        QPushButton:pressed {{ background: {p.pressed_bg}; }}
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
        QPushButton:pressed {{ background: {p.pressed_bg}; }}
        """


def progress_bar_qss(p: Palette) -> str:
    """QSS for the custom progress bar."""
    return f"""
    QProgressBar {{
        border: none;
        background: {p.progress_bg};
        border-radius: 5px;
        height: 10px;
        text-align: center;
        color: {p.text_primary};
        font-size: 11px;
    }}
    QProgressBar::chunk {{
        background: {p.progress_fill};
        border-radius: 5px;
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
    color = QColor(p.shadow)
    shadow.setColor(color)
    widget.setGraphicsEffect(shadow)


# ---------------------------------------------------------------------------
# Dashboard application stylesheet
# ---------------------------------------------------------------------------
def app_qss(p: Palette) -> str:
    """Global stylesheet used by the main dashboard window."""
    return f"""
    QWidget {{
        color: {p.text_primary};
        font-family: "Segoe UI Variable Text", "Segoe UI", sans-serif;
        font-size: 13px;
    }}
    QMainWindow, QWidget#appRoot, QStackedWidget#pages {{
        background: {p.background};
    }}
    QWidget#sidebar {{
        background: {p.sidebar};
        border-right: 1px solid {p.border};
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel#pageTitle {{
        font-size: 26px;
        font-weight: 700;
        color: {p.text_primary};
    }}
    QLabel#pageSubtitle {{
        font-size: 13px;
        color: {p.text_secondary};
    }}
    QLabel#brandTitle {{
        font-size: 16px;
        font-weight: 700;
        color: {p.text_primary};
    }}
    QLabel#brandSubtitle {{
        font-size: 11px;
        color: {p.text_tertiary};
    }}
    QLabel#cardTitle {{
        font-size: 15px;
        font-weight: 600;
        color: {p.text_primary};
    }}
    QLabel#sectionTitle {{
        font-size: 12px;
        font-weight: 700;
        color: {p.text_tertiary};
    }}
    QLabel#muted {{
        color: {p.text_secondary};
    }}
    QLabel#caption {{
        color: {p.text_tertiary};
        font-size: 11px;
    }}
    QLabel#bigValue {{
        font-size: 24px;
        font-weight: 700;
    }}
    QLabel#successText {{
        color: {p.success};
        font-weight: 600;
    }}
    QLabel#errorText {{
        color: {p.error};
        font-weight: 600;
    }}
    QLabel#warningText {{
        color: {p.warning};
        font-weight: 600;
    }}
    QLabel#link {{
        color: {p.accent};
    }}
    QFrame#card {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 12px;
    }}
    QFrame#nestedCard {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: 10px;
    }}
    QFrame#iconTile {{
        background: {p.accent_soft};
        border: none;
        border-radius: 11px;
    }}
    QFrame#banner {{
        background: {p.accent_soft};
        border: 1px solid {p.accent};
        border-radius: 10px;
    }}
    QFrame#dropZone {{
        background: {p.input_bg};
        border: 2px dashed {p.border};
        border-radius: 12px;
    }}
    QFrame#dropZone[dragActive="true"] {{
        border-color: {p.accent};
        background: {p.accent_soft};
    }}
    QFrame#divider {{
        background: {p.border};
        max-height: 1px;
        min-height: 1px;
        border: none;
    }}

    /* ---- Buttons -------------------------------------------------- */
    QPushButton {{
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 600;
        font-size: 13px;
    }}
    QPushButton#primary {{
        background: {p.accent};
        color: {p.text_on_accent};
        border: 1px solid {p.accent};
    }}
    QPushButton#primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
    QPushButton#primary:pressed {{ background: {p.accent_pressed}; }}
    QPushButton#primary:disabled {{
        background: {p.progress_bg};
        color: {p.text_tertiary};
        border-color: {p.border};
    }}
    QPushButton#secondary {{
        background: {p.input_bg};
        color: {p.text_primary};
        border: 1px solid {p.border};
    }}
    QPushButton#secondary:hover {{ background: {p.hover_bg}; }}
    QPushButton#secondary:pressed {{ background: {p.pressed_bg}; }}
    QPushButton#secondary:disabled {{ color: {p.text_tertiary}; }}
    QPushButton#ghost {{
        background: transparent;
        color: {p.text_secondary};
        border: 1px solid transparent;
        padding: 6px 10px;
    }}
    QPushButton#ghost:hover {{
        background: {p.hover_bg};
        color: {p.text_primary};
    }}
    QPushButton#danger {{
        background: transparent;
        color: {p.error};
        border: 1px solid {p.border};
    }}
    QPushButton#danger:hover {{ background: {p.error}; color: white; border-color: {p.error}; }}
    QPushButton#nav {{
        background: transparent;
        color: {p.text_secondary};
        border: none;
        border-radius: 9px;
        padding: 10px 12px;
        text-align: left;
        font-weight: 500;
        font-size: 13px;
    }}
    QPushButton#nav:hover {{
        background: {p.hover_bg};
        color: {p.text_primary};
    }}
    QPushButton#nav:checked {{
        background: {p.accent_soft};
        color: {p.accent};
        font-weight: 700;
    }}
    QPushButton#segment {{
        background: transparent;
        color: {p.text_secondary};
        border: 1px solid {p.border};
        border-radius: 7px;
        padding: 7px 14px;
        font-weight: 500;
    }}
    QPushButton#segment:hover {{ background: {p.hover_bg}; }}
    QPushButton#segment:checked {{
        background: {p.accent};
        color: {p.text_on_accent};
        border-color: {p.accent};
    }}

    /* ---- Inputs --------------------------------------------------- */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
        background: {p.input_bg};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 8px 10px;
        color: {p.text_primary};
        selection-background-color: {p.accent};
        selection-color: white;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus, QComboBox:focus {{
        border: 1px solid {p.accent};
    }}
    QLineEdit:disabled, QComboBox:disabled {{ color: {p.text_tertiary}; }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface};
        border: 1px solid {p.border};
        selection-background-color: {p.accent_soft};
        selection-color: {p.text_primary};
        outline: none;
    }}
    QCheckBox {{
        color: {p.text_primary};
        spacing: 9px;
    }}
    QCheckBox::indicator {{
        width: 17px;
        height: 17px;
        border-radius: 4px;
        border: 1px solid {p.border};
        background: {p.input_bg};
    }}
    QCheckBox::indicator:hover {{ border-color: {p.accent}; }}
    QCheckBox::indicator:checked {{
        background: {p.accent};
        border-color: {p.accent};
    }}
    QRadioButton {{ color: {p.text_primary}; spacing: 8px; }}

    /* ---- Lists / tables ------------------------------------------ */
    QListWidget, QTableWidget, QTreeWidget {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 10px;
        outline: none;
        gridline-color: {p.border};
    }}
    QListWidget::item, QTableWidget::item {{
        padding: 8px;
        border: none;
    }}
    QListWidget::item:selected, QTableWidget::item:selected {{
        background: {p.accent_soft};
        color: {p.text_primary};
    }}
    QHeaderView::section {{
        background: {p.surface_alt};
        color: {p.text_secondary};
        border: none;
        border-bottom: 1px solid {p.border};
        padding: 8px;
        font-weight: 600;
    }}
    QTableWidget {{ gridline-color: {p.border}; }}

    /* ---- Progress ------------------------------------------------- */
    QProgressBar {{
        border: none;
        background: {p.progress_bg};
        border-radius: 5px;
        height: 8px;
        text-align: center;
        color: transparent;
    }}
    QProgressBar::chunk {{
        background: {p.accent};
        border-radius: 5px;
    }}

    /* ---- Scrollbars ---------------------------------------------- */
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollArea > QWidget > QScrollBar {{ background: transparent; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {p.border};
        border-radius: 4px;
        min-height: 28px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {p.text_tertiary}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {p.border};
        border-radius: 4px;
        min-width: 28px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* ---- Misc ----------------------------------------------------- */
    QToolTip {{
        background: {p.surface};
        color: {p.text_primary};
        border: 1px solid {p.border};
        padding: 6px;
    }}
    QMenu {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 6px; }}
    QMenu::item:selected {{ background: {p.accent_soft}; }}
    """
