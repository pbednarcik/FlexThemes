"""Reject faded text/guides even after approved value snapshots are refreshed."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from unittest.mock import patch

import editor_contrast as contrast
from built import requires_build
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ENTRY = "colorSchemes/PeriwinkleIslands.xml"


@requires_build
class EditorContrastTests(unittest.TestCase):
    def setUp(self) -> None:
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        with zipfile.ZipFile(ROOT / f"dist/flex-themes-{version}.jar") as package:
            self.schemes = {
                entry: package.read(entry)
                for entry in json.loads(contrast.CONTRACT.read_text(encoding="utf-8"))["schemes"]
            }
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "build")
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def test_all_eight_schemes_meet_independent_contrast_and_preservation_rules(self):
        self.assertEqual(8, len(self.schemes))
        for entry, scheme in self.schemes.items():
            with self.subTest(entry=entry):
                values = contrast.check(scheme, entry)
                self.assertGreaterEqual(values["NOT_USED_ELEMENT_ATTRIBUTES"], 4.5)
                self.assertGreaterEqual(values["SELECTED_INDENT_GUIDE"], 2.5)

    def test_refreshing_value_snapshot_cannot_accept_faded_colours(self):
        for path, faded in (
            ("colors.INDENT_GUIDE", "#FFFFFF"),
            ("colors.SELECTED_INDENT_GUIDE", "#FFFFFF"),
            ("colors.LINE_NUMBERS_COLOR", "#EEEEEE"),
            ("attributes.NOT_USED_ELEMENT_ATTRIBUTES.FOREGROUND", "#A9A9A9"),
        ):
            entry = (
                ENTRY
                if path != "colors.LINE_NUMBERS_COLOR"
                else "colorSchemes/MorningMist.xml"
            )
            with self.subTest(role=path):
                root = ET.fromstring(self.schemes[entry])
                parts = path.split(".")
                query = (
                    f"./colors/option[@name='{parts[1]}']"
                    if parts[0] == "colors"
                    else f"./attributes/option[@name='{parts[1]}']/value/option[@name='{parts[2]}']"
                )
                root.find(query).set("value", faded[1:])
                updated = json.loads(contrast.CONTRACT.read_text(encoding="utf-8"))
                updated["schemes"][entry]["changes"][path] = faded
                path_on_disk = self.folder / "contract.json"
                path_on_disk.write_text(json.dumps(updated), encoding="utf-8", newline="\n")
                with (
                    patch.object(contrast, "CONTRACT", path_on_disk),
                    self.assertRaisesRegex(ValueError, "Faded editor"),
                ):
                    contrast.check(ET.tostring(root), entry)

    def test_changing_syntax_or_font_is_rejected_independently_of_snapshot_hashes(self):
        for query, value in (
            (
                "./attributes/option[@name='DEFAULT_KEYWORD']/value/option[@name='FOREGROUND']",
                "FFFFFF",
            ),
            ("./option[@name='EDITOR_FONT_SIZE']", "24"),
        ):
            root = ET.fromstring(self.schemes[ENTRY])
            node = root.find(query)
            if node is None:
                node = ET.SubElement(root, "option", {"name": "EDITOR_FONT_SIZE"})
            node.set("value", value)
            with self.assertRaisesRegex(ValueError, "outside the approved"):
                contrast.check(ET.tostring(root), ENTRY)

    def native_images(self, entry=ENTRY, product=None):
        result = {"geometry": {}}
        if product:
            result["product"] = product
        root = ET.fromstring(self.schemes[entry])
        ordinary = contrast.read_colour(root, "colors.INDENT_GUIDE")
        numbers = contrast.read_colour(root, "colors.LINE_NUMBERS_COLOR")
        for state in ("editor-focus", "solution-focus", "text-selection"):
            image = Image.new("RGB", (1800, 1200) if product else (500, 500), "white")
            draw = ImageDraw.Draw(image)
            draw.line((120, 80, 120, 300), fill=ordinary)
            draw.text((20, 90), "123456789", fill=numbers)
            draw.line((25, 110, 25, 170), fill=numbers)
            if product:
                draw.rectangle(
                    contrast.UNUSED_NAME_PROBES[product],
                    fill=contrast.read_colour(
                        root, "attributes.NOT_USED_ELEMENT_ATTRIBUTES.FOREGROUND"
                    ),
                )
            if product == "RD" and entry == "colorSchemes/MorningMist.xml":
                draw.rectangle((475, 120, 545, 139), fill="#F2F2F2")
            image.save(self.folder / f"{state}.png")
            result["geometry"][state] = {
                "gutter": [10, 30, 60, 450],
                "editor": [80, 30, 410, 450],
            }
        return result, ordinary

    def test_native_unused_name_and_small_antialiased_label_cannot_fade(self):
        entry = "colorSchemes/MorningMist.xml"
        result, _ = self.native_images(entry, "RD")
        probes = contrast.verify_native(self.folder, result, self.schemes[entry], entry)
        self.assertGreater(
            probes["editor-focus"]["textProbes"]["unusedName"]["exactColourPixels"], 30
        )
        for role in ("unused", "vision"):
            with self.subTest(role=role):
                self.native_images(entry, "RD")
                path = self.folder / "text-selection.png"
                with Image.open(path) as opened:
                    image = opened.copy()
                draw = ImageDraw.Draw(image)
                if role == "unused":
                    draw.rectangle(contrast.UNUSED_NAME_PROBES["RD"], fill="#A9A9A9")
                else:
                    draw.rectangle((475, 120, 545, 139), fill="#F4F4F4")
                image.save(path)
                with self.assertRaisesRegex(ValueError, "paint (missing|faded)"):
                    contrast.verify_native(
                        self.folder, result, self.schemes[entry], entry
                    )

    def test_native_paint_requires_numbers_and_continuous_editor_guides(self):
        result, ordinary = self.native_images()
        probes = contrast.verify_native(self.folder, result, self.schemes[ENTRY], ENTRY)
        self.assertEqual(3, len(probes))
        self.assertGreater(probes["editor-focus"]["longestOrdinaryGuideStroke"], 200)
        self.assertEqual(
            0, probes["editor-focus"]["exactColourPixels"]["SELECTED_INDENT_GUIDE"]
        )
        for role in ("guide", "numbers"):
            with self.subTest(role=role):
                state = "solution-focus"
                self.native_images()
                with Image.open(self.folder / f"{state}.png") as source:
                    image = source.copy()
                draw = ImageDraw.Draw(image)
                if role == "guide":
                    draw.line((120, 80, 120, 300), fill="white")
                    # Scattered same-colour pixels must not impersonate a guide.
                    for x in range(100, 220, 3):
                        draw.point((x, 100), fill=ordinary)
                else:
                    draw.rectangle((10, 30, 69, 479), fill="white")
                image.save(self.folder / f"{state}.png")
                with self.assertRaisesRegex(ValueError, "paint missing"):
                    contrast.verify_native(
                        self.folder, result, self.schemes[ENTRY], ENTRY
                    )
        incomplete = copy.deepcopy(result)
        incomplete["geometry"].pop("text-selection")
        self.native_images()
        with self.assertRaisesRegex(ValueError, "all three states"):
            contrast.verify_native(self.folder, incomplete, self.schemes[ENTRY], ENTRY)


if __name__ == "__main__":
    unittest.main()
