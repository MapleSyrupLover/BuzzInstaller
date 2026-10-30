#!/usr/bin/env python
"""
BuzzInstaller developer CLI.

Examples
--------
    python tools/dev.py run                 # run the app from source
    python tools/dev.py build               # build app bundle + installer
    python tools/dev.py test                # compile + import + unit tests
    python tools/dev.py bump patch          # 1.2.0 -> 1.2.1
    python tools/dev.py push -m "Fix menu"  # commit + push
    python tools/dev.py release --bump patch --notes "New dashboard"
    python tools/dev.py gui                 # open the Developer Studio
    python tools/dev.py status
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

import build  # noqa: E402
import release  # noqa: E402

APP_MAIN = ROOT / "src" / "BuzzInstaller" / "main.py"


def _python() -> str:
    return sys.executable


def _print_header(title: str) -> None:
    print()
    print(title)
    print("=" * len(title))


def cmd_status(_args) -> int:
    app_version, installer_version = release.read_versions()
    print(f"Repository : {ROOT}")
    print(f"App version: {app_version}")
    print(f"Installer  : {installer_version}")
    print(f"Branch     : {release.git_branch()}")
    print(f"Dirty      : {'yes' if release.git_is_dirty() else 'no'}")
    print(f"Last tag   : {release.git_last_tag() or '(none)'}")
    print(f"gh CLI     : {'authenticated' if release.gh_authenticated() else 'NOT authenticated'}")
    print("Artifacts  :")
    artifacts = build.artifact_paths()
    if not artifacts:
        print("  (none — run a build)")
    for artifact in artifacts:
        size = artifact.stat().st_size
        print(f"  {artifact.relative_to(ROOT)}  ({size / (1024 * 1024):.1f} MB)")
    return 0


def cmd_run(args) -> int:
    command = [_python(), str(APP_MAIN), *args.extra]
    _print_header("Running BuzzInstaller from source")
    return subprocess.call(command, cwd=str(ROOT / "src" / "BuzzInstaller"))


def cmd_build(args) -> int:
    _print_header("Building BuzzInstaller")
    try:
        result = build.build_all(
            log=print,
            build_application=True,
            build_updater=not args.no_installer,
            make_archive=not args.no_zip,
            kill_running=not args.keep_running,
        )
    except build.BuildError as exc:
        print(f"\nBUILD FAILED\n{exc}", file=sys.stderr)
        return 1
    _print_header("Build artifacts")
    artifacts = result.get("artifacts") or []
    if not artifacts:
        print("(nothing was produced)")
    for artifact in artifacts:
        print(artifact)
    return 0


def cmd_clean(args) -> int:
    build.clean(log=print, remove_dist=args.all)
    print("Build cache cleaned." + (" dist/ removed." if args.all else ""))
    return 0


def cmd_test(_args) -> int:
    _print_header("Compiling sources")
    code = subprocess.call(
        [_python(), "-m", "compileall", "-q", str(ROOT / "src"), str(ROOT / "tools")]
    )
    if code != 0:
        return code

    targets = [
        (ROOT / "src" / "BuzzInstaller", "test_imports.py"),
        (ROOT / "src" / "BuzzInstaller" / "tests", None),
        (ROOT / "src" / "BuzzInstallerInstaller" / "tests", None),
        (ROOT / "tools" / "tests", None),
    ]
    failures = 0
    for directory, single in targets:
        if single:
            scripts = [directory / single]
        else:
            scripts = sorted(directory.glob("test_*.py"))
        for script in scripts:
            _print_header(f"Running {script.relative_to(ROOT)}")
            code = subprocess.call(
                [_python(), str(script)],
                cwd=str(script.parent),
                env={**os.environ, "PYTHONPATH": str(script.parent)},
            )
            if code != 0:
                failures += 1
                print(f"FAILED: {script.relative_to(ROOT)} (exit {code})")
    if failures:
        print(f"\n{failures} test file(s) failed.", file=sys.stderr)
        return 1
    print("\nAll tests passed.")
    return 0


def cmd_bump(args) -> int:
    current, _ = release.read_versions()
    if args.set:
        new_version = args.set.lstrip("vV")
    else:
        new_version = release.increment_version(current, args.part)
    release.set_versions(new_version, log=print)
    print(f"Bumped {current} -> {new_version}")
    return 0


def cmd_push(args) -> int:
    try:
        release.push_changes(message=args.message or "Update source", log=print)
    except release.ReleaseError as exc:
        print(f"Push failed: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_release(args) -> int:
    try:
        result = release.publish(
            bump=args.bump,
            explicit_version=args.version,
            notes=args.notes or "",
            do_build=not args.no_build,
            build_installer=not args.no_installer,
            make_zip=not args.no_zip,
            do_commit=not args.no_commit,
            do_push=not args.no_push,
            do_release=not args.no_release,
            draft=args.draft,
            prerelease=args.prerelease,
            kill_running=not args.keep_running,
            log=print,
        )
    except (release.ReleaseError, build.BuildError) as exc:
        print(f"\nRELEASE FAILED\n{exc}", file=sys.stderr)
        return 1
    _print_header("Release complete")
    print(f"Version: {result['version']}")
    if result.get("release_url"):
        print(f"Release: {result['release_url']}")
    return 0


def cmd_open_dist(_args) -> int:
    target = build.DIST if build.DIST.exists() else ROOT
    os.startfile(str(target))  # type: ignore[attr-defined]
    return 0


def cmd_gui(_args) -> int:
    gui = TOOLS_DIR / "dev_gui.py"
    return subprocess.call([_python(), str(gui)], cwd=str(ROOT))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dev.py",
        description="BuzzInstaller development & release tooling.",
    )
    sub = parser.add_subparsers(dest="command")

    status = sub.add_parser("status", help="Show repository and build status")
    status.set_defaults(func=cmd_status)

    run = sub.add_parser("run", help="Run BuzzInstaller from source")
    run.add_argument("extra", nargs=argparse.REMAINDER, help="Arguments for the app")
    run.set_defaults(func=cmd_run)

    build_cmd = sub.add_parser("build", help="Build the app and installer")
    build_cmd.add_argument("--no-installer", action="store_true", help="Skip the installer")
    build_cmd.add_argument("--no-zip", action="store_true", help="Skip the app ZIP")
    build_cmd.add_argument("--keep-running", action="store_true",
                           help="Do not stop a running BuzzInstaller")
    build_cmd.set_defaults(func=cmd_build)

    clean = sub.add_parser("clean", help="Remove build caches")
    clean.add_argument("--all", action="store_true", help="Also remove dist/")
    clean.set_defaults(func=cmd_clean)

    test = sub.add_parser("test", help="Compile and run all tests")
    test.set_defaults(func=cmd_test)

    bump = sub.add_parser("bump", help="Bump the version in src/VERSIONS.py")
    bump.add_argument("part", nargs="?", choices=["patch", "minor", "major"],
                      default="patch")
    bump.add_argument("--set", help="Set an explicit version instead")
    bump.set_defaults(func=cmd_bump)

    push = sub.add_parser("push", help="Commit all changes and push to GitHub")
    push.add_argument("-m", "--message", help="Commit message")
    push.set_defaults(func=cmd_push)

    rel = sub.add_parser("release", help="Build, commit, tag, push and publish a release")
    rel.add_argument("--bump", choices=["patch", "minor", "major", "keep"],
                     default="patch", help="Version bump (default: patch)")
    rel.add_argument("--version", help="Explicit new version")
    rel.add_argument("--notes", help="Release notes")
    rel.add_argument("--no-build", action="store_true", help="Use existing dist/ artifacts")
    rel.add_argument("--no-installer", action="store_true", help="Do not build the installer")
    rel.add_argument("--no-zip", action="store_true", help="Do not create the app ZIP")
    rel.add_argument("--no-commit", action="store_true", help="Do not commit or tag")
    rel.add_argument("--no-push", action="store_true", help="Do not push")
    rel.add_argument("--no-release", action="store_true", help="Do not create a GitHub release")
    rel.add_argument("--draft", action="store_true", help="Create the release as a draft")
    rel.add_argument("--prerelease", action="store_true", help="Mark the release as a prerelease")
    rel.add_argument("--keep-running", action="store_true",
                     help="Do not stop a running BuzzInstaller before building")
    rel.set_defaults(func=cmd_release)

    gui = sub.add_parser("gui", help="Open the Developer Studio GUI")
    gui.set_defaults(func=cmd_gui)

    opend = sub.add_parser("open-dist", help="Open the dist folder in Explorer")
    opend.set_defaults(func=cmd_open_dist)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
