"""Check the newest built jar of every plugin for every shared fix, reading the built files, not the maps.

Usage: python verify_jars.py <dist dir>
A fix that belongs in every theme gets a check here; a MISS means a theme did not receive it.
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

PLUGINS = [spec["Jar"] for spec in json.loads((Path(__file__).resolve().parents[1] / "plugins.json").read_text(encoding="utf-8")).values()]
JETBRAINS_GRASS = {"#CCF0DAFF", "#2D522EFF"}  # Islands Light, Islands Dark


def get(node: dict, dotted: str) -> object:
    """Read a dotted UI key from the nested theme structure."""
    if dotted in node:
        return node[dotted]
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:cut])
        if head in node and isinstance(node[head], dict):
            return get(node[head], ".".join(parts[cut:]))
    return None


def version_key(text: str) -> tuple | None:
    """Sort key for "1.2.3" or "1.2.3-rc1" (a pre-release sorts below its release); None if not a version."""
    number, _, suffix = text.partition("-")
    parts = number.split(".")
    if not all(part.isdigit() for part in parts):
        return None
    return tuple(int(part) for part in parts), suffix == "", suffix


def newest_jars(dist: Path) -> list[str]:
    names = []
    for plugin in PLUGINS:
        versions = {
            p.name: key
            for p in dist.glob(f"{plugin}-*.jar")
            if not p.stem.endswith("-personal") and (key := version_key(p.stem.removeprefix(plugin + "-"))) is not None
        }
        if not versions:
            raise SystemExit(f"no jar for {plugin} in {dist}")
        names.append(max(versions, key=versions.__getitem__))
    return names


def main() -> int:
    dist = Path(sys.argv[1])
    failures = 0
    for jar in newest_jars(dist):
        with zipfile.ZipFile(dist / jar) as archive:
            for name in archive.namelist():
                if name.endswith("/"):
                    continue
                if name.startswith("themes/"):
                    theme = json.loads(archive.read(name))
                    ui, colours = theme["ui"], theme["colors"]
                    checks = {
                        "tab names full strength": get(ui, "EditorTabs.unselectedAlpha") == 1.0,
                        "milder unfocused fade": get(ui, "Island.inactiveAlpha") == 0.8,
                        "line-number gap": get(ui, "Editor.Gutter.gapAfterVcsMarkersWidth") == 12,
                        "line-number gap, compact": get(ui, "Editor.Gutter.gapAfterVcsMarkersWidth.compact") == 12,
                        "project glow in palette": colours.get("grad-g9-grass-a1", "#CCF0DAFF").upper() not in JETBRAINS_GRASS,
                        "check box in accent": theme.get("dark") or theme.get("icons", {}).get("ColorPalette", {}).get("Checkbox.Background.Selected") == colours.get("accent-brand-bg"),
                    }
                    for label, ok in checks.items():
                        failures += 0 if ok else 1
                        print(f"{'ok  ' if ok else 'MISS'} {jar:32} {name.split('/')[-1]:32} {label}")
                if name.startswith("colorSchemes/"):
                    root = ET.fromstring(archive.read(name))
                    inlay = root.find("./attributes/option[@name='INLAY_DEFAULT']/value/option[@name='FOREGROUND']")
                    ok = inlay is not None and inlay.get("value").upper() not in {"7A7A7A", "787878"}
                    failures += 0 if ok else 1
                    print(f"{'ok  ' if ok else 'MISS'} {jar:32} {name.split('/')[-1]:32} inlay hints set ({None if inlay is None else inlay.get('value')})")
                    # Rider draws C# methods in the generic method keys too, so they must match the C# method colour.
                    methods = {
                        key: (found.get("value").upper() if (found := root.find(f"./attributes/option[@name='{key}']/value/option[@name='FOREGROUND']")) is not None else None)
                        for key in ("ReSharper.CSHARP_METHOD_IDENTIFIER", "DEFAULT_INSTANCE_METHOD", "DEFAULT_STATIC_METHOD")
                    }
                    ok = None not in methods.values() and len(set(methods.values())) == 1
                    failures += 0 if ok else 1
                    print(f"{'ok  ' if ok else 'MISS'} {jar:32} {name.split('/')[-1]:32} generic method keys match C# methods ({'/'.join(map(str, methods.values()))})")
    print(f"{failures} missing")
    bundle_spec = Path(__file__).resolve().parents[1] / "bundle.json"
    if bundle_spec.exists():
        from bundle import check, config, release_name

        repo = bundle_spec.parent
        if (dist / release_name(repo, config(repo))).exists():
            try:
                check(repo)
            except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
                print(f"MISS bundle: {error}")
                failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
