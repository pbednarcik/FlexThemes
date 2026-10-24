"""Run palette-specific language and UI verification for every theme in a plugin."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from editor_contrast import check as check_editor_contrast
from generate import load_map, merge_overrides
from ide_home import parse_homes
from language_mappings import check_jar
from ui_contract import check_package


def validate_theme_catalogue(
    entries: set[str], descriptor: ET.Element, profiles: list[dict]
) -> None:
    expected = {
        (profile["providerId"], f"themes/{profile['fileName']}.theme.json")
        for profile in profiles
    }
    providers = [
        (node.attrib["id"], node.attrib["path"].lstrip("/"))
        for node in descriptor.findall("./extensions/themeProvider")
    ]
    actual_files = {
        entry
        for entry in entries
        if entry.startswith("themes/") and entry.endswith(".theme.json")
    }
    if (
        len(providers) != len(expected)
        or set(providers) != expected
        or actual_files != {entry for _, entry in expected}
    ):
        raise ValueError(
            "packaged theme providers/files do not match the complete source catalogue"
        )


def without_gutters(scheme: bytes) -> str:
    """Fingerprint every pre-existing field except the two reviewed gutter keys."""
    root = ET.fromstring(scheme)
    colors = root.find("colors")
    for node in list(colors):
        if node.get("name") in ("GUTTER_BACKGROUND", "EDITOR_GUTTER_BACKGROUND"):
            colors.remove(node)
    for node in root.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    return hashlib.sha256(ET.tostring(root)).hexdigest()


def check_gutter(scheme: bytes, expected: dict) -> None:
    root = ET.fromstring(scheme)
    for key in ("GUTTER_BACKGROUND", "EDITOR_GUTTER_BACKGROUND"):
        nodes = root.findall(f"./colors/option[@name='{key}']")
        if len(nodes) != 1 or nodes[0].get("value", "").upper() != expected[
            "gutter"
        ].lstrip("#"):
            raise ValueError(
                f"{key}: expected exactly one palette-specific gutter colour"
            )
    if without_gutters(scheme) != expected["withoutGuttersSha256"]:
        raise ValueError("Fields outside the two gutter keys changed")


def check_frozen_schemes(jar: Path, source: Path) -> int:
    baseline = json.loads(
        (source / "palette-baseline.json").read_text(encoding="utf-8")
    )
    expected = {
        f"colorSchemes/{load_map(path)['fileName']}.xml"
        for path in (source / "scheme").glob("*.scheme-map.json")
    }
    if set(baseline["schemes"]) != expected:
        raise ValueError(
            "frozen palette baselines must cover every bundled scheme, including alternatives"
        )
    if set(baseline.get("gutters", {})) != expected:
        raise ValueError("gutter baselines must cover every bundled scheme")
    with zipfile.ZipFile(jar) as archive:
        actual = {
            entry
            for entry in archive.namelist()
            if entry.startswith("colorSchemes/") and entry.endswith(".xml")
        }
        if actual != expected:
            raise ValueError(
                "packaged schemes differ from the declared scheme catalogue"
            )
        for entry, digest in baseline["schemes"].items():
            check_editor_contrast(archive.read(entry), entry)
            check_gutter(archive.read(entry), baseline["gutters"][entry])
            if hashlib.sha256(archive.read(entry)).hexdigest() != digest:
                raise ValueError(
                    f"{entry}: editor palette differs from the frozen baseline"
                )
    return len(expected)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path)
    parser.add_argument("source", type=Path)
    parser.add_argument("repo", type=Path)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("docs", type=Path)
    parser.add_argument("--home", action="append", default=[], metavar="PRODUCT=PATH")
    parser.add_argument("--overrides", type=Path)
    args = parser.parse_args()
    try:
        overrides = (
            json.loads(args.overrides.read_text(encoding="utf-8"))
            if args.overrides
            else {}
        )
        theme_paths = sorted((args.source / "theme").glob("*.theme-map.json"))
        with zipfile.ZipFile(args.jar) as archive:
            validate_theme_catalogue(
                set(archive.namelist()),
                ET.fromstring(archive.read("META-INF/plugin.xml")),
                [load_map(path) for path in theme_paths],
            )
        schemes = sorted((args.source / "scheme").glob("*.scheme-map.json"))
        for path in schemes:
            scheme = load_map(path)
            if not scheme.get("languageMappings"):
                raise ValueError(f"{path}: no shared language mapping contract")
            if args.overrides:
                merge_overrides(scheme, overrides.get("scheme", {}))
            suffix = "" if len(schemes) == 1 else "." + scheme["fileName"]
            report = (
                None if args.overrides else args.docs / f"LanguageMappings{suffix}.md"
            )
            check_jar(
                args.jar,
                scheme,
                args.repo / scheme["languageMappings"],
                args.evidence,
                report,
            )
        if args.overrides:
            print(
                "Personal build: fixed UI/editor snapshots not applied; generation contrast and language checks still apply."
            )
            return 0
        print(
            f"Frozen editor palettes OK: {check_frozen_schemes(args.jar, args.source)} schemes"
        )
        contracts = (
            [args.source / "ui-contract.json"]
            if (args.source / "ui-contract.json").exists()
            else sorted((args.source / "ui-contracts").glob("*.json"))
        )
        themes = theme_paths
        if len(contracts) != len(themes):
            raise ValueError(
                f"{args.source}: expected one approved UI contract per theme"
            )
        homes = parse_homes(args.home)
        entries = set()
        for path in contracts:
            contract = load_map(path)
            if contract["themeEntry"] in entries:
                raise ValueError(
                    "duplicate UI contract; another theme may be unchecked"
                )
            entries.add(contract["themeEntry"])
            suffix = "" if len(contracts) == 1 else "." + path.stem
            check_package(
                args.jar, path, homes, args.docs / f"UiVerification{suffix}.md"
            )
        expected = {
            f"themes/{load_map(path)['fileName']}.theme.json" for path in themes
        }
        if entries != expected:
            raise ValueError("UI contracts do not cover every generated theme")
    except (OSError, ValueError, KeyError) as error:
        print(f"PACK CHECK FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
