"""Stage and verify one theme-only plugin; plan conservative release JAR cleanup."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

from check_pack import check_frozen_schemes, validate_theme_catalogue
from generate import load_map
from language_mappings import check_scheme, read_contract
from report_paths import reports_root
from ui_contract import validate


def config(repo: Path) -> dict:
    return json.loads((repo / "bundle.json").read_text(encoding="utf-8"))


def release_name(repo: Path, spec: dict) -> str:
    if spec["Version"] != "VERSION":
        raise ValueError("All release packages must use the shared root VERSION")
    version = (repo / spec["Version"]).read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"invalid release version: {version}")
    return f"{spec['Jar']}-{version}.jar"


def collect(repo: Path) -> dict[str, bytes]:
    """Recheck current standalone inputs before composing unchanged resources."""
    catalogue = json.loads((repo / "plugins.json").read_text(encoding="utf-8"))
    spec = config(repo)
    payload: dict[str, bytes] = {}
    members = []
    extensions = []
    registrations = set()
    identities = set()
    compatibility = None
    for key, plugin in catalogue.items():
        source = repo / plugin["Source"]
        jar = repo / "dist" / release_name(repo, plugin)
        check_frozen_schemes(jar, source)
        with zipfile.ZipFile(jar) as archive:
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError(f"duplicate ZIP entries in {jar.name}")
            descriptor = ET.fromstring(archive.read("META-INF/plugin.xml"))
            version = (repo / plugin["Version"]).read_text(encoding="utf-8").strip()
            identity = descriptor.findtext("id")
            if not identity or identity in identities:
                raise ValueError("missing or duplicate standalone plugin identity")
            identities.add(identity)
            if descriptor.findtext("version") != version:
                raise ValueError(f"{jar.name}: stale standalone descriptor version")
            bounds = descriptor.find("idea-version").attrib
            if compatibility is not None and bounds != compatibility:
                raise ValueError(
                    "standalone IDE compatibility ranges differ; review before bundling"
                )
            compatibility = bounds
            if [node.text for node in descriptor.findall("depends")] != [
                "com.intellij.modules.platform"
            ]:
                raise ValueError(
                    "bundle inputs must depend only on the common platform"
                )
            profiles = [
                load_map(path)
                for path in sorted((source / "theme").glob("*.theme-map.json"))
            ]
            validate_theme_catalogue(set(archive.namelist()), descriptor, profiles)
            scheme_entries = set()
            for path in sorted((source / "scheme").glob("*.scheme-map.json")):
                profile = load_map(path)
                entry = f"colorSchemes/{profile['fileName']}.xml"
                scheme_entries.add(entry)
                failures = check_scheme(
                    ET.fromstring(archive.read(entry)),
                    read_contract(repo / profile["languageMappings"]),
                    profile["semanticRoles"],
                )
                if failures:
                    raise ValueError(f"{jar.name}: {failures}")
            contracts = (
                [source / "ui-contract.json"]
                if (source / "ui-contract.json").exists()
                else sorted((source / "ui-contracts").glob("*.json"))
            )
            if {load_map(path)["themeEntry"] for path in contracts} != {
                f"themes/{profile['fileName']}.theme.json" for profile in profiles
            }:
                raise ValueError("bundle inputs need UI contracts for every appearance")
            for path in contracts:
                contract = load_map(path)
                failures = validate(
                    json.loads(archive.read(contract["themeEntry"])),
                    archive.read(contract["schemeEntry"]),
                    contract,
                )
                if failures:
                    raise ValueError(f"{jar.name}: {failures}")
            registered_schemes = set()
            for node in descriptor.find("extensions"):
                registration = (node.tag, node.get("id"))
                if (
                    node.tag not in {"themeProvider", "bundledColorScheme"}
                    or registration in registrations
                ):
                    raise ValueError(
                        "bundle inputs contain unsupported or duplicate registrations"
                    )
                registrations.add(registration)
                extensions.append(ET.tostring(node, encoding="unicode").strip())
                if node.tag == "bundledColorScheme":
                    registered_schemes.add(node.get("path", "").lstrip("/") + ".xml")
            if registered_schemes != scheme_entries:
                raise ValueError(
                    "bundle scheme registrations must cover every scheme, including alternatives"
                )
            for entry in archive.namelist():
                if entry.endswith("/"):
                    continue
                if entry.startswith(("themes/", "colorSchemes/")):
                    if entry in payload:
                        raise ValueError(f"resource collision: {entry}")
                    payload[entry] = archive.read(entry)
                elif entry in {"META-INF/LICENSE", "META-INF/NOTICE"}:
                    data = archive.read(entry)
                    if entry in payload and payload[entry] != data:
                        raise ValueError(f"inconsistent licence resource: {entry}")
                    payload[entry] = data
                elif entry not in {
                    "META-INF/MANIFEST.MF",
                    "META-INF/plugin.xml",
                    "META-INF/pluginIcon.svg",
                }:
                    raise ValueError(f"unexpected input resource: {entry}")
            members.append(
                {
                    "family": key,
                    "pluginId": identity,
                    "version": version,
                    "jar": jar.name,
                    "sha256": hashlib.sha256(jar.read_bytes()).hexdigest(),
                }
            )
    for name in ("LICENSE", "NOTICE"):
        if payload.get("META-INF/" + name) != (repo / name).read_bytes():
            raise ValueError(f"bundle must preserve the repository {name}")
    descriptor = (repo / spec["Source"] / "META-INF/plugin.xml").read_text(
        encoding="utf-8"
    )
    descriptor = descriptor.replace(
        "@VERSION@", (repo / spec["Version"]).read_text(encoding="utf-8").strip()
    )
    descriptor = descriptor.replace(
        "@COMPATIBILITY@",
        " ".join(
            f"{key}={quoteattr(value)}" for key, value in sorted(compatibility.items())
        ),
    )
    descriptor = descriptor.replace(
        "@EXTENSIONS@", "\n".join("    " + node for node in extensions)
    )
    descriptor = descriptor.replace(
        "@INCOMPATIBLE@",
        "\n".join(
            f"  <incompatible-with>{identity}</incompatible-with>"
            for identity in sorted(identities)
        ),
    )
    root = ET.fromstring(descriptor)
    if root.findtext("id") in identities or re.search(r"@[A-Z]+@", descriptor):
        raise ValueError("invalid bundle descriptor identity or unresolved template")
    payload["META-INF/plugin.xml"] = descriptor.encode("utf-8")
    payload["META-INF/pluginIcon.svg"] = (
        repo / spec["Source"] / "META-INF/pluginIcon.svg"
    ).read_bytes()
    payload["META-INF/theme-bundle.json"] = (
        json.dumps({"members": members}, indent=2) + "\n"
    ).encode()
    return payload


def validate_bundle(actual: dict[str, bytes], expected: dict[str, bytes]) -> None:
    # The JDK supplies MANIFEST.MF. Every other byte is accounted for by the verified inputs.
    actual = {
        key: value for key, value in actual.items() if key != "META-INF/MANIFEST.MF"
    }
    if set(actual) != set(expected):
        raise ValueError(
            f"bundle resource catalogue differs: {sorted(set(actual) ^ set(expected))}"
        )
    for entry, data in expected.items():
        if actual[entry] != data:
            raise ValueError(f"bundle resource differs from verified inputs: {entry}")


def check(repo: Path, report: bool = False) -> None:
    jar = repo / "dist" / release_name(repo, config(repo))
    with zipfile.ZipFile(jar) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())):
            raise ValueError("duplicate ZIP entries in bundle")
        actual = {
            name: archive.read(name)
            for name in archive.namelist()
            if not name.endswith("/")
        }
    validate_bundle(actual, collect(repo))
    themes = sorted(
        json.loads(data)["name"]
        for name, data in actual.items()
        if name.startswith("themes/")
    )
    schemes = sorted(
        ET.fromstring(data).get("name")
        for name, data in actual.items()
        if name.startswith("colorSchemes/")
    )
    if report:
        members = json.loads(actual["META-INF/theme-bundle.json"])["members"]
        lines = [
            "# Bundle verification",
            "",
            f"Artifact: `{jar.name}`.",
            "",
            f"SHA-256: `{hashlib.sha256(jar.read_bytes()).hexdigest()}`.",
            "",
            f"Verified **{len(themes)} appearances and {len(schemes)} editor schemes**.",
            "",
            "Every theme and scheme matches its current standalone package byte for byte. Input packages pass their frozen editor baselines, language roles and UI contracts. The standard build also checks the installed declarations and UI catalogues in all four IDEs.",
            "",
            "## Appearances",
            "",
            *[f"- {name}" for name in themes],
            "",
            "## Editor schemes",
            "",
            *[f"- {name}" for name in schemes],
            "",
            "## Included releases",
            "",
            "| Family | Version | Source SHA-256 |",
            "|---|---|---|",
            *[f"| {m['family']} | {m['version']} | `{m['sha256']}` |" for m in members],
            "",
            "The bundle contains only theme resources and one descriptor. It declares the standalone plugin IDs incompatible to avoid duplicate theme registrations; uninstall them before installing the bundle. No installed IDE or plugin is modified by the build.",
            "",
            "These are packaged-file and declaration checks, not a visual smoke test in running IDEs.",
            "",
        ]
        reports_root(repo).mkdir(parents=True, exist_ok=True)
        (reports_root(repo) / "BundleVerification.md").write_text(
            "\n".join(lines), encoding="utf-8", newline="\n"
        )
    print(
        f"Bundle OK: {len(themes)} appearances, {len(schemes)} editor schemes; all resources match verified inputs"
    )


def obsolete_jars(dist: Path, releases: dict[str, str]) -> list[str]:
    """Plan file-only cleanup; ignore unknown and personal JARs, reject newer versions."""
    outdated = []
    for prefix, current in releases.items():
        if not (dist / current).is_file():
            raise ValueError(f"cannot prune without current release: {current}")
        pattern = re.compile(re.escape(prefix) + r"-(\d+)\.(\d+)\.(\d+)\.jar")
        current_match = pattern.fullmatch(current)
        if current_match is None:
            raise ValueError(f"invalid current release name: {current}")
        version = tuple(map(int, current_match.groups()))
        for candidate in dist.iterdir():
            match = pattern.fullmatch(candidate.name)
            if match is None or not candidate.is_file():
                continue
            if candidate.is_symlink():
                raise ValueError(f"refusing release symlink: {candidate.name}")
            candidate_version = tuple(map(int, match.groups()))
            if candidate_version > version:
                raise ValueError(
                    f"newer release requires review before cleanup: {candidate.name}"
                )
            if candidate_version < version:
                outdated.append(candidate.name)
    return sorted(outdated)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("stage", "check", "obsolete"))
    parser.add_argument("repo", type=Path)
    args = parser.parse_args()
    repo = args.repo.resolve()
    try:
        if args.command == "stage":
            payload = collect(repo)
            output = (repo / config(repo)["Output"]).resolve()
            if not output.is_relative_to(repo / "build"):
                raise ValueError("bundle output must stay inside the build directory")
            for entry, data in payload.items():
                target = (output / entry).resolve()
                if not target.is_relative_to(output):
                    raise ValueError(f"bundle resource escapes output: {entry}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            print(f"Bundle resources staged: {len(payload)} files")
        elif args.command == "check":
            check(repo, report=True)
        else:
            # Verify the finished bundle before producing any removal candidates.
            check(repo)
            specs = list(json.loads((repo / "plugins.json").read_text(encoding="utf-8")).values()) + [
                config(repo)
            ]
            for name in obsolete_jars(
                repo / "dist", {s["Jar"]: release_name(repo, s) for s in specs}
            ):
                print("REMOVE:" + name)
    except (OSError, ValueError, KeyError, ET.ParseError, zipfile.BadZipFile) as error:
        print(f"BUNDLE FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
