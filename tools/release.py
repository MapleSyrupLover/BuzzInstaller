"""
Release automation for BuzzInstaller.

One call to :func:`publish` performs the full release pipeline:

1. bump the version in ``src/VERSIONS.py``
2. prepend a ``CHANGELOG.md`` entry
3. build the app bundle + installer (optional)
4. commit every change, tag ``vX.Y.Z`` and push to GitHub
5. create the GitHub Release and upload the assets with ``gh``

The same functions back both ``tools/dev.py release`` and the Developer
Studio GUI's *Rebuild & Publish* button.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Optional

from build import (
    APP_ZIP, CHECKSUMS, INSTALLER_EXE, ROOT, SRC,
    artifact_paths, build_all, BuildError, run_command,
)

VERSIONS_FILE = SRC / "VERSIONS.py"
CHANGELOG_FILE = ROOT / "CHANGELOG.md"

LogFn = Callable[[str], None]

TAG_PREFIX = "v"
REPO_SLUG = "MapleSyrupLover/BuzzInstaller"


class ReleaseError(RuntimeError):
    """Raised when a release step fails."""


def _noop(_: str) -> None:
    pass


def _run(
    args: list[str],
    log: LogFn = _noop,
    check: bool = True,
    cwd: Path = ROOT,
    echo: bool = False,
) -> subprocess.CompletedProcess:
    if echo:
        log("$ " + " ".join(args))
    result = subprocess.run(
        args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if echo and result.stdout.strip():
        for line in result.stdout.strip().splitlines():
            log("  " + line)
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "").strip()
        raise ReleaseError(
            f"Command failed ({result.returncode}): {' '.join(args)}\n{message}"
        )
    return result


# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------
_VERSION_RE = re.compile(
    r'^(BUZZINSTALLER_VERSION\s*=\s*")([^"]+)(")$', re.MULTILINE
)
_INSTALLER_RE = re.compile(
    r'^(BUZZINSTALLER_INSTALLER_VERSION\s*=\s*")([^"]+)(")$', re.MULTILINE
)


def read_versions() -> tuple[str, str]:
    text = VERSIONS_FILE.read_text(encoding="utf-8")
    app_match = _VERSION_RE.search(text)
    installer_match = _INSTALLER_RE.search(text)
    if not app_match or not installer_match:
        raise ReleaseError(f"Could not parse versions from {VERSIONS_FILE}")
    return app_match.group(2), installer_match.group(2)


def parse_version(text: str) -> tuple[int, int, int]:
    numbers = re.findall(r"\d+", text)
    if not numbers:
        raise ReleaseError(f"Invalid version: {text!r}")
    parts = [int(number) for number in numbers[:3]]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)  # type: ignore[return-value]


def increment_version(version: str, part: str) -> str:
    major, minor, patch = parse_version(version)
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ReleaseError(f"Unknown bump part: {part!r}")


def set_versions(version: str, log: LogFn = _noop) -> None:
    version = version.lstrip("vV").strip()
    parse_version(version)  # validate
    text = VERSIONS_FILE.read_text(encoding="utf-8")
    text = _VERSION_RE.sub(rf'\g<1>{version}\g<3>', text)
    text = _INSTALLER_RE.sub(rf'\g<1>{version}\g<3>', text)
    VERSIONS_FILE.write_text(text, encoding="utf-8")
    log(f"Version set to {version} in {VERSIONS_FILE.relative_to(ROOT)}")


# ---------------------------------------------------------------------------
# Git helpers
# ---------------------------------------------------------------------------
def git_branch() -> str:
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    return result.stdout.strip() or "HEAD"


def git_is_dirty() -> bool:
    result = _run(["git", "status", "--porcelain"])
    return bool(result.stdout.strip())


def git_last_tag() -> Optional[str]:
    result = _run(["git", "describe", "--tags", "--abbrev=0"], check=False)
    tag = result.stdout.strip()
    return tag or None


def git_commits_since(tag: Optional[str]) -> list[str]:
    range_spec = f"{tag}..HEAD" if tag else "HEAD"
    result = _run(
        ["git", "log", range_spec, "--pretty=format:%s", "--no-merges"],
        check=False,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_tag_exists(tag: str) -> bool:
    result = _run(["git", "rev-parse", "--verify", f"refs/tags/{tag}"], check=False)
    if result.returncode == 0:
        return True
    # The tag may exist on the remote without being fetched locally.
    remote = _run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        check=False,
    )
    return remote.returncode == 0 and bool(remote.stdout.strip())


def git_add_all(log: LogFn = _noop) -> None:
    _run(["git", "add", "-A"], echo=False)
    log("Staged all changes.")


def git_commit(message: str, log: LogFn = _noop) -> str:
    result = _run(["git", "commit", "-m", message], echo=False)
    log(result.stdout.strip())
    return result.stdout.strip()


def git_create_tag(tag: str, message: str, log: LogFn = _noop) -> None:
    _run(["git", "tag", "-a", tag, "-m", message])
    log(f"Created tag {tag}.")


def git_push(tag: Optional[str] = None, log: LogFn = _noop) -> None:
    branch = git_branch()
    log(f"Pushing branch {branch}…")
    _run(["git", "push", "origin", "HEAD"], echo=True, log=log)
    if tag:
        log(f"Pushing tag {tag}…")
        _run(["git", "push", "origin", tag], echo=True, log=log)


# ---------------------------------------------------------------------------
# GitHub CLI
# ---------------------------------------------------------------------------
def gh_available() -> bool:
    result = _run(["gh", "--version"], check=False)
    return result.returncode == 0


def gh_authenticated() -> bool:
    if not gh_available():
        return False
    result = _run(["gh", "auth", "status"], check=False)
    return result.returncode == 0


def gh_release_create(
    tag: str,
    version: str,
    notes_text: str,
    assets: list[Path],
    draft: bool = False,
    prerelease: bool = False,
    log: LogFn = _noop,
) -> str:
    if not assets:
        raise ReleaseError("No release assets found — build first.")

    handle, notes_path = tempfile.mkstemp(
        prefix=f"buzz-release-{version}-", suffix=".md"
    )
    os.close(handle)
    Path(notes_path).write_text(notes_text, encoding="utf-8")

    try:
        args = [
            "gh", "release", "create", tag,
            *[str(asset) for asset in assets],
            "--title", f"BuzzInstaller {tag}",
            "--notes-file", notes_path,
            "--verify-tag",
        ]
        if draft:
            args.append("--draft")
        if prerelease:
            args.append("--prerelease")
        log("Creating GitHub release…")
        result = _run(args, echo=False)
        output = (result.stdout or "").strip()
        url = next(
            (line.strip() for line in output.splitlines() if line.strip().startswith("http")),
            "",
        )
        if not url:
            url = f"https://github.com/{REPO_SLUG}/releases/tag/{tag}"
        log(f"Release published: {url}")
        return url
    finally:
        try:
            os.unlink(notes_path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Changelog
# ---------------------------------------------------------------------------
def update_changelog(version: str, sections: str, log: LogFn = _noop) -> None:
    header = "# Changelog\n\n"
    entry = f"## v{version} — {time.strftime('%Y-%m-%d')}\n\n{sections.strip()}\n\n"
    existing = ""
    if CHANGELOG_FILE.is_file():
        existing = CHANGELOG_FILE.read_text(encoding="utf-8")
    if existing.startswith(header):
        body = existing[len(header):]
    else:
        body = existing
    CHANGELOG_FILE.write_text(header + entry + body.lstrip(), encoding="utf-8")
    log(f"Updated {CHANGELOG_FILE.relative_to(ROOT)}")


def build_notes(version: str, custom_notes: str, commits: list[str]) -> str:
    bullets = "\n".join(f"- {line}" for line in commits if line)
    if custom_notes.strip():
        body = custom_notes.strip()
        if bullets:
            body += "\n\n### Commits\n\n" + bullets
    else:
        body = bullets or "- Maintenance release."
    return body


# ---------------------------------------------------------------------------
# Pipelines
# ---------------------------------------------------------------------------
def push_changes(message: str = "", log: LogFn = _noop) -> dict:
    """Commit everything and push the current branch (no tag/release)."""
    if not git_is_dirty():
        log("Working tree is clean — nothing to commit.")
    else:
        git_add_all(log)
        git_commit(message or "Update source", log)
    branch = git_branch()
    _run(["git", "push", "origin", "HEAD"], echo=True, log=log)
    log(f"Pushed {branch} to origin.")
    return {"branch": branch}


def publish(
    bump: str = "patch",
    explicit_version: Optional[str] = None,
    notes: str = "",
    do_build: bool = True,
    build_installer: bool = True,
    make_zip: bool = True,
    do_commit: bool = True,
    do_push: bool = True,
    do_release: bool = True,
    draft: bool = False,
    prerelease: bool = False,
    kill_running: bool = False,
    log: LogFn = _noop,
) -> dict:
    """Run the full release pipeline and return a result summary."""
    if not (ROOT / ".git").exists():
        raise ReleaseError("This folder is not a git repository.")

    current_version, _ = read_versions()
    if explicit_version:
        version = explicit_version.lstrip("vV").strip()
    elif bump == "keep":
        version = current_version
    else:
        version = increment_version(current_version, bump)

    tag = f"{TAG_PREFIX}{version}"
    if git_tag_exists(tag):
        raise ReleaseError(f"Tag {tag} already exists. Bump the version first.")

    if do_release and not do_push:
        raise ReleaseError("Creating a GitHub release requires pushing the tag.")
    if do_release and not do_commit:
        raise ReleaseError("Creating a GitHub release requires committing and tagging.")
    if do_release and not gh_authenticated():
        raise ReleaseError(
            "GitHub CLI is not authenticated. Run 'gh auth login' first."
        )

    last_tag = git_last_tag()
    commits = git_commits_since(last_tag)
    log(f"Releasing v{version} (previous tag: {last_tag or 'none'})")
    log(f"Commits since last release: {len(commits)}")

    original_versions = VERSIONS_FILE.read_text(encoding="utf-8")
    original_changelog = (
        CHANGELOG_FILE.read_text(encoding="utf-8")
        if CHANGELOG_FILE.is_file() else None
    )

    set_versions(version, log)
    note_body = build_notes(version, notes, commits)
    update_changelog(version, note_body, log)

    artifacts: list[Path] = []
    if do_build:
        log("Starting build…")
        try:
            build_result = build_all(
                log=log,
                build_application=True,
                build_updater=build_installer,
                make_archive=make_zip,
                kill_running=kill_running,
            )
        except BuildError as exc:
            # A failed build must not leave a half-bumped version behind.
            VERSIONS_FILE.write_text(original_versions, encoding="utf-8")
            if original_changelog is None:
                CHANGELOG_FILE.unlink(missing_ok=True)
            else:
                CHANGELOG_FILE.write_text(original_changelog, encoding="utf-8")
            raise ReleaseError(
                f"Build failed. Version and changelog were restored.\n{exc}"
            ) from exc
        artifacts = list(build_result.get("artifacts") or [])
        for artifact in artifacts:
            log(f"Artifact: {artifact.relative_to(ROOT)}")
    else:
        artifacts = artifact_paths()

    if do_commit:
        git_add_all(log)
        commit_message = f"Release v{version}"
        if notes.strip():
            first_line = notes.strip().splitlines()[0]
            commit_message += f" — {first_line}"
        git_commit(commit_message, log)
        git_create_tag(tag, f"BuzzInstaller {tag}", log)

    if do_push:
        git_push(tag if do_commit else None, log)

    release_url = ""
    if do_release:
        if not do_build and not artifacts:
            raise ReleaseError(
                "No build artifacts found in dist/. Run a build before releasing."
            )
        release_url = gh_release_create(
            tag=tag,
            version=version,
            notes_text=f"# BuzzInstaller {tag}\n\n{note_body}\n",
            assets=artifacts,
            draft=draft,
            prerelease=prerelease,
            log=log,
        )

    log(f"Done. BuzzInstaller v{version} released.")
    return {
        "version": version,
        "tag": tag,
        "release_url": release_url,
        "artifacts": [str(path) for path in artifacts],
        "previous_tag": last_tag,
    }
