# BuzzInstaller developer tools

Everything needed to run, test, build, version and publish BuzzInstaller.
The same code powers the command line and the **Developer Studio** GUI.

## Requirements

- Python 3.12 with `PySide6` and `pyinstaller`
- Git
- GitHub CLI (`gh`) authenticated with `repo` scope: `gh auth login`

Check the setup at any time:

```cmd
python tools/dev.py status
```

## Developer Studio (GUI)

Double-click **`dev-gui.cmd`** in the repository root, or run:

```cmd
python tools/dev_gui.py
```

The window shows the current version, branch, working-tree state, last tag and
GitHub CLI status. Its **Rebuild & Publish** button runs the complete release
pipeline and streams every command's output into the log panel.

Typical one-click release:

1. Choose a version bump (patch / minor / major / keep).
2. Optionally add release notes.
3. Leave the build / commit / push / release options enabled.
4. Click **Rebuild & Publish**.

The pipeline:

1. bumps `src/VERSIONS.py`
2. prepends an entry to `CHANGELOG.md`
3. builds `dist/BuzzInstaller/BuzzInstaller.zip` and
   `dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe`
4. writes `dist/checksums.txt`
5. commits everything, creates tag `vX.Y.Z`, pushes to GitHub
6. creates the GitHub Release and uploads the assets with `gh`

Once the release is public, installed copies of BuzzInstaller detect it through
the GitHub Releases API and offer the in-app update.

> The GUI also has **Build only** (build without touching git) and **Push only**
> (commit and push source without creating a release).

## Command line

```cmd
python tools/dev.py run                  # run the dashboard from source
python tools/dev.py build                # build app bundle + installer + ZIP
python tools/dev.py build --no-installer # faster app-only build
python tools/dev.py test                 # compile + import + all test suites
python tools/dev.py bump patch           # 1.2.2 -> 1.2.3
python tools/dev.py bump --set 1.3.0     # explicit version
python tools/dev.py push -m "Fix menu"   # commit + push source only
python tools/dev.py release --bump patch --notes "New dashboard"
python tools/dev.py release --bump keep --notes "Patch release"
python tools/dev.py clean                # remove build cache
python tools/dev.py clean --all          # also remove dist/
python tools/dev.py open-dist            # open dist/ in Explorer
python tools/dev.py gui                  # open Developer Studio
python tools/dev.py status               # repository + artifact status
```

Useful release flags:

| Flag | Effect |
|------|--------|
| `--bump patch\|minor\|major\|keep` | Version bump (default `patch`) |
| `--version X.Y.Z` | Explicit version, overrides `--bump` |
| `--notes "..."` | Release notes (commit subjects are appended) |
| `--no-build` | Publish existing `dist/` artifacts |
| `--no-installer` | Skip the installer executable |
| `--no-zip` | Skip the app ZIP |
| `--no-commit` / `--no-push` | Dry-ish runs without git/release |
| `--no-release` | Build, commit and push but do not create a GitHub release |
| `--draft` / `--prerelease` | GitHub release flags |
| `--keep-running` | Do not stop a running BuzzInstaller before building |

## Artifacts

| Path | Purpose |
|------|---------|
| `dist/BuzzInstaller/BuzzInstaller/BuzzInstaller.exe` | Onedir application |
| `dist/BuzzInstaller/BuzzInstaller.zip` | Release asset used by the in-app updater |
| `dist/BuzzInstallerInstaller/BuzzInstallerInstaller.exe` | Standalone installer/updater |
| `dist/checksums.txt` | SHA-256 checksums for both release assets |

The ZIP has `BuzzInstaller.exe` and `_internal/` at its root, which is exactly
what the updater expects when it extracts over the install directory.

Both executables embed Windows version metadata (Explorer → Properties →
Details shows *File version* and *Product version*), generated from
`src/VERSIONS.py` during the build — no manual version editing anywhere.

## Tests

```cmd
python tools/dev.py test
```

Compiles `src/` and `tools/`, then runs every suite including
`tools/tests/test_build.py` (version resource generation, archive layout).

## Versioning

`src/VERSIONS.py` is the single source of truth for both the app and the
installer. Never edit version strings anywhere else. `tools/release.py` bumps
this file and refuses to publish a tag that already exists locally or on
`origin`.

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `gh CLI: NOT authenticated` | `gh auth login` and grant `repo` access |
| `Tag vX.Y.Z already exists` | Bump to a different version |
| Build fails with a file lock | Close BuzzInstaller, or leave **Stop running BuzzInstaller** checked |
| No release assets | Build first, or do not use `--no-build` |
| Update not detected | Confirm the release is public (not a draft/prerelease) and contains `BuzzInstaller.zip` |
| GUI log needs details | Click **Open log** (`build/devtools/dev_gui.log`) |
