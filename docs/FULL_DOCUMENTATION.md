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
   - [Dashboard](#dashboard)
   - [Quick Send (Upload)](#quick-send-upload)
   - [Quick Receive (Download)](#quick-receive-download)
   - [Transfer History](#transfer-history)
   - [Global Hotkey](#global-hotkey)
   - [System Tray](#system-tray)
7. [In-App Updates](#in-app-updates)
8. [Configuration](#configuration)
9. [Background Process](#background-process)
10. [Windows Integration](#windows-integration)
11. [Developer Tools](#developer-tools)
12. [Troubleshooting](#troubleshooting)

---

## Overview

BuzzInstaller is a Windows utility for sending and receiving files through **BuzzHeavier** (`buzzheavier.com`). It provides:

- **Dashboard** — a sidebar-based menu with Overview, Send, Receive, History, Settings and About.
- **Quick Send** — drag files/folders onto the window, or right-click any file/folder → "Quick Send" to upload and get a `BZ://` link.
- **Quick Receive** — open a `BZ://code` link (or use the global hotkey) to download.
- **Transfer History** — searchable record of transfers with links that can be copied again.
- **In-App Updates** — checks GitHub Releases and installs new versions from inside the app.
- **Global Hotkey** — a lightweight background process keeps the hotkey working even when no window is open.
- **System Tray** — optional tray icon with quick actions.

The app is written in **Python 3.12** with **PySide6** (Qt) for the GUI, and packaged as an **onedir** bundle via **PyInstaller**.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    BuzzInstaller.exe                        │
│                                                             │
│  ┌──────────────────────┐      ┌─────────────────────────┐  │
│  │  Foreground (GUI)    │      │  Background (headless)  │  │
│  │  - Dashboard pages   │      │  - Global hotkey owner  │  │
│  │  - Send / Receive    │      │  - ~0 CPU idle          │  │
│  │  - History / Settings│      │  - No window            │  │
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
│   │   ├── history.py              # Transfer history store
│   │   ├── updater.py              # GitHub release check + self-update
│   │   └── protocol_handler.py     # BZ:// URL / CLI arg parsing
│   ├── system/
│   │   ├── background_service.py   # Headless hotkey process (single-instance)
│   │   ├── hotkey_service.py       # RegisterHotKey + WM_HOTKEY handling
│   │   ├── clipboard_service.py    # Thread-safe clipboard (GUI-thread marshalling)
│   │   ├── clipboard.py            # (legacy) clipboard helpers
│   │   ├── context_menu.py         # Windows shell integration (protocol, menu, autostart)
│   │   ├── path_manager.py         # User PATH management (add/remove BuzzInstaller dir)
│   │   ├── registry_manager.py     # winreg wrapper
│   │   └── system_tray.py          # Optional tray icon
│   ├── ui/
│   │   ├── main_window.py          # Dashboard shell, sidebar, updates, tray
│   │   ├── pages/                  # Home, Send, Receive, History, Settings, About
│   │   ├── widgets.py              # Cards, stat tiles, drop zone, toast, checkbox
│   │   ├── workers.py              # Threaded upload/download/update workers
│   │   ├── update_dialog.py        # Release notes + update download UI
│   │   ├── base_window.py          # Frameless draggable window base
│   │   ├── settings_window.py      # Legacy CLI settings dialog
│   │   ├── progress_window.py      # Upload/download progress
│   │   ├── result_window.py        # Post-upload result
│   │   ├── error_dialog.py         # Error display
│   │   └── theme.py                # Light/dark palette + QSS
│   ├── utils/
│   │   ├── file_utils.py           # Size formatting, ZIP extraction, sanitize
│   │   ├── logging.py              # Logging setup
│   │   └── notifications.py        # Windows toast notifications
│   └── assets/                     # Icons
├── BuzzInstallerInstaller/         # Separate installer/updater
├── VERSIONS.py                     # Authoritative app + installer versions
└── docs/
    └── FULL_DOCUMENTATION.md       # This file

tools/
├── build.py                        # PyInstaller build + ZIP + checksums
├── release.py                      # Version bump, changelog, git, gh release
├── dev.py                          # Command-line developer tool
├── dev_gui.py                      # Developer Studio GUI
└── README.md                       # Developer documentation
```

---

## Installation & Building

### Prerequisites

- **Python 3.12** (64-bit)
- **PySide6** (`pip install PySide6`)
- **PyInstaller** (`pip install pyinstaller`)

### Build the executable

```cmd
python tools/dev.py build
```

The command stops any running BuzzInstaller, builds the onedir app and the
installer, then writes the release assets:

```
dist/
    BuzzInstaller/
        BuzzInstaller/             # onedir application
        BuzzInstaller.zip          # release asset (contents at archive root)
    BuzzInstallerInstaller/
        BuzzInstallerInstaller.exe
    checksums.txt                  # SHA-256 for both assets
```

Use `--no-installer` for a faster app-only build, or `--no-zip` to skip the
archive.

### Run from source (development)

```cmd
python tools/dev.py run
```

Or launch the app directly:

```cmd
python src/BuzzInstaller/main.py
```

---

## Command-Line Interface (CMD)

BuzzInstaller accepts several command-line arguments. All are optional.

| Argument | Description |
|----------|-------------|
| *(no args)* | Opens the **dashboard** (Overview). |
| `--settings` | Opens the **Settings** window (legacy dialog). |
| `--send <path> [path...]` | **Quick Send** — upload the given file(s)/folder(s). |
| `BZ://code` | **Quick Receive** — download the given code (protocol activation). |
| `--protocol <url>` | **Quick Receive** — handle a `BZ://` or `https://buzzheavier.com/` URL. |
| `--check-updates` | Check GitHub Releases and print the result (headless). |
| `--install` | Register the `BZ://` protocol + Explorer context menu. |
| `--uninstall` | Remove all Windows integration (including the PATH entry). |
| `--background` | Run the **headless background hotkey process** (no window). |
| `--tray` | Run in **system-tray mode**. |
| `--version` | Print version and exit. |
| `-?` / `--help` | Show help. |

### Examples

```cmd
:: Open the dashboard
BuzzInstaller.exe

:: Check for updates
BuzzInstaller.exe --check-updates

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

### `bz` — Short Command

Once **"Add BuzzInstaller to PATH"** is enabled in Settings, the `bz` command is available from any terminal. It simply forwards all arguments to `BuzzInstaller.exe`:

```cmd
:: Upload a file
bz --send "C:\path\to\file.zip"

:: Download a BZ link
bz BZ://h0l6lgmru052

:: Register Windows integration
bz --install

:: Remove Windows integration
bz --uninstall

:: Run the background hotkey process
bz --background

:: Run in system tray
bz --tray

:: Show version
bz --version

:: Show help
bz --help
```

> **Note:** `bz` and `BuzzInstaller.exe` are interchangeable — both accept the same arguments.
> After enabling the setting, open a **new** terminal window for `bz` to be available.
> An already-open CMD/PowerShell window will not see the updated PATH until it is restarted.

### Protocol activation

When Windows opens a `BZ://` link, it invokes:

```cmd
"BuzzInstaller.exe" "BZ://code"
```

The app extracts the code and starts the download automatically.

---

## Usage

### Dashboard

Launching BuzzInstaller with no arguments opens the dashboard. The sidebar
switches between six pages:

| Page | Purpose |
|------|---------|
| **Overview** | Drop target, BZ link box, lifetime stats and recent activity. |
| **Send** | Files/folders queue, GitHub-style drop zone and upload button. |
| **Receive** | Paste a `BZ://` code or full URL, set download options, download. |
| **History** | Search/filter every upload and download; copy links again. |
| **Settings** | All options, applied immediately (no restart). |
| **About** | Version, update check, release/source links and diagnostic folders. |

Convenience shortcuts:

| Shortcut | Action |
|----------|--------|
| `Ctrl+1` … `Ctrl+6` | Jump to a page |
| `Ctrl+R` | Receive |
| `Ctrl+H` | History |
| `Ctrl+,` | Settings |
| `F1` | About |
| Drag & drop anywhere | Send the dropped files |

Closing the window keeps BuzzInstaller running in the tray when
**Keep running in the background when the window is closed** is enabled.

### Quick Send (Upload)

1. Right-click any file or folder in Explorer and select **Quick Send**, or
   drag items onto the dashboard / Send page.
2. A progress view appears; on completion the `BZ://` link is copied to your
   clipboard and shown in the result view and History.

### Quick Receive (Download)

**Method 1 — Protocol link:** Click a `BZ://code` link (e.g. in Discord, a browser, or a document). BuzzInstaller launches and downloads automatically.

**Method 2 — Global hotkey (default Alt+F1):**

1. Highlight a `BZ://code` in any app (Discord, browser, Notepad, etc.).
2. Press **Alt+F1**.
3. BuzzInstaller launches (if needed) and the download begins immediately.

> The hotkey works even when BuzzInstaller has **no window open** — a lightweight background process handles it.

### Transfer History

Every completed upload and download is recorded in `history.json` under
`%LOCALAPPDATA%\BuzzInstaller`. The History page supports:

- free-text search across names, codes and paths
- filtering by uploads / downloads
- copying a link or BZ code again
- opening the download location
- clearing the history

### Global Hotkey

- **Default:** `Alt+F1`
- **Configurable:** Change it in Settings → "Record Hotkey…"
- **Modes:**
  - **Clipboard Mode** — reads the current clipboard; if it contains exactly one `BZ://code`, starts the download.
  - **Selected Text Mode** — retrieves the highlighted text (uses UI Automation where possible; falls back to a temporary Ctrl+C that preserves/restores your full clipboard).

### System Tray

If enabled (Settings → "Minimize to tray" or "Start with Windows"), a tray icon provides:

- **Open BuzzInstaller**
- **Send files…**
- **Receive a file…**
- **Update available / Check for updates**
- **Settings**
- **Open Downloads**
- **About**
- **Exit**

---

## In-App Updates

The dashboard checks the GitHub Releases API for the newest public release.
The check runs on startup when **Check for updates when BuzzInstaller starts**
is enabled, and can be triggered manually from Settings, About, the sidebar or
`--check-updates`.

When a newer version exists:

1. a banner appears in the sidebar, Overview and Settings;
2. the update dialog shows the version, download size and release notes;
3. **Install update** downloads `BuzzInstaller.zip` with a progress bar;
4. BuzzInstaller exits and a detached PowerShell helper waits for it, backs up
   the current installation to `BuzzInstaller.backup`, extracts the new files,
   restores `config.json`, `history.json` and the `logs`/`cache`/`updates`
   folders, and relaunches the app.

The helper log is written to
`%LOCALAPPDATA%\BuzzInstaller\logs\update.log`.

> Self-update requires an installed (frozen) build. Source runs can still check
> for updates, but the dialog links to the release page instead of installing.

Publishing that release is one click in the **Developer Studio**
(`dev-gui.cmd`); see [Developer Tools](#developer-tools).

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
| `start_with_windows` | `false` | Launch BuzzInstaller at login. |
| `minimize_to_tray` | `false` | Always keep a tray icon. |
| `close_to_tray` | `true` | Closing the window keeps the app in the tray. |
| `remember_window_size` | `true` | Restore the dashboard geometry. |
| `add_to_path` | `false` | Add the BuzzInstaller folder to the user PATH (enables `bz`). |
| `enable_global_hotkey` | `true` | Enable the global hotkey. |
| `global_hotkey` | `"Alt+F1"` | The hotkey combination. |
| `use_selected_text` | `true` | Read highlighted text instead of the clipboard. |
| `start_background_at_login` | `false` | Start the background hotkey process at login. |
| `dark_mode` | `false` | `false`=light, `true`=dark, `null`=system. |
| `auto_check_updates` | `true` | Check GitHub Releases on startup. |
| `auto_download_updates` | `false` | Open the update dialog automatically. |
| `last_update_check` | `0` | Unix timestamp of the last check. |
| `skipped_update_version` | `""` | Version the user dismissed. |
| `enable_notifications` | `true` | Show toast notifications. |
| `check_for_updates` | `true` | Legacy alias kept for compatibility. |
| `show_downloads_folder_button` | `true` | Reserved. |

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

### Add BuzzInstaller to PATH

The **"Add BuzzInstaller to PATH"** setting (Settings → checkbox) adds the directory containing `BuzzInstaller.exe` to the **user** PATH:

- Registry key: `HKCU\Environment\Path`
- Value type: `REG_EXPAND_SZ` (preserves `%VAR%` entries)
- No administrator privileges required
- The system PATH (`HKLM\...\Environment`) is **never** modified
- Duplicate entries are avoided (case-insensitive, normalised comparison)
- A companion registry value (`HKCU\Environment\BuzzInstallerPath`) records the exact entry BuzzInstaller added, so removal only deletes our own entry
- After modification, `WM_SETTINGCHANGE` is broadcast so newly launched applications see the updated PATH

**Enabling:**

1. Open Settings.
2. Check **"Add BuzzInstaller to PATH"**.
3. Click **Save**.
4. Open a **new** terminal window.

**Disabling:**

1. Open Settings.
2. Uncheck **"Add BuzzInstaller to PATH"**.
3. Click **Save**.
4. Open a **new** terminal window.

> **Note:** An already-open CMD/PowerShell window retains its old environment variables. This is normal Windows behaviour — not a failure. Open a new terminal to use `bz`.

**Uninstall:** `--uninstall` also removes the BuzzInstaller-managed PATH entry.

---

## Developer Tools

All developer tooling lives in `tools/`; full reference in
[`tools/README.md`](../tools/README.md).

### Command line

```cmd
python tools/dev.py run                  # run from source
python tools/dev.py test                 # compile + all test suites
python tools/dev.py build                # app + installer + ZIP + checksums
python tools/dev.py build --no-installer # faster app-only build
python tools/dev.py bump patch           # 1.2.2 -> 1.2.3
python tools/dev.py push -m "Fix menu"   # commit + push source only
python tools/dev.py release --bump patch --notes "New dashboard"
python tools/dev.py status               # versions, branch, gh, artifacts
```

### Developer Studio

```cmd
python tools/dev_gui.py
```

`dev-gui.cmd` in the repository root opens the same window without a console.
It shows the current version, branch, working tree, last tag and GitHub CLI
status, and its **Rebuild & Publish** button runs the complete pipeline:

1. bump `src/VERSIONS.py`
2. prepend a `CHANGELOG.md` entry
3. build `BuzzInstaller.zip`, `BuzzInstallerInstaller.exe` and checksums
4. commit, tag `vX.Y.Z`, push
5. create the GitHub Release and upload the assets

Installed apps detect that release through the in-app updater.

### Adding a new setting

1. Add a default to `DEFAULTS` in `config/config_manager.py`.
2. Add a control in `ui/pages/settings_page.py` and load/save it via
   `load_config()` / `save_config()`.

### PATH integration

The PATH feature lives in `system/path_manager.py`:

- `add_to_path()` — adds the BuzzInstaller directory to the user PATH.
- `remove_from_path()` — removes only the BuzzInstaller-managed entry.
- `is_in_path()` — checks whether the directory is currently present.
- `get_buzzinstaller_dir()` — resolves the executable's directory dynamically.
- `get_path_entries()` — returns the current user PATH entries.

The `bz.cmd` launcher is copied next to `BuzzInstaller.exe` by the build script.

### Adding a new CLI flag

1. Add an argument in `core/protocol_handler.py` (`build_parser`).
2. Handle it in `main.py` (`main()`).

### Logging

Logs are written to:

```
%LOCALAPPDATA%\BuzzInstaller\logs\buzzinstaller.log
%LOCALAPPDATA%\BuzzInstaller\logs\startup.log
%LOCALAPPDATA%\BuzzInstaller\logs\crash.log
%LOCALAPPDATA%\BuzzInstaller\logs\update.log
```

### Running tests

```cmd
python tools/dev.py test
```

This compiles every source file and runs the app import suite, hotkey tests,
background handoff tests, installer tests, shortcut tests, update-check tests
and version-comparison tests.

---

## Troubleshooting

| Issue | Likely cause / fix |
|-------|--------------------|
| Hotkey doesn't work | Ensure "Enable Global Hotkey" is on in Settings. Check the background process is running (Task Manager → `BuzzInstaller.exe`). |
| Hotkey conflicts with another app | Change the hotkey in Settings → "Record Hotkey…". |
| `BZ://` link doesn't open | Run `BuzzInstaller.exe --install` to re-register the protocol. |
| Download fails with "Blocked by Cloudflare" | BuzzHeavier is temporarily rate-limiting; wait a moment and retry. |
| App won't start | Check `%LOCALAPPDATA%\BuzzInstaller\logs\crash.log` for a traceback. |
| Update says "Unable to check" | Check the network/proxy; GitHub must be reachable. |
| Update downloads but does not install | Self-update requires an installed build. See `logs/update.log`. |
| Update installed but version is unchanged | The release must be public (not draft/prerelease) and contain `BuzzInstaller.zip`. |
| Large build size | Expected — the onedir bundle ships PySide6/Qt. |
| Release tag already exists | Bump the version; tags are checked locally and on `origin`. |