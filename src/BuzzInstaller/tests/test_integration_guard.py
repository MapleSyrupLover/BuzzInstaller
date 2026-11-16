"""
Regression tests for Windows shell-integration registration.

A source checkout has no ``BuzzInstaller.exe``: ``sys.executable`` is
``python.exe``.  Registering from source used to point the Explorer Quick Send
menu and the ``BZ://`` protocol at Python (wrong icon, and the command did
nothing because the script path was missing).

These tests assert that only a frozen (installed) build may write shell
integration, and that an installed build still registers its own executable.
"""

from __future__ import annotations

import os
import sys
import traceback
from unittest.mock import patch

_PKG = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _PKG not in sys.path:
    sys.path.insert(0, _PKG)

import system.context_menu as cm  # noqa: E402

EXE = r"C:\Users\tester\AppData\Local\BuzzInstaller\BuzzInstaller.exe"


class _FakeRegistry:
    """Record every write so tests can assert nothing was touched."""

    def __init__(self) -> None:
        self.values: list[tuple] = []
        self.keys: list[tuple] = []
        self.deleted: list[tuple] = []

    def set_value(self, root, subkey, name, value) -> None:
        self.values.append((root, subkey, name, value))

    def create_key(self, root, subkey) -> None:
        self.keys.append((root, subkey))

    def delete_key_tree(self, root, subkey) -> None:
        self.deleted.append((root, subkey))

    def delete_value(self, root, subkey, name) -> None:
        self.deleted.append((root, subkey, name))

    def get_value(self, root, subkey, name):
        return None

    def key_exists(self, root, subkey) -> bool:
        return False


def _manager():
    with patch.object(cm, "_get_exe", return_value=EXE):
        return cm.WindowsIntegrationManager(reg=_FakeRegistry())


def test_source_registration_is_refused() -> None:
    mgr = _manager()
    calls = [
        mgr.register_all,
        mgr.register_protocol,
        mgr.register_context_menu,
        mgr.register_start_with_windows,
        mgr.register_background_at_login,
    ]
    with patch.object(cm, "is_frozen", return_value=False):
        for call in calls:
            try:
                call()
            except cm.RegistryError as exc:
                assert "installed" in str(exc).lower(), str(exc)
            else:
                raise AssertionError(f"{call.__name__} did not refuse a source run")
    assert mgr.reg.values == [], mgr.reg.values
    assert mgr.reg.keys == [], mgr.reg.keys


def test_installed_registration_uses_its_own_exe() -> None:
    mgr = _manager()
    with patch.object(cm, "is_frozen", return_value=True), \
         patch.object(mgr, "_notify_assoc_changed"), \
         patch.object(mgr, "_verify_protocol", return_value=[]):
        mgr.register_all()

    commands = [
        value for _root, subkey, name, value in mgr.reg.values
        if name == "" and subkey.endswith("\\command")
    ]
    assert commands, "no command values were written"
    for command in commands:
        assert EXE in command, command
        assert "python.exe" not in command.lower(), command

    icons = [
        value for _root, _subkey, name, value in mgr.reg.values
        if name == "Icon"
    ]
    assert icons, "no context-menu icon was written"
    for icon in icons:
        assert icon.startswith(EXE), icon


def main_() -> int:
    tests = [
        test_source_registration_is_refused,
        test_installed_registration_uses_its_own_exe,
    ]
    failed = 0
    for test in tests:
        try:
            test()
            print(f"PASS: {test.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL: {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main_())
