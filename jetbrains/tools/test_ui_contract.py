"""Fault-injection checks for the approved UI, using the real packaged plugin."""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from built import requires_build
from generate import contrast_ratio, load_map, parse_rgb, set_ui
from ui_contract import (
    FOCUS_TEXT_PAIRS,
    focus_text_foreground,
    installed_keys,
    resolve,
    validate,
)

ROOT = Path(__file__).resolve().parents[1]
POP_PALETTE = json.loads(
    (ROOT / "tests/fixtures/palette-changes/periwinkle-pop-2026-10-23.json").read_text(encoding="utf-8")
)


def before_contrast_polish(scheme: bytes) -> bytes:
    """Restore only the approved refinements for historical UI fingerprints.

    Current values and all remaining fields have separate independent guards in
    test_editor_contrast; these older fingerprints still protect earlier UI work.
    In order: the Periwinkle Islands pop palette (exactly the fields
    recorded in tests/fixtures/palette-changes), three contrast refinements, the
    Kotlin modifier fix (KOTLIN_BUILTIN_ANNOTATION moved from the metadata role to
    the keyword role, as IntelliJ's own fallback has it), and Periwinkle's deeper
    types, fields and comments.
    """
    root = ET.fromstring(scheme)
    for query, value in POP_PALETTE["previous"].items():
        node = root.find(query)
        if node is not None:
            node.set("value", value)
    for query, value in (
        ("./colors/option[@name='INDENT_GUIDE']", "e6e6e6"),
        ("./colors/option[@name='SELECTED_INDENT_GUIDE']", "c8c8c8"),
        ("./attributes/option[@name='NOT_USED_ELEMENT_ATTRIBUTES']/value/option[@name='FOREGROUND']", "a9a9a9"),
        ("./attributes/option[@name='KOTLIN_BUILTIN_ANNOTATION']/value/option[@name='FOREGROUND']", "6B6B00"),
    ):
        root.find(query).set("value", value)
    # Periwinkle Islands' deeper types, fields and comments (8.5:1 on white). These three
    # values appear nowhere else in the scheme, so mapping them back restores exactly the earlier fields.
    deepened = {"0F5368": "17708A", "095747": "0F7560", "1F5721": "2F7D32"}
    for option in root.iter("option"):
        if (value := option.get("value", "").upper()) in deepened:
            option.set("value", deepened[value])
    restored = ET.tostring(root)
    return restored.replace(b"\n", b"\r\n") if b"\r\n" in scheme else restored


class FocusTextForegroundTests(unittest.TestCase):
    """The guard and the generated report must check the same foreground (Indigo overrides the inactive tab)."""

    def test_indigo_inactive_tab_uses_its_approved_override_and_others_do_not(self) -> None:
        contract = load_map(ROOT / "src/lavender/ui-contracts/Indigo.json")
        for label, foreground, _ in FOCUS_TEXT_PAIRS:
            expected = "colors.text-default" if label == "Inactive editor tab text" else foreground
            with self.subTest(label=label):
                self.assertEqual(expected, focus_text_foreground(label, foreground, contract))

    def test_unapproved_override_is_rejected(self) -> None:
        contract = {"values": {}, "inactiveEditorTabForeground": "colors.text-default"}
        with self.assertRaisesRegex(ValueError, "explicit approved value"):
            focus_text_foreground("Inactive editor tab text", "ui.EditorTabs.underlinedTabForeground", contract)


@requires_build
class UiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "src/periwinklenight/ui-contract.json").read_text(encoding="utf-8")
        )
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with zipfile.ZipFile(ROOT / f"dist/periwinkle-night-{version}.jar") as archive:
            cls.theme = json.loads(archive.read(cls.contract["themeEntry"]))
            cls.scheme = archive.read(cls.contract["schemeEntry"])

    def test_packaged_approved_ui_and_frozen_editor_scheme(self) -> None:
        self.assertEqual([], validate(self.theme, self.scheme, self.contract))

    def test_each_approved_value_detects_a_packaging_regression(self) -> None:
        for path, expected in self.contract["values"].items():
            with self.subTest(path=path):
                changed = copy.deepcopy(self.theme)
                section, key = path.split(".", 1)
                set_ui(
                    changed[section],
                    key,
                    "#FFFFFF" if isinstance(expected, str) else expected + 1,
                )
                self.assertTrue(validate(changed, self.scheme, self.contract))

    def test_main_window_cannot_silently_revert_to_dialog_layer(self) -> None:
        changed = copy.deepcopy(self.theme)
        changed["colors"]["main-window-bg"] = "layer-1-bg"
        self.assertTrue(
            any(
                "MainWindow.background" in error
                for error in validate(changed, self.scheme, self.contract)
            )
        )

    def test_missing_ui_value_fails(self) -> None:
        changed = copy.deepcopy(self.theme)
        set_ui(changed["ui"], "Island.borderColor", None)
        self.assertTrue(
            any(
                "missing UI value" in error
                for error in validate(changed, self.scheme, self.contract)
            )
        )

    def test_cyclic_colour_reference_fails(self) -> None:
        changed = copy.deepcopy(self.theme)
        changed["colors"]["main-window-bg"] = "main-window-bg"
        self.assertTrue(
            any(
                "cyclic" in error
                for error in validate(changed, self.scheme, self.contract)
            )
        )

    def test_unknown_colour_reference_fails(self) -> None:
        changed = copy.deepcopy(self.theme)
        changed["colors"]["main-window-bg"] = "missing-token"
        self.assertTrue(
            any(
                "unresolved" in error
                for error in validate(changed, self.scheme, self.contract)
            )
        )

    def test_separation_guard_fails_even_if_approved_value_is_updated(self) -> None:
        changed = copy.deepcopy(self.theme)
        changed["colors"]["main-window-bg"] = "tool-window-bg"
        relaxed = copy.deepcopy(self.contract)
        relaxed["values"] = {}
        self.assertTrue(
            any(
                "Tool panes against frame" in error
                for error in validate(changed, self.scheme, relaxed)
            )
        )

    def test_text_contrast_guard_is_independent_of_exact_values(self) -> None:
        changed = copy.deepcopy(self.theme)
        changed["colors"]["text-default"] = resolve(changed, "colors.tool-window-bg")
        relaxed = copy.deepcopy(self.contract)
        relaxed["values"] = {}
        self.assertTrue(
            any(
                "Primary text" in error
                for error in validate(changed, self.scheme, relaxed)
            )
        )

    def test_syntax_mutation_fails(self) -> None:
        changed = self.scheme.replace(b"DCDDEB", b"FFFFFF", 1)
        self.assertNotEqual(self.scheme, changed)
        self.assertIn(
            "editor scheme changed from the approved pre-UI baseline",
            validate(self.theme, changed, self.contract),
        )

    def test_missing_product_is_not_a_pass(self) -> None:
        with self.assertRaisesRegex(ValueError, "missing installation"):
            installed_keys(self.contract, {})


@requires_build
class PeriwinkleLightUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_map(ROOT / "src/periwinkle/ui-contracts/Periwinkle.json")
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with zipfile.ZipFile(
            ROOT / f"dist/periwinkle-islands-{version}.jar"
        ) as archive:
            cls.theme = json.loads(archive.read(cls.contract["themeEntry"]))
            cls.scheme = archive.read(cls.contract["schemeEntry"])
            cls.entries = archive.namelist()
            cls.descriptor = ET.fromstring(archive.read("META-INF/plugin.xml"))

    def test_native_focus_values_detect_packaging_regressions(self) -> None:
        for key, broken in (
            ("EditorTabs.underlinedTabBackground", "#FFFFFF"),
            ("EditorTabs.underlinedBorderColor", "#A9B5DD"),
            ("EditorTabs.inactiveUnderlinedTabBackground", "#ECE3FA"),
            ("EditorTabs.inactiveUnderlinedTabBorderColor", "#5C2D91"),
            ("Tree.selectionBackground", "#ECE3FA"),
            ("Tree.selectionForeground", "#DED7F3"),
            ("Tree.selectionInactiveBackground", "#DED7F3"),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.theme)
                set_ui(changed["ui"], key, broken)
                self.assertTrue(
                    any(
                        key in error
                        for error in validate(changed, self.scheme, self.contract)
                    )
                )

    def test_native_focus_states_are_distinct_and_readable(self) -> None:
        for active, inactive in (
            (
                "EditorTabs.underlinedTabBackground",
                "EditorTabs.inactiveUnderlinedTabBackground",
            ),
            (
                "EditorTabs.underlinedBorderColor",
                "EditorTabs.inactiveUnderlinedTabBorderColor",
            ),
            ("Tree.selectionBackground", "Tree.selectionInactiveBackground"),
        ):
            with self.subTest(active=active):
                self.assertNotEqual(
                    resolve(self.theme, "ui." + active),
                    resolve(self.theme, "ui." + inactive),
                )
        for foreground, background in (
            (
                "EditorTabs.underlinedTabForeground",
                "EditorTabs.underlinedTabBackground",
            ),
            (
                "EditorTabs.underlinedTabForeground",
                "EditorTabs.inactiveUnderlinedTabBackground",
            ),
            ("Tree.selectionForeground", "Tree.selectionBackground"),
            ("Tree.selectionForeground", "Tree.selectionInactiveBackground"),
        ):
            with self.subTest(background=background):
                self.assertGreaterEqual(
                    contrast_ratio(
                        parse_rgb(resolve(self.theme, "ui." + foreground)[1:]),
                        parse_rgb(resolve(self.theme, "ui." + background)[1:]),
                    ),
                    4.5,
                )

    def test_plugin_remains_a_theme_only_package(self) -> None:
        self.assertFalse(any(name.endswith(".class") for name in self.entries))
        self.assertEqual(
            {"bundledColorScheme", "themeProvider"},
            {node.tag for node in self.descriptor.find("extensions")},
        )
        self.assertIsNone(self.descriptor.find("applicationListeners"))

    def test_packaged_gutter_matches_accepted_preview(self) -> None:
        for key in ("EDITOR_GUTTER_BACKGROUND", "GUTTER_BACKGROUND"):
            self.assertEqual("#EEF0FA", self.contract["schemeColors"][key])
        self.assertEqual([], validate(self.theme, self.scheme, self.contract))

    def test_gutter_regressions_fail_even_if_scheme_hash_is_refreshed(self) -> None:
        for key, mutation in (
            (key, mutation)
            for key in ("EDITOR_GUTTER_BACKGROUND", "GUTTER_BACKGROUND")
            for mutation in ("white", "missing", "duplicate")
        ):
            with self.subTest(key=key, mutation=mutation):
                root = ET.fromstring(self.scheme)
                colours = root.find("colors")
                gutter = colours.find(f"./option[@name='{key}']")
                if mutation == "white":
                    gutter.set("value", "FFFFFF")
                elif mutation == "missing":
                    colours.remove(gutter)
                else:
                    colours.append(copy.deepcopy(gutter))
                changed = ET.tostring(root)
                contract = copy.deepcopy(self.contract)
                contract["schemeSha256"] = hashlib.sha256(changed).hexdigest()
                self.assertTrue(
                    any(
                        key in error
                        for error in validate(self.theme, changed, contract)
                    )
                )

    def test_line_numbers_remain_readable_on_gutter_and_caret_row(self) -> None:
        root = ET.fromstring(self.scheme)
        colours = {
            node.get("name"): node.get("value")
            for node in root.findall("./colors/option")
        }
        for foreground, background in (
            ("LINE_NUMBERS_COLOR", "EDITOR_GUTTER_BACKGROUND"),
            ("LINE_NUMBER_ON_CARET_ROW_COLOR", "EDITOR_GUTTER_BACKGROUND"),
            ("LINE_NUMBERS_COLOR", "GUTTER_BACKGROUND"),
            ("LINE_NUMBER_ON_CARET_ROW_COLOR", "GUTTER_BACKGROUND"),
            ("LINE_NUMBER_ON_CARET_ROW_COLOR", "CARET_ROW_COLOR"),
        ):
            with self.subTest(foreground=foreground, background=background):
                self.assertGreaterEqual(
                    contrast_ratio(
                        parse_rgb(colours[foreground]), parse_rgb(colours[background])
                    ),
                    4.5,
                )

    # Only these four reviewed UI fields may differ from the scheme before the UI refinements.
    SCHEME_PATHS = (
        "./colors/option[@name='DOCUMENTATION_COLOR']",
        "./attributes/option[@name='BREADCRUMBS_CURRENT']/value/option[@name='BACKGROUND']",
        "./attributes/option[@name='BREADCRUMBS_CURRENT']/value/option[@name='FOREGROUND']",
        "./attributes/option[@name='BREADCRUMBS_DEFAULT']/value/option[@name='FOREGROUND']",
    )

    def test_approved_hierarchy_routes_and_exact_scheme_fields(self) -> None:
        expected = {
            "ui.MainToolbar.background": "#D9E0F8",
            "ui.ToolWindow.Header.inactiveBackground": "#D9E0F8",
            "ui.EditorTabs.background": "#EEF0FA",
            "ui.Tree.hash": "#CCD3EC",
            "ui.Popup.borderColor": "#9AA6D4",
        }
        for path, value in expected.items():
            self.assertEqual(value, self.contract["values"][path])
            self.assertEqual(value, resolve(self.theme, path))
        self.assertEqual(
            "#F7F9FE", self.contract["schemeColors"]["DOCUMENTATION_COLOR"]
        )
        self.assertEqual(
            {
                "BREADCRUMBS_CURRENT": {
                    "BACKGROUND": "#ECE3FA",
                    "FOREGROUND": "#5C2D91",
                },
                "BREADCRUMBS_DEFAULT": {"FOREGROUND": "#4E5670"},
            },
            self.contract["schemeAttributes"],
        )
        self.assertEqual([], validate(self.theme, self.scheme, self.contract))

    def test_old_hierarchy_values_fail_packaged_checks(self) -> None:
        for key, old in (
            ("MainToolbar.background", "#CCD5F0"),
            ("ToolWindow.Header.inactiveBackground", "#EBEDF9"),
            ("EditorTabs.background", "#E6EBFA"),
            ("Tree.hash", "#9AA6D4"),
            ("Popup.borderColor", "#B8C2E6"),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.theme)
                set_ui(changed["ui"], key, old)
                self.assertTrue(
                    any(
                        key in error
                        for error in validate(changed, self.scheme, self.contract)
                    )
                )

    def test_shared_bands_and_edges_survive_snapshot_refresh(self) -> None:
        for key in ("ui.MainToolbar.background", "ui.Popup.borderColor"):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.theme)
                contract = copy.deepcopy(self.contract)
                set_ui(changed["ui"], key.removeprefix("ui."), "#FFFFFF")
                contract["values"][key] = "#FFFFFF"
                self.assertTrue(
                    any(
                        "UI coherence" in error
                        for error in validate(changed, self.scheme, contract)
                    )
                )

    def test_refined_file_rail_and_all_prior_scheme_bytes_are_preserved(self) -> None:
        for path, expected in {
            "ui.EditorTabs.background": "#EEF0FA",
            "ui.EditorTabs.underlinedTabBackground": "#DED7F3",
            "ui.EditorTabs.underlinedTabForeground": "#5C2D91",
            "ui.EditorTabs.inactiveUnderlinedTabBackground": "#F4F0FC",
            "ui.EditorTabs.inactiveUnderlinedTabBorderColor": "#A9B5DD",
            "ui.EditorTabs.unselectedAlpha": 1.0,
            "ui.EditorTabs.unselectedBlend": 0.9,
            "ui.List.selectionBackground": "#ECE3FA",
            "ui.Tree.selectionBackground": "#DED7F3",
        }.items():
            with self.subTest(path=path):
                self.assertEqual(expected, resolve(self.theme, path))
                self.assertEqual(expected, self.contract["values"][path])
        # LF bytes (generate.py writes LF everywhere); the CRLF value before that change was bfafd9b6...2745.
        self.assertEqual(
            "04388f4ba91cff0f9a6f679204a222ca01630251affb7989a6163d635990ff99",
            hashlib.sha256(
                re.sub(
                    rb'    <option name="EDITOR_GUTTER_BACKGROUND" value="EEF0FA" />\r?\n',
                    b"",
                    before_contrast_polish(self.scheme),
                    count=1,
                )
            ).hexdigest(),
        )

    def test_previous_file_rail_and_faded_names_fail(self) -> None:
        for key, value in (
            ("EditorTabs.background", "#D9E0F8"),
            ("EditorTabs.underlinedTabBackground", "#ECE3FA"),
            ("EditorTabs.unselectedAlpha", 0.7),
            ("EditorTabs.unselectedBlend", 0.5),
        ):
            with self.subTest(key=key):
                changed = copy.deepcopy(self.theme)
                set_ui(changed["ui"], key, value)
                self.assertTrue(
                    any(
                        key in error
                        for error in validate(changed, self.scheme, self.contract)
                    )
                )

    def test_rail_selection_separation_survives_snapshot_refresh(self) -> None:
        for value in ("#ECE3FA", "#EEF0FA"):
            with self.subTest(fill=value):
                changed = copy.deepcopy(self.theme)
                contract = copy.deepcopy(self.contract)
                set_ui(changed["ui"], "EditorTabs.underlinedTabBackground", value)
                contract["values"]["ui.EditorTabs.underlinedTabBackground"] = value
                self.assertTrue(
                    any(
                        "Selected tab against rail" in error
                        for error in validate(changed, self.scheme, contract)
                    )
                )

    def test_documentation_and_breadcrumb_faults_survive_hash_refresh(self) -> None:
        for path in self.SCHEME_PATHS:
            for mutation in ("changed", "missing", "duplicate"):
                with self.subTest(path=path, mutation=mutation):
                    root = ET.fromstring(self.scheme)
                    node = root.find(path)
                    parent = next(
                        parent for parent in root.iter() if node in list(parent)
                    )
                    if mutation == "changed":
                        node.set("value", "FFFFFF")
                    elif mutation == "missing":
                        parent.remove(node)
                    else:
                        parent.append(copy.deepcopy(node))
                    contract = copy.deepcopy(self.contract)
                    changed = ET.tostring(root)
                    contract["schemeSha256"] = hashlib.sha256(changed).hexdigest()
                    self.assertTrue(validate(self.theme, changed, contract))

    def test_breadcrumb_contrast_survives_colour_and_hash_refresh(self) -> None:
        root = ET.fromstring(self.scheme)
        root.find(self.SCHEME_PATHS[2]).set("value", "ECE3FA")
        changed = ET.tostring(root)
        contract = copy.deepcopy(self.contract)
        contract["schemeSha256"] = hashlib.sha256(changed).hexdigest()
        contract["schemeAttributes"]["BREADCRUMBS_CURRENT"]["FOREGROUND"] = "#ECE3FA"
        self.assertTrue(
            any(
                "Current breadcrumb text" in error
                for error in validate(self.theme, changed, contract)
            )
        )

    def test_every_other_scheme_field_matches_the_reviewed_pre_ui_baseline(
        self,
    ) -> None:
        root = ET.fromstring(before_contrast_polish(self.scheme))
        # 0.5.9 adds only this New UI key beyond the earlier four UI refinements.
        root.find("colors").remove(
            root.find("./colors/option[@name='EDITOR_GUTTER_BACKGROUND']")
        )
        for path in self.SCHEME_PATHS:
            node = root.find(path)
            self.assertIsNotNone(node)
            parent = next(parent for parent in root.iter() if node in list(parent))
            parent.remove(node)
        for node in root.iter():
            if node.text is not None and not node.text.strip():
                node.text = None
            if node.tail is not None and not node.tail.strip():
                node.tail = None
        self.assertEqual(
            "70b9d3827243bd2ef394e4857bc9fe60f6bc3cc196462231a7cd59819370f5da",
            hashlib.sha256(ET.tostring(root)).hexdigest(),
        )


if __name__ == "__main__":
    unittest.main()

