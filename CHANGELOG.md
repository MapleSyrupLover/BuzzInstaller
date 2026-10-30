# Changelog

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

