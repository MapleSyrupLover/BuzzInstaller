# Changelog

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

