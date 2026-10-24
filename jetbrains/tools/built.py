"""Tests that read built jars or the installed-IDE inventory skip, with the reason, until a build has produced them.

A fresh clone has neither (dist/ and build/ are not committed); `python tools/build.py` makes both.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _expected() -> list[Path]:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    plugins = json.loads((ROOT / "plugins.json").read_text(encoding="utf-8"))
    bundle = json.loads((ROOT / "bundle.json").read_text(encoding="utf-8"))
    jars = [ROOT / "dist" / f"{spec['Jar']}-{version}.jar" for spec in plugins.values()]
    return [*jars, ROOT / "dist" / f"{bundle['Jar']}-{version}.jar", ROOT / "build" / "language-evidence.json"]


MISSING = [path.relative_to(ROOT).as_posix() for path in _expected() if not path.is_file()]

requires_build = unittest.skipIf(
    bool(MISSING),
    f"needs build output ({', '.join(MISSING[:3])}{', ...' if len(MISSING) > 3 else ''}); run python tools/build.py first",
)
