"""Extract only the Fonts and Colors section from a Visual Studio .vssettings export.

A full export holds every Visual Studio setting; this script reads the FontsAndColors subtree and nothing
else, and writes it as JSON. Colours in the file are COLORREF values, 0xTTBBGGRR: TT = 00 is a plain
colour (stored blue-green-red), 01 is a system colour index, 02 is "automatic".

Usage:
    python extract_vssettings_colours.py <export.vssettings> <output.json>
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def colour(text: str | None) -> dict | None:
    if text is None:
        return None
    value = int(text, 16)
    kind = value >> 24
    if kind == 0:
        red, green, blue = value & 0xFF, (value >> 8) & 0xFF, (value >> 16) & 0xFF
        return {"type": "raw", "rgb": f"{red:02X}{green:02X}{blue:02X}"}
    names = {1: "system", 2: "automatic"}
    return {"type": names.get(kind, f"type-{kind}"), "value": value & 0xFFFFFF}


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    root = ET.parse(sys.argv[1]).getroot()
    sections = list(root.iter("FontsAndColors"))
    if not sections:
        print("no FontsAndColors section in this export", file=sys.stderr)
        return 1
    result = []
    for section in sections:
        # <Theme Id=.../> is an empty sibling of <Categories>, not their parent.
        theme = section.find("Theme")
        theme_id = theme.get("Id") if theme is not None else None
        categories = []
        for category in section.iter("Category"):
            items = {
                item.get("Name"): {
                    "foreground": colour(item.get("Foreground")),
                    "background": colour(item.get("Background")),
                    "bold": item.get("BoldFont") == "Yes",
                }
                for item in category.iter("Item")
            }
            categories.append(
                {
                    "guid": category.get("GUID"),
                    "font": category.get("FontName"),
                    "fontSize": category.get("FontSize"),
                    "fontIsDefault": category.get("FontIsDefault"),
                    "items": items,
                }
            )
        result.append({"themeId": theme_id, "categories": categories})
    Path(sys.argv[2]).write_text(json.dumps(result, indent=1), encoding="utf-8", newline="\n")
    for entry in result:
        print(f"theme {entry['themeId']}")
        for category in entry["categories"]:
            print(f"  {category['guid']}  font={category['font']} {category['fontSize']}")
            for name, item in category["items"].items():
                shown = {part: (value["rgb"] if value and value["type"] == "raw" else (value or {}).get("type")) for part, value in item.items() if part != "bold"}
                print(f"      {name:42} fg={shown['foreground']}  bg={shown['background']}  bold={item['bold']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
