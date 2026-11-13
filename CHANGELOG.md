# Changelog

## v1.5.1 — 2026-11-13

Polish release for the redesigned interface.

- The interface style dropdown now calls the balanced preset **Default** (same look as before).
- The window opens compact and only grows when **Settings** or **Customization** is opened.
- Uploads, Downloads and About now scroll when the window is short instead of clipping.
- About links and folder buttons use a neat two-column layout; long paths are shortened with a full-path tooltip.
- Mobile: the style chip also reads **Default**.

### Mobile APKs

Android builds are attached to this release:

- `BuzzInstallerMobile-1.0.0.apk` — universal, install this if unsure.
- `BuzzInstallerMobile-1.0.0-arm64-v8a.apk` — smaller build for current phones.
- `BuzzInstallerMobile-1.0.0-armeabi-v7a.apk` — older 32-bit devices.
- `BuzzInstallerMobile-1.0.0-x86_64.apk` — emulators and x86 tablets.

### Commits

- Start compact, grow for Settings/Customization, and label the default style Default

## v1.5.0 — 2026-11-13

A redesigned interface, upload-time chunk planning, safer updates and a new
Android companion app.

- **New interface** — five focused pages (Uploads, Downloads, Settings, Customization, About) with a flat Discord-style look: light/dark/system theme, three density presets, accent colour, corner radius and text size.
- **Chunking is now an upload option** — automatic, ask every time, 2/4/8-part presets or a custom part count or size, with each part's size previewed before you start.
- **Safer updates** — the updater removes the previous installation and keeps only your configuration and transfer history, so relaunching never shows an old version.
- **Clipboard lifetime** — the BZ:// link copied after an upload can be kept until something else is copied, cleared after a delay, or cleared after the first paste.
- **BuzzInstaller Mobile (Android)** — a Flutter companion app in `mobile/` with the same BuzzHeavier links, chunk planning, downloads that rejoin parts, themes and `BZ://` link handling.

### Mobile APKs

Android builds are attached to this release:

- `BuzzInstallerMobile-1.0.0.apk` — universal, install this if unsure.
- `BuzzInstallerMobile-1.0.0-arm64-v8a.apk` — smaller build for current phones.
- `BuzzInstallerMobile-1.0.0-armeabi-v7a.apk` — older 32-bit devices.
- `BuzzInstallerMobile-1.0.0-x86_64.apk` — emulators and x86 tablets.

### Commits

- Keep file extensions in mobile chunk names and copy links without blocking
- Add BuzzInstaller Mobile, a Flutter Android companion app
- Redesign the interface with Discord-style themes and simpler navigation
- Add configurable upload chunk plans and chunk-choice dialog
- Fix updater to fully replace the installation and preserve user data
- Add configurable clipboard lifetime for auto-copied links

## v1.4.2 — 2026-11-07

Update Test!

### Commits

- Stop tracking private developer tooling

## v1.4.1 — 2026-11-02

BuzzInstaller 1.4.1 - smoother startup and a single background process.

- Fixed the dashboard startup flash: card, stat and page labels are now parented before being shown, so opening the app no longer briefly creates many tiny native windows.
- Fixed duplicate background hotkey processes: the Win32 single-instance mutex now detects ERROR_ALREADY_EXISTS correctly, so only one background service runs.

## v1.4.0 — 2026-11-02

BuzzInstaller 1.4.0 - resumable chunked transfers for large files and folders.

- Large files and folders are split into independently verifiable chunks (10 GiB by default) and uploaded as ordinary BuzzHeavier files, so a single `BZ://` link still works exactly as before.
- New versioned `BZB` JSON manifest describes the original name, size, SHA-256 and every chunk; receivers detect it automatically and fall back to the unchanged legacy downloader for normal links.
- Transfer state is persisted in SQLite (`transfers.db`), so interrupted uploads and downloads resume after a pause, crash or restart without re-sending verified chunks.
- Chunk uploads stream byte ranges directly from the original file; no duplicate local chunk files are created and the source is never modified.
- SHA-256 verification at every step: planned chunks, downloaded chunks, the reassembled file, and the source when resuming an upload.
- Folder transfers are packaged as ZIP64 archives in STORE mode, chunked like any other file, then extracted with the existing hardened extractor (zip-slip protection, no silent overwrites).
- Send/Receive pages show filename, total size, chunk counts, current chunk, per-chunk status, speed, ETA, and Pause/Resume/Cancel controls. An Interrupted Transfers panel lists resumable transfers from previous sessions.
- Settings gained a Resumable transfers card: enable/disable, chunk size, upload/download concurrency, retry limit, optional BuzzHeavier account/parent IDs, and keep-archive option.
- Cross-process transfer locks and duplicate-transfer prevention; atomic database updates and safe cleanup of incomplete temporary files.
- Retries use exponential backoff with a configurable limit, and stale `RUNNING` transfers are recovered to `INTERRUPTED` on startup.

## v1.3.3 — 2026-10-30

Test For Auto Updating System.

## v1.3.2 — 2026-10-30

BuzzInstaller 1.3.2 - update checks are visible in the installed app.

- --check-updates now opens the in-app update dialog (release notes, download, install) in the installed windowed build instead of writing to a console that does not exist.
- Console/source runs still print the result and keep their exit codes.
- dev.py status shows small artifacts such as checksums.txt in KB instead of 0.0 MB.

## v1.3.1 — 2026-10-30

BuzzInstaller 1.3.1 - version metadata and publishing polish.

- Both executables now embed Windows file/product version metadata, so Explorer shows the correct version.
- Build tooling reports exactly which artifacts it produced instead of stale files from earlier builds.
- Version comparison no longer freezes the running version at import time, keeping update checks testable.
- Added build-helper tests (version resource generation, archive layout) to the developer test suite.

### Commits

- Let is_newer fall back to the running version at call time

## v1.3.0 — 2026-10-30

BuzzInstaller 1.3.0 - a complete dashboard redesign.

- New sidebar dashboard: Overview, Send, Receive, History, Settings and About.
- Searchable transfer history with re-copy and open-folder actions.
- Drag and drop anywhere, keyboard shortcuts, theme switching and close-to-tray.
- In-app auto-updater: checks GitHub Releases, shows notes, downloads and installs updates.
- Update helper preserves config, history, logs, cache and downloads.
- New Developer Studio (dev-gui.cmd) and tools/dev.py for one-click build, version bump, push and release.
- Refreshed light/dark themes, reusable cards, toasts and themed checkboxes.

