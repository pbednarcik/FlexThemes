"""Locate installed JetBrains IDEs on Windows, macOS and Linux.

A home is the folder that holds the IDE's `lib/` and `plugins/`: the installation folder on Windows and Linux,
`<Name>.app/Contents` on macOS (an `.app` path is accepted and resolved). product-info.json sits in that folder,
or in its `Resources/` subfolder on macOS.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

PRODUCTS = ("RD", "IU", "GO", "PY")
# Installed folder or application names to try, first match wins.
NAMES = {
    "RD": ("Rider",),
    "IU": ("IntelliJ IDEA Ultimate", "IntelliJ IDEA"),
    "GO": ("GoLand",),
    "PY": ("PyCharm", "PyCharm Professional Edition"),
}


def resolve(home: Path) -> Path:
    """The folder holding lib/ and plugins/, given an install folder, an .app bundle or its Contents."""
    home = Path(home)
    if home.suffix == ".app" and (home / "Contents").is_dir():
        return home / "Contents"
    return home


def product_info_path(home: Path) -> Path:
    home = resolve(home)
    for candidate in (home / "product-info.json", home / "Resources" / "product-info.json"):
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no JetBrains IDE at {home}: product-info.json not found")


def product_info(home: Path) -> dict:
    return json.loads(product_info_path(home).read_text(encoding="utf-8"))


def candidates(code: str, platform: str = sys.platform, environ=os.environ) -> list[Path]:
    if platform == "win32":
        programs = Path(environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "Programs"
        return [programs / name for name in NAMES[code]]
    if platform == "darwin":
        roots = (Path("/Applications"), Path.home() / "Applications")
        return [root / f"{name}.app" for name in NAMES[code] for root in roots]
    return []


def default_home(code: str, platform: str = sys.platform, environ=os.environ) -> Path | None:
    """The first installed candidate, else the first candidate (so the error names a path), else None."""
    options = candidates(code, platform, environ)
    for option in options:
        try:
            product_info_path(option)
            return resolve(option)
        except FileNotFoundError:
            continue
    return options[0] if options else None


def parse_homes(values: list[str]) -> dict[str, Path]:
    """--home PRODUCT=PATH arguments, resolved."""
    homes = {}
    for value in values:
        code, separator, path = value.partition("=")
        if not separator or not path:
            raise ValueError(f"expected --home PRODUCT=PATH, got {value!r}")
        homes[code] = resolve(Path(path))
    return homes
