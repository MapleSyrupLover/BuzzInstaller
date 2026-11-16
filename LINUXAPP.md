# Porting BuzzInstaller to Linux

This document is a complete work order for porting the Windows desktop app in
`src/BuzzInstaller` to Linux. It says exactly:

1. what must be **clipped** (deleted from the Linux code path),
2. what must be **moved** behind a platform abstraction (and which API it must
   expose),
3. what must be **added** (new Linux implementations, packaging, desktop
   integration), and
4. how to **build, install and test** the result.

It is written against the current tree (`1.7.2`). Read `README.md` and
`docs/FULL_DOCUMENTATION.md` first if you need product context; this file does
not repeat the feature tour.

> **Do not make the Windows code worse.** Keep every existing Windows module
> working. Add Linux implementations next to them and select one at import
> time. A Windows build must still pass `python tools/dev.py test` after the
> port.

---

## 1. Current shape of the application

Entry point: `src/BuzzInstaller/main.py`.

| Invocation | Behaviour |
| --- | --- |
| no args / `--settings` | Dashboard (Send page / Settings page) |
| `--send <path> [path...]` | Quick Send upload flow, then result window |
| `BZ://code` / `--protocol <url>` | Quick Receive download flow |
| `--install` | Register shell integration |
| `--uninstall` | Remove shell integration |
| `--tray` | Legacy tray-only mode |
| `--background` | Headless hotkey/clipboard process (`system/background_service.py`) |

Long-lived subsystems:

- **Global hotkey** — `system/hotkey_service.py` (Win32 `RegisterHotKey`,
  message-only window, `SendInput` Ctrl+C).
- **Background process** — `system/background_service.py` (single instance,
  Qt event loop, config watcher, launches a foreground process on hotkey).
- **Clipboard** — `system/clipboard_service.py` (Qt CPU-thread marshalling,
  verification, auto-clear, first-paste watcher).
- **Shell integration** — `system/context_menu.py`,
  `system/registry_manager.py`, `system/path_manager.py`,
  `system/start_menu.py`.
- **Notifications** — `utils/notifications.py` (`Shell_NotifyIconW`).
- **Tray** — `system/system_tray.py` (Qt `QSystemTrayIcon`, already portable
  in principle).
- **Updater** — `src/BuzzInstallerUpdater` + `src/buzzcommon/install.py`.
- **Setup** — `src/BuzzInstallerSetup` (fresh install only).

`src/BuzzInstallerInstaller` is legacy and is **not built**. Ignore it for
Linux; optionally delete it in a follow-up.

Data location today: one folder, `%LOCALAPPDATA%\BuzzInstaller` (config, logs,
cache, transfer DB, and usually the installed executable). `buzzcommon`
assumes the install directory is also the data directory so a transactional
update can preserve user files.

---

## 2. What stays exactly as it is

These parts are already portable and must not be rewritten:

| Area | Files |
| --- | --- |
| Qt/PySide6 GUI, windows, pages, theme engine | `ui/**`, `ui/theme.py`, `ui/widgets.py` |
| BuzzHeavier API, chunking, resumable bundles | `core/buzzheavier_api.py`, `core/bundle.py`, `core/upload_service.py`, `core/download_service.py` |
| Transfer state (SQLite) and history | `core/transfer_db.py`, `core/history.py` |
| Config file format and merge-with-defaults | `config/config_manager.py` (JSON only; the file-lock implementation must change) |
| Clipboard service **API and GUI-thread marshalling** | `system/clipboard_service.py` |
| Tray menu code | `system/system_tray.py` |
| Version source | `src/VERSIONS.py`, `src/BuzzInstaller/version.py` |
| CLI argument parsing | `core/protocol_handler.py`, `main.py` |
| Logging, file utilities | `utils/logging.py`, `utils/file_utils.py` |
| Git/GitHub release tooling | `tools/release.py` (with Linux asset changes, see §8) |

The Qt UI is the single biggest asset of this project. Keep it. Do not fork a
GTK UI.

---

## 3. What must be clipped from the Linux code path

These modules can remain in the repository for Windows, but they must not be
imported, executed or packaged on Linux. Most of them import `winreg` or call
`ctypes.windll` **at module import time**, so an unguarded import breaks the
whole app.

### 3.1 Delete/disable at import time

| Module | Why it breaks Linux | Action |
| --- | --- | --- |
| `system/registry_manager.py` | `import winreg` at top level | Move to `system/windows/registry_manager.py`; never import on Linux |
| `system/path_manager.py` | imports `registry_manager`, uses `WM_SETTINGCHANGE` | Windows-only; replace with `system/linux/path_integration.py` |
| `system/context_menu.py` | imports `registry_manager` + `path_manager`, Windows registry keys | Windows-only; replace with Linux integration manager |
| `system/start_menu.py` | PowerShell `.lnk`, `SHChangeNotify`, `APPDATA` | Windows-only; replace with `.desktop` helper |
| `utils/notifications.py` | `ctypes.windll.shell32` at import time | Rewrite with a guarded implementation (see §5.4) |
| `system/hotkey_service.py` | `ctypes.windll.user32`, `RegisterHotKey`, `SendInput` | Keep parsing helpers; replace backend (see §5.3) |
| `system/background_service.py` | `CreateMutexW`, `ctypes.wintypes` import, Win32 process flags | Replace single-instance + spawn logic (see §5.5) |
| `system/clipboard.py` | legacy Win32 clipboard helper | Not used by `clipboard_service.py`; delete or move to `system/windows/` |
| `buzzcommon/shortcuts.py` | PowerShell `.lnk`, App Paths, `SHChangeNotify` | Windows-only; Linux uses `.desktop` files |
| `buzzcommon/pathman.py` | `winreg`, `WM_SETTINGCHANGE` | Windows-only; Linux PATH handling is separate |
| `buzzcommon/install.py` | `from ctypes import wintypes` at import time, `taskkill`, Toolhelp32, `GetFileVersionInfoW` | Refactor: split Win32 pieces into a backend, keep transactional bundle install |
| `buzzcommon/metadata.py` | `LOCALAPPDATA` only | Move paths to a platform module |
| `BuzzInstallerInstaller/**` | legacy Windows installer | Not built; ignore |

> **Import-time landmine:** `from ctypes import wintypes` raises on Linux
> (it depends on Windows-only types). `buzzcommon/install.py` and
> `system/background_service.py` do this at module scope, so importing either
> one fails before any function runs. Guard those imports or move the Win32
> structs into `system/windows/`.

### 3.2 Windows-only behaviour to remove, not emulate

- **Explorer context-menu registration** (`HKCU\Software\Classes\*\shell\...`).
  Linux file managers do not share a registry. Replace with per-file-manager
  action files (§5.7). Do not pretend a registry-like store exists.
- **`BZ://` registration through `HKCU\Software\Classes\BZ`.** Replace with a
  desktop entry + `xdg-mime` (§5.6).
- **App Paths / Start Menu pins / `SHChangeNotify`.** Replace with a `.desktop`
  entry and `update-desktop-database` (§5.7).
- **"Start with Windows" Run key.** Replace with
  `~/.config/autostart/*.desktop` (§5.8).
- **AppUserModelID / `SetCurrentProcessExplicitAppUserModelID`.** No Linux
  equivalent; remove the call, keep the Windows branch.
- **Windows theme registry read** (`AppsUseLightTheme`). Replace with Qt's
  colour-scheme API or `gsettings` (§5.11).
- **`taskkill`, `tasklist`, Toolhelp32 process snapshots, `msvcrt.locking`.**
  Replace, do not shell out to Wine or `wmic`.
- **PowerShell, `explorer.exe`, `os.startfile`.** Replace with `xdg-open`,
  `gio` and D-Bus file-manager APIs (§5.9).
- **Version resource files** (`.ico`, `--version-file`). Linux binaries have no
  PE version resource; use the `installed-version.txt` marker only (§5.10).

### 3.3 Calls scattered through the UI

These are not separate modules but must be replaced (ideally via one helper):

| File | Windows call | Linux replacement |
| --- | --- | --- |
| `core/download_service.py` | `subprocess.Popen(["explorer.exe", ...])` | `xdg-open <dir>`; reveal via FileManager1 D-Bus |
| `ui/recent_transfers.py` | `os.startfile`, `explorer.exe /select,` | `xdg-open` / FileManager1 |
| `ui/pages/history_page.py` | `os.startfile`, `explorer.exe /select,` | same |
| `ui/pages/receive_page.py` | `os.startfile`, `explorer.exe /select,` | same |
| `ui/pages/send_page.py` | `os.startfile(value)` | `xdg-open` |
| `ui/pages/about_page.py` | `os.startfile(url)`, `os.startfile(path)` | `xdg-open` |
| `ui/pages/settings_page.py` | "Windows integration" card, start-with-Windows toggle | Linux integration card (desktop entry, autostart, URL handler) |
| `ui/settings_window.py` | same + `_sync_start_with_windows` | same |
| `ui/update_dialog.py` | `os.startfile(url)` | `xdg-open` |
| `ui/theme.py` | `winreg` theme read | Qt / gsettings |
| `main.py` | `ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID` | guarded no-op on Linux |
| `main.py` | `_ensure_start_menu_entry` (Start Menu + App Paths) | `_ensure_desktop_entry` on Linux |
| `main.py` | `WindowsIntegrationManager` import at top | import the platform integration manager instead |

The cleanest fix is a single `system/platform/` package (see §4) and replacing
direct Windows calls with it.

---

## 4. Add a platform abstraction layer

Create one small package and make the rest of the app call it. Suggested
layout:

```
src/BuzzInstaller/system/
    platform/
        __init__.py          # selects the backend, exports the API
        base.py              # Protocol/ABC definitions
        windows.py           # thin wrappers around today's modules
        linux.py             # Linux implementation
    windows/                 # move winreg/PowerShell modules here
        registry_manager.py
        path_manager.py
        context_menu.py
        start_menu.py
        shortcuts.py
```

Minimum API (keep it boring and synchronous):

```python
# system/platform/base.py
class Integration(Protocol):
    def register_all(self) -> None: ...
    def unregister_all(self) -> None: ...
    def is_registered(self) -> bool: ...

    def register_autostart(self, enabled: bool) -> None: ...
    def register_url_handler(self) -> None: ...
    def register_file_manager_actions(self) -> None: ...
    def register_path(self, install_dir: Path) -> None: ...
    def unregister_path(self, install_dir: Path) -> None: ...

class Shell(Protocol):
    def open_path(self, path: Path) -> None: ...          # xdg-open
    def reveal_path(self, path: Path) -> None: ...        # select in file manager
    def notify(self, title: str, body: str) -> None: ...

class Paths(Protocol):
    def data_dir(self) -> Path: ...
    def config_dir(self) -> Path: ...
    def cache_dir(self) -> Path: ...
    def log_dir(self) -> Path: ...
    def downloads_dir(self) -> Path: ...
    def install_dir(self) -> Path: ...

class SingleInstance(Protocol):
    def already_running(self, name: str) -> bool: ...
    def acquire(self, name: str) -> bool: ...
    def release(self) -> None: ...

class Hotkey(Protocol):
    def start(self, spec: str, callback) -> bool: ...
    def stop(self) -> None: ...
    def available(self) -> bool: ...
```

Selection at import time:

```python
# system/platform/__init__.py
import sys
if sys.platform == "win32":
    from .windows import integration, shell, paths, single_instance, hotkey
else:
    from .linux import integration, shell, paths, single_instance, hotkey
```

Migrate call sites gradually:

- `main.py`: `from system.platform import integration` instead of
  `from system.context_menu import WindowsIntegrationManager`.
- `settings_page.py`, `settings_window.py`: call `integration.*`.
- `paths.py`: delegate to `platform.paths`.
- `context_menu`/`start_menu` imports disappear from Linux modules.

Keep the existing class names where practical (`WindowsIntegrationManager`
inside `platform/windows.py`) so Windows behaviour is unchanged.

---

## 5. Linux implementation details

### 5.1 Filesystem layout (XDG)

Replace `%LOCALAPPDATA%`, `%APPDATA%` and `%USERPROFILE%` with XDG base
directories. Recommended mapping:

| Purpose | Linux path | Environment |
| --- | --- | --- |
| App data / transfer DB | `~/.local/share/BuzzInstaller` | `XDG_DATA_HOME` |
| Install directory | `~/.local/share/BuzzInstaller` (same root as data, option A) | — |
| Config | `~/.config/BuzzInstaller/config.json` | `XDG_CONFIG_HOME` |
| Cache / updates | `~/.cache/BuzzInstaller` | `XDG_CACHE_HOME` |
| Logs | `~/.local/share/BuzzInstaller/logs` (option A) or `~/.local/state/BuzzInstaller/logs` | `XDG_STATE_HOME` |
| Downloads | `~/Downloads` (respect `xdg-user-dir DOWNLOAD`) | — |

Two viable designs:

- **A. Single root (simplest, preserves today's update logic):** use
  `~/.local/share/BuzzInstaller` for data **and** the installed onedir build.
  `buzzcommon.install.install_bundle(..., data_dir=install_dir)` already
  preserves `PRESERVE_ON_UPDATE` entries, so updates keep config/history/DB.
  This is the recommended first port.
- **B. Split install/data (matches XDG more strictly):** install under
  `~/.local/opt/BuzzInstaller` and keep data under `~/.local/share/BuzzInstaller`.
  Then pass `data_dir=` explicitly and make sure `PRESERVE_ON_UPDATE` is
  restored from the data dir, not the install dir. More work; do it later.

Implementation notes:

- Resolve `XDG_*` with fallbacks and **expand `~`**.
- Use `pathlib.Path`, never string concatenation with `\\`.
- On non-Windows, `Path.home()` and `Path("/")` behave correctly; do not use
  `os.environ["LOCALAPPDATA"]`.
- Keep the `BUZZINSTALLER_TRANSFER_DB` environment override — the tests use it.

Suggested helper:

```python
def _xdg(var: str, default: str) -> Path:
    return Path(os.environ.get(var) or Path.home() / default).expanduser()
```

### 5.2 Config and transfer locks

`config/config_manager.py` uses `msvcrt.locking`; `core/transfer_lock.py` does
the same. On Linux:

- Use `fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)` for the config lock.
- Use `fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 1)` or `flock` for
  transfer chunk locks.
- Keep the retry loop and the "never delete the old config before the atomic
  rename" behaviour. `os.replace()` is atomic on the same filesystem on Linux.
- Do not use `msvcrt` on Linux. Guard the import:

```python
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl
```

The tests already skip/accept non-Windows locking; keep them passing.

### 5.3 Global hotkey

Status: `HotkeyService` parses specs on any platform, but registering and
detecting the key is Win32-only (`RegisterHotKey`, message-only window,
`SendInput` Ctrl+C).

Linux has three options, in order of preference:

1. **XDG Desktop Portal GlobalShortcuts** (Wayland-first, sandbox-friendly):
   `org.freedesktop.portal.GlobalShortcuts` over D-Bus. Requires a recent
   portal (`xdg-desktop-portal` + backend). Users get a permission dialog.
   This is the only fully supported Wayland route.
2. **X11 global grab** (`XGrabKey`) for X11 sessions. Implement with
   `python-xlib` (new dependency) or a tiny ctypes binding. Works on X11 and
   under XWayland only for XWayland windows, not native Wayland apps.
3. **Compositor shortcuts.** Document a manual fallback: the user binds
   Alt+F1 in GNOME/KDE settings to `buzzinstaller --hotkey-trigger`.

Regardless of backend, keep the existing hotkey *parsing* (`parse_hotkey`,
`format_hotkey`, `key_code_for_name`) in a portable module, and move the Win32
backend into `system/windows/hotkey_win32.py`.

Simulating Ctrl+C:

- **Windows:** `SendInput`.
- **X11:** `XTestFakeKeyEvent` (python-xlib).
- **Wayland:** not possible for security reasons. Do **not** attempt
  `ydotool` unless the user installs it; treat "hotkey reads selected text"
  as unsupported on Wayland and fall back to reading the current clipboard
  (which the existing service already does when no selection is found).
- The UI must show an honest capability label: "Selected-text capture is not
  available on Wayland; the hotkey uses the clipboard instead."

If no global-hotkey backend is available:

- `HotkeyService.start()` returns `False`.
- `BackgroundService` should not spawn a useless process; log once and keep the
  app fully functional without the hotkey.
- Settings should disable the hotkey controls with an explanation.

### 5.4 Notifications

`utils/notifications.py` currently calls `Shell_NotifyIconW` at import time.
Rewrite it as:

```python
def show_toast(title: str, message: str, *, timeout_ms: int = 4000) -> bool:
    if sys.platform == "win32":
        return _show_toast_win32(title, message, timeout_ms)
    return _show_toast_linux(title, message, timeout_ms)
```

Linux implementation order:

1. **D-Bus `org.freedesktop.Notifications`** (preferred):
   - Use `notify-send` via `subprocess` for zero dependencies:
     `["notify-send", "--app-name=BuzzInstaller", "--icon=buzzinstaller", title, message]`.
   - Or send the `Notify` call with `dbus-send`/`gdbus` if `notify-send` is not
     installed.
2. **`QSystemTrayIcon.showMessage`** if a tray is available (already used on
   first run in `system_tray.py`).
3. **No-op with a log line** if neither works (headless sessions).

Rules:

- Never raise from `show_toast`; callers treat notifications as best-effort.
- Truncate bodies to a sane length.
- Do not import `ctypes.wintypes` on Linux.

### 5.5 Single instance + background process

`system/background_service.py` uses a global Win32 mutex
(`Global\BuzzInstallerBackgroundMutex`). Replace with a POSIX advisory lock:

```python
import fcntl
lock_path = app_data_dir() / "background.lock"
fh = open(lock_path, "a+")
try:
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    owns = True
except BlockingIOError:
    owns = False
```

- Keep the file handle open for the lifetime of the process; closing releases
  the lock.
- The foreground process probes the same way (open + try-lock; if it fails,
  a background instance exists).
- Do **not** delete the lock file on exit; a stale file is harmless with
  `flock`. (If you prefer `lockf`, document the cleanup rule.)
- Spawn detached with `subprocess.Popen(..., start_new_session=True,
  close_fds=True)`. Drop `DETACHED_PROCESS`/`CREATE_NO_WINDOW`.
- Keep `QFileSystemWatcher` for config reload — it works on Linux (inotify).
- The headless Qt app still needs `QT_QPA_PLATFORM=offscreen`? No: the
  clipboard service wants a real GUI thread. Use `QApplication` with the normal
  platform plugin but never create a window. If the session has no display,
  use `offscreen` and document that clipboard/hotkey are unavailable.

Optional but recommended: a **systemd user unit** for login autostart instead
of `--background` from an autostart entry:

```ini
# ~/.config/systemd/user/buzzinstaller-background.service
[Unit]
Description=BuzzInstaller background hotkey service
After=graphical-session.target

[Service]
ExecStart=%h/.local/share/BuzzInstaller/BuzzInstaller --background
Restart=on-failure

[Install]
WantedBy=default.target
```

Use either the systemd unit **or** an autostart `.desktop` entry, not both.

### 5.6 `BZ://` URL handler

Linux associates URL schemes through desktop entries + MIME registration.

1. Install `~/.local/share/applications/buzzinstaller.desktop` (see §5.7).
2. Add the scheme to the entry:

```ini
[Desktop Entry]
Type=Application
Name=BuzzInstaller
Comment=Send and receive files with BuzzHeavier
Exec=/home/<user>/.local/share/BuzzInstaller/BuzzInstaller %u
Icon=buzzinstaller
Terminal=false
Categories=Utility;Network;
MimeType=x-scheme-handler/bz;
StartupWMClass=BuzzInstaller
```

3. Register it:

```bash
update-desktop-database ~/.local/share/applications
xdg-mime default buzzinstaller.desktop x-scheme-handler/bz
```

4. Verify:

```bash
xdg-mime query default x-scheme-handler/bz
gio open "BZ://code"
```

Notes:

- The scheme is case-insensitive in practice; register `x-scheme-handler/bz`.
- `Exec=... %u` passes the full URL; `main.py` already extracts the code via
  `extract_protocol_code`.
- `StartupWMClass` should match the Qt window class (`BuzzInstaller`) so the
  app groups correctly in docks/taskbars.
- Do not write to `/usr/share/applications` from a per-user install.

### 5.7 File-manager context menu ("Quick Send")

There is no universal Linux context-menu API. Implement per file manager and
degrade gracefully. The action is always:

```bash
BuzzInstaller --send %F
```

(`%F` = selected files; `%f` = one file.)

Targets, in priority order:

1. **Nautilus / GNOME Files** — `~/.local/share/nautilus/scripts/Quick Send`
   (executable shell script; `NAUTILUS_SCRIPT_SELECTED_FILE_PATHS` available)
   or a `nautilus-python` extension for a true menu item. Ship the script by
   default; mention the extension in docs.
2. **Dolphin / KDE** — `~/.local/share/kio/servicemenus/buzzinstaller.desktop`
   with `ServiceTypes=KonqPopupMenu/Plugin`, `MimeType=all/allfiles;inode/directory;`,
   `Actions=QuickSend`, and an `Exec=BuzzInstaller --send %F`.
3. **Thunar** — `~/.config/Thunar/uca.xml` entry (custom action) or
   `~/.local/share/thunar/uca.xml`; use `%F`.
4. **Nemo / Cinnamon** — `~/.local/share/nemo/actions/buzzinstaller.nemo_action`.
5. **PCManFM / others** — document a manual action; do not claim support.

Implementation rules:

- Quote paths safely. Prefer passing a list to `subprocess`; if the file
  manager requires a shell string, use `shlex.quote`.
- Create action files only if the corresponding config directory exists, so an
  installer does not litter unrelated desktops.
- Provide `--send` with multiple paths; it already accepts `[path...]`.
- Uninstall must remove every file you created (track them, or derive the
  paths deterministically).

### 5.8 Autostart ("Start with Linux")

Replace the `HKCU\...\Run` value with an XDG autostart entry:

```ini
# ~/.config/autostart/buzzinstaller-background.desktop
[Desktop Entry]
Type=Application
Name=BuzzInstaller background
Exec=/home/<user>/.local/share/BuzzInstaller/BuzzInstaller --background
Terminal=false
X-GNOME-Autostart-enabled=true
NoDisplay=true
```

- Create it when the user enables "Start with Linux".
- Remove it on disable and on uninstall.
- Only launch `--background` (hotkey + clipboard), not the full window.
- If you use a systemd user unit instead, enable/disable that and skip this
  file. Do not create both.

### 5.9 Opening folders and revealing files

Add one helper and replace every `os.startfile` / `explorer.exe` call:

```python
def open_path(path: Path) -> bool:
    return subprocess.run(["xdg-open", str(path)]).returncode == 0
```

For "reveal in file manager", try D-Bus first, then fall back to opening the
parent folder:

```python
def reveal_path(path: Path) -> bool:
    # org.freedesktop.FileManager1.ShowItems (Nautilus, Dolphin, Nemo, ...)
    uri = path.resolve().as_uri()
    try:
        subprocess.run([
            "dbus-send", "--session", "--dest=org.freedesktop.FileManager1",
            "--type=method_call", "/org/freedesktop/FileManager1",
            "org.freedesktop.FileManager1.ShowItems",
            f"array:string:{uri}", "string:",
        ], check=False, timeout=5)
        return True
    except (OSError, subprocess.SubprocessError):
        return open_path(path.parent)
```

- Never block the GUI thread on `xdg-open`; use `Popen` and detach.
- `xdg-open` may return non-zero in headless sessions; log, do not crash.

### 5.10 PATH / `bz` command

Windows edits `HKCU\Environment\Path`. On Linux do **not** rewrite the user's
PATH. Preferred:

1. Install a symlink `~/.local/bin/buzzinstaller -> .../BuzzInstaller`
   (create `~/.local/bin` if missing).
2. Optionally install `~/.local/bin/bz` as a small shell script:
   ```sh
   #!/bin/sh
   exec "$HOME/.local/share/BuzzInstaller/BuzzInstaller" "$@"
   ```
3. Tell the user to ensure `~/.local/bin` is on `PATH`; do not silently edit
   `.bashrc`, `.zshrc` or `/etc/environment`.
4. If you must persist an environment change, write
   `~/.config/environment.d/90-buzzinstaller.conf` and explain that it applies
   to the next login. There is no `WM_SETTINGCHANGE` equivalent.

Uninstall removes only the symlinks it created; never delete `~/.local/bin`.

### 5.11 Theme / system dark mode

`ui/theme.py::_system_uses_dark()` reads the Windows registry. On Linux:

- Prefer Qt 6's `QGuiApplication.styleHints().colorScheme()` (Qt ≥ 6.5) — it
  reflects the portal/desktop preference.
- Fallback: `gsettings get org.gnome.desktop.interface color-scheme` and
  `gtk-theme`, or the `QT_STYLE_OVERRIDE`/`QT_QPA_PLATFORMTHEME` environment.
- Keep light as the final fallback.
- Do not import `winreg` on Linux.

### 5.12 Process management (updater, "is running?", stop before update)

`buzzcommon/install.py` uses Toolhelp32 + `taskkill`, and
`BuzzInstallerInstaller/installer.py` uses `tasklist/taskkill`. On Linux:

- Find processes by scanning `/proc/*/exe` (or `/proc/*/cmdline`) and comparing
  resolved paths; this is dependency-free and works for the onedir build.
- Terminate with `os.kill(pid, signal.SIGTERM)`, wait, then `SIGKILL` after a
  timeout. Never `SIGKILL` first — the app needs to flush config/history.
- Skip the caller's own PID and its ancestors.
- If `psutil` is acceptable as a dependency, it is simpler; but keep the
  `/proc` fallback for minimal installs.
- `stop_app_processes()` must not return early with `[]` on Linux (today it
  does because of `if sys.platform != "win32": return []`). That would let an
  update replace a running binary (which is actually allowed on Linux for the
  executable, but Qt plugin/DLL replacement is still unsafe). Implement it.
- Locking/atomic replacement in `install_bundle` works on Linux; only the
  *discovery/termination* and *version check* parts need platform branches.

### 5.13 File version

Linux ELF binaries have no PE version resource. Keep:

- `get_file_version()` returning `None` on non-Windows (already the case).
- `installed_version()` falling back to `installed-version.txt` (already the
  case).
- `_verify_version()` skipping when no version is readable (already the case).

So no functional change is needed here — just do not add a Linux implementation
that tries to read a Windows resource.

### 5.14 Clipboard details (already mostly portable)

`system/clipboard_service.py` already:

- uses `QClipboard` for set/get/clear/save/restore,
- marshals to the GUI thread,
- verifies by reading back,
- falls back from "first paste" to a timeout when `sys.platform != "win32"`.

Remaining work:

- `_qt_clipboard_sequence_number()` calls `ctypes.windll.user32...`; it is
  wrapped in `try/except` and returns `0` on Linux. That means the timeout
  expiry cannot detect "clipboard changed by another app" by sequence number.
  On Linux, replace the sequence check with a **content comparison** plus a
  `QClipboard.dataChanged` signal subscription, or simply accept "clear only
  if the text still equals the copied link".
- The first-paste keyboard hook stays Windows-only. Do not try to install a
  global keyboard hook on Wayland. On X11, optionally implement XRecord later.
- `system/clipboard.py` (raw Win32) is unused by the service; do not port it.

### 5.15 Qt platform requirements

Document and check at startup:

- `libxcb` stack for X11 sessions (`libxcb-cursor0`, `libxkbcommon-x11-0`, etc.
  on Debian/Ubuntu).
- Wayland: Qt 6 uses the `wayland` platform plugin when available; otherwise
  falls back to XWayland. Do not force `QT_QPA_PLATFORM=xcb` unless the user
  asks.
- HiDPI works through Qt; keep `ui_scale` as-is.
- `--windowed` equivalent is not needed: PyInstaller on Linux has no console
  subsystem. A launched app inherits the terminal; that is normal. If you want
  to suppress stdout, redirect in the `.desktop` `Exec` line, not in code.

---

## 6. New files to add

Suggested names (adjust to taste, but keep the split):

```
src/BuzzInstaller/system/platform/__init__.py
src/BuzzInstaller/system/platform/base.py
src/BuzzInstaller/system/platform/windows.py
src/BuzzInstaller/system/platform/linux.py
src/BuzzInstaller/system/linux/
    desktop_entry.py        # write/remove .desktop files, update-desktop-database
    mime.py                 # xdg-mime registration/query
    autostart.py            # ~/.config/autostart handling
    file_manager.py         # Nautilus/Dolphin/Thunar/Nemo actions
    hotkey_portal.py        # GlobalShortcuts portal or X11 grab
    single_instance.py      # flock-based lock
    processes.py            # /proc scan + SIGTERM/SIGKILL
    notify.py               # notify-send / D-Bus
    paths.py                # XDG dirs
src/BuzzInstaller/tests/test_linux_platform.py
```

Also add a repo-root helper:

```
tools/install_linux_desktop.py   # dev convenience: create the .desktop entry
```

And packaging metadata (choose one, see §8):

```
packaging/linux/buzzinstaller.desktop
packaging/linux/buzzinstaller.png
packaging/linux/buzzinstaller.svg
packaging/linux/AppImageBuilder.yml      # optional
packaging/linux/debian/control           # optional
```

Do not add these files to the Windows bundle unless they are harmless data.

---

## 7. Build system changes (`tools/build.py`)

The build is PyInstaller-based and currently produces:

- `dist/BuzzInstaller/BuzzInstaller.zip` (onedir app)
- `dist/BuzzInstallerUpdater/BuzzInstallerUpdater.exe` (onefile)
- `dist/BuzzInstallerSetup/BuzzInstallerSetup.exe` (onefile)
- `dist/checksums.txt`

`_pyinstaller_base()` already adds `src` and the package dir to `--paths`, so
module resolution works. The Windows-specific parts are:

| Current code | Linux change |
| --- | --- |
| `--icon assets/buzzinstaller.ico` | Use `.png` (PyInstaller accepts PNG on Linux); keep `.ico` for Windows |
| `--version-file <file>` | Remove on Linux (PE-only) |
| Output names ending in `.exe` | Use `BuzzInstaller`, `BuzzInstallerUpdater`, `BuzzInstallerSetup` |
| `--windowed` | Harmless on Linux, but unnecessary; keep it for consistency |
| `--add-data f"{ASSETS}{os.pathsep}assets"` | Works; keep `os.pathsep` (`:` on Linux) |
| `_creation_flags()` returns `CREATE_NO_WINDOW` on Windows, `0` elsewhere | Already correct |
| `stop_running_app()` uses `taskkill` | Use the Linux process helper |
| `make_zip()` creates a ZIP of the onedir output | Works, but preserve the executable bit (§7.1) |
| `write_checksums()` | Works; keep SHA-256 |
| `APP_ICON`, `APP_OUT`, `APP_ZIP` constants | Make platform-aware |

### 7.1 ZIP and permissions

`zipfile` on Linux does **not** store Unix permissions unless you set
`ZipInfo.external_attr`. A zip extracted on Linux would lose `+x` on the main
binary. Either:

- set the executable bit when writing:
  ```python
  info = zipfile.ZipInfo.from_file(path, arcname)
  info.external_attr = 0o755 << 16
  archive.writestr(info, path.read_bytes())
  ```
- or ship a `tar.gz` for Linux and keep the zip for Windows.

Recommendation: product-specific Linux artifact `BuzzInstaller-linux-x86_64.tar.gz`
with a top-level directory, plus a `.desktop` file and an `install.sh`. Keep
the Windows zip untouched.

### 7.2 Where PyInstaller puts things

- onedir: `dist/BuzzInstaller/BuzzInstaller` + `_internal/` (Linux uses
  `_internal` too).
- onefile: updater/setup binaries land directly in their dist dirs.
- The `Building EXE from EXE-00.toc` messages are Windows wording; Linux logs
  say COLLECT/BUNDLE. Do not parse them.

### 7.3 Icon

Add `src/BuzzInstaller/assets/buzzinstaller.png` (256×256 or 512×512) and an
SVG if you want scalable menu icons. Keep the `.ico` for Windows. Install the
PNG under `~/.local/share/icons/hicolor/256x256/apps/buzzinstaller.png` (or use
an absolute `Icon=` path in the desktop entry) and refresh the icon cache:

```bash
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor 2>/dev/null || true
```

---

## 8. Install, update and uninstall on Linux

### 8.1 Installer (`BuzzInstallerSetup`)

Rewrite `setup_core.py` around a Linux path:

1. Choose install dir (`~/.local/share/BuzzInstaller` for option A, or
   `~/.local/opt/BuzzInstaller` + a separate data dir for option B).
2. Download `BuzzInstaller-linux-x86_64.tar.gz` (new asset name) or the zip.
3. Extract transactionally with `install_bundle` (works on Linux; make sure it
   handles directories, symlinks and permissions).
4. Write the `.desktop` entry, icon, MIME handler and optional autostart.
5. Optionally create `~/.local/bin` symlinks.
6. Launch the app.

Keep the "fresh install only" rule: do not turn setup into an updater.

### 8.2 Updater

The in-app updater is a separate executable that waits for the app PID, swaps
the install, and relaunches. On Linux:

- Waiting for a PID: poll `os.kill(pid, 0)` (catch `ProcessLookupError`);
  `/proc/<pid>` is fine too.
- Replacing the onedir build: `install_bundle` already uses staging + backup.
  Ensure it preserves the executable bit (Python `shutil` preserves mode with
  `copytree`; verify).
- Relaunch detached with `start_new_session=True`.
- The updater binary itself may live in the install dir; do not delete the
  running updater. The existing copy-to-temp logic in
  `core/updater.py` should be reused.
- Release assets: keep the same GitHub release flow, but publish the Linux
  artifact and update `buzzcommon/release.py` constants
  (`BUNDLE_ASSET_NAME`, `UPDATER_ASSET_NAME`, `SETUP_ASSET_NAME`) or add
  platform-specific names. Do not make the Windows build look for a Linux
  asset.

### 8.3 Uninstall

Remove:

- the target install dir (only if it looks like a BuzzInstaller install:
  contains the app binary or `_internal/`),
- the `.desktop` entry and icon,
- MIME registration (`xdg-mime default` reset; remove the scheme handler),
- autostart entry and/or systemd user unit,
- file-manager action files created by the port,
- `~/.local/bin` symlinks created by the port,
- metadata marker.

Never delete `~/.local/share/BuzzInstaller` wholesale if it contains user data
outside `PRESERVE_ON_UPDATE` and the user asked to keep settings. Follow the
existing Windows semantics: "Delete BuzzInstaller" removes everything; the
updater preserves user data.

### 8.4 Packaging options (pick one for the first release)

- **Tarball + `install.sh`** — simplest, matches `BuzzInstaller.zip`.
- **`.deb`** — best desktop integration; use `fpm` or `dpkg-deb`. Install to
  `/opt/buzzinstaller` + `/usr/share/applications`, data in `~/.local/share`.
- **AppImage** — one file, no root; good for "download and run". Requires
  bundling Qt plugins and testing on a clean distro.
- **Flatpak** — strongest sandbox, but the global hotkey, portal permissions
  and clipboard need explicit manifests. Defer until the native port is stable.

Do not attempt a Snap for the first port.

---

## 9. Step-by-step port order

Work in this order; each step should leave the tree importable and testable.

1. **Create the platform package skeleton** and move Windows-only modules into
   `system/windows/`. Keep Windows imports working via
   `system/platform/windows.py`.
2. **Make `src/BuzzInstaller` importable on Linux.** Replace top-level
   `winreg`/`windll` imports with lazy imports or platform dispatch. Run
   `python -c "import main"` with `QT_QPA_PLATFORM=offscreen` until it succeeds.
3. **Port paths** (`config/paths.py`, `buzzcommon/metadata.py`) to XDG.
4. **Port the config/transfer locks** (`msvcrt` → `fcntl`).
5. **Port notifications** (`utils/notifications.py`) with guarded imports.
6. **Port process discovery/termination** in `buzzcommon/install.py`.
7. **Port shell helpers** (`xdg-open`, reveal) and replace UI call sites.
8. **Implement the Linux integration manager** (desktop entry, MIME,
   autostart, file-manager actions).
9. **Port the background process** (flock single instance, detached spawn).
10. **Port the hotkey** (portal/X11) and make the UI report capabilities
    honestly.
11. **Port the tray** (Qt code is mostly fine; verify on GNOME/KDE/XFCE).
12. **Port the build** (icons, asset names, permissions) and produce a tarball.
13. **Port setup/updater** and test a real update end-to-end.
14. **Add tests** and CI (see §10).

Do not start with the hotkey: it is the hardest and least essential part.
The upload/download GUI must be usable without it.

---

## 10. Tests and CI

Add tests that run on Linux without a display:

```bash
QT_QPA_PLATFORM=offscreen python tools/dev.py test
```

New test targets:

- `test_linux_paths.py` — XDG env overrides, fallbacks, `~` expansion.
- `test_linux_desktop_entry.py` — writes/removes the `.desktop` file, correct
  `Exec` quoting, MIME lines, idempotency.
- `test_linux_single_instance.py` — two `flock` attempts; second fails while
  the first handle is open.
- `test_linux_processes.py` — find/kill a harmless child process.
- `test_linux_notifications.py` — no-op/failure path never raises.
- Existing `test_upload_result_window.py`, `test_clipboard_expiry.py` and
  `test_chunked_transfers.py` must still pass with the offscreen platform.

CI matrix:

- Ubuntu latest (X11 and Wayland sessions if you can; at least offscreen),
- Python 3.12,
- install `PySide6`, `requests`, `pyinstaller`, plus `libxcb-*` dev packages
  for the Qt platform plugin,
- run the build and a smoke launch of the onedir binary with
  `QT_QPA_PLATFORM=offscreen`.

Manual test checklist:

- [ ] Launch with no args; dashboard opens.
- [ ] Drag/drop a small file; upload completes; result window stays open.
- [ ] `--send <file>` from a terminal; same behaviour.
- [ ] `BZ://code` opens the app and downloads.
- [ ] `xdg-open "BZ://code"` reaches the app.
- [ ] Right-click "Quick Send" in at least one file manager.
- [ ] Copy link; paste into another app.
- [ ] Auto-clear timeout mode.
- [ ] Global hotkey (X11 and/or portal) reads a selected link; clipboard
      fallback works.
- [ ] Tray icon, menu and close-to-tray (if the desktop supports a tray).
- [ ] Start with Linux creates/removes the autostart entry.
- [ ] Update replaces the build and preserves config/history/transfers.
- [ ] Uninstall removes desktop integration but leaves unrelated files.

---

## 11. Known limitations to document in the app

Be explicit in Settings/About rather than silently degrading:

| Feature | Linux status |
| --- | --- |
| Global hotkey | X11: works with an X11 grab; Wayland: portal only, may require user permission |
| Selected-text capture | Windows/X11 only; Wayland cannot simulate Ctrl+C globally |
| First-paste clipboard clear | Not available without a global keyboard hook; falls back to timeout |
| Explorer menu | Replaced by per-file-manager actions; not every file manager is supported |
| Start Menu pinning | No equivalent; `.desktop` entry is used instead |
| App Paths | Not applicable; use a `~/.local/bin` symlink |
| PE version resource | Not applicable; `installed-version.txt` is authoritative |
| "Always on top" | Works via Qt window flags on both X11 and Wayland (compositor may ignore) |
| Tray | Depends on desktop; GNOME needs an extension, KDE/XFCE work |
| Auto-update | Works if the Linux artifact is published and the executable bit is preserved |

---

## 12. Definition of done

A Linux port is done when all of the following are true:

1. `python tools/dev.py test` passes on Linux (offscreen) and on Windows.
2. `python tools/dev.py build` produces a runnable Linux artifact.
3. A clean user can install, launch, upload a file, copy the link, download it,
   and uninstall, without touching a terminal.
4. No module imported on Linux references `winreg`, `msvcrt`, `windll` or
   `taskkill` at import time.
5. Desktop integration is idempotent: running register/unregister twice leaves
   no duplicates and no stale files.
6. Updates preserve `config.json`, `history.json`, `transfers.db*`, logs and
   cache.
7. The app's Settings UI accurately describes what is and is not supported on
   the current session (X11 vs Wayland, tray availability, hotkey backend).
8. `README.md` has a Linux quick-start and the release process publishes the
   Linux artifact alongside the Windows one.
