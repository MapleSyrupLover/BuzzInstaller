# BuzzInstaller — Full Documentation

Complete reference for the **source code**, **usage**, and **command-line interface** of BuzzInstaller.

> **Quick start:** see the [README](../README.md) for a concise overview. This document is the deep-dive.

---

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Installation & Building](#installation--building)
5. [Command-Line Interface (CMD)](#command-line-interface-cmd)
6. [Usage](#usage)
   - [Quick Send (Upload)](#quick-send-upload)
   - [Quick Receive (Download)](#quick-receive-download)
   - [Global Hotkey](#global-hotkey)
   - [System Tray](#system-tray)
7. [Configuration](#configuration)
8. [Background Process](#background-process)
9. [Windows Integration](#windows-integration)
10. [Development](#development)
11. [Troubleshooting](#troubleshooting)

---

## Overview

BuzzInstaller is a Windows utility for sending and receiving files through **BuzzHeavier** (`buzzheavier.com`). It provides:

- **Quick Send** — right-click any file/folder → "Quick Send" to upload and get a `BZ://` link.
- **Quick Receive** — open a `BZ://code` link (or use the global hotkey) to download.
- **Global Hotkey** — a lightweight background process keeps the hotkey working even when no window is open.
- **System Tray** — optional tray icon for quick access.

The app is written in **Python 3.12** with **PySide6** (Qt) for the GUI, and packaged as a single standalone `.exe` via **PyInstaller**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BuzzInstaller.exe                        │
│                                                             │
│  ┌──────────────────────┐      ┌─────────────────────────┐  │
│  │  Foreground (GUI)    │      │  Background (headless)  │  │
│  │  - Settings window   │      │  - Global hotkey owner  │  │
│  │  - Upload flow       │      │  - ~0 CPU idle          │  │
│  │  - Download flow     │      │  - No window            │  │
│  │  - Tray mode         │      │  - Config watcher       │  │
│  └──────────┬───────────┘      └───────────┬─────────────┘  │
│             │                              │                │
│             │  spawns if not running       │                │
│             └──────────────┬───────────────┘                │
│                            │                                │
│              ┌─────────────▼─────────────┐                  │
│              │  Single-instance mutex    │                  │
│              │  Global\BuzzInstaller...  │                  │
│              └───────────────────────────┘                  │
└─────────────────────────────────────────────────────────────┘
```

### Key design decisions

| Decision | Rationale |
|----------|-----------|
| **Dedicated background process** | The global hotkey must work even when all windows are closed. A separate headless process owns it. |
| **`RegisterHotKey` (not polling)** | Native Windows API — the process waits on `WM_HOTKEY` messages, so CPU usage is effectively **zero** while idle. |
| **Single-instance mutex** | `CreateMutexW`/`OpenMutexW` on a global named mutex guarantees only one background process runs. |
| **Event-driven config watcher** | `QFileSystemWatcher` (uses `ReadDirectoryChangesW`) re-applies hotkey changes live — no polling. |
| **Process-spawn download dispatch** | The background shows no UI; when a valid `BZ://` link is detected it launches a fresh process that runs the existing download flow. No duplicated download logic. |

---

## Project Structure

```
src/
├── BuzzInstaller/
│   ├── main.py                     # Entry point & controller (arg dispatch)
│   ├── config/
│   │   ├── config_manager.py       # JSON config load/save + defaults
│   │   └── paths.py                # App data / config / log paths
│   ├── core/
│   │   ├── buzzheavier_api.py      # BuzzHeavier HTTP API client
│   │   ├── download_service.py     # Download orchestration (extract, launch)
│   │   ├── upload_service.py       # Upload orchestration
│   │   └── protocol_handler.py     # BZ:// URL / CLI arg parsing
│   ├── system/
│   │   ├── background_service.py   # Headless hotkey process (single-instance)
│   │   ├── hotkey_service.py       # RegisterHotKey + WM_HOTKEY handling
│   │   ├── clipboard_service.py    # Thread-safe clipboard (GUI-thread marshalling)
│   │   ├── clipboard.py            # (legacy) clipboard helpers
│   │   ├── context_menu.py         # Windows shell integration (protocol, menu, autostart)
│   │   ├── registry_manager.py     # winreg wrapper
│   │   └── system_tray.py          # Optional tray icon
│   ├── ui/
│   │   ├── base_window.py          # Frameless draggable window base
│   │   ├── settings_window.py      # Settings dialog
│   │   ├── progress_window.py      # Upload/download progress
│   │   ├── result_window.py        # Post-upload result
│   │   ├── error_dialog.py         # Error display
│   │   └── theme.py                # Light/dark palette + QSS
│   ├── utils/
│   │   ├── file_utils.py           # Size formatting, ZIP extraction, sanitize
│   │   ├── logging.py              # Logging setup
│   │   └── notifications.py        # Windows toast notifications
│   ├── assets/                     # Icons
│   └── build/
│       ├── build_exe.py            # PyInstaller build script
│       └── generate_icon.py        # Icon generator
├── dist/BuzzInstaller/             # Build output (BuzzInstaller.exe)
└── docs/
    └── FULL_DOCUMENTATION.md       # This file
```

---

## Installation & Building

### Prerequisites

- **Python 3.12** (64-bit)
- **PySide6** (`pip install PySide6`)
- **PyInstaller** (`pip install pyinstaller`)

### Build the executable

```cmd
python src/BuzzInstaller/build/build_exe.py
```

The output is a single standalone windowed executable:

```
src/dist/BuzzInstaller/BuzzInstaller.exe
```

> **Note:** The build uses `--collect-all PySide6`, so the `.exe` is large (~245 MB). This ensures all Qt plugins/translations are bundled and the app works on any Windows 10/11 machine without extra installs.

### Run from source (development)

```cmd
python src/BuzzInstaller/main.py
```

---

## Command-Line Interface (CMD)

BuzzInstaller accepts several command-line arguments. All are optional.

| Argument | Description |
|----------|-------------|
| *(no args)* | Opens the **Settings** window. |
| `--settings` | Opens the **Settings** window (explicit). |
| `--send <path> [path...]` | **Quick Send** — upload the given file(s)/folder(s). |
| `BZ://code` | **Quick Receive** — download the given code (protocol activation). |
| `--protocol <url>` | **Quick Receive** — handle a `BZ://` or `https://buzzheavier.com/` URL. |
| `--install` | Register the `BZ://` protocol + Explorer context menu. |
| `--uninstall` | Remove all Windows integration. |
| `--background` | Run the **headless background hotkey process** (no window). |
| `--tray` | Run in **system-tray mode**. |
| `--version` | Print version and exit. |
| `-?` / `--help` | Show help. |

### Examples

```cmd
:: Open settings
BuzzInstaller.exe

:: Upload a file
BuzzInstaller.exe --send "C:\path\to\file.zip"

:: Upload multiple files
BuzzInstaller.exe --send "C:\a.zip" "C:\b.zip"

:: Download via BZ:// link
BuzzInstaller.exe "BZ://h0l6lgmru052"

:: Download via full URL
BuzzInstaller.exe --protocol "https://buzzheavier.com/h0l6lgmru052"

:: Register Windows integration
BuzzInstaller.exe --install

:: Remove Windows integration
BuzzInstaller.exe --uninstall

:: Start the background hotkey process (headless)
BuzzInstaller.exe --background

:: Start in tray mode
BuzzInstaller.exe --tray
```

### Protocol activation

When Windows opens a `BZ://` link, it invokes:

```cmd
"BuzzInstaller.exe" "BZ://code"
```

The app extracts the code and starts the download automatically.

---

## Usage

### Quick Send (Upload)

1. Right-click any file or folder in Explorer.
2. Select **Quick Send**.
3. A progress window appears; on completion the `BZ://` link is copied to your clipboard and a result window shows the link.

### Quick Receive (Download)

**Method 1 — Protocol link:** Click a `BZ://code` link (e.g. in Discord, a browser, or a document). BuzzInstaller launches and downloads automatically.

**Method 2 — Global hotkey (default Alt+F1):**

1. Highlight a `BZ://code` in any app (Discord, browser, Notepad, etc.).
2. Press **Alt+F1**.
3. BuzzInstaller launches (if needed) and the download begins immediately.

> The hotkey works even when BuzzInstaller has **no window open** — a lightweight background process handles it.

### Global Hotkey

- **Default:** `Alt+F1`
- **Configurable:** Change it in Settings → "Record Hotkey…"
- **Modes:**
  - **Clipboard Mode** — reads the current clipboard; if it contains exactly one `BZ://code`, starts the download.
  - **Selected Text Mode** — retrieves the highlighted text (uses UI Automation where possible; falls back to a temporary Ctrl+C that preserves/restores your full clipboard).

### System Tray

If enabled (Settings → "Minimize to tray" or "Start with Windows"), a tray icon provides:

- **Quick Receive…** — prompt for a BZ code
- **Settings**
- **Open Downloads**
- **About**
- **Exit**

---

## Configuration

Settings are stored as JSON in:

```
%LOCALAPPDATA%\BuzzInstaller\config.json
```

| Key | Default | Description |
|-----|---------|-------------|
| `download_folder` | `""` (user Downloads) | Where files are saved. |
| `auto_extract_zip` | `true` | Auto-extract ZIP archives after download. |
| `delete_zip_after_extract` | `false` | Delete the ZIP after extraction. |
| `launch_folder_after_download` | `true` | Open Explorer on the destination after download. |
| `start_with_windows` | `false` | Launch the background process at login. |
| `minimize_to_tray` | `false` | Keep a tray icon. |
| `enable_global_hotkey` | `true` | Enable the global hotkey. |
| `global_hotkey` | `"Alt+F1"` | The hotkey combination. |
| `hotkey_mode` | `"clipboard"` | `"clipboard"` or `"selected_text"`. |
| `start_background_at_login` | `false` | Start the background hotkey process at login (independent of tray). |
| `dark_mode` | `false` | `false`=light, `true`=dark, `null`=system. |
| `check_for_updates` | `true` | (reserved) |
| `enable_notifications` | `true` | Show toast notifications. |
| `show_downloads_folder_button` | `true` | (reserved) |

---

## Background Process

The background process is the heart of the "hotkey always works" feature.

### How it starts

1. **First run:** When BuzzInstaller is launched with the hotkey enabled (default), `main.py` calls `_ensure_background_process()`, which probes for an existing instance and spawns `--background` if none is running.
2. **At login:** If "Start with Windows" or "Start background hotkey process at login" is enabled, Windows launches `BuzzInstaller.exe --background` at login.

### What it does

- Acquires the single-instance mutex (`Global\BuzzInstallerBackgroundMutex`).
- Creates a minimal `QApplication` (required for GUI-thread clipboard access) but **no window**.
- Registers the global hotkey via `RegisterHotKey`.
- Watches the config file with `QFileSystemWatcher` so hotkey changes apply live.
- Waits on Windows messages (`WM_HOTKEY`) — **zero CPU while idle**.
- On a valid `BZ://code`, launches a fresh BuzzInstaller process to run the existing download flow.

### When it exits

If the hotkey **and** all autostart options are disabled, the background process quits itself (it no longer serves a purpose).

---

## Windows Integration

### `BZ://` protocol

Registered under `HKCU\Software\Classes\BZ` (per-user, no admin required):

```
HKCU\Software\Classes\BZ
  (Default)       = "URL:BZ Protocol"
  URL Protocol    = ""
  DefaultIcon     = "<exe>,0"
  shell\open\command = "<exe>" "%1"
```

### Context menu

- `HKCU\Software\Classes\*\shell\BuzzInstallerSend` — files
- `HKCU\Software\Classes\Directory\shell\BuzzInstallerSend` — folders

Both run `"<exe>" --send "%1"`.

### Start with Windows

- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\BuzzInstaller` → `"<exe>" --background`
- `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\BuzzInstallerBackground` → `"<exe>" --background` (independent opt-in)

---

## Development

### Adding a new setting

1. Add a default to `DEFAULTS` in `config/config_manager.py`.
2. Add a checkbox/field in `ui/settings_window.py` (`_build_form`, `_load_settings`, `_save_settings`).
3. Use `load_config()` / `save_config()` to read/write it.

### Adding a new CLI flag

1. Add an argument in `core/protocol_handler.py` (`build_parser`).
2. Handle it in `main.py` (`main()`).

### Logging

Logs are written to:

```
%LOCALAPPDATA%\BuzzInstaller\logs\buzzinstaller.log
%LOCALAPPDATA%\BuzzInstaller\logs\startup.log
%LOCALAPPDATA%\BuzzInstaller\logs\crash.log
```

### Running tests

```cmd
python src/BuzzInstaller/test_imports.py
```

---

## Troubleshooting

| Issue | Likely cause / fix |
|-------|--------------------|
| Hotkey doesn't work | Ensure "Enable Global Hotkey" is on in Settings. Check the background process is running (Task Manager → `BuzzInstaller.exe`). |
| Hotkey conflicts with another app | Change the hotkey in Settings → "Record Hotkey…". |
| `BZ://` link doesn't open | Run `BuzzInstaller.exe --install` to re-register the protocol. |
| Download fails with "Blocked by Cloudflare" | BuzzHeavier is temporarily rate-limiting; wait a moment and retry. |
| App won't start | Check `%LOCALAPPDATA%\BuzzInstaller\logs\crash.log` for a traceback. |
| Large `.exe` size | Expected — PySide6 is fully bundled. |