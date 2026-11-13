"""Customization page — theme, accent colour, density and font size."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QColorDialog, QComboBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from config.config_manager import load_config, save_config
from ui.theme import STYLE_PRESETS, get_palette_from_config
from ui.widgets import Card, PageHeader, SettingRow, SliderSpin

ACCENT_PRESETS = (
    ("Blurple", "#5865F2"),
    ("Green", "#23A55A"),
    ("Red", "#F23F43"),
    ("Yellow", "#F0B232"),
    ("Teal", "#1ABC9C"),
    ("Blue", "#3498DB"),
    ("Purple", "#9B59B6"),
    ("Pink", "#EB459E"),
    ("Orange", "#E67E22"),
    ("Graphite", "#747F8D"),
)


class CustomizationPage(QWidget):
    """Live appearance editor."""

    themeChanged = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._accent = ""
        self._build_ui()
        self.load()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 18)
        outer.setSpacing(14)

        header = PageHeader(
            "Customization",
            "Make BuzzInstaller yours. Changes apply instantly.",
        )
        self.btn_reset = QPushButton("Reset appearance")
        self.btn_reset.setObjectName("ghost")
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._reset)
        header.add_action(self.btn_reset)
        outer.addWidget(header)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 6, 0)
        layout.setSpacing(14)

        layout.addWidget(self._build_theme_card())
        layout.addWidget(self._build_style_card())
        layout.addWidget(self._build_accent_card())
        layout.addWidget(self._build_preview_card())
        layout.addStretch(1)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)

        self.status = QLabel("")
        self.status.setObjectName("caption")
        outer.addWidget(self.status)

    # ------------------------------------------------------------------
    def _build_theme_card(self) -> Card:
        card = Card("Theme", "Pick a base theme or follow Windows.")
        row = QHBoxLayout()
        row.setSpacing(8)
        self.theme_group = QButtonGroup(self)
        self.theme_group.setExclusive(True)
        self._theme_buttons: dict[object, QPushButton] = {}
        for key, label in ((None, "System"), (False, "Light"), (True, "Dark")):
            button = QPushButton(label)
            button.setObjectName("segment")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setMinimumHeight(34)
            self.theme_group.addButton(button)
            self._theme_buttons[key] = button
            button.clicked.connect(lambda _=False, value=key: self._set_theme(value))
            row.addWidget(button)
        row.addStretch(1)
        card.body.addLayout(row)
        return card

    def _build_style_card(self) -> Card:
        card = Card("Interface", "Density and shape of the interface.")
        self.combo_style = QComboBox()
        for key, preset in STYLE_PRESETS.items():
            self.combo_style.addItem(preset["label"], key)
        self.combo_style.currentIndexChanged.connect(self._on_style_changed)
        card.body.addWidget(SettingRow(
            "Style",
            "Discord is the default flat look. Compact fits more on screen.",
            self.combo_style,
        ))

        self.slider_radius = SliderSpin(0, 18, 8, step=1, suffix=" px", width=110)
        self.slider_radius.valueChanged.connect(self._save)
        card.body.addWidget(SettingRow(
            "Corner radius",
            "0 gives sharp corners; 18 gives soft rounded cards.",
            self.slider_radius,
        ))

        self.slider_scale = SliderSpin(85, 135, 100, step=5, suffix=" %", width=110)
        self.slider_scale.valueChanged.connect(self._save)
        card.body.addWidget(SettingRow(
            "Text size",
            "Scales the whole interface without changing the layout.",
            self.slider_scale,
        ))
        return card

    def _build_accent_card(self) -> Card:
        card = Card(
            "Accent colour",
            "Used for buttons, links, selected items and progress bars.",
        )
        grid = QGridLayout()
        grid.setSpacing(8)
        self._swatch_buttons: list[QPushButton] = []
        for index, (name, colour) in enumerate(ACCENT_PRESETS):
            button = QPushButton()
            button.setObjectName("swatch")
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedSize(34, 34)
            button.setToolTip(name)
            button.setProperty("colour", colour)
            button.setStyleSheet(
                f"QPushButton#swatch {{ background: {colour}; }}"
                f"QPushButton#swatch:checked {{ border: 3px solid white; }}"
            )
            button.clicked.connect(
                lambda _=False, value=colour: self._set_accent(value)
            )
            grid.addWidget(button, index // 5, index % 5)
            self._swatch_buttons.append(button)
        card.body.addLayout(grid)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.txt_hex = QLineEdit()
        self.txt_hex.setPlaceholderText("#5865F2")
        self.txt_hex.setMaxLength(7)
        self.txt_hex.editingFinished.connect(self._apply_hex)
        row.addWidget(self.txt_hex, 1)
        self.btn_pick = QPushButton("Pick…")
        self.btn_pick.setObjectName("secondary")
        self.btn_pick.setCursor(Qt.PointingHandCursor)
        self.btn_pick.clicked.connect(self._pick_colour)
        row.addWidget(self.btn_pick)
        self.btn_default_accent = QPushButton("Use default")
        self.btn_default_accent.setObjectName("ghost")
        self.btn_default_accent.setCursor(Qt.PointingHandCursor)
        self.btn_default_accent.clicked.connect(lambda: self._set_accent(""))
        row.addWidget(self.btn_default_accent)
        card.body.addLayout(row)

        self.accent_note = QLabel("")
        self.accent_note.setObjectName("caption")
        self.accent_note.setWordWrap(True)
        card.body.addWidget(self.accent_note)
        return card

    def _build_preview_card(self) -> Card:
        card = Card("Preview", "A quick look at the current appearance.")
        self.preview = QFrame()
        self.preview.setObjectName("nestedCard")
        preview_layout = QVBoxLayout(self.preview)
        preview_layout.setContentsMargins(16, 14, 16, 14)
        preview_layout.setSpacing(10)

        title = QLabel("Ready to share")
        title.setObjectName("cardTitle")
        preview_layout.addWidget(title)
        sample = QLabel("BZ://h0l6lgmru052")
        sample.setObjectName("muted")
        preview_layout.addWidget(sample)

        row = QHBoxLayout()
        row.setSpacing(8)
        primary = QPushButton("Primary action")
        primary.setObjectName("primary")
        row.addWidget(primary)
        secondary = QPushButton("Secondary")
        secondary.setObjectName("secondary")
        row.addWidget(secondary)
        row.addStretch(1)
        preview_layout.addLayout(row)
        card.body.addWidget(self.preview)
        return card

    # ------------------------------------------------------------------
    def load(self) -> None:
        self._loading = True
        try:
            cfg = load_config()
            dark = cfg.get("dark_mode", False)
            button = self._theme_buttons.get(dark)
            if button is not None:
                button.setChecked(True)
            style = str(cfg.get("ui_style", "discord") or "discord")
            index = self.combo_style.findData(style)
            self.combo_style.setCurrentIndex(index if index >= 0 else 0)
            try:
                radius = int(cfg.get("ui_radius", 8))
            except (TypeError, ValueError):
                radius = 8
            self.slider_radius.setValue(max(0, min(18, radius)))
            try:
                scale = int(cfg.get("ui_scale", 100))
            except (TypeError, ValueError):
                scale = 100
            self.slider_scale.setValue(max(85, min(135, scale)))
            self._accent = str(cfg.get("accent_color", "") or "")
            self._sync_accent()
        finally:
            self._loading = False

    def _on_style_changed(self, _index: int = 0) -> None:
        if self._loading:
            return
        key = self.combo_style.currentData() or "discord"
        preset = STYLE_PRESETS.get(key, STYLE_PRESETS["discord"])
        self.slider_radius.setValue(int(preset["radius"]))
        self._save()

    def _save(self, *_args) -> None:
        if self._loading:
            return
        cfg = load_config()
        cfg["dark_mode"] = self._selected_theme()
        cfg["ui_style"] = self.combo_style.currentData() or "discord"
        cfg["ui_radius"] = int(self.slider_radius.value())
        cfg["ui_scale"] = int(self.slider_scale.value())
        cfg["accent_color"] = self._accent
        if save_config(cfg):
            self.status.setText("Appearance saved.")
            self.themeChanged.emit()
        else:
            self.status.setText("Could not save appearance settings.")

    # ------------------------------------------------------------------
    def _selected_theme(self):
        for key, button in self._theme_buttons.items():
            if button.isChecked():
                return key
        return False

    def _set_theme(self, dark) -> None:
        cfg = load_config()
        cfg["dark_mode"] = dark
        save_config(cfg)
        self.status.setText("Theme updated.")
        self.themeChanged.emit()

    def _set_accent(self, colour: str) -> None:
        self._accent = colour or ""
        self._sync_accent()
        self._save()

    def _apply_hex(self) -> None:
        from ui.theme import _normalise_colour
        colour = _normalise_colour(self.txt_hex.text())
        if not colour:
            self.status.setText("Enter a colour like #5865F2.")
            self._sync_accent()
            return
        self._set_accent(colour)

    def _pick_colour(self) -> None:
        initial = self._accent or get_palette_from_config().accent
        colour = QColorDialog.getColor(
            initial, self, "Choose an accent colour"
        )
        if colour.isValid():
            self._set_accent(colour.name())

    def _sync_accent(self) -> None:
        chosen = (self._accent or "").lower()
        for button in self._swatch_buttons:
            colour = str(button.property("colour") or "").lower()
            button.setChecked(bool(chosen) and colour == chosen)
        self.txt_hex.setText(self._accent or "")
        self.accent_note.setText(
            "Using the custom accent colour."
            if self._accent else
            "Using the default accent for the selected theme."
        )

    def _reset(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        answer = QMessageBox.question(
            self, "Reset appearance",
            "Reset the theme, colours and interface style to defaults?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        cfg = load_config()
        cfg["dark_mode"] = False
        cfg["ui_style"] = "discord"
        cfg["ui_radius"] = 8
        cfg["ui_scale"] = 100
        cfg["accent_color"] = ""
        save_config(cfg)
        self.load()
        self.themeChanged.emit()
        self.status.setText("Appearance reset to defaults.")

    def refresh(self) -> None:
        self.load()
