"""Generate the Rider theme, the editor colour scheme, the colour table and the preview image from the maps in src/.

The colour table and preview are reports, written to the report folder (tools/report_paths.py) unless --docs names
another folder.

Every colour in a map is an expression, never a bare guess:

    #RRGGBB or #RRGGBBAA             a literal (the map entry must say why)
    vs:Category|Name                 background of a decoded Visual Studio theme colour
    vs:Category|Name|fg              its foreground
    vs:Category|Name@51              the same colour with its alpha replaced (0..255)
    user:Item name[|fg]              a personal colour from a Fonts and Colors export (overrides only)
    $token                           another colour token defined in the same theme map
    A over B [over C]                alpha-composite A on B, the way Visual Studio paints Fluent fills

Visual Studio values come from data/<vs version>/ when the decoded theme data is present (run
decode_vs_theme.py against an installed Visual Studio). Every value a build uses is also written to
src/vs-values/, a small snapshot that is committed, so the plugin builds on a machine without Visual Studio.

The build fails when
  * a Visual Studio colour named in a map exists neither in the decoded data nor in the snapshot,
  * an entry marked "measured" does not resolve to the pixel value recorded beside it,
  * a Rider UI key, colour token or scheme attribute is not known to the installed Rider
    (Rider ignores unknown keys silently, so a typo would otherwise ship unnoticed).

Usage:
    python generate.py <repo root> <rider resources dir> <vs data dir> <output resources dir> [--overrides FILE]
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

from language_mappings import MappingError, expand_roles
from report_paths import source_reports

STATUSES = {
    "measured": "measured in a Visual Studio screenshot; the generated value is asserted equal to the sample",
    "vs-data": "value decoded from the installed Visual Studio theme files; which element uses it is by token name, not pixel-checked",
    "inferred": "no decoded Visual Studio value exists; the reasoning is given in the row",
    "user-export": "a personal customisation from a Fonts and Colors export, applied through an overrides file",
    "ms-blue": "follows the colour of the Visual Studio 2019/2022 Blue theme, which inspired this theme family",
    "designed": "chosen for this theme; not a Visual Studio value",
    "vs-default": "Visual Studio's default editor colour, which the Blue theme does not override; the value is the one decoded from Visual Studio's Light theme data for Morning Mist",
    "inherited": "JetBrains' base value, restated so the contrast guard can check it",
}

# Fonts and Colors categories in a .vssettings export, most specific first.
USER_CATEGORY_ORDER = [
    "{75A05685-00A8-4DED-BAE5-E7A50BFA929A}",
    "{E0187991-B458-4F7E-8CA9-42C9A573B56C}",
    "{58E96763-1D3B-4E05-B6BA-FF7115FD0B7B}",
]


class BuildError(Exception):
    pass


@dataclass(frozen=True)
class Colour:
    r: int
    g: int
    b: int
    a: int = 255

    @property
    def rgb(self) -> str:
        return f"{self.r:02X}{self.g:02X}{self.b:02X}"

    def theme_text(self) -> str:
        return f"#{self.rgb}" if self.a == 255 else f"#{self.rgb}{self.a:02X}"

    def shown(self) -> str:
        return self.rgb if self.a == 255 else f"{self.rgb}/{self.a}"

    def over(self, backdrop: Colour) -> Colour:
        if backdrop.a != 255:
            raise BuildError("the backdrop of a composite must be opaque")
        alpha = self.a / 255

        def blend(top: int, bottom: int) -> int:
            return int(top * alpha + bottom * (1 - alpha) + 0.5)

        return Colour(blend(self.r, backdrop.r), blend(self.g, backdrop.g), blend(self.b, backdrop.b))


def parse_rgb(rgb: str, alpha: int = 255) -> Colour:
    return Colour(int(rgb[0:2], 16), int(rgb[2:4], 16), int(rgb[4:6], 16), alpha)


class VsTheme:
    """Visual Studio colours: the decoded theme data when present, otherwise the committed snapshot."""

    def __init__(self, data_dir: Path, base: str, tint: str | None, snapshot: Path) -> None:
        self.label = base if tint is None else f"{base} + {tint}"
        self.snapshot_path = snapshot
        self.used: dict[str, dict] = {}
        self.categories: dict | None = None
        self.origin: dict[tuple[str, str], str] = {}
        if (data_dir / f"{base}.json").exists():
            self.categories = json.loads((data_dir / f"{base}.json").read_text(encoding="utf-8"))["categories"]
            for category, body in self.categories.items():
                for name in body["colours"]:
                    self.origin[(category, name)] = base
            if tint is not None:
                overlay = json.loads((data_dir / f"{tint}.json").read_text(encoding="utf-8"))["categories"]
                for category, body in overlay.items():
                    target = self.categories.setdefault(category, {"colours": {}})
                    for name, value in body["colours"].items():
                        target["colours"][name] = value
                        self.origin[(category, name)] = tint
        elif snapshot.exists():
            self.saved = json.loads(snapshot.read_text(encoding="utf-8"))["values"]
        else:
            raise BuildError(f"no Visual Studio data in {data_dir} and no snapshot at {snapshot}; run decode_vs_theme.py first")
        self.user: dict[str, dict] = {}
        export = data_dir / "user-fonts-and-colors.json"
        if export.exists():
            categories = {c["guid"]: c for entry in json.loads(export.read_text(encoding="utf-8")) for c in entry["categories"]}
            for guid in reversed(USER_CATEGORY_ORDER):
                self.user.update(categories.get(guid, {}).get("items", {}))

    @property
    def from_snapshot(self) -> bool:
        return self.categories is None

    def lookup(self, category: str, name: str, part: str) -> tuple[Colour, str]:
        key = f"{category}|{name}|{part}"
        if self.categories is None:
            saved = self.saved.get(key)
            if saved is None:
                raise BuildError(f"'{key}' is not in the snapshot {self.snapshot_path.name}; decode Visual Studio to add it")
            self.used[key] = saved
            return parse_rgb(saved["rgb"], saved["alpha"]), saved["source"]
        try:
            entry = self.categories[category]["colours"][name]
        except KeyError:
            raise BuildError(f"Visual Studio theme data has no colour '{category}|{name}'") from None
        value = entry["foreground" if part == "fg" else "background"]
        if value is None or value.get("type") != "raw":
            kind = "none" if value is None else value["type"]
            raise BuildError(f"'{category}|{name}|{part}' is not a raw colour in the Visual Studio data (it is {kind})")
        where = f"{self.origin[(category, name)]}: {entry['source']}"
        self.used[key] = {"rgb": value["rgb"], "alpha": value["alpha"], "source": where}
        return parse_rgb(value["rgb"], value["alpha"]), where

    def lookup_user(self, name: str, part: str) -> tuple[Colour, str]:
        item = self.user.get(name)
        value = None if item is None else item["foreground" if part == "fg" else "background"]
        if value is None or value.get("type") != "raw":
            raise BuildError(f"the Fonts and Colors export has no raw {part} colour for '{name}'")
        return parse_rgb(value["rgb"]), "Fonts and Colors export (.vssettings)"

    def write_snapshot(self) -> None:
        if self.from_snapshot:
            return
        document = {
            "about": "Visual Studio colour values used by this theme, decoded from an installed Visual Studio. Generated; do not edit.",
            "theme": self.label,
            "values": dict(sorted(self.used.items())),
        }
        self.snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        self.snapshot_path.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n")


@dataclass
class Resolved:
    colour: Colour
    vs_terms: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)


class Resolver:
    def __init__(self, vs: VsTheme, tokens: dict[str, dict] | None = None) -> None:
        self.vs = vs
        self.tokens = tokens or {}
        self.cache: dict[str, Resolved] = {}
        self.active: list[str] = []

    def term(self, text: str) -> Resolved:
        text = text.strip()
        alpha_override = None
        match = re.fullmatch(r"(.+)@(\d{1,3})", text)
        if match and not text.startswith("#"):
            text, alpha_override = match[1], int(match[2])
            if alpha_override > 255:
                raise BuildError(f"alpha {alpha_override} is out of range in '{text}'")
        resolved = self._term(text)
        if alpha_override is not None:
            colour = Colour(resolved.colour.r, resolved.colour.g, resolved.colour.b, alpha_override)
            terms = [f"{term} at alpha {alpha_override}" for term in resolved.vs_terms]
            return Resolved(colour, terms, resolved.sources)
        return resolved

    def _term(self, text: str) -> Resolved:
        if text.startswith("#"):
            digits = text[1:]
            if not re.fullmatch(r"[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?", digits):
                raise BuildError(f"bad literal colour '{text}'")
            alpha = int(digits[6:8], 16) if len(digits) == 8 else 255
            return Resolved(parse_rgb(digits, alpha))
        if text.startswith("$"):
            name = text[1:]
            if name not in self.tokens:
                raise BuildError(f"'{text}' refers to a token this map does not define")
            if name in self.active:
                raise BuildError(f"token cycle through '{name}'")
            if name not in self.cache:
                self.active.append(name)
                self.cache[name] = self.resolve(self.tokens[name]["value"])
                self.active.pop()
            return self.cache[name]
        if text.startswith("user:"):
            parts = text[5:].split("|")
            if len(parts) not in (1, 2) or (len(parts) == 2 and parts[1] != "fg"):
                raise BuildError(f"bad Fonts and Colors reference '{text}'")
            part = "fg" if len(parts) == 2 else "bg"
            colour, where = self.vs.lookup_user(parts[0], part)
            suffix = " (foreground)" if part == "fg" else ""
            return Resolved(colour, [f"user: {parts[0]}{suffix} = {colour.rgb}"], [where])
        if text.startswith("vs:"):
            parts = text[3:].split("|")
            if len(parts) not in (2, 3) or (len(parts) == 3 and parts[2] != "fg"):
                raise BuildError(f"bad Visual Studio reference '{text}'")
            part = "fg" if len(parts) == 3 else "bg"
            colour, where = self.vs.lookup(parts[0], parts[1], part)
            suffix = " (foreground)" if part == "fg" else ""
            return Resolved(colour, [f"{parts[0]}.{parts[1]}{suffix} = {colour.shown()}"], [where])
        raise BuildError(f"cannot read colour expression term '{text}'")

    def resolve(self, expression: str) -> Resolved:
        terms = [self.term(part) for part in expression.split(" over ")]
        colour = terms[-1].colour
        for upper in reversed(terms[:-1]):
            colour = upper.colour.over(colour)
        vs_terms: list[str] = []
        sources: list[str] = []
        for item in terms:
            vs_terms += [t for t in item.vs_terms if t not in vs_terms]
            sources += [s for s in item.sources if s not in sources]
        return Resolved(colour, vs_terms, sources)


def _is_group(value: object) -> bool:
    return isinstance(value, dict) and not all(child.startswith("os.") for child in value)


def flatten_ui(node: dict, prefix: str = "") -> set[str]:
    keys: set[str] = set()
    for key, value in node.items():
        path = f"{prefix}.{key}" if prefix else key
        if _is_group(value):
            keys |= flatten_ui(value, path)
        else:
            keys.add(path)
    return keys


def set_ui(node: dict, dotted: str, value: str | float) -> None:
    """Set a dotted UI key inside the nested theme structure, reusing the nesting that is already there."""
    if dotted in node and not _is_group(node[dotted]):
        node[dotted] = value
        return
    parts = dotted.split(".")
    for cut in range(len(parts) - 1, 0, -1):
        head = ".".join(parts[:cut])
        if head in node and _is_group(node[head]):
            set_ui(node[head], ".".join(parts[cut:]), value)
            return
    node[dotted] = value


def known_ui_keys(rider: Path) -> set[str]:
    keys: set[str] = set()
    for metadata in rider.rglob("*.themeMetadata.json"):
        document = json.loads(metadata.read_text(encoding="utf-8"))
        keys |= {entry["key"] for entry in document.get("ui", [])}
    for theme in rider.rglob("*.theme.json"):
        keys |= flatten_ui(json.loads(theme.read_text(encoding="utf-8")).get("ui", {}))
    return keys


def deprecated_ui_keys(rider: Path) -> dict[str, str]:
    """Keys the platform metadata marks deprecated, with its reason. The IDE ignores them, so mapping one is a no-op."""
    deprecated: dict[str, str] = {}
    for metadata in rider.rglob("*.themeMetadata.json"):
        document = json.loads(metadata.read_text(encoding="utf-8"))
        for entry in document.get("ui", []):
            if entry.get("deprecated"):
                deprecated[entry["key"]] = entry.get("description", "")
    return deprecated


def scheme_elements(root: ET.Element) -> list[ET.Element]:
    """A scheme file holds one <scheme>; the platform's DefaultColorSchemesManager.xml holds several."""
    return [root] if root.tag == "scheme" else list(root.iter("scheme"))


def known_scheme_names(rider: Path) -> tuple[set[str], set[str]]:
    colours: set[str] = set()
    attributes: set[str] = set()
    for scheme in rider.rglob("*.xml"):
        try:
            root = ET.parse(scheme).getroot()
        except ET.ParseError:
            continue
        for element in scheme_elements(root):
            colours |= {o.get("name") for o in element.findall("./colors/option")}
            attributes |= {o.get("name") for o in element.findall("./attributes/option")}
    return colours, attributes


def platform_default_attributes(rider: Path, scheme_name: str = "Default") -> dict[str, dict[str, str]]:
    """Attributes of a platform default scheme ("Default" for light, "Darcula" for dark), which a bundled scheme
    inherits for everything it does not set."""
    path = rider / "platform" / "DefaultColorSchemesManager.xml"
    if not path.exists():
        # Without it the inherited check would silently check nothing.
        raise BuildError(f"{path} is missing; the inherited contrast check needs the platform default schemes")
    for element in scheme_elements(ET.parse(path).getroot()):
        if element.get("name") == scheme_name:
            attributes = {
                option.get("name"): {o.get("name"): o.get("value") for o in option.findall("./value/option")}
                for option in element.findall("./attributes/option")
            }
            if not attributes:
                # An empty default would turn the inherited check into a no-op.
                raise BuildError(f"platform default scheme '{scheme_name}' in {path.name} has no attributes")
            return attributes
    raise BuildError(f"platform default scheme '{scheme_name}' is not in {path.name}")


@dataclass
class Row:
    element: str
    vs_value: str
    source: str
    rider_key: str
    rider_value: str
    status: str
    note: str = ""


def check_status(key: str, entry: dict) -> None:
    if entry.get("status") not in STATUSES:
        raise BuildError(f"'{key}' needs a status of {sorted(STATUSES)}")
    if not entry.get("element"):
        raise BuildError(f"'{key}' needs an 'element' description")


def check_measured(key: str, entry: dict, colour: Colour) -> None:
    if entry["status"] != "measured":
        return
    if "measured" not in entry or "sample" not in entry:
        raise BuildError(f"'{key}' is marked measured but records no pixel value and sample")
    if colour.a != 255 or colour.rgb != entry["measured"].upper():
        raise BuildError(f"'{key}' resolves to {colour.theme_text()} but the screenshot sample is #{entry['measured']}")


def luminance(colour: Colour) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(colour.r) + 0.7152 * channel(colour.g) + 0.0722 * channel(colour.b)


def contrast_ratio(a: Colour, b: Colour) -> float:
    """WCAG 2.x contrast ratio."""
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def apca_lc(text: Colour, background: Colour) -> float:
    """APCA 0.0.98G-4g lightness contrast. Reported only: WCAG 3 has not adopted a contrast method."""

    def y(colour: Colour) -> float:
        weights = (0.2126729, 0.7151522, 0.0721750)
        lum = sum(k * (v / 255) ** 2.4 for k, v in zip(weights, (colour.r, colour.g, colour.b), strict=True))
        return lum if lum > 0.022 else lum + (0.022 - lum) ** 1.414

    text_y, back_y = y(text), y(background)
    if abs(back_y - text_y) < 0.0005:
        return 0.0
    if back_y > text_y:
        s = (back_y**0.56 - text_y**0.57) * 1.14
        return 0.0 if s < 0.1 else (s - 0.027) * 100
    s = (back_y**0.65 - text_y**0.62) * 1.14
    return 0.0 if s > -0.1 else (s + 0.027) * 100


def check_contrast(key: str, entry: dict, colour: Colour, resolver: Resolver, default_on: str | None = None) -> str:
    """Fail when a colour declared to sit on a background is below its minimum; return the figures for the table.

    "on" names the background expression; "minContrast" defaults to 4.5 (WCAG 2.2 text) and is 3 for icons and
    other non-text. An entry without "on" (and no default) is not a foreground and is not checked.
    """
    if entry.get("under"):
        # The entry is a fill; "under" names the text or icon colour Rider draws on it.
        as_foreground = {key: value for key, value in entry.items() if key != "under"} | {"on": f"#{colour.theme_text()[1:]}"}
        return check_contrast(key, as_foreground, resolver.resolve(entry["under"]).colour, resolver)
    background_expression = entry.get("on", default_on)
    if background_expression is None:
        return ""
    background = resolver.resolve(background_expression).colour
    if colour.a != 255 or background.a != 255:
        raise BuildError(f"{key}: contrast needs opaque colours; composite them first")
    ratio = contrast_ratio(colour, background)
    lc = apca_lc(colour, background)
    minimum = float(entry.get("minContrast", 4.5))
    figures = f"contrast {ratio:.1f}:1, APCA Lc {lc:.0f} on {background.rgb}"
    waiver = entry.get("contrastWaiver")
    if ratio < minimum:
        if waiver:
            # A deliberate exception, such as an exact Visual Studio colour in the faithful theme: shown, not hidden.
            return f"{figures}; below {minimum}:1, kept on purpose: {waiver}"
        raise BuildError(f"{key}: {colour.rgb} on {background.rgb} is {ratio:.2f}:1, below {minimum}:1")
    min_lc = entry.get("minLc")
    if min_lc is not None and abs(lc) < float(min_lc):
        raise BuildError(f"{key}: {colour.rgb} on {background.rgb} is APCA Lc {abs(lc):.0f}, below Lc {min_lc}")
    return figures


def make_row(key: str, entry: dict, resolved: Resolved, shown: str, figures: str = "") -> Row:
    note = entry.get("sample") or entry.get("note") or ""
    return Row(
        element=entry["element"],
        vs_value="; ".join(resolved.vs_terms) or "none",
        source=" + ".join(resolved.sources) or "no Visual Studio source",
        rider_key=key,
        rider_value=shown,
        status=entry["status"],
        note="; ".join(part for part in (note, figures) if part),
    )


def merge_overrides(target: dict, overrides: dict) -> None:
    """Lay personal overrides over a map. A value of null removes the entry."""
    for section, entries in overrides.items():
        bucket = target.setdefault(section, {})
        for key, entry in entries.items():
            if entry is None:
                bucket.pop(key, None)
            else:
                bucket[key] = entry


def build_theme(theme_map: dict, rider: Path, vs: VsTheme, out: Path) -> tuple[list[Row], dict[str, str]]:
    base = json.loads((rider / theme_map["baseThemeFile"]).read_text(encoding="utf-8"))
    theme = copy.deepcopy(base)
    theme.pop("commentary", None)
    theme["name"] = theme_map["name"]
    theme["author"] = theme_map["author"]
    theme["dark"] = theme_map["dark"]
    theme["parentTheme"] = theme_map["parentTheme"]
    theme["editorScheme"] = theme_map["editorScheme"]

    resolver = Resolver(vs, theme_map["colors"])
    ui_keys = known_ui_keys(rider)
    deprecated = deprecated_ui_keys(rider)
    rows: list[Row] = []
    palette: dict[str, str] = {}

    for token, entry in theme_map["colors"].items():
        check_status(token, entry)
        if token not in base["colors"]:
            raise BuildError(f"colour token '{token}' does not exist in {theme_map['baseThemeFile']}")
        resolved = resolver.term(f"${token}")
        check_measured(token, entry, resolved.colour)
        figures = check_contrast(token, entry, resolved.colour, resolver)
        theme["colors"][token] = resolved.colour.theme_text()
        palette[token] = resolved.colour.theme_text()
        rows.append(make_row(f"colors.{token}", entry, resolved, resolved.colour.theme_text(), figures))

    for key, entry in theme_map["ui"].items():
        check_status(key, entry)
        if key not in ui_keys:
            raise BuildError(f"UI key '{key}' is not known to the installed Rider (metadata and bundled themes)")
        if key in deprecated:
            raise BuildError(f"UI key '{key}' is deprecated in the installed Rider's metadata, so it has no effect: {deprecated[key]}")
        if "raw" in entry:
            # A size or other non-colour value; "vsValue" says what was measured in Visual Studio.
            set_ui(theme["ui"], key, entry["raw"])
            note = entry.get("sample") or entry.get("note") or ""
            rows.append(Row(entry["element"], entry.get("vsValue", "none"), "screenshot", f"ui.{key}", str(entry["raw"]), entry["status"], note))
            continue
        resolved = resolver.resolve(entry["value"])
        check_measured(key, entry, resolved.colour)
        figures = check_contrast(key, entry, resolved.colour, resolver)
        set_ui(theme["ui"], key, resolved.colour.theme_text())
        palette[key] = resolved.colour.theme_text()
        rows.append(make_row(f"ui.{key}", entry, resolved, resolved.colour.theme_text(), figures))

    # icons.ColorPalette with a raw hex key replaces that colour in every SVG icon (IntelliJ SDK, "Customizing Icons").
    for source_colour, entry in theme_map.get("iconPalette", {}).items():
        check_status(source_colour, entry)
        named = base.get("icons", {}).get("ColorPalette", {})
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", source_colour) and source_colour not in named:
            raise BuildError(f"icon palette key '{source_colour}' must be a #RRGGBB colour or a key of the base theme's icon palette")
        resolved = resolver.resolve(entry["value"])
        if resolved.colour.a != 255:
            raise BuildError(f"icon palette value for '{source_colour}' must be opaque")
        check_measured(source_colour, entry, resolved.colour)
        figures = check_contrast(f"icon {source_colour}", entry, resolved.colour, resolver)
        theme.setdefault("icons", {}).setdefault("ColorPalette", {})[source_colour] = resolved.colour.theme_text()
        palette["icon"] = resolved.colour.theme_text()
        rows.append(make_row(f"icons.ColorPalette.{source_colour}", entry, resolved, resolved.colour.theme_text(), figures))

    target = out / "themes" / f"{theme_map['fileName']}.theme.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(theme, indent=2) + "\n", encoding="utf-8", newline="\n")
    return rows, palette


def build_scheme(scheme_map: dict, rider: Path, vs: VsTheme, out: Path, *,
                 contract: Path | None = None, evidence: Path | None = None) -> tuple[list[Row], dict[str, str]]:
    tree = ET.parse(rider / scheme_map["baseSchemeFile"])
    root = tree.getroot()
    root.set("name", scheme_map["name"])
    colours_node = root.find("colors")
    attributes_node = root.find("attributes")
    known_colours, known_attributes = known_scheme_names(rider)
    resolver = Resolver(vs)
    if scheme_map.get("languageMappings"):
        if contract is None or evidence is None:
            raise BuildError("language mappings require --language-evidence from the installed IDE inventory")
        try:
            known_attributes |= expand_roles(scheme_map, root, contract, evidence,
                lambda expression: resolver.resolve(expression).colour.theme_text())
        except MappingError as error:
            raise BuildError(str(error)) from error
    rows: list[Row] = []

    def opaque(key: str, expression: str) -> Resolved:
        resolved = resolver.resolve(expression)
        if resolved.colour.a != 255:
            raise BuildError(f"scheme value '{key}' must be opaque; composite it over its backdrop")
        return resolved

    for key, entry in scheme_map["colors"].items():
        check_status(key, entry)
        if key not in known_colours:
            raise BuildError(f"scheme colour '{key}' is not used by any scheme bundled with the installed Rider")
        resolved = opaque(key, entry["value"])
        check_measured(key, entry, resolved.colour)
        option = colours_node.find(f"./option[@name='{key}']")
        if option is None:
            option = ET.SubElement(colours_node, "option", {"name": key})
        option.set("value", resolved.colour.rgb)
        rows.append(make_row(f"colors/{key}", entry, resolved, resolved.colour.rgb))

    for key, entry in scheme_map["attributes"].items():
        check_status(key, entry)
        if key not in known_attributes:
            raise BuildError(f"scheme attribute '{key}' is not used by any scheme bundled with the installed Rider")
        option = attributes_node.find(f"./option[@name='{key}']")
        if option is None:
            option = ET.SubElement(attributes_node, "option", {"name": key})
        option.attrib.pop("baseAttributes", None)
        value = option.find("value")
        if value is None:
            value = ET.SubElement(option, "value")
        for part in ("FOREGROUND", "BACKGROUND", "EFFECT_COLOR", "ERROR_STRIPE_COLOR"):
            if part not in entry:
                continue
            existing = value.find(f"./option[@name='{part}']")
            if entry[part] is None:
                if existing is not None:
                    value.remove(existing)
                continue
            resolved = opaque(f"{key}.{part}", entry[part])
            figures = ""
            if part == "FOREGROUND":
                check_measured(key, entry, resolved.colour)
                # A scheme opts in with "defaultOn" (the editor background); an attribute with its own
                # background is checked against that instead, including one it keeps from the base scheme.
                on = entry.get("on")
                if on is None and "defaultOn" in scheme_map:
                    if "BACKGROUND" in entry:
                        on = entry["BACKGROUND"] or scheme_map["defaultOn"]
                    else:
                        kept = value.find("./option[@name='BACKGROUND']")
                        kept_value = kept.get("value") if kept is not None else ""
                        on = f"#{int(kept_value, 16):06X}" if kept_value else scheme_map["defaultOn"]
                figures = check_contrast(key, {**entry, "on": on}, resolved.colour, resolver) if on else ""
            if existing is None:
                existing = ET.SubElement(value, "option", {"name": part})
            existing.set("value", resolved.colour.rgb)
            rows.append(make_row(f"attributes/{key}.{part}", entry, resolved, resolved.colour.rgb, figures))
        for part in ("FONT_TYPE", "EFFECT_TYPE"):
            if part in entry:
                existing = value.find(f"./option[@name='{part}']")
                if existing is None:
                    existing = ET.SubElement(value, "option", {"name": part})
                existing.set("value", str(entry[part]))

    if "defaultOn" in scheme_map:
        # Inherited attributes are checked too: a colour nobody chose must still be readable. Exemptions need a reason.
        exempt = scheme_map.get("contrastExempt", {})
        default_background = resolver.resolve(scheme_map["defaultOn"]).colour
        failures = []
        # The effective attributes: the platform default, overlaid by what the scheme file sets.
        # The scheme inherits from its XML parent; a map that names another platform default contradicts it.
        parent = root.get("parent_scheme") or "Default"
        if scheme_map.get("platformDefault", parent) != parent:
            raise BuildError(f"platformDefault '{scheme_map['platformDefault']}' contradicts the base scheme's parent_scheme '{parent}'")
        effective = platform_default_attributes(rider, parent)
        for option in attributes_node.findall("./option"):
            effective[option.get("name")] = {o.get("name"): o.get("value") for o in option.findall("./value/option")}
        for name, fields in effective.items():
            mapped = scheme_map["attributes"].get(name)
            # A mapped foreground was checked where it was set; a mapped background with a foreground the
            # scheme keeps (or a mapping of neither) still needs this check.
            if name in exempt or (mapped is not None and ("FOREGROUND" in mapped or mapped.get("contrastWaiver"))):
                continue
            if not fields.get("FOREGROUND"):
                continue
            fg = parse_rgb(f"{int(fields['FOREGROUND'], 16):06X}")
            bg = parse_rgb(f"{int(fields['BACKGROUND'], 16):06X}") if fields.get("BACKGROUND") else default_background
            ratio = contrast_ratio(fg, bg)
            if ratio < 4.5:
                failures.append(f"{name} {fg.rgb} on {bg.rgb} {ratio:.2f}:1")
        if failures:
            raise BuildError(f"{len(failures)} inherited scheme foregrounds are below 4.5:1; map or exempt them: " + "; ".join(failures))

        # Rider keeps syntax colours on the caret line and inside a selection. The caret line is where one reads, so
        # it must keep text at 4.5:1; a selection is a passing state and must keep it at 3:1 (WCAG's floor for large
        # text and UI components, applied here as a design rule). Waived and exempt attributes are skipped.
        fills = {
            "CARET_ROW_COLOR": 4.5,
            "SELECTION_BACKGROUND": 3.0,
        }
        fill_failures = []
        if scheme_map.get("fillRuleWaiver"):
            # A faithful copy keeps its source's caret line and selection; the reason goes into the colour table.
            rows.append(Row(f"Caret-line and selection rule not applied: {scheme_map['fillRuleWaiver']}", "none", "", "colors/SELECTION_BACKGROUND", "", "inherited"))
            fills = {}
        for fill_name, minimum in fills.items():
            option = colours_node.find(f"./option[@name='{fill_name}']")
            if option is None or not option.get("value"):
                continue
            fill = parse_rgb(f"{int(option.get('value'), 16):06X}")
            for name, fields in effective.items():
                entry = scheme_map["attributes"].get(name, {})
                if name in exempt or entry.get("contrastWaiver") or fields.get("BACKGROUND") or not fields.get("FOREGROUND"):
                    continue
                if name in scheme_map["attributes"] and entry.get("FOREGROUND"):
                    fg = opaque(f"{name}.FOREGROUND", entry["FOREGROUND"]).colour
                else:
                    fg = parse_rgb(f"{int(fields['FOREGROUND'], 16):06X}")
                if contrast_ratio(fg, fill) < minimum:
                    fill_failures.append(f"{name} {fg.rgb} on {fill_name} {fill.rgb} {contrast_ratio(fg, fill):.2f}:1 (needs {minimum})")
        # Highlights that set only a background (identifier under caret, search results, ...) carry plain text.
        text_fields = effective.get("TEXT", {})
        plain = parse_rgb(f"{int(text_fields.get('FOREGROUND') or '000000', 16):06X}")
        for name, fields in effective.items():
            if name in exempt or not fields.get("BACKGROUND") or fields.get("FOREGROUND") or name == "TEXT":
                continue
            fill = parse_rgb(f"{int(fields['BACKGROUND'], 16):06X}")
            if contrast_ratio(plain, fill) < 4.5:
                fill_failures.append(f"plain text {plain.rgb} on {name} {fill.rgb} {contrast_ratio(plain, fill):.2f}:1 (needs 4.5)")
        if fill_failures:
            raise BuildError(f"{len(fill_failures)} syntax colours are too weak on the caret line or selection: " + "; ".join(fill_failures))
        for name, reason in exempt.items():
            rows.append(Row(f"Contrast not checked: {reason}", "none", "", f"attributes/{name}", "inherited", "inherited"))

    for unmapped in scheme_map.get("unmapped", []):
        rows.append(Row(unmapped["reason"], unmapped["vs"], "", "not mapped", "", "unmapped"))

    ET.indent(tree, space="  ")
    target = out / "colorSchemes" / f"{scheme_map['fileName']}.xml"
    target.parent.mkdir(parents=True, exist_ok=True)
    # LF on every platform, so the frozen scheme hashes do not depend on the build machine.
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        tree.write(handle, encoding="unicode", xml_declaration=False)

    def foreground(name: str, fallback: str) -> str:
        node = attributes_node.find(f"./option[@name='{name}']/value/option[@name='FOREGROUND']")
        text = (node.get("value") if node is not None else "") or fallback
        return f"#{int(text, 16):06X}"

    syntax = {
        "keyword": foreground("DEFAULT_KEYWORD", "0000FF"),
        "control": foreground("ReSharper.CONTROL_FLOW_KEYWORD", "8F08C4"),
        "type": foreground("ReSharper.CSHARP_CLASS_IDENTIFIER", "2B91AF"),
        "method": foreground("ReSharper.CSHARP_METHOD_IDENTIFIER", "74531F"),
        "local": foreground("ReSharper.CSHARP_LOCAL_VARIABLE_IDENTIFIER", "1F377F"),
        "string": foreground("DEFAULT_STRING", "A31515"),
        "comment": foreground("DEFAULT_LINE_COMMENT", "008000"),
        "text": foreground("TEXT", "000000"),
    }
    line_numbers = colours_node.find("./option[@name='LINE_NUMBERS_COLOR']")
    syntax["lineNumber"] = f"#{int(line_numbers.get('value'), 16):06X}"
    return rows, syntax


def table(rows: list[Row]) -> list[str]:
    lines = [
        "| Visual Studio element | Visual Studio value | Source | Rider key | Rider value | Status | Sample or note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        cells = [row.element, row.vs_value, row.source, f"`{row.rider_key}`", row.rider_value, row.status, row.note]
        lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return lines


def deviation_table(deviations: list[dict]) -> list[str]:
    if not deviations:
        return []
    lines = ["## Deliberate deviations from Visual Studio", "", "| Visual Studio | Rider | Why |", "|---|---|---|"]
    lines += [f"| {d['vs']} | {d['rider']} | {d['reason']} |" for d in deviations]
    return [*lines, ""]


def scheme_base_label(base_file: str) -> str:
    """Display name of a JetBrains base scheme from its file name: VisualStudioDark.xml -> "Visual Studio Dark"."""
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", Path(base_file).stem)


def write_colours(path: Path, theme_map: dict, vs: VsTheme, theme_rows: list[Row], scheme_rows: list[Row], bases: dict) -> None:
    title = theme_map["name"]
    deviations = theme_map.get("deviations", [])
    source_note = theme_map.get("sourceNote", f"Visual Studio side: **{vs.label}**, decoded from an installed Visual Studio by `tools/decode_vs_theme.py`.")
    mapped = [row for row in scheme_rows if row.status != "unmapped"]
    unmapped = [row for row in scheme_rows if row.status == "unmapped"]
    counts: dict[str, int] = {}
    for row in theme_rows + mapped:
        counts[row.status] = counts.get(row.status, 0) + 1
    lines = [
        f"# Colours: {title}",
        "",
        "Generated by `tools/generate.py`. Do not edit; change the maps in `src/` and rebuild.",
        "",
        source_note,
        "A value written `RRGGBB/A` is a colour with alpha A out of 255; `X over Y` is X alpha-composited on Y,",
        "which is how Visual Studio 2026 paints its Fluent fills. Samples named `vs-…` and `rider-…` are reference",
        "screenshots; they show private source code and are not part of the repository.",
        "",
        "## What the Status column means",
        "",
    ]
    lines += [f"- **{name}** ({counts.get(name, 0)} rows): {text}." for name, text in STATUSES.items() if counts.get(name)]
    lines += [
        "",
        "The Status column describes how well the Visual Studio side is known. Whether Rider renders the value as",
        "specified has been checked by sampling Rider screenshots for a few key surfaces (window, tool panes, document tab well, editor, panel outline, keyword and Code Vision text).",
        "",
        "Everything the maps do not override keeps JetBrains' value from the base files and is not verified:",
        "",
        f"- UI theme base: `{bases['theme']}` (JetBrains, Apache-2.0, taken from the installed Rider at build time)",
        f"- Editor scheme base: `{bases['scheme']}` (JetBrains' {scheme_base_label(bases['scheme'])} scheme, Apache-2.0, same)",
        "",
        "## UI theme: colour tokens, UI keys and icon palette",
        "",
        *table(theme_rows),
        "",
        *deviation_table(deviations),
        "## Editor colour scheme",
        "",
        *table(mapped),
        "",
        "## Visual Studio colours with no Rider counterpart",
        "",
        "| Visual Studio colour | Why it is not mapped |",
        "|---|---|",
        *[f"| {row.vs_value.replace('|', ' / ')} | {row.element} |" for row in unmapped],
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def write_preview(path: Path, title: str, p: dict[str, str], s: dict[str, str]) -> None:
    """Draw a schematic of the IDE from the generated values. It is a diagram of the palette, not a screenshot."""
    window, band, tool, editor = p["layer-0-bg"], p["layer-0-bg-inline"], p["tool-window-bg"], p["editor-bg"]
    outline, text, muted, accent = p["Island.borderColor"], p["text-default"], p["text-muted"], p["accent-brand-bg"]
    well, tab_fill, tab_line = p["EditorTabs.background"], p["EditorTabs.underlinedTabBackground"], p["EditorTabs.underlinedBorderColor"]
    selection, pill = p["Tree.selectionBackground"], p["MainToolbar.Dropdown.background"]
    mono = "font-family='Cascadia Mono, Consolas, monospace' font-size='13'"
    sans = "font-family='Segoe UI, sans-serif' font-size='12'"

    def panel(x: int, y: int, w: int, h: int, fill: str) -> str:
        return f"<rect x='{x}' y='{y}' width='{w}' height='{h}' rx='10' fill='{fill}' stroke='{outline}'/>"

    def code(y: int, number: int, spans: list[tuple[str, str]]) -> str:
        parts = [f"<text x='292' y='{y}' {mono} fill='{s['lineNumber']}' text-anchor='end'>{number}</text>", f"<text x='306' y='{y}' {mono} xml:space='preserve'>"]
        parts += [f"<tspan fill='{s[kind]}'>{value}</tspan>" for kind, value in spans]
        return "".join(parts) + "</text>"

    tree_rows = ["Solution 'Sample'", "  src", "    Orders", "      OrderService.cs", "      Order.cs", "    Program.cs", "  tests"]
    tree = []
    for index, label in enumerate(tree_rows):
        y = 94 + index * 22
        if index == 3:
            tree.append(f"<rect x='18' y='{y - 15}' width='214' height='21' rx='5' fill='{selection}'/>")
        tree.append(f"<text x='26' y='{y}' {sans} fill='{text}' xml:space='preserve'>{label}</text>")
    tabs = []
    for index, label in enumerate(["OrderService.cs", "Order.cs", "Program.cs"]):
        y = 78 + index * 26
        if index == 0:
            tabs.append(f"<rect x='738' y='{y - 16}' width='144' height='23' rx='5' fill='{tab_fill}' stroke='{tab_line}'/>")
        tabs.append(f"<text x='748' y='{y}' {sans} fill='{text}'>{label}</text>")
    lines = [
        code(84, 1, [("keyword", "namespace "), ("text", "Sample.Orders;")]),
        code(128, 3, [("comment", "// Totals are recomputed on every change.")]),
        code(150, 4, [("keyword", "public sealed class "), ("type", "OrderService"), ("text", " : "), ("type", "IOrderService")]),
        code(172, 5, [("text", "{")]),
        code(194, 6, [("keyword", "    public decimal "), ("method", "Total"), ("text", "("), ("type", "Order"), ("text", " "), ("local", "order"), ("text", ")")]),
        code(216, 7, [("text", "    {")]),
        code(238, 8, [("control", "        if "), ("text", "("), ("local", "order"), ("text", ".Lines.Count == 0) "), ("control", "return"), ("text", " 0m;")]),
        code(260, 9, [("keyword", "        var "), ("local", "label"), ("text", " = "), ("string", "\"total\""), ("text", ";")]),
        code(282, 10, [("control", "        return "), ("local", "order"), ("text", ".Lines."), ("method", "Sum"), ("text", "("), ("local", "l"), ("text", " => "), ("local", "l"), ("text", ".Price);")]),
        code(304, 11, [("text", "    }")]),
        code(326, 12, [("text", "}")]),
    ]
    svg = f"""<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 900 520' role='img' aria-label='{title}: schematic of the palette'>
<clipPath id='window'><rect width='900' height='520' rx='12'/></clipPath>
<g clip-path='url(#window)'>
<rect width='900' height='520' fill='{window}'/>
<rect x='14' y='9' width='150' height='26' rx='6' fill='{pill}'/>
<text x='26' y='27' {sans} fill='{text}'>Sample</text>
<text x='190' y='27' {sans} fill='{muted}'>main</text>
{panel(10, 44, 230, 440, tool)}
<text x='24' y='64' {sans} font-weight='600' fill='{text}'>Solution</text>
{''.join(tree)}
{panel(250, 44, 640, 300, editor)}
<path d='M730 45 H880 a9 9 0 0 1 9 9 V334 a9 9 0 0 1 -9 9 H730 Z' fill='{well}'/>
<path d='M730 45 V343' stroke='{outline}' stroke-opacity='.5'/>
{''.join(tabs)}
{''.join(lines)}
{panel(250, 354, 640, 130, tool)}
<text x='264' y='376' {sans} font-weight='600' fill='{text}'>Build</text>
<text x='264' y='404' {mono} fill='{muted}'>Build succeeded.</text>
<rect x='262' y='420' width='96' height='26' rx='5' fill='{accent}'/>
<text x='310' y='437' {sans} fill='{p["text-over-accent"]}' text-anchor='middle'>Rebuild</text>
<rect y='494' width='900' height='26' fill='{band}'/>
<text x='14' y='511' {sans} fill='{muted}'>Sample  ›  src  ›  Orders  ›  OrderService.cs</text>
<text x='886' y='511' {sans} fill='{muted}' text-anchor='end'>8:24   CRLF   UTF-8</text>
</g>
</svg>
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(svg, encoding="utf-8", newline="\n")


def load_map(path: Path) -> dict:
    """Read a map; "extends" names a base map in the same folder that this one is laid over."""
    document = json.loads(path.read_text(encoding="utf-8"))
    parent_name = document.pop("extends", None)
    if parent_name is None:
        return document
    parent_path = path.parent / parent_name
    if not parent_path.exists():
        raise BuildError(f"{path.name} extends '{parent_name}', which does not exist")
    merged = load_map(parent_path)
    sections = {key: value for key, value in document.items() if isinstance(value, dict) and isinstance(merged.get(key), dict)}
    merge_overrides(merged, sections)
    merged.update({key: value for key, value in document.items() if key not in sections})
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the Rider theme resources.")
    parser.add_argument("repo", type=Path)
    parser.add_argument("rider", type=Path)
    parser.add_argument("data", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--overrides", type=Path, help="JSON file with personal overrides laid over the maps")
    parser.add_argument("--source", type=Path, help="folder holding theme/, scheme/ and vs-values/ (default: <repo>/src)")
    parser.add_argument("--docs", type=Path,
                        help="folder for the generated colour tables and previews (default: the source folder's report "
                             "folder, see tools/report_paths.py; never the public docs/)")
    parser.add_argument("--language-evidence", type=Path, help="verified installed language declarations for opted-in schemes")
    arguments = parser.parse_args()
    repo = arguments.repo
    source = arguments.source or repo / "src"
    docs = arguments.docs or source_reports(repo, source)
    personal = arguments.overrides is not None
    overrides = json.loads(arguments.overrides.read_text(encoding="utf-8")) if personal else {}
    try:
        theme_files = sorted((source / "theme").glob("*.theme-map.json"))
        if not theme_files:
            raise BuildError(f"no theme maps in {source / 'theme'}")
        themes: dict[str, VsTheme] = {}

        def vs_for(document: dict, stem: str) -> VsTheme:
            name = document.get("snapshot", stem)
            if name not in themes:
                themes[name] = VsTheme(arguments.data, document["vsBase"], document.get("vsTint"), source / "vs-values" / f"{name}.json")
            return themes[name]

        scheme_results: dict[str, tuple[dict, VsTheme, list[Row], dict[str, str]]] = {}

        def scheme_for(stem: str) -> tuple[dict, VsTheme, list[Row], dict[str, str]]:
            if stem not in scheme_results:
                scheme_map = load_map(source / "scheme" / f"{stem}.scheme-map.json")
                if personal:
                    merge_overrides(scheme_map, overrides.get("scheme", {}))
                vs = vs_for(scheme_map, stem)
                contract = repo / scheme_map["languageMappings"] if scheme_map.get("languageMappings") else None
                rows, syntax = build_scheme(scheme_map, arguments.rider, vs, arguments.out,
                                            contract=contract, evidence=arguments.language_evidence)
                scheme_results[stem] = (scheme_map, vs, rows, syntax)
            return scheme_results[stem]

        for theme_file in theme_files:
            theme_map = load_map(theme_file)
            stem = theme_file.name.removesuffix(".theme-map.json")
            if personal:
                merge_overrides(theme_map, overrides.get("theme", {}))
            vs = vs_for(theme_map, stem)
            theme_rows, palette = build_theme(theme_map, arguments.rider, vs, arguments.out)
            scheme_map, _, scheme_rows, syntax = scheme_for(theme_map.get("schemeMap", stem))
            mode = "snapshot" if vs.from_snapshot else "decoded Visual Studio data"
            print(f"{theme_map['name']}: {len(theme_rows)} theme rows, {len(scheme_rows)} scheme rows ({mode})")
            if personal:
                continue
            bases = {"theme": theme_map["baseThemeFile"], "scheme": scheme_map["baseSchemeFile"]}
            document = "Colours.md" if len(theme_files) == 1 else f"Colours.{stem}.md"
            write_colours(docs / document, theme_map, vs, theme_rows, scheme_rows, bases)
            write_preview(docs / "images" / f"{theme_map['fileName']}.svg", theme_map["name"], palette, syntax)
        for scheme_file in sorted((source / "scheme").glob("*.scheme-map.json")):
            stem = scheme_file.name.removesuffix(".scheme-map.json")
            if stem not in scheme_results:
                scheme_map, _, scheme_rows, _ = scheme_for(stem)
                print(f"{scheme_map['name']}: {len(scheme_rows)} scheme rows (scheme only)")
        if personal:
            print("personal overrides applied; docs, snapshot and preview left untouched")
        else:
            for vs in themes.values():
                vs.write_snapshot()
    except BuildError as error:
        print(f"BUILD FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
