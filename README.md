<div align="center">

# BuzzInstaller

**Send & receive files through [BuzzHeavier](https://buzzheavier.com) — right from your desktop.**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![PySide6](https://img.shields.io/badge/GUI-PySide6-green.svg)](https://doc.qt.io/qtforpython-6/)
[![Platform](https://img.shields.io/badge/Platform-Windows%2010%2F11-lightgrey.svg)](https://www.microsoft.com/windows)
[![License](https://img.shields.io/badge/License-MIT-orange.svg)](LICENSE)

</div>

---

## Features

- **Quick Send** — right-click any file/folder -> **Quick Send** -> get a `BZ://` link instantly.
- **Quick Receive** — click a `BZ://code` link (Discord, browser, anywhere) -> auto-download.
- **Global Hotkey** — press **Alt+F1** on any highlighted `BZ://code` to download — **even when no window is open**.
- **Lightweight Background Process** — ~0% CPU idle, minimal RAM, single-instance, event-driven (no polling).
- **Auto-Extract** — ZIP archives are extracted automatically (optional).
- **Dark Mode** — light/dark/system theme.
- **System Tray** — optional tray icon for quick access.
- **Add to PATH** — use `bz` from any terminal to send/receive files.

---

## Quick Start

### Option 1: Use the prebuilt executable

Download the latest `BuzzInstaller.exe` from [Releases](https://github.com/your-org/BuzzInstaller/releases) and run it.

### Option 2: Build from source

```cmd
git clone https://github.com/your-org/BuzzInstaller.git
cd BuzzInstaller
pip install PySide6 pyinstaller
python src/BuzzInstaller/build/build_exe.py
```

The executable is produced at `src/dist/BuzzInstaller/BuzzInstaller.exe`.

---

## How It Works

### The "hotkey always works" workflow

1. A friend sends `BZ://xxxxxxxxxxxx` in Discord.
2. You highlight the text.
3. You press **Alt+F1** (configurable).
4. BuzzInstaller launches automatically (if needed) and the download begins immediately.

> **No need to open BuzzInstaller beforehand.** A dedicated background process owns the hotkey and stays alive even when all windows are closed.

### Architecture

```
+------------------------------+      +------------------------------+
|      Foreground (GUI)       |      |     Background (headless)    |
|  - Settings / Upload / DL   |      |  - Owns the global hotkey    |
|  - Tray mode                |      |  - ~0% CPU idle              |
+--------------+--------------+      +--------------+---------------+
               |                                     |
               |   spawns if not running            |
               +-----------------+-------------------+
                                 |
                      +----------+----------+
                      |  Single-instance   |
                      |  mutex (global)    |
                      +---------------------+
```

- **`RegisterHotKey`** (native Windows API) — no keyboard polling.
- **`QFileSystemWatcher`** — hotkey changes apply live, no polling.
- **Single-instance mutex** — only one background process ever runs.

---

## Documentation

**Full documentation** — source code reference, all CMD commands, configuration, architecture, and troubleshooting:

**[Read the Full Documentation](docs/FULL_DOCUMENTATION.md)**

---

## Command-Line Interface

| Command | Description |
|---------|-------------|
| *(no args)* | Open Settings |
| `--send <path>` | Quick Send (upload) |
| `BZ://code` | Quick Receive (download) |
| `--install` | Register Windows integration |
| `--uninstall` | Remove Windows integration |
| `--background` | Run headless hotkey process |
| `--tray` | Run in system tray |
| `--version` | Show version |

```cmd
:: Upload a file
BuzzInstaller.exe --send "C:\path\to\file.zip"

:: Download a BZ link
BuzzInstaller.exe "BZ://h0l6lgmru052"

:: Register Windows integration
BuzzInstaller.exe --install
```

### `bz` — Short Command

Once **"Add BuzzInstaller to PATH"** is enabled in Settings, you can use the short `bz` command from any terminal:

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

> **Note:** `bz` simply forwards all arguments to `BuzzInstaller.exe` — both work identically.
> After enabling the setting, open a **new** terminal window for `bz` to be available.

---

## Configuration

Settings are stored in `%LOCALAPPDATA%\BuzzInstaller\config.json`.

| Setting | Default | Description |
|---------|---------|-------------|
| Global Hotkey | `Alt+F1` | The hotkey combination |
| Hotkey Mode | `clipboard` | `clipboard` or `selected_text` |
| Download Folder | user Downloads | Where files are saved |
| Auto-Extract ZIP | `true` | Extract ZIPs after download |
| Start with Windows | `false` | Launch background at login |
| Add BuzzInstaller to PATH | `false` | Add the app folder to your user PATH so `bz` works |
| Dark Mode | `false` | Light / dark / system |

---

## Development

```cmd
# Run from source
python src/BuzzInstaller/main.py

# Run import tests
python src/BuzzInstaller/test_imports.py

# Build the executable
python src/BuzzInstaller/build/build_exe.py
```

### Project layout

```
src/BuzzInstaller/
├── main.py                  # Entry point & arg dispatch
├── config/                  # Config manager & paths
├── core/                    # API, upload, download, protocol
├── system/                  # Background, hotkey, clipboard, tray, registry
├── ui/                      # Qt windows & theme
├── utils/                   # Logging, file helpers, notifications
└── build/                   # PyInstaller build script
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Made for the BuzzHeavier community**

</div>