"""Verify every packaged appearance and editor scheme, not just the pilot theme."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from built import requires_build
from check_pack import check_frozen_schemes, check_gutter, validate_theme_catalogue
from generate import load_map, set_ui
from language_mappings import check_scheme, fields, read_contract
from ui_contract import FOCUS_STATES, FOCUS_TEXT_PAIRS, resolve, validate

ROOT = Path(__file__).resolve().parents[1]
PLUGINS = json.loads((ROOT / "plugins.json").read_text(encoding="utf-8"))


def artifact(spec: dict) -> Path:
    version = (ROOT / spec["Version"]).read_text(encoding="utf-8").strip()
    return ROOT / "dist" / f"{spec['Jar']}-{version}.jar"


@requires_build
class ThemeFamilyTests(unittest.TestCase):
    def test_dark_tree_guides_cannot_blend_even_after_snapshot_refresh(self) -> None:
        count = 0
        for contract, theme, scheme in self.appearances():
            if not theme.get("dark"):
                continue
            self.assertTrue(contract.get("treeGuides"))
            updated = copy.deepcopy(contract)
            broken = copy.deepcopy(theme)
            background = resolve(theme, "colors.tool-window-bg")
            set_ui(broken["ui"], "Tree.hash", background)
            broken["colors"]["tree-indent-guide-border"] = background
            updated["values"].update(
                {
                    "ui.Tree.hash": background,
                    "colors.tree-indent-guide-border": background,
                }
            )
            self.assertTrue(
                any(
                    "native tree guide contrast" in s
                    for s in validate(broken, scheme, updated)
                )
            )
            count += 1
        self.assertEqual(3, count)

    def test_every_scheme_has_palette_specific_gutters_and_unchanged_other_fields(
        self,
    ) -> None:
        count = 0
        for spec in PLUGINS.values():
            baseline = json.loads(
                (ROOT / spec["Source"] / "palette-baseline.json").read_text(encoding="utf-8")
            )
            self.assertEqual(set(baseline["schemes"]), set(baseline["gutters"]))
            with zipfile.ZipFile(artifact(spec)) as archive:
                for entry, expected in baseline["gutters"].items():
                    scheme = archive.read(entry)
                    check_gutter(scheme, expected)
                    for key in ("GUTTER_BACKGROUND", "EDITOR_GUTTER_BACKGROUND"):
                        for mutation in ("missing", "duplicate", "white"):
                            with self.subTest(entry=entry, key=key, mutation=mutation):
                                root = ET.fromstring(scheme)
                                node = root.find(f"./colors/option[@name='{key}']")
                                if mutation == "missing":
                                    root.find("colors").remove(node)
                                elif mutation == "duplicate":
                                    root.find("colors").append(copy.deepcopy(node))
                                else:
                                    node.set("value", "FFFFFF")
                                with self.assertRaisesRegex(
                                    ValueError, "palette-specific"
                                ):
                                    check_gutter(ET.tostring(root), expected)
                    root = ET.fromstring(scheme)
                    root.find(
                        "./attributes/option[@name='DEFAULT_KEYWORD']/value/option[@name='FOREGROUND']"
                    ).set("value", "FFFFFF")
                    with self.assertRaisesRegex(ValueError, "outside the two gutter"):
                        check_gutter(ET.tostring(root), expected)
                    count += 1
        self.assertEqual(8, count)

    def appearances(self):
        for spec in PLUGINS.values():
            source = ROOT / spec["Source"]
            contracts = (
                [source / "ui-contract.json"]
                if (source / "ui-contract.json").exists()
                else sorted((source / "ui-contracts").glob("*.json"))
            )
            with zipfile.ZipFile(artifact(spec)) as archive:
                for path in contracts:
                    contract = load_map(path)
                    yield (
                        contract,
                        json.loads(archive.read(contract["themeEntry"])),
                        archive.read(contract["schemeEntry"]),
                    )

    def test_all_appearances_have_native_focus_guards(self) -> None:
        count = 0
        required = {
            path for _, a, b in (*FOCUS_STATES, *FOCUS_TEXT_PAIRS) for path in (a, b)
        }
        for contract, theme, scheme in self.appearances():
            with self.subTest(theme=theme["name"]):
                self.assertTrue(contract.get("nativeFocus"))
                self.assertLessEqual(required, contract["values"].keys())
                self.assertEqual([], validate(theme, scheme, contract))
            count += 1
        self.assertEqual(9, count)

    def test_focus_state_collapse_fails_after_snapshot_update(self) -> None:
        for contract, theme, scheme in self.appearances():
            for label, active, inactive in FOCUS_STATES:
                with self.subTest(theme=theme["name"], state=label):
                    broken = copy.deepcopy(theme)
                    updated = copy.deepcopy(contract)
                    value = resolve(theme, active)
                    set_ui(broken["ui"], inactive.removeprefix("ui."), value)
                    updated["values"][inactive] = value
                    self.assertTrue(
                        any(
                            "native focus states collapsed" in error
                            for error in validate(broken, scheme, updated)
                        )
                    )

    def test_tool_window_focus_text_guard_survives_snapshot_update(self) -> None:
        key = "ui.ToolWindow.HeaderTab.underlinedTabForeground"
        for contract, theme, scheme in self.appearances():
            with self.subTest(theme=theme["name"]):
                broken = copy.deepcopy(theme)
                updated = copy.deepcopy(contract)
                value = resolve(theme, "ui.EditorTabs.underlinedTabBackground")
                set_ui(broken["ui"], key.removeprefix("ui."), value)
                updated["values"][key] = value
                self.assertTrue(
                    any(
                        "native focus text: Tool-window tab" in error
                        for error in validate(broken, scheme, updated)
                    )
                )

    def test_indigo_unfocused_editor_uses_dark_file_text(self) -> None:
        found = False
        key = "ui.EditorTabs.inactiveUnderlinedTabBackground"
        for contract, theme, scheme in self.appearances():
            if theme["name"] != "Lavender Mist Indigo":
                continue
            found = True
            broken = copy.deepcopy(theme)
            updated = copy.deepcopy(contract)
            set_ui(broken["ui"], key.removeprefix("ui."), "#55506B")
            updated["values"][key] = "#55506B"
            # Even a refreshed exact snapshot must reject the old dark fill.
            self.assertTrue(
                any(
                    "native focus text: Inactive editor tab text" in error
                    for error in validate(broken, scheme, updated)
                )
            )
        self.assertTrue(found)

    def test_distinctive_tab_treatments_keep_their_own_colours(self) -> None:
        expected = {
            "Lavender Mist Gold": "#F5CC84",
            "Lavender Mist Indigo": "#ECE3FA",
            "Blue Mist": "#F5CC84",
            "Abyss": "#5EEAD4",
        }
        for _, theme, _ in self.appearances():
            if theme["name"] in expected:
                self.assertEqual(
                    expected.pop(theme["name"]),
                    resolve(theme, "ui.EditorTabs.underlinedTabBackground"),
                )
        self.assertFalse(expected)

    def test_missing_or_misdirected_theme_provider_fails(self) -> None:
        spec = PLUGINS["LavenderMist"]
        profiles = [
            load_map(path)
            for path in (ROOT / spec["Source"] / "theme").glob("*.theme-map.json")
        ]
        with zipfile.ZipFile(artifact(spec)) as archive:
            entries = set(archive.namelist())
            descriptor = ET.fromstring(archive.read("META-INF/plugin.xml"))
        validate_theme_catalogue(entries, descriptor, profiles)
        descriptor.find("./extensions/themeProvider").set(
            "path", "/themes/LavenderMistBlue.theme.json"
        )
        with self.assertRaisesRegex(ValueError, "complete source catalogue"):
            validate_theme_catalogue(entries, descriptor, profiles)

    def test_every_scheme_has_verified_language_roles_and_its_own_palette(self) -> None:
        count = 0
        for name, spec in PLUGINS.items():
            source = ROOT / spec["Source"]
            baseline = json.loads((source / "palette-baseline.json").read_text(encoding="utf-8"))
            with zipfile.ZipFile(artifact(spec)) as archive:
                for path in (source / "scheme").glob("*.scheme-map.json"):
                    profile = load_map(path)
                    entry = f"colorSchemes/{profile['fileName']}.xml"
                    root = ET.fromstring(archive.read(entry))
                    with self.subTest(plugin=name, scheme=profile["name"]):
                        self.assertEqual(
                            [],
                            check_scheme(
                                root,
                                read_contract(ROOT / profile["languageMappings"]),
                                profile["semanticRoles"],
                            ),
                        )
                        for role, colour in (
                            baseline.get("paletteAnchors", {}).get(entry, {}).items()
                        ):
                            actual = fields(root, profile["semanticRoles"][role])[
                                "FOREGROUND"
                            ]
                            self.assertEqual(int(colour[1:], 16), int(actual, 16), role)
                    count += 1
            check_frozen_schemes(artifact(spec), source)
        self.assertEqual(8, count)

    def test_every_appearance_has_an_independent_ui_contract(self) -> None:
        count = 0
        for name, spec in PLUGINS.items():
            source = ROOT / spec["Source"]
            contracts = (
                [source / "ui-contract.json"]
                if (source / "ui-contract.json").exists()
                else list((source / "ui-contracts").glob("*.json"))
            )
            expected = {
                f"themes/{load_map(path)['fileName']}.theme.json"
                for path in (source / "theme").glob("*.theme-map.json")
            }
            self.assertEqual(
                expected, {load_map(path)["themeEntry"] for path in contracts}, name
            )
            with zipfile.ZipFile(artifact(spec)) as archive:
                for path in contracts:
                    contract = load_map(path)
                    theme = json.loads(archive.read(contract["themeEntry"]))
                    scheme = archive.read(contract["schemeEntry"])
                    with self.subTest(plugin=name, appearance=theme["name"]):
                        self.assertEqual([], validate(theme, scheme, contract))
                        broken = copy.deepcopy(theme)
                        set_ui(broken["ui"], "Island.borderColor", "#FFFFFF")
                        self.assertTrue(validate(broken, scheme, contract))
                    count += 1
        self.assertEqual(9, count)

    def test_visual_studio_dark_retains_distinct_interface_and_struct_colours(
        self,
    ) -> None:
        with zipfile.ZipFile(artifact(PLUGINS["Graphite"])) as archive:
            root = ET.fromstring(archive.read("colorSchemes/Graphite.xml"))

        def fg(key: str) -> int:
            return int(fields(root, key)["FOREGROUND"], 16)

        self.assertNotEqual(
            fg("CLASS_NAME_ATTRIBUTES"), fg("INTERFACE_NAME_ATTRIBUTES")
        )
        self.assertNotEqual(
            fg("ReSharper.CSHARP_CLASS_IDENTIFIER"),
            fg("ReSharper.CSHARP_STRUCT_IDENTIFIER"),
        )
        self.assertEqual(
            fg("INTERFACE_NAME_ATTRIBUTES"), fg("ReSharper.CSHARP_INTERFACE_IDENTIFIER")
        )
        self.assertEqual(
            fg("GO_PACKAGE_EXPORTED_STRUCT"), fg("ReSharper.CSHARP_STRUCT_IDENTIFIER")
        )

    def test_alternative_editor_scheme_cannot_be_omitted_from_frozen_baseline(
        self,
    ) -> None:
        source = ROOT / PLUGINS["LavenderMist"]["Source"]
        baseline = json.loads((source / "palette-baseline.json").read_text(encoding="utf-8"))
        baseline["schemes"].pop("colorSchemes/LavenderMistPurple.xml")
        scratch = ROOT / "build/family-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as folder:
            fixture = Path(folder)
            (fixture / "scheme").mkdir()
            for name in ("LavenderMist", "LavenderMistPurple"):
                (fixture / "scheme" / f"{name}.scheme-map.json").write_text(
                    json.dumps({"fileName": name}), encoding="utf-8", newline="\n"
                )
            (fixture / "palette-baseline.json").write_text(
                json.dumps(baseline), encoding="utf-8", newline="\n"
            )
            with self.assertRaisesRegex(ValueError, "every bundled scheme"):
                check_frozen_schemes(artifact(PLUGINS["LavenderMist"]), fixture)


if __name__ == "__main__":
    unittest.main()
