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
- **Separate Installer/Updater** — `BuzzInstallerInstaller.exe` installs and updates BuzzInstaller from GitHub Releases.

---

## Quick Start

### Option 1: Use the prebuilt executable

Download the latest `BuzzInstaller.exe` from [Releases](https://github.com/MapleSyrupLover/BuzzInstaller/releases) and run it.

### Option 2: Install with BuzzInstallerInstaller

Download `BuzzInstallerInstaller.exe` from [Releases](https://github.com/MapleSyrupLover/BuzzInstaller/releases) and run it.
It will:
- Check GitHub Releases for the latest version
- Install BuzzInstaller if not present, or update it if a newer version exists
- Launch BuzzInstaller when done

### Option 3: Build from source

```cmd
git clone https://github.com/MapleSyrupLover/BuzzInstaller.git
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

## Versioning Convention

Releases use the **`vMAJOR.MINOR.PATCH`** convention:

- `v1.0.0` — initial release
- `v1.1.0` — minor feature release
- `v1.2.3` — patch release

The GitHub Release tag is the authoritative source for the newest published
version. The installed application's `--version` output is the authoritative
source for the currently installed version. Version comparisons are done
semantically (numeric component-wise), never as string comparison — so
`1.10.0` correctly sorts newer than `1.9.0`.

## BuzzInstallerInstaller

`BuzzInstallerInstaller.exe` is a **separate, lightweight installer/updater** for BuzzInstaller:

- **Installs** BuzzInstaller (if not already installed)
- **Checks** GitHub Releases for the latest version
- **Compares** the installed and latest versions (semantic version comparison)
- **Updates** BuzzInstaller when a newer release exists
- **Deletes** BuzzInstaller (with confirmation)
- **Remembers** the installation location for future updates

### Installation location

When BuzzInstaller is not installed, the installer asks where to install it:

- **Install to PATH** — installs to `%LOCALAPPDATA%\BuzzInstaller\` and adds
  that directory to the user's PATH (no admin required, no duplicates).
- **Custom directory** — use the Browse button to select any folder.
  Custom directories are NOT added to PATH unless the user explicitly
  chooses the PATH option.

The selected location is remembered in per-user metadata so future updates
and deletion know where BuzzInstaller lives.

### Automatic version check

The installer automatically checks GitHub Releases on startup — no button
click needed. It displays the appropriate state:

| State | Display |
|-------|---------|
| Not installed | "BuzzInstaller is not installed." + Install button |
| Up to date | "BuzzInstaller is up to date." + Reinstall / Delete |
| Update available | "BuzzInstaller x.x.x is available." + Update / Delete |
| Newer installed | "Installed version is newer than the latest release." |
| Error | "Unable to check for updates." + Retry |

### Safe replacement

The installer is a separate, disposable process. It is the only component
that ever replaces `BuzzInstaller.exe`. This ensures BuzzInstaller never
overwrites its own running executable.

The update flow:

1. Query the GitHub Releases API for the latest release.
2. If installed, run `BuzzInstaller.exe --version` to get the installed version.
3. Compare versions semantically.
4. If `installed == latest` → "Latest version is already installed." and exit.
5. If `installed > latest` → do **not** downgrade; exit.
6. If `installed < latest` (or not installed) → download the new executable **to a temporary file**.
7. Verify the download completed (non-empty, correct size).
8. Wait for / close any running BuzzInstaller process.
9. Replace the executable (the download is already complete — never delete the old one first).
10. Launch BuzzInstaller.
11. Clean up temporary files.

### Download progress

The progress bar becomes visible before downloading begins and shows:
- Percentage (e.g. `68%`)
- Downloaded size / total size (e.g. `180 MB / 264 MB`)

The Cancel button (grey background, white text) safely aborts an in-progress
download and cleans up the temporary file.

### Delete BuzzInstaller

The "Delete BuzzInstaller" button:
1. Confirms with the user.
2. Terminates any running BuzzInstaller process.
3. Deletes `BuzzInstaller.exe`.
4. Removes the PATH entry (if PATH installation was used).
5. Unregisters Windows integration.
6. Clears installation metadata.

### Building the installer

```cmd
python src/BuzzInstallerInstaller/build/build_installer.py
```

Produces `src/dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe`.

### Testing the installer

```cmd
python src/BuzzInstallerInstaller/tests/test_version.py
python src/BuzzInstallerInstaller/tests/test_installer.py
python src/BuzzInstallerInstaller/tests/test_improvements.py
```

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
├── version.py               # Single authoritative version source
└── build/                   # PyInstaller build script

src/BuzzInstallerInstaller/
├── main.py                  # Separate installer/updater entry point
├── installer.py             # Install/update orchestration & safe replacement
├── github_api.py            # GitHub Releases API client
├── downloader.py            # Asset download with progress & validation
├── ui.py                    # Minimal installer window
├── version.py               # Semantic version parsing & comparison
├── build/                   # PyInstaller build script
└── tests/                   # Version & installer tests
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Made for the BuzzHeavier community**

</div>