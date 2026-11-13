"""
Visual theme engine for BuzzInstaller.

Provides a Discord-inspired palette for light and dark modes plus helpers
that generate Qt style sheets for the legacy frameless dialogs and the
dashboard window.

The dashboard relies on the global application stylesheet returned by
:func:`app_qss`.  All colours, corner radii and font scaling are derived
from the user's Customization settings, so a theme switch is a single
``app.setStyleSheet(...)`` call.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
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


# Discord-inspired palettes.  Flat surfaces, low-contrast borders and a
# single strong blurple accent.
LIGHT = Palette(
    name="Light",
    background="#F2F3F5",
    surface="#FFFFFF",
    surface_alt="#F7F8FA",
    sidebar="#F2F3F5",
    border="#E3E5E8",
    input_bg="#FFFFFF",
    text_primary="#060607",
    text_secondary="#5C5E66",
    text_tertiary="#80848E",
    text_on_accent="#FFFFFF",
    accent="#5865F2",
    accent_hover="#4752C4",
    accent_pressed="#3C45A5",
    accent_soft="#EAEBFF",
    progress_bg="#E3E5E8",
    progress_fill="#5865F2",
    error="#D83C3E",
    success="#248046",
    warning="#B7791F",
    shadow="rgba(15, 23, 42, 0.10)",
    hover_bg="#EBEDEF",
    pressed_bg="#E0E2E6",
    selection="#DDE1FF",
)

DARK = Palette(
    name="Dark",
    background="#313338",
    surface="#2B2D31",
    surface_alt="#35373C",
    sidebar="#1E1F22",
    border="#3F4147",
    input_bg="#1E1F22",
    text_primary="#F2F3F5",
    text_secondary="#B5BAC1",
    text_tertiary="#949BA4",
    text_on_accent="#FFFFFF",
    accent="#5865F2",
    accent_hover="#4752C4",
    accent_pressed="#3C45A5",
    accent_soft="#3A3D63",
    progress_bg="#3F4147",
    progress_fill="#5865F2",
    error="#F23F43",
    success="#23A55A",
    warning="#F0B232",
    shadow="rgba(0, 0, 0, 0.45)",
    hover_bg="#35373C",
    pressed_bg="#3F4147",
    selection="#404675",
)

# Map config values → Palette
PALETTES = {
    False: LIGHT,   # light
    True: DARK,     # dark
}

# Interface style presets: density + shape.
STYLE_PRESETS = {
    "discord": {
        "label": "Default",
        "radius": 8,
        "padding_v": 8,
        "padding_h": 14,
        "nav_padding": 9,
        "page_margin": 24,
        "spacing": 14,
    },
    "comfortable": {
        "label": "Comfortable",
        "radius": 12,
        "padding_v": 9,
        "padding_h": 18,
        "nav_padding": 11,
        "page_margin": 30,
        "spacing": 18,
    },
    "compact": {
        "label": "Compact",
        "radius": 6,
        "padding_v": 6,
        "padding_h": 12,
        "nav_padding": 7,
        "page_margin": 18,
        "spacing": 10,
    },
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


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------
def _normalise_colour(value) -> str:
    """Return a ``#RRGGBB`` colour or an empty string when invalid."""
    text = str(value or "").strip()
    if not text:
        return ""
    if not text.startswith("#"):
        text = "#" + text
    colour = QColor(text)
    if not colour.isValid():
        return ""
    return colour.name()


def _mix(first: str, second: str, weight: float) -> str:
    """Mix two colours; *weight* is the share of *first*."""
    weight = max(0.0, min(1.0, float(weight)))
    a = QColor(first)
    b = QColor(second)
    if not a.isValid():
        a = QColor(second)
    if not b.isValid():
        b = QColor(first)
    return QColor.fromRgbF(
        a.redF() * weight + b.redF() * (1.0 - weight),
        a.greenF() * weight + b.greenF() * (1.0 - weight),
        a.blueF() * weight + b.blueF() * (1.0 - weight),
    ).name()


def _lighten(colour: str, amount: int = 112) -> str:
    value = QColor(colour)
    return value.lighter(amount).name() if value.isValid() else colour


def _darken(colour: str, amount: int = 114) -> str:
    value = QColor(colour)
    return value.darker(amount).name() if value.isValid() else colour


def build_palette(config: Optional[dict] = None) -> Palette:
    """Build the effective palette from a configuration dict."""
    cfg = config or {}
    dark = cfg.get("dark_mode")
    base = get_palette(dark)
    accent = _normalise_colour(cfg.get("accent_color"))
    if not accent:
        return base
    is_dark = base.background == DARK.background
    soft_weight = 0.24 if is_dark else 0.12
    return replace(
        base,
        accent=accent,
        accent_hover=_lighten(accent, 114),
        accent_pressed=_darken(accent, 116),
        accent_soft=_mix(accent, base.surface, soft_weight),
        progress_fill=accent,
        selection=_mix(accent, base.surface, 0.32 if is_dark else 0.18),
    )


def get_palette_from_config() -> Palette:
    """Convenience: read appearance settings from config."""
    from config.config_manager import load_config
    return build_palette(load_config())


def style_settings(config: Optional[dict] = None) -> dict:
    """Resolve interface-style, radius and font-scale settings."""
    cfg = config or {}
    style = str(cfg.get("ui_style", "discord") or "discord").lower()
    preset = dict(STYLE_PRESETS.get(style, STYLE_PRESETS["discord"]))
    try:
        radius = int(cfg.get("ui_radius", preset["radius"]))
    except (TypeError, ValueError):
        radius = preset["radius"]
    radius = max(0, min(18, radius))
    try:
        scale = int(cfg.get("ui_scale", 100))
    except (TypeError, ValueError):
        scale = 100
    scale = max(85, min(135, scale))
    preset.update({
        "name": style,
        "radius": radius,
        "scale": scale / 100.0,
        "body": round(13 * scale / 100),
        "title": round(25 * scale / 100),
        "card_title": round(15 * scale / 100),
        "caption": round(11 * scale / 100),
    })
    return preset


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
def app_qss(p: Palette, config: Optional[dict] = None) -> str:
    """Global stylesheet used by the main dashboard window."""
    s = style_settings(config)
    r = s["radius"]
    card_r = max(0, r + 2)
    btn_r = max(0, r - 1)
    nav_r = r
    pv = s["padding_v"]
    ph = s["padding_h"]
    nav_pad = s["nav_padding"]
    body = s["body"]
    caption = s["caption"]
    title = s["title"]
    card_title = s["card_title"]
    field_h = max(30, round(34 * s["scale"]))

    return f"""
    QWidget {{
        color: {p.text_primary};
        font-family: "Segoe UI", "Segoe UI Variable Text", sans-serif;
        font-size: {body}px;
    }}
    QMainWindow, QWidget#appRoot, QStackedWidget#pages {{
        background: {p.background};
    }}
    QWidget#sidebar {{
        background: {p.sidebar};
        border-right: 1px solid {p.border};
    }}
    QWidget#contentPane {{
        background: {p.background};
    }}
    QLabel {{
        background: transparent;
    }}
    QLabel#pageTitle {{
        font-size: {title}px;
        font-weight: 700;
        color: {p.text_primary};
    }}
    QLabel#pageSubtitle {{
        font-size: {body}px;
        color: {p.text_secondary};
    }}
    QLabel#brandTitle {{
        font-size: {round(16 * s['scale'])}px;
        font-weight: 700;
        color: {p.text_primary};
    }}
    QLabel#brandSubtitle {{
        font-size: {caption}px;
        color: {p.text_tertiary};
    }}
    QLabel#cardTitle {{
        font-size: {card_title}px;
        font-weight: 600;
        color: {p.text_primary};
    }}
    QLabel#sectionTitle {{
        font-size: {caption}px;
        font-weight: 700;
        color: {p.text_tertiary};
        letter-spacing: 1px;
    }}
    QLabel#muted {{
        color: {p.text_secondary};
    }}
    QLabel#caption {{
        color: {p.text_tertiary};
        font-size: {caption}px;
    }}
    QLabel#bigValue {{
        font-size: {round(24 * s['scale'])}px;
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
        border-radius: {card_r}px;
    }}
    QFrame#nestedCard {{
        background: {p.surface_alt};
        border: 1px solid {p.border};
        border-radius: {max(0, r)}px;
    }}
    QFrame#iconTile {{
        background: {p.accent_soft};
        border: none;
        border-radius: {btn_r}px;
    }}
    QFrame#banner {{
        background: {p.accent_soft};
        border: 1px solid {p.accent};
        border-radius: {card_r}px;
    }}
    QFrame#dropZone {{
        background: {p.input_bg};
        border: 2px dashed {p.border};
        border-radius: {card_r}px;
    }}
    QFrame#dropZone[dragActive="true"] {{
        border-color: {p.accent};
        background: {p.accent_soft};
    }}
    QFrame#swatch {{
        border: 2px solid {p.border};
        border-radius: {btn_r}px;
    }}
    QFrame#divider {{
        background: {p.border};
        max-height: 1px;
        min-height: 1px;
        border: none;
    }}

    /* ---- Buttons -------------------------------------------------- */
    QPushButton {{
        border-radius: {btn_r}px;
        padding: {pv}px {ph}px;
        font-weight: 600;
        font-size: {body}px;
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
        background: {p.surface_alt};
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
        border-radius: {nav_r}px;
        padding: {nav_pad}px 12px;
        text-align: left;
        font-weight: 500;
        font-size: {body}px;
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
        border-radius: {btn_r}px;
        padding: 7px 14px;
        font-weight: 500;
    }}
    QPushButton#segment:hover {{ background: {p.hover_bg}; }}
    QPushButton#segment:checked {{
        background: {p.accent};
        color: {p.text_on_accent};
        border-color: {p.accent};
    }}
    QPushButton#swatch {{
        border-radius: {btn_r}px;
        border: 2px solid {p.border};
        min-width: 24px;
        min-height: 24px;
    }}
    QPushButton#swatch:checked {{
        border: 2px solid {p.text_primary};
    }}

    /* ---- Inputs --------------------------------------------------- */
    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox {{
        background: {p.input_bg};
        border: 1px solid {p.border};
        border-radius: {btn_r}px;
        padding: 7px 10px;
        min-height: {max(20, field_h - 18)}px;
        color: {p.text_primary};
        selection-background-color: {p.accent};
        selection-color: white;
    }}
    QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus,
    QComboBox:focus, QSpinBox:focus {{
        border: 1px solid {p.accent};
    }}
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
        color: {p.text_tertiary};
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {p.surface};
        border: 1px solid {p.border};
        selection-background-color: {p.accent_soft};
        selection-color: {p.text_primary};
        outline: none;
        padding: 4px;
    }}
    QSpinBox::up-button, QSpinBox::down-button {{
        width: 18px;
        border: none;
        background: transparent;
    }}
    QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
        background: {p.hover_bg};
        border-radius: 3px;
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
    QRadioButton::indicator {{ width: 16px; height: 16px; }}

    QSlider::groove:horizontal {{
        height: 4px;
        background: {p.progress_bg};
        border-radius: 2px;
    }}
    QSlider::sub-page:horizontal {{
        background: {p.accent};
        border-radius: 2px;
    }}
    QSlider::handle:horizontal {{
        background: {p.surface};
        border: 2px solid {p.accent};
        width: 14px;
        height: 14px;
        margin: -7px 0;
        border-radius: 9px;
    }}
    QSlider::handle:horizontal:hover {{
        background: {p.accent};
    }}

    /* ---- Lists / tables ------------------------------------------ */
    QListWidget, QTableWidget, QTreeWidget {{
        background: {p.surface};
        border: 1px solid {p.border};
        border-radius: {card_r}px;
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
        border-radius: {card_r}px;
        padding: 6px;
    }}
    QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: {btn_r}px; }}
    QMenu::item:selected {{ background: {p.accent_soft}; }}
    """
