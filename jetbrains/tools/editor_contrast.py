"""Independent contrast and preservation guards for the approved editor polish."""

from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

from generate import contrast_ratio, parse_rgb
from PIL import Image

CONTRACT = Path(__file__).resolve().parents[1] / "src/common/editor-contrast.json"
# Native-pixel rectangles around CalculateTotal/calculateTotal in the pinned
# 1800x1200, Consolas-16 showcase. Readiness and scene/font hashes are guarded
# separately. No unused declaration is visible in the Go/Python fixture states.
UNUSED_NAME_PROBES = {"RD": (635, 669, 762, 689), "IU": (738, 651, 865, 672)}


def read_colour(root: ET.Element, path: str) -> str:
    parts = path.split(".")
    query = (
        f"./colors/option[@name='{parts[1]}']"
        if parts[0] == "colors"
        else f"./attributes/option[@name='{parts[1]}']/value/option[@name='{parts[2]}']"
    )
    nodes = root.findall(query)
    if len(nodes) != 1 or not nodes[0].get("value"):
        raise ValueError(f"Missing or duplicate editor contrast role: {path}")
    return f"#{int(nodes[0].get('value'), 16):06X}"


def preserved_fingerprint(scheme: bytes, changes: dict) -> str:
    root = ET.fromstring(scheme)
    for path in changes:
        parts = path.split(".")
        parent = (
            root.find("colors")
            if parts[0] == "colors"
            else root.find(f"./attributes/option[@name='{parts[1]}']/value")
        )
        if parent is not None:
            name = parts[1] if parts[0] == "colors" else parts[2]
            for node in list(parent):
                if node.get("name") == name:
                    parent.remove(node)
    for node in root.iter():
        if node.text is not None and not node.text.strip():
            node.text = None
        if node.tail is not None and not node.tail.strip():
            node.tail = None
    return hashlib.sha256(ET.tostring(root)).hexdigest()


def ratio(a: str, b: str) -> float:
    return contrast_ratio(
        parse_rgb(a.removeprefix("#")), parse_rgb(b.removeprefix("#"))
    )


def check(scheme: bytes, entry: str) -> dict:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))["schemes"][entry]
    root = ET.fromstring(scheme)
    for path, expected in contract["changes"].items():
        if read_colour(root, path) != expected:
            raise ValueError(f"Unreviewed editor contrast colour: {entry} {path}")
    if (
        preserved_fingerprint(scheme, contract["changes"])
        != contract["preservedSha256"]
    ):
        raise ValueError(
            f"Editor fields outside the approved contrast changes differ: {entry}"
        )
    background = contract["editorBackground"]
    rows = {}
    for role in ("NOT_USED_ELEMENT_ATTRIBUTES", "CODE_VISION_DEFAULT"):
        foreground = read_colour(root, f"attributes.{role}.FOREGROUND")
        rows[role] = ratio(foreground, background)
        if rows[role] < 4.5:
            raise ValueError(f"Faded editor text: {entry} {role}")
    for role, minimum, surface in (
        (
            "LINE_NUMBERS_COLOR",
            4.5,
            read_colour(root, "colors.EDITOR_GUTTER_BACKGROUND"),
        ),
        ("INDENT_GUIDE", 1.35, background),
        ("SELECTED_INDENT_GUIDE", 2.5, background),
    ):
        rows[role] = ratio(read_colour(root, f"colors.{role}"), surface)
        if rows[role] < minimum:
            raise ValueError(f"Faded editor guide/number: {entry} {role}")
    ordinary = read_colour(root, "colors.INDENT_GUIDE")
    selected = read_colour(root, "colors.SELECTED_INDENT_GUIDE")
    if ratio(ordinary, selected) < 1.5:
        raise ValueError(f"Active editor guide blends into ordinary guides: {entry}")
    return rows


def verify_native(folder: Path, result: dict, scheme: bytes, entry: str) -> dict:
    """Require actual numbers and long editor-guide strokes in every native state.

    Selected guides depend on caret scope; their painted count is recorded without
    asserting presence in languages/scopes where JetBrains does not draw one.
    """
    ratios = check(scheme, entry)
    root = ET.fromstring(scheme)
    colours = {
        role: tuple(bytes.fromhex(read_colour(root, f"colors.{role}")[1:]))
        for role in ("LINE_NUMBERS_COLOR", "INDENT_GUIDE", "SELECTED_INDENT_GUIDE")
    }
    probes = {}
    for state, geometry in result["geometry"].items():
        with Image.open(folder / f"{state}.png") as opened:
            image = opened.convert("RGB")
        counts = {}
        for region, roles in (
            ("gutter", ("LINE_NUMBERS_COLOR",)),
            ("editor", ("INDENT_GUIDE", "SELECTED_INDENT_GUIDE")),
        ):
            x, y, width, height = geometry[region]
            if not (0 <= x < image.width and 0 <= y < image.height):
                raise ValueError(
                    "Native editor contrast geometry is outside screenshot"
                )
            patch = image.crop(
                (x, y, min(x + width, image.width), min(y + height, image.height))
            )
            pixels = {
                rgb: count for count, rgb in patch.getcolors(patch.width * patch.height)
            }
            for role in roles:
                counts[role] = pixels.get(colours[role], 0)
        ex, ey, ew, eh = geometry["editor"]
        longest = 0
        for x in range(ex + 4, min(image.width, ex + min(230, ew))):
            run = 0
            for y in range(ey + 4, min(image.height, ey + eh)):
                run = (
                    run + 1 if image.getpixel((x, y)) == colours["INDENT_GUIDE"] else 0
                )
                longest = max(longest, run)
        if longest < 20 or counts["LINE_NUMBERS_COLOR"] < 30:
            raise ValueError(
                f"Native editor contrast paint missing: {state}; guide={longest}, numbers={counts['LINE_NUMBERS_COLOR']}"
            )
        text_probes = {}
        if box := UNUSED_NAME_PROBES.get(result.get("product")):
            colour = read_colour(
                root, "attributes.NOT_USED_ELEMENT_ATTRIBUTES.FOREGROUND"
            )
            rgb = tuple(bytes.fromhex(colour[1:]))
            count = list(image.crop(box).get_flattened_data()).count(rgb)
            if count < 30:
                raise ValueError(f"Native unused-name paint missing: {state}; {count}")
            text_probes["unusedName"] = {
                "bounds": box,
                "expected": colour,
                "exactColourPixels": count,
            }
        if (
            result.get("product") == "RD"
            and entry == "colorSchemes/MorningMist.xml"
        ):
            box = (475, 120, 545, 139)
            colour = read_colour(root, "attributes.CODE_VISION_DEFAULT.FOREGROUND")
            count = list(image.crop(box).get_flattened_data()).count(
                tuple(bytes.fromhex(colour[1:]))
            )
            pixels = list(image.crop(box).get_flattened_data())
            means = [
                sum(pixel[channel] for pixel in pixels) / len(pixels)
                for channel in range(3)
            ]
            # This small label uses subpixel antialiasing with no exact-colour
            # cores. The reviewed native patch averages 241.8–241.95 per channel;
            # the old faint label was 243.3–243.5. Keep a bounded paint guard,
            # separate from the configured 4.5:1 colour contrast requirement.
            if any(not 230 <= mean <= 242.6 for mean in means):
                raise ValueError(
                    f"Native Code Vision paint faded or missing: {state}; {means}"
                )
            text_probes["codeVision"] = {
                "bounds": box,
                "expected": colour,
                "exactColourPixels": count,
                "meanRgb": means,
            }
        probes[state] = {
            "ratios": ratios,
            "exactColourPixels": counts,
            "longestOrdinaryGuideStroke": longest,
            "textProbes": text_probes,
        }
    if len(probes) != 3:
        raise ValueError("Native editor contrast requires all three states")
    return probes
