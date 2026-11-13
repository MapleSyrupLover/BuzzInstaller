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

- **Simple navigation** — Uploads, Downloads, Settings, Customization and About, with a flat Discord-style interface.
- **Quick Upload** — drag files/folders onto the window, or right-click any file/folder -> **Quick Send**, to get a `BZ://` link instantly.
- **Quick Download** — paste a `BZ://code` or open a link (Discord, browser, anywhere) and download it.
- **Configurable Chunking** — split large uploads automatically, on demand, or into 2/4/8/custom parts, with each part's size shown before you start.
- **Global Hotkey** — press **Alt+F1** on any highlighted `BZ://code` to download — **even when no window is open**.
- **Recent Transfers** — uploads and downloads are listed on their pages, with one-click copy / open / share.
- **In-App Auto-Updater** — checks GitHub Releases, shows release notes, downloads and installs new versions without a separate installer.
- **Lightweight Background Process** — ~0% CPU idle, minimal RAM, single-instance, event-driven (no polling).
- **Auto-Extract** — ZIP archives are extracted automatically (optional).
- **Customization** — light/dark/system theme, three density presets, accent colour, corner radius and text size.
- **System Tray** — optional tray icon with quick actions; close-to-tray supported.
- **Add to PATH** — use `bz` from any terminal to send/receive files.
- **BuzzInstaller Mobile** — an Android companion in [`mobile/`](mobile/) with the same BuzzHeavier links, chunking options and themes.
- **Developer Studio** — one-click rebuild and GitHub release from a GUI (`dev-gui.cmd`).

---

## Quick Start

### Option 1: Use the prebuilt bundle

Download the latest `BuzzInstaller.zip` from [Releases](https://github.com/MapleSyrupLover/BuzzInstaller/releases), extract it, and run `BuzzInstaller.exe` from the extracted folder.

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

:: run the dashboard from source
python tools/dev.py run

:: or build everything (app bundle + installer + ZIP)
python tools/dev.py build
```

Build output:

```
dist/
    BuzzInstaller/
        BuzzInstaller.exe
        BuzzInstaller.zip          <- release asset / auto-update bundle
        BuzzInstaller/             <- onedir app
    BuzzInstallerInstaller/
        BuzzInstallerInstaller.exe
    checksums.txt
```

`BuzzInstaller.zip` has `BuzzInstaller.exe` and `_internal/` at its root, so
the in-app updater can extract it directly over an existing installation.

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
|  - Dashboard / Send / Recv  |      |  - Owns the global hotkey    |
|  - History / Settings       |      |  - ~0% CPU idle              |
|  - Tray mode                |      |  - Event-driven              |
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
| *(no args)* | Open the dashboard |
| `--send <path>` | Quick Send (upload) |
| `BZ://code` | Quick Receive (download) |
| `--check-updates` | Check for updates (prints in a console; opens the update dialog in an installed build) |
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

## In-App Updates

Installed builds check the latest GitHub Release on startup (configurable) and
show an update banner in the sidebar, Overview and Settings. Clicking it opens
the update dialog with:

- the new version and download size
- the release notes
- a progress bar while `BuzzInstaller.zip` downloads
- **Install update**, which closes the app, replaces the installed files and
  relaunches automatically

The replacement is handled by a detached PowerShell helper, so the running
application never has to overwrite itself. The previous installation is copied
to `BuzzInstaller.backup` before extraction, and `config.json`, `history.json`
and the `logs`/`cache`/`updates` folders are moved aside and restored so an
update never wipes your settings. Update activity is logged to
`%LOCALAPPDATA%\BuzzInstaller\logs\update.log`.

Self-update is only available in an installed (frozen) build. When running from
source the dialog points to the release page instead.

---

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
:: build both the app bundle and the installer, plus checksums
python tools/dev.py build

:: app bundle only (faster)
python tools/dev.py build --no-installer
```

Produces `dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe`.

### Testing the installer

```cmd
python tools/dev.py test
```

This compiles all sources and runs every test suite (app, hotkey, background
handoff, installer, shortcuts, update checks and version comparison).

---

## BuzzInstaller Mobile

An Android companion app lives in [`mobile/`](mobile/) and is built with
Flutter. It talks to the same BuzzHeavier endpoints, so links created on
either device open on the other.

- Upload files with the same chunk options (automatic, ask, 2/4/8 parts or
  custom), download one link or several chunk parts that are joined again.
- Light/dark/system themes, interface density presets, accent colour, corner
  radius and text size — the same customisation as the desktop app.
- `BZ://` links can open the app directly (Android intent filter), and recent
  transfers stay available for copy/share.

```powershell
cd mobile
flutter pub get
flutter test
flutter build apk --release
```

The release APK is written to
`mobile/build/app/outputs/flutter-apk/app-release.apk`.

---

## Configuration

Settings are stored in `%LOCALAPPDATA%\BuzzInstaller\config.json`.

| Setting | Default | Description |
|---------|---------|-------------|
| Global Hotkey | `Alt+F1` | The hotkey combination |
| Download from selected text | `true` | Hotkey reads highlighted text |
| Download Folder | user Downloads | Where files are saved |
| Auto-Extract ZIP | `true` | Extract ZIPs after download |
| Delete ZIP after extraction | `false` | Remove the archive after extracting |
| Open folder when done | `true` | Open Downloads after a transfer |
| Start with Windows | `false` | Launch at login |
| Start background at login | `true` | Launch the headless hotkey process at login |
| Keep running in background | `true` | Closing the window keeps the tray/background alive |
| Add BuzzInstaller to PATH | `false` | Add the app folder to your user PATH so `bz` works |
| Theme | `system` | Light / dark / system |
| Check for updates on startup | `true` | Automatic GitHub release check |
| Auto-download updates | `false` | Open the update dialog automatically |
| Notifications | `true` | Show in-app toasts |

---

## Development

The developer tooling lives in `tools/` and is documented in
[`tools/README.md`](tools/README.md).

```cmd
:: run from source
python tools/dev.py run

:: run all tests
python tools/dev.py test

:: build app + installer + release assets
python tools/dev.py build
```

### Developer Studio (GUI)

Double-click **`dev-gui.cmd`** or run `python tools/dev_gui.py`. It can rebuild
BuzzInstaller and publish the new package + updated source to GitHub in one
click. See [`tools/README.md`](tools/README.md) for the full pipeline.

### Project layout

```
src/BuzzInstaller/
├── main.py                  # Entry point & arg dispatch
├── config/                  # Config manager & paths
├── core/                    # API, upload, download, history, updater, protocol
├── system/                  # Background, hotkey, clipboard, tray, registry
├── ui/                      # Dashboard window, pages, widgets, theme, workers
│   └── pages/               # Uploads, Downloads, Settings, Customization, About
├── utils/                   # Logging, file helpers, notifications
└── version.py               # Reads the authoritative version from src/VERSIONS.py

src/BuzzInstallerInstaller/
├── main.py                  # Separate installer/updater entry point
├── installer.py             # Install/update orchestration & safe replacement
├── github_api.py            # GitHub Releases API client
├── downloader.py            # Asset download with progress & validation
├── ui.py                    # Minimal installer window
├── version.py               # Semantic version parsing & comparison
└── tests/                   # Version & installer tests

tools/
├── build.py                 # PyInstaller build + ZIP + checksums
├── release.py               # Version bump, changelog, git, gh release
├── dev.py                   # Command-line developer tool
├── dev_gui.py               # Developer Studio GUI
└── README.md                # Developer documentation

mobile/
├── lib/
│   ├── pages/               # Uploads, Downloads, Settings, Style, About
│   ├── services/            # API client, config, history, chunking
│   ├── theme/               # Palette, style presets, Material theme
│   └── widgets/             # Cards, list rows, shared components
├── android/                 # Android host project (BZ:// intent handling)
└── test/                    # Chunking, link parsing and UI smoke tests
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

<div align="center">

**Made for the BuzzHeavier community**

</div>