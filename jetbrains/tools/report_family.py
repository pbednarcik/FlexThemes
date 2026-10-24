"""Write a compact verification index from the final packaged theme family."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from check_pack import check_frozen_schemes, validate_theme_catalogue
from generate import load_map
from language_mappings import check_scheme, read_contract
from report_paths import family_reports, reports_root
from ui_contract import resolve, validate


def main(repo: Path | None = None) -> None:
    repo = repo or Path(__file__).resolve().parents[1]
    catalogue = json.loads((repo / "plugins.json").read_text(encoding="utf-8"))
    lines = [
        "# Theme family verification",
        "",
        "Panel separation follows the Periwinkle Night clarity rules. Each theme retains its own palette, accent and editor background.",
        "",
        "Rebuild and verify all plugins with `python tools/build.py --plugin All`.",
        "",
        "## Packaged appearances",
        "",
        "| Appearance | Version | Frame | Tool panes | Editor | Accent | UI report |",
        "|---|---|---|---|---|---|---|",
    ]
    artifacts = []
    scheme_count = 0
    appearance_count = 0
    for spec in catalogue.values():
        source = repo / spec["Source"]
        version = (repo / spec["Version"]).read_text(encoding="utf-8").strip()
        jar = repo / "dist" / f"{spec['Jar']}-{version}.jar"
        scheme_count += check_frozen_schemes(jar, source)
        contracts = (
            [source / "ui-contract.json"]
            if (source / "ui-contract.json").exists()
            else sorted((source / "ui-contracts").glob("*.json"))
        )
        with zipfile.ZipFile(jar) as archive:
            profiles = [
                load_map(path) for path in (source / "theme").glob("*.theme-map.json")
            ]
            if {load_map(path)["themeEntry"] for path in contracts} != {
                f"themes/{profile['fileName']}.theme.json" for profile in profiles
            }:
                raise ValueError(
                    "family report requires a UI contract for every appearance"
                )
            validate_theme_catalogue(
                set(archive.namelist()),
                ET.fromstring(archive.read("META-INF/plugin.xml")),
                profiles,
            )
            for path in (source / "scheme").glob("*.scheme-map.json"):
                profile = load_map(path)
                root = ET.fromstring(
                    archive.read(f"colorSchemes/{profile['fileName']}.xml")
                )
                if failures := check_scheme(
                    root,
                    read_contract(repo / profile["languageMappings"]),
                    profile["semanticRoles"],
                ):
                    raise ValueError(failures)
            for path in contracts:
                contract = load_map(path)
                theme = json.loads(archive.read(contract["themeEntry"]))
                if failures := validate(
                    theme, archive.read(contract["schemeEntry"]), contract
                ):
                    raise ValueError(failures)
                appearance_count += 1
                suffix = "" if len(contracts) == 1 else "." + path.stem
                relative = (
                    (family_reports(repo, spec) / f"UiVerification{suffix}.md")
                    .relative_to(reports_root(repo))
                    .as_posix()
                )
                colours = [
                    resolve(theme, key)
                    for key in (
                        "colors.main-window-bg",
                        "colors.tool-window-bg",
                        "colors.editor-bg",
                        "colors.accent-brand-bg",
                    )
                ]
                lines.append(
                    f"| {theme['name']} | {version} | `{colours[0]}` | `{colours[1]}` | `{colours[2]}` | `{colours[3]}` | [Verified]({relative}) |"
                )
        artifacts.append((jar.name, hashlib.sha256(jar.read_bytes()).hexdigest()))
    lines += [
        "",
        f"Coverage: **{len(catalogue)} plugins, {appearance_count} appearances, {scheme_count} editor schemes**. Each scheme checks 275 language keys; each appearance checks at least 34 UI values and 10 separation/text pairs.",
        "Periwinkle Islands also checks its #EEF0FA gutter in the packaged editor scheme; regression tests reject a white, missing or duplicate gutter entry and check line-number contrast.",
        "Periwinkle Islands pins its VS-inspired hierarchy: five UI routes, the documentation colour and three breadcrumb fields; independent guards check shared bands/edges and text readability, and a fingerprint rejects changes to every other scheme field, including syntax and fonts.",
        "Its file rail uses #EEF0FA and the selected-tab fill #DED7F3, shared with Islands tool-window tabs. Regression checks reject faded settings and weak selected-tab separation even after snapshots are refreshed.",
        "All nine appearances check native focused/inactive tab fills, tab borders and tree selections independently of their exact colour snapshots, plus seven text pairs at 4.5:1. Editor and tool-window tabs use explicit palette-specific foregrounds. Gold, indigo and turquoise tab treatments retain their identities; no runtime UI drawing code is bundled.",
        "",
        "The standard builds also verify UI routes and language declarations from installed Rider, IntelliJ IDEA, GoLand and PyCharm. Product versions are recorded in the linked reports.",
        "",
        "## Palette preservation",
        "",
        "Each scheme's semantic palette anchors are checked against its frozen baseline, so a palette can only change on purpose.",
        "VS Dark retains distinct interface, struct and enum colours. Faithful VS palettes retain their pre-existing documented contrast exceptions; no blanket new exception was added.",
        "All eight editor schemes, including Lavender Mist Purple, use their own faint gutter palette through both supported UI keys. Independent pre-change fingerprints preserve every non-gutter field, including syntax and fonts. All schemes have frozen baselines checked on every standard build.",
        "",
        "## Artifact fingerprints",
        "",
        "| Artifact | SHA-256 |",
        "|---|---|",
    ]
    lines += [f"| `{name}` | `{digest}` |" for name, digest in artifacts]
    lines += [
        "",
        "## Evidence boundary",
        "",
        "This report covers declarations and packaged files. The separate native showcase gate, kept outside this repository, checks fixed C#, Java, Go and Python fixtures in all four IDEs, with independent rendered-pixel probes and reviewed screenshot baselines. Neither gate certifies every runtime token, framework or operating system.",
        "Python has no dedicated instance-field key in the inspected highlighter, so attribute references cannot be promised a member colour. User overrides and rainbow highlighting can also affect runtime colours.",
        "",
        "See docs/Development.md for the workflow, personal builds and intentional baseline updates.",
        "",
    ]
    reports_root(repo).mkdir(parents=True, exist_ok=True)
    (reports_root(repo) / "ThemeVerification.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(
        f"Family verification index written: {len(catalogue)} plugins, {scheme_count} schemes"
    )


if __name__ == "__main__":
    main()
