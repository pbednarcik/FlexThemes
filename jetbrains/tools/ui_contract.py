"""Check approved UI values, their actual colour references, and frozen syntax.

Usage: python tools/ui_contract.py <jar> <contract> --home PRODUCT=PATH [...]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from generate import contrast_ratio, flatten_ui, load_map, parse_rgb
from ide_home import parse_homes, product_info
from verify_jars import get

FOCUS_STATES = (
    (
        "Selected tab fill",
        "ui.EditorTabs.underlinedTabBackground",
        "ui.EditorTabs.inactiveUnderlinedTabBackground",
    ),
    (
        "Selected tab border",
        "ui.EditorTabs.underlinedBorderColor",
        "ui.EditorTabs.inactiveUnderlinedTabBorderColor",
    ),
    (
        "Selected tree row",
        "ui.Tree.selectionBackground",
        "ui.Tree.selectionInactiveBackground",
    ),
)
FOCUS_TEXT_PAIRS = (
    (
        "Editor tab text",
        "ui.EditorTabs.underlinedTabForeground",
        "ui.EditorTabs.underlinedTabBackground",
    ),
    (
        "Inactive editor tab text",
        "ui.EditorTabs.underlinedTabForeground",
        "ui.EditorTabs.inactiveUnderlinedTabBackground",
    ),
    (
        "Tool-window tab text",
        "ui.ToolWindow.HeaderTab.underlinedTabForeground",
        "ui.EditorTabs.underlinedTabBackground",
    ),
    (
        "Inactive tool-window tab text",
        "ui.ToolWindow.HeaderTab.underlinedTabInactiveForeground",
        "ui.EditorTabs.inactiveUnderlinedTabBackground",
    ),
    (
        "Selected tree text",
        "ui.Tree.selectionForeground",
        "ui.Tree.selectionBackground",
    ),
    (
        "Inactive tree accent text",
        "ui.Tree.selectionForeground",
        "ui.Tree.selectionInactiveBackground",
    ),
    (
        "Inactive tree default text",
        "colors.text-default",
        "ui.Tree.selectionInactiveBackground",
    ),
)


def focus_text_foreground(label: str, foreground: str, contract: dict) -> str:
    """The foreground a native focus-text pair is checked with; the guard and the report must agree.

    Native paint can use a different text route for the inactive editor tab than for
    the focused one. Record confirmed exceptions rather than assuming one route for
    light and dark appearances alike.
    """
    if label != "Inactive editor tab text":
        return foreground
    chosen = contract.get("inactiveEditorTabForeground", foreground)
    if chosen not in contract["values"]:
        raise ValueError("inactive editor-tab foreground needs an explicit approved value")
    return chosen


def focus_failures(theme: dict, contract: dict) -> list[str]:
    """Native state guards remain effective even after exact snapshots are changed."""
    failures = []
    required = {
        path for _, a, b in (*FOCUS_STATES, *FOCUS_TEXT_PAIRS) for path in (a, b)
    }
    if missing := required - contract["values"].keys():
        failures.append(
            f"native focus paths need explicit approved values: {sorted(missing)}"
        )
    for label, active, inactive in FOCUS_STATES:
        try:
            if resolve(theme, active) == resolve(theme, inactive):
                failures.append(f"native focus states collapsed: {label}")
        except ValueError as error:
            failures.append(str(error))
    for label, declared_foreground, background in FOCUS_TEXT_PAIRS:
        try:
            foreground = focus_text_foreground(label, declared_foreground, contract)
            ratio = pair_ratio(
                theme, {"a": foreground, "b": background, "label": label}
            )
            if ratio < 4.5:
                failures.append(f"native focus text: {label} {ratio:.3f}:1 below 4.5:1")
        except ValueError as error:
            failures.append(str(error))
    return failures


def resolve(theme: dict, path: str) -> str | float:
    section, key = path.split(".", 1)
    value = get(theme[section], key)
    seen = set()
    while isinstance(value, str) and not value.startswith("#"):
        if value in seen:
            raise ValueError(f"{path}: cyclic colour reference {value}")
        seen.add(value)
        if value not in theme["colors"]:
            raise ValueError(f"{path}: unresolved colour reference {value}")
        value = theme["colors"][value]
    if value is None:
        raise ValueError(f"{path}: missing UI value")
    return value.upper() if isinstance(value, str) else value


def pair_ratio(theme: dict, pair: dict) -> float:
    colours = [resolve(theme, pair[key]) for key in ("a", "b")]
    if any(not isinstance(value, str) or len(value) != 7 for value in colours):
        raise ValueError(f"{pair['label']}: needs two opaque RGB colours")
    return contrast_ratio(*(parse_rgb(value[1:]) for value in colours))


def scheme_value(root: ET.Element, path: str) -> str:
    """Read one explicit scheme colour, rejecting missing or duplicate fields."""
    section, key = path.removeprefix("scheme.").split(".", 1)
    if section == "colors":
        options = root.findall(f"./colors/option[@name='{key}']")
    elif section == "attributes":
        name, field = key.rsplit(".", 1)
        attributes = root.findall(f"./attributes/option[@name='{name}']")
        if len(attributes) != 1:
            raise ValueError(f"{path}: expected exactly one packaged scheme attribute")
        options = attributes[0].findall(f"./value/option[@name='{field}']")
    else:
        raise ValueError(f"{path}: unknown scheme section")
    if len(options) != 1:
        raise ValueError(f"{path}: expected exactly one packaged scheme colour")
    value = options[0].get("value", "")
    if not value or len(value) > 6:
        raise ValueError(f"{path}: needs one explicit opaque RGB colour")
    return f"#{int(value, 16):06X}"


def scheme_expected(contract: dict) -> dict[str, str]:
    return {
        **{
            f"scheme.colors.{key}": value
            for key, value in contract.get("schemeColors", {}).items()
        },
        **{
            f"scheme.attributes.{key}.{field}": value
            for key, fields in contract.get("schemeAttributes", {}).items()
            for field, value in fields.items()
        },
    }


def scheme_pair_ratio(theme: dict, root: ET.Element, pair: dict) -> float:
    colours = {
        key: scheme_value(root, pair[key])
        if pair[key].startswith("scheme.")
        else resolve(theme, pair[key])
        for key in ("a", "b")
    }
    return pair_ratio({"colors": colours}, {**pair, "a": "colors.a", "b": "colors.b"})


def validate(theme: dict, scheme: bytes, contract: dict) -> list[str]:
    failures = []
    if contract.get("nativeFocus"):
        failures += focus_failures(theme, contract)
    for path, expected in contract["values"].items():
        try:
            actual = resolve(theme, path)
            if actual != expected:
                failures.append(f"{path}: expected {expected}, found {actual}")
        except ValueError as error:
            failures.append(str(error))
    for group in contract.get("equalGroups", []):
        try:
            if len({resolve(theme, path) for path in group["paths"]}) != 1:
                failures.append(
                    f"UI coherence: {group['label']} must use the same colour"
                )
        except ValueError as error:
            failures.append(str(error))
    for pair in (*contract["pairs"], *contract.get("additionalPairs", [])):
        try:
            actual = pair_ratio(theme, pair)
            if actual < pair["minimum"]:
                failures.append(
                    f"{pair['label']}: {actual:.3f}:1 below {pair['minimum']}:1"
                )
        except ValueError as error:
            failures.append(str(error))
    if contract.get("treeGuides"):
        try:
            guide = resolve(theme, "ui.Tree.hash")
            if guide != resolve(theme, "colors.tree-indent-guide-border"):
                failures.append("native tree guide route and colour token disagree")
            ratio = pair_ratio(
                theme,
                {
                    "a": "ui.Tree.hash",
                    "b": "colors.tool-window-bg",
                    "label": "Tree guides",
                },
            )
            if ratio < 2.2:
                failures.append(
                    "native tree guide contrast is below the 2.2 design floor"
                )
        except ValueError as error:
            failures.append(str(error))
    if hashlib.sha256(scheme).hexdigest() != contract["schemeSha256"]:
        failures.append("editor scheme changed from the approved pre-UI baseline")
    if scheme_expected(contract) or contract.get("schemePairs"):
        try:
            root = ET.fromstring(scheme)
            for path, expected in scheme_expected(contract).items():
                try:
                    actual = scheme_value(root, path)
                    if actual != expected.upper():
                        failures.append(f"{path}: expected {expected}, found {actual}")
                except ValueError as error:
                    failures.append(str(error))
            for pair in contract.get("schemePairs", []):
                try:
                    ratio = scheme_pair_ratio(theme, root, pair)
                    if ratio < pair["minimum"]:
                        failures.append(
                            f"{pair['label']}: {ratio:.3f}:1 below {pair['minimum']}:1"
                        )
                except ValueError as error:
                    failures.append(str(error))
        except ET.ParseError as error:
            failures.append(f"invalid editor scheme XML: {error}")
    return failures


def installed_keys(contract: dict, homes: dict[str, Path]) -> dict:
    """Verify routes against each product's shipped theme/metadata catalogue."""
    products = {}
    required = {
        key.removeprefix("ui.") for key in contract["values"] if key.startswith("ui.")
    }
    for code in ("RD", "IU", "GO", "PY"):
        if code not in homes:
            raise ValueError(f"missing installation for {code}")
        info = product_info(homes[code])
        if info["productCode"] != code:
            raise ValueError(f"expected {code}, found {info['productCode']}")
        keys = set()
        with zipfile.ZipFile(
            homes[code] / "lib/intellij.platform.ide.impl.jar"
        ) as archive:
            for entry in archive.namelist():
                if entry.endswith(".theme.json"):
                    keys |= flatten_ui(json.loads(archive.read(entry)).get("ui", {}))
                elif entry.endswith(".themeMetadata.json"):
                    keys |= {
                        item["key"]
                        for item in json.loads(archive.read(entry)).get("ui", [])
                    }
        if missing := required - keys:
            raise ValueError(
                f"{code}: UI keys absent from installed catalogue: {sorted(missing)}"
            )
        products[code] = {
            "name": info["name"],
            "version": info["version"],
            "build": info["buildNumber"],
        }
    return products


def check_package(
    jar: Path, contract_path: Path, homes: dict[str, Path], report: Path | None = None
) -> None:
    contract = load_map(contract_path)
    products = installed_keys(
        contract,
        homes,
    )
    with zipfile.ZipFile(jar) as archive:
        theme = json.loads(archive.read(contract["themeEntry"]))
        scheme = archive.read(contract["schemeEntry"])
    if failures := validate(theme, scheme, contract):
        raise ValueError("; ".join(failures))
    if report:
        lines = [
            f"# {theme['name']} UI verification",
            "",
            f"Artifact: `{jar.name}`; SHA-256 `{hashlib.sha256(jar.read_bytes()).hexdigest()}`.",
            "",
            contract.get(
                "design",
                "Palette-specific clarity rules derived from Periwinkle Night.",
            ),
            "",
            "## UI routes verified in installed products",
            "",
            "| Product | Version | Build |",
            "|---|---|---|",
        ]
        for product in products.values():
            lines.append(
                f"| {product['name']} | {product['version']} | {product['build']} |"
            )
        lines += [
            "",
            "## Packaged values after resolving colour references",
            "",
            "| Key | Value |",
            "|---|---|",
        ]
        lines += [
            f"| `{key}` | `{resolve(theme, key)}` |" for key in contract["values"]
        ]
        if scheme_expected(contract):
            scheme_root = ET.fromstring(scheme)
            lines += [
                "",
                "## Packaged editor colours",
                "",
                "| Key | Value |",
                "|---|---|",
            ]
            for path in scheme_expected(contract):
                lines.append(f"| `{path}` | `{scheme_value(scheme_root, path)}` |")
        lines += [
            "",
            "## Separation and text guards",
            "",
            "| Pair | Ratio | Floor |",
            "|---|---|---|",
        ]
        lines += [
            f"| {pair['label']} | {pair_ratio(theme, pair):.3f}:1 | {pair['minimum']}:1 |"
            for pair in (*contract["pairs"], *contract.get("additionalPairs", []))
        ]
        if contract.get("equalGroups"):
            lines += ["", "## Shared hierarchy colours", ""]
            lines += [
                f"- {group['label']}: "
                + ", ".join(f"`{path}`" for path in group["paths"])
                + "."
                for group in contract["equalGroups"]
            ]
        if contract.get("schemePairs"):
            scheme_root = ET.fromstring(scheme)
            lines += [
                "",
                "## Documentation and breadcrumb text guards",
                "",
                "| Pair | Ratio | Floor |",
                "|---|---|---|",
            ]
            lines += [
                f"| {pair['label']} | {scheme_pair_ratio(theme, scheme_root, pair):.3f}:1 | {pair['minimum']}:1 |"
                for pair in contract["schemePairs"]
            ]
        if contract.get("nativeFocus"):
            lines += [
                "",
                "## Native focus guards",
                "",
                "Focused/inactive tab fills, tab borders and tree selections must remain distinct. Native editor and tool-window tab labels use each theme's own foreground; no custom drawing code is bundled.",
                "",
                "| Text pair | Ratio | Floor |",
                "|---|---|---|",
            ]
            for label, declared_foreground, background in FOCUS_TEXT_PAIRS:
                foreground = focus_text_foreground(label, declared_foreground, contract)
                ratio = pair_ratio(
                    theme, {"a": foreground, "b": background, "label": label}
                )
                lines.append(f"| {label} | {ratio:.3f}:1 | 4.5:1 |")
        lines += [
            "",
            "Surface separation floors are design regression guards, not WCAG accessibility thresholds.",
            "Text guards use 4.5:1; the normal generator also checks declared text/icon pairs and APCA limits.",
            "",
            f"Editor scheme SHA-256: `{contract['schemeSha256']}` ({contract['schemeBaseline']}).",
            "",
            "These checks read shipped key catalogues and the packaged files. They do not launch IDEs or prove rendered pixels.",
            "",
        ]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(
        f"UI contract OK: {len(contract['values'])} values, {len(contract['pairs']) + len(contract.get('additionalPairs', []))} UI pair guards, {len(products)} IDE catalogues; {len(scheme_expected(contract))} explicit scheme colours, {len(contract.get('schemePairs', []))} scheme pair guards; editor scheme matches frozen baseline"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("jar", type=Path)
    parser.add_argument("contract", type=Path)
    parser.add_argument("--home", action="append", default=[], metavar="PRODUCT=PATH")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        check_package(
            args.jar,
            args.contract,
            parse_homes(args.home),
            args.report,
        )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        print(f"UI CHECK FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
