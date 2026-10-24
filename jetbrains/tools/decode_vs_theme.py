"""Decode Visual Studio theme colours from the pkgdef files of an installed Visual Studio.

Visual Studio stores each theme category as a binary "Data" value under
[$RootKey$\\Themes\\{theme-guid}\\CategoryName]. Layout (little endian):

    int32  total length of the record
    int32  version (11)
    int32  category count
    per category:
        16 bytes  category GUID
        int32     colour count
        per colour:
            int32  name length, then that many ASCII bytes
            byte   background type, followed by 4 value bytes unless the type is 0
            byte   foreground type, followed by 4 value bytes unless the type is 0

Colour types (__VSCOLORTYPE): 0 invalid (no value bytes), 1 raw RGBA, 2 colour index,
3 system colour, 4 VS colour, 5 automatic, 6 track foreground, 7 track background.
Observed in the 18.10.1 files: type 5 is written as 05,00,00,00,00, so every type but 0
carries four bytes.

The decoder refuses a record whose declared length does not equal the bytes it consumed,
so a wrong guess about the layout fails instead of producing plausible garbage.

Usage:
    python decode_vs_theme.py <VS install dir> <output dir>
"""

from __future__ import annotations

import json
import re
import struct
import sys
import uuid
from pathlib import Path

SECTION = re.compile(r"^\[\$RootKey\$\\Themes\\\{(?P<theme>[0-9a-fA-F-]{36})\}(?:\\(?P<category>[^\]]+))?\]\s*$")
DEFAULT_VALUE = re.compile(r'^@="(?P<name>.*)"\s*$')
TYPE_NAMES = {
    0: "invalid",
    1: "raw",
    2: "colorindex",
    3: "syscolor",
    4: "vscolor",
    5: "automatic",
    6: "track-foreground",
    7: "track-background",
}


class FormatError(Exception):
    pass


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig", errors="replace")


def logical_lines(text: str):
    """Yield lines with pkgdef backslash continuations joined."""
    pending = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        if pending or stripped.lstrip().startswith('"Data"=hex:'):
            if stripped.endswith("\\"):
                pending += stripped[:-1].strip()
                continue
            yield pending + stripped.strip()
            pending = ""
        else:
            yield stripped
    if pending:
        yield pending


def read_colour(data: bytes, pos: int) -> tuple[dict | None, int]:
    colour_type = data[pos]
    pos += 1
    if colour_type not in TYPE_NAMES:
        raise FormatError(f"unknown colour type {colour_type} at offset {pos - 1}")
    if colour_type == 0:
        return None, pos
    r, g, b, a = data[pos : pos + 4]
    pos += 4
    if colour_type == 1:
        return {"type": "raw", "rgb": f"{r:02X}{g:02X}{b:02X}", "alpha": a}, pos
    return {"type": TYPE_NAMES[colour_type], "value": struct.unpack_from("<I", data, pos - 4)[0]}, pos


def decode_record(data: bytes) -> list[dict]:
    total, version, category_count = struct.unpack_from("<III", data, 0)
    if total != len(data):
        raise FormatError(f"declared length {total} differs from actual {len(data)}")
    if version != 11:
        raise FormatError(f"unexpected version {version}")
    pos = 12
    categories = []
    for _ in range(category_count):
        category_guid = str(uuid.UUID(bytes_le=data[pos : pos + 16]))
        pos += 16
        (colour_count,) = struct.unpack_from("<I", data, pos)
        pos += 4
        colours = {}
        for _ in range(colour_count):
            (name_length,) = struct.unpack_from("<I", data, pos)
            pos += 4
            name = data[pos : pos + name_length].decode("ascii")
            pos += name_length
            background, pos = read_colour(data, pos)
            foreground, pos = read_colour(data, pos)
            colours[name] = {"background": background, "foreground": foreground}
        categories.append({"guid": category_guid, "colours": colours})
    if pos != len(data):
        raise FormatError(f"consumed {pos} of {len(data)} bytes")
    return categories


def decode_pkgdef(path: Path, themes: dict, names: dict, root: Path) -> int:
    theme = category = None
    records = 0
    for line in logical_lines(read_text(path)):
        match = SECTION.match(line)
        if match:
            theme = match["theme"].lower()
            category = match["category"]
            continue
        if line.startswith("["):
            theme = category = None
            continue
        if theme is None:
            continue
        if category is None:
            name = DEFAULT_VALUE.match(line)
            if name:
                names[theme] = name["name"]
            continue
        if not line.startswith('"Data"=hex:'):
            continue
        data = bytes(int(part, 16) for part in line[len('"Data"=hex:') :].split(",") if part.strip())
        try:
            decoded = decode_record(data)
        except (FormatError, struct.error, UnicodeDecodeError, IndexError) as error:
            raise FormatError(f"{path}: [{category}] {error}") from error
        records += 1
        for entry in decoded:
            target = themes.setdefault(theme, {}).setdefault(category, {"guid": entry["guid"], "colours": {}})
            for colour_name, value in entry["colours"].items():
                value["source"] = str(path.relative_to(root))
                target["colours"][colour_name] = value
    return records


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    root = Path(sys.argv[1])
    out = Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    themes: dict = {}
    names: dict = {}
    total = 0
    for path in sorted((root / "Common7" / "IDE").rglob("*.pkgdef")):
        try:
            text = path.read_bytes()
        except OSError:
            continue
        if b"\\Themes\\{" not in text and b"\\\x00T\x00h\x00e\x00m\x00e\x00s\x00" not in text:
            continue
        total += decode_pkgdef(path, themes, names, root)
    for theme, categories in themes.items():
        label = re.sub(r"[^A-Za-z0-9]+", "", names.get(theme, "")) or theme
        document = {"theme": names.get(theme, theme), "guid": theme, "categories": categories}
        (out / f"{label}.json").write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8", newline="\n")
        colours = sum(len(c["colours"]) for c in categories.values())
        print(f"{label:28} {theme}  categories={len(categories):4}  colours={colours}")
    print(f"records decoded: {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
