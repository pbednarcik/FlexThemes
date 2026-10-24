"""Verify and apply shared semantic colour roles using installed JetBrains declarations.

No IDE is launched or modified. `inventory` reads product-info.json and the declared
JAR entries; javap inspects key registrations without executing plugin code.
`check` inspects the packaged scheme, not the source maps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from pathlib import Path

from ide_home import parse_homes, product_info


class MappingError(ValueError):
    pass


def read_contract(path: Path) -> dict:
    contract = json.loads(path.read_text(encoding="utf-8"))
    attribute_roles(contract)
    return contract


def attribute_roles(contract: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for language, group in contract["languages"].items():
        seen: set[str] = set()
        for role, keys in group["roles"].items():
            for key in keys:
                if key in seen or (key in result and result[key] != role):
                    raise MappingError(
                        f"conflicting or duplicate role for {language}/{key}"
                    )
                seen.add(key)
                result[key] = role
        ignored = [key for keys in group.get("preserve", {}).values() for key in keys]
        if len(ignored) != len(set(ignored)) or seen.intersection(ignored):
            raise MappingError(f"mapped and preserved keys overlap in {language}")
    if order := contract.get("keyOrder"):
        if len(order) != len(set(order)) or set(order) != set(result):
            raise MappingError(
                "language key order must cover each mapped key exactly once"
            )
        return {key: result[key] for key in order}
    return result


def registrations(disassembly: str) -> set[str]:
    """Only literal arguments at actual TextAttributesKey factory calls count.

    A string elsewhere in the constant pool is not evidence of a registered key.
    Reject unsupported declaration shapes instead of silently guessing their keys.
    """
    factory = r"com/intellij/openapi/editor/colors/TextAttributesKey\.createTextAttributesKey:"
    # The shipped declarations use a string literal and optionally one fallback
    # field, immediately followed by the factory. Fail closed on other shapes.
    pattern = (
        r"\d+: ldc(?:_w)?\s+#\d+\s+// String (\S+)\s*\n"
        r"(?:\s*\d+: getstatic\s+#\d+\s+// Field [^\n]+\n|"
        r"\s*\d+: aconst_null\s*\n\s*\d+: checkcast\s+#\d+\s+// class com/intellij/openapi/editor/colors/TextAttributesKey\s*\n)?"
        r"\s*\d+: invokestatic\s+#\d+\s+// Method " + factory
    )
    matches = re.findall(pattern, disassembly)
    if not matches or len(matches) != len(re.findall(factory, disassembly)):
        raise MappingError(
            "unsupported key registration shape; review the declaration parser"
        )
    return set(matches)


def validate_coverage(language: str, group: dict, declared: set[str]) -> None:
    mapped = {key for keys in group["roles"].values() for key in keys}
    preserved = {key for keys in group.get("preserve", {}).values() for key in keys}
    missing = (mapped | preserved) - declared
    added = declared - mapped - preserved if group.get("complete", True) else set()
    if missing or added:
        raise MappingError(
            f"{language}: declaration drift; missing={sorted(missing)}, unreviewed={sorted(added)}"
        )


def collect_inventory(contract_path: Path, homes: dict[str, Path], javap: str) -> dict:
    contract = read_contract(contract_path)
    products = {}
    for code in {
        source["product"]
        for group in contract["languages"].values()
        for source in group["sources"]
    }:
        if code not in homes:
            raise MappingError(
                f"missing IDE home for {code}; supply --home {code}=<installation>"
            )
        info = product_info(homes[code])
        if info["productCode"] != code:
            raise MappingError(
                f"expected {code}, found {info['productCode']} at {homes[code]}"
            )
        products[code] = {
            "name": info["name"],
            "version": info["version"],
            "build": info["buildNumber"],
        }
    evidence = {
        "contractSha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
        "products": products,
        "languages": {},
    }
    for language, group in contract["languages"].items():
        declarations = []
        for source in group["sources"]:
            jar = homes[source["product"]] / source["jar"]
            entry = (
                source.get("resource") or source["class"].replace(".", "/") + ".class"
            )
            with zipfile.ZipFile(jar) as archive:
                content = archive.read(entry)
            if "class" in source:
                run = subprocess.run(
                    [javap, "-c", "-p", "-classpath", str(jar), source["class"]],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                try:
                    declared = registrations(run.stdout)
                except MappingError as error:
                    raise MappingError(
                        f"{language}/{source['class']}: {error}"
                    ) from error
            else:
                declared = {
                    node.attrib["name"]
                    for node in ET.fromstring(content).findall("./attributes/option")
                }
            validate_coverage(language, group, declared)
            declarations.append(
                {
                    **source,
                    "entrySha256": hashlib.sha256(content).hexdigest(),
                    "keys": sorted(declared),
                }
            )
        evidence["languages"][language] = declarations
    return evidence


def verified_roles(
    contract_path: Path, evidence_path: Path
) -> tuple[dict, dict[str, str]]:
    contract = read_contract(contract_path)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    if (
        evidence["contractSha256"]
        != hashlib.sha256(contract_path.read_bytes()).hexdigest()
    ):
        raise MappingError("language evidence is stale; rerun inventory")
    for language, group in contract["languages"].items():
        declarations = evidence["languages"].get(language, [])
        if len(declarations) != len(group["sources"]):
            raise MappingError(f"{language}: missing declaration evidence")
        for source, declaration in zip(group["sources"], declarations, strict=True):
            if any(declaration.get(key) != value for key, value in source.items()):
                raise MappingError(
                    f"{language}: evidence comes from a different declaration"
                )
            validate_coverage(language, group, set(declaration["keys"]))
    return evidence, attribute_roles(contract)


def fields(root: ET.Element, key: str) -> dict[str, str]:
    return {
        node.attrib["name"]: node.attrib["value"]
        for node in root.findall(f"./attributes/option[@name='{key}']/value/option")
    }


def expand_roles(
    scheme_map: dict,
    base: ET.Element,
    contract_path: Path,
    evidence_path: Path,
    resolve_colour: Callable[[str], str] | None = None,
) -> set[str]:
    _, targets = verified_roles(contract_path, evidence_path)
    anchors = scheme_map["semanticRoles"]
    colours = {}
    for role in set(targets.values()):
        if role not in anchors:
            raise MappingError(f"no palette anchor for semantic role {role}")
        anchor = anchors[role]
        mapped_colour = scheme_map["attributes"].get(anchor, {}).get("FOREGROUND")
        colour = mapped_colour or fields(base, anchor).get("FOREGROUND")
        if not colour:
            raise MappingError(
                f"palette anchor {anchor} for {role} has no explicit foreground"
            )
        expression = colour if mapped_colour else "#" + colour.zfill(6)
        colours[role] = resolve_colour(expression) if resolve_colour else expression
    for key, role in targets.items():
        # Palette anchors are authoritative. Other language defaults are the values
        # this opt-in profile intentionally replaces, including inherited map entries.
        existing = scheme_map["attributes"].get(key, {}).get("FOREGROUND")
        if (
            key in anchors.values()
            and existing
            and (resolve_colour(existing) if resolve_colour else existing).upper()
            != colours[role].upper()
        ):
            raise MappingError(
                f"{key}: existing foreground conflicts with semantic role {role}"
            )
        scheme_map["attributes"][key] = {
            **scheme_map["attributes"].get(key, {}),
            "FOREGROUND": colours[role],
            "element": f"{role}: shared language role, palette anchor {anchors[role]}",
            "status": scheme_map["attributes"].get(key, {}).get("status", "inherited"),
        }
        anchor = scheme_map["attributes"].get(anchors[role], {})
        if anchor.get("contrastWaiver"):
            scheme_map["attributes"][key].setdefault(
                "contrastWaiver", anchor["contrastWaiver"]
            )
    return set(targets)


def check_scheme(
    root: ET.Element, contract: dict, anchors: dict[str, str]
) -> list[str]:
    failures = []
    for key, role in attribute_roles(contract).items():
        expected = fields(root, anchors[role]).get("FOREGROUND")
        actual = fields(root, key).get("FOREGROUND")
        if expected is None or actual is None or int(actual, 16) != int(expected, 16):
            failures.append(f"{key}: {role} expected {expected}, found {actual}")
    return failures


def check_jar(
    jar: Path,
    scheme_map: dict,
    contract_path: Path,
    evidence_path: Path,
    report: Path | None,
) -> None:
    evidence, _ = verified_roles(contract_path, evidence_path)
    contract = read_contract(contract_path)
    scheme_entry = f"colorSchemes/{scheme_map['fileName']}.xml"
    with zipfile.ZipFile(jar) as archive:
        root = ET.fromstring(archive.read(scheme_entry))
        descriptor = ET.fromstring(archive.read("META-INF/plugin.xml"))
        theme_nodes = descriptor.findall("./extensions/themeProvider")
        themes = [
            json.loads(archive.read(node.attrib["path"].lstrip("/")))
            for node in theme_nodes
        ]
    bundled = descriptor.find(
        f"./extensions/bundledColorScheme[@id='{root.attrib['name']}']"
    )
    bundled_correctly = (
        bundled is not None
        and bundled.attrib.get("path", "").lstrip("/") + ".xml" == scheme_entry
    )
    if not bundled_correctly:
        raise MappingError("checked editor scheme is not bundled at its declared path")
    # A family may bundle several schemes, including alternatives that are not a
    # theme's default. Every actual theme must still link to a bundled scheme.
    bundled_nodes = descriptor.findall("./extensions/bundledColorScheme")
    valid_schemes = {node.attrib["id"] for node in bundled_nodes}
    valid_schemes |= {node.attrib["path"] + ".xml" for node in bundled_nodes}
    if not themes or any(
        theme.get("editorScheme") not in valid_schemes for theme in themes
    ):
        raise MappingError("theme is not linked to a bundled editor scheme")
    if [node.text for node in descriptor.findall("depends")] != [
        "com.intellij.modules.platform"
    ]:
        raise MappingError("pilot descriptor must depend only on the shared platform")
    since = tuple(
        int(part)
        for part in descriptor.find("idea-version").attrib["since-build"].split(".")
    )
    for code, product in evidence["products"].items():
        build = tuple(int(part) for part in product["build"].split("."))
        if build < since:
            raise MappingError(
                f"{code}: build {product['build']} is older than the plugin's since-build"
            )
    for role, anchor in scheme_map["semanticRoles"].items():
        expected = scheme_map["attributes"].get(anchor, {}).get("FOREGROUND")
        actual = fields(root, anchor).get("FOREGROUND")
        if (
            expected
            and expected.startswith("#")
            and (actual is None or int(actual, 16) != int(expected[1:], 16))
        ):
            raise MappingError(
                f"{role}: packaged palette anchor {anchor} differs from the source palette"
            )
    failures = check_scheme(root, contract, scheme_map["semanticRoles"])
    if failures:
        raise MappingError("packaged language mappings failed: " + "; ".join(failures))
    if report:
        lines = [
            f"# {scheme_map['name']} language mappings",
            "",
            "Generated by `tools/language_mappings.py check` from the packaged JAR and installed declarations.",
            "",
            f"Artifact: `{jar.name}`; SHA-256 `{hashlib.sha256(jar.read_bytes()).hexdigest()}`.",
            "",
            "## Verified installations",
            "",
            "| Product | Version | Build |",
            "|---|---|---|",
        ]
        for product in evidence["products"].values():
            lines.append(
                f"| {product['name']} | {product['version']} | {product['build']} |"
            )
        lines += [
            "",
            "## Semantic roles",
            "",
            "| Role | Palette anchor | Colour |",
            "|---|---|---|",
        ]
        for role, anchor in scheme_map["semanticRoles"].items():
            lines.append(
                f"| {role} | `{anchor}` | `#{fields(root, anchor)['FOREGROUND'].upper()}` |"
            )
        for language, group in contract["languages"].items():
            lines += [
                "",
                f"## {language}",
                "",
                "| Key | Role | Colour |",
                "|---|---|---|",
            ]
            for role, keys in group["roles"].items():
                for key in keys:
                    lines.append(
                        f"| `{key}` | {role} | `#{fields(root, key)['FOREGROUND'].upper()}` |"
                    )
            for reason, keys in group.get("preserve", {}).items():
                lines += [
                    "",
                    f"Preserved ({reason}): "
                    + ", ".join(f"`{key}`" for key in keys)
                    + ".",
                ]
            for source in evidence["languages"][language]:
                entry = source.get("class") or source["resource"]
                lines += [
                    "",
                    f"Evidence: {source['product']}, `{source['jar']}`, `{entry}`; entry SHA-256 `{source['entrySha256']}`.",
                ]
        lines += [
            "",
            "## Scope and limits",
            "",
            "This checks registered key names, complete declaration coverage (except the wider Rider base scheme),",
            "packaged foregrounds, palette anchors and the theme-to-scheme link. The normal build checks contrast.",
            "It does not launch IDEs, execute their semantic annotators, prove screenshot pixels or replace visual QA.",
            "Java, Kotlin, Go and Python expose a shared keyword key here; Rider retains this theme's separate control-flow role.",
            "The inspected Python highlighter has no dedicated instance-field key; generic member defaults",
            "are mapped, but jade on every Python attribute reference is not established by this check.",
            "Effect-only overlays (mutability, smart casts, diagnostics) are preserved, not recoloured as syntax.",
            "Semantic/rainbow highlighting or user overrides can change runtime colours.",
            "",
            "See docs/Development.md (Checks) for how the mappings are built and checked.",
            "",
        ]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(
        f"Language mappings OK: {len(attribute_roles(contract))} keys, {len(contract['languages'])} groups; packaged {jar.name}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    inventory = sub.add_parser("inventory")
    inventory.add_argument("contract", type=Path)
    inventory.add_argument("out", type=Path)
    inventory.add_argument(
        "--home", action="append", default=[], metavar="PRODUCT=PATH"
    )
    inventory.add_argument("--javap", default="javap")
    check = sub.add_parser("check")
    check.add_argument("jar", type=Path)
    check.add_argument("scheme_map", type=Path)
    check.add_argument("contract", type=Path)
    check.add_argument("evidence", type=Path)
    check.add_argument("--report", type=Path)
    check.add_argument("--overrides", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "inventory":
            homes = parse_homes(args.home)
            evidence = collect_inventory(args.contract, homes, args.javap)
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(
                json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
            )
            print(
                "Installed declarations verified: " + ", ".join(evidence["languages"])
            )
        else:
            from generate import load_map, merge_overrides

            scheme_map = load_map(args.scheme_map)
            if args.overrides:
                overrides = json.loads(args.overrides.read_text(encoding="utf-8"))
                merge_overrides(scheme_map, overrides.get("scheme", {}))
            check_jar(
                args.jar,
                scheme_map,
                args.contract,
                args.evidence,
                args.report,
            )
    except (
        MappingError,
        OSError,
        KeyError,
        ValueError,
        subprocess.CalledProcessError,
        zipfile.BadZipFile,
        ET.ParseError,
    ) as error:
        print(f"LANGUAGE CHECK FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
