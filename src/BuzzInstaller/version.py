"""
Single authoritative version source for BuzzInstaller.

All version references (``--version`` output, package metadata) must
import ``APP_VERSION`` from this module.  The actual value lives in
``src/VERSIONS.py`` — the single place to bump versions.
"""

import os
import sys

# Ensure the parent (src/) is on sys.path so VERSIONS.py is importable.
_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_DIR)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from VERSIONS import BUZZINSTALLER_VERSION as APP_VERSION  # noqa: E402
