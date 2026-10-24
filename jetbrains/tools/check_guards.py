"""Prove that each build guard in generate.py can fail.

Each case copies src/ into a scratch folder, breaks one thing on purpose and runs the generator for one
theme family. The case passes only when the build fails with the message that names the planted fault.
Every family first runs unchanged and must stay green, so a red case means the planted fault and nothing
else. A guard that stays green here is a guard that checks nothing.

The copy includes the committed src/**/vs-values snapshots, so no decoded Visual Studio data is needed:
pass any <vs data dir>, including one that does not exist. The generator runs as the build runs it,
language mappings included, so it needs the installed-IDE inventory tools/build.py writes to
build/language-evidence.json.

Usage:
    python check_guards.py <repo root> <rider resources dir> <vs data dir> <scratch dir> <language evidence>
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Theme families the cases run against: source folder under src/, theme map stem, scheme map stem.
COOL_BREEZE = (".", "CoolBreeze", "CoolBreeze")  # light, faithful copy, no inherited check of its own
BLUE_MIST = ("bluemist", "BlueMist", "BlueMist")  # light, designed, inherited check on
VS_DARK = ("vsdark", "Vs2026Dark", "Vs2026Dark")  # dark, Darcula platform default
FAMILIES = (COOL_BREEZE, BLUE_MIST, VS_DARK)


def mutate_unknown_ui_key(theme: dict, scheme: dict) -> None:
    theme["ui"]["EditorTabs.backgroundColour"] = theme["ui"]["EditorTabs.background"]


def mutate_deprecated_ui_key(theme: dict, scheme: dict) -> None:
    # The platform metadata marks this key "Not used, selection foreground doesn't change".
    theme["ui"]["CompletionPopup.selectionInfoForeground"] = {"value": "#FF00FF", "element": "planted fault", "status": "inferred"}


def mutate_unknown_token(theme: dict, scheme: dict) -> None:
    theme["colors"]["tool-window-background"] = theme["colors"]["tool-window-bg"]


def mutate_wrong_measurement(theme: dict, scheme: dict) -> None:
    theme["colors"]["tool-window-bg"]["measured"] = "D3E3EE"


def mutate_unknown_vs_colour(theme: dict, scheme: dict) -> None:
    theme["colors"]["layer-0-bg"]["value"] = "vs:ShellInternal|EnvironmentBackgroundX"


def mutate_unknown_scheme_attribute(theme: dict, scheme: dict) -> None:
    scheme["attributes"]["ReSharper.CSHARP_CLASS_IDENTIFIERS"] = scheme["attributes"]["ReSharper.CSHARP_CLASS_IDENTIFIER"]


def mutate_unknown_scheme_colour(theme: dict, scheme: dict) -> None:
    scheme["colors"]["LINE_NUMBER_COLOR"] = scheme["colors"]["LINE_NUMBERS_COLOR"]


def mutate_translucent_scheme_value(theme: dict, scheme: dict) -> None:
    scheme["colors"]["CONSOLE_BACKGROUND_KEY"]["value"] = "vs:Shell|SubtleFillSecondary"


def mutate_low_contrast(theme: dict, scheme: dict) -> None:
    theme["colors"]["text-default"] = {**theme["colors"]["text-default"], "value": "#EEEEEE", "on": "#FFFFFF"}


def mutate_apca_floor(theme: dict, scheme: dict) -> None:
    # An APCA floor no colour can meet must fail the minLc route.
    entry = theme["colors"]["text-default"]
    theme["colors"]["text-default"] = {**entry, "on": entry.get("on", "#FFFFFF"), "minLc": 200}


def mutate_unreadable_fill(theme: dict, scheme: dict) -> None:
    # A fill whose declared text is unreadable must fail the "under" route.
    entry = {key: value for key, value in theme["colors"]["tool-window-bg"].items() if key != "measured"}
    theme["colors"]["tool-window-bg"] = {**entry, "under": "#FEFEFE", "status": "inferred"}


def mutate_inherited_contrast(theme: dict, scheme: dict) -> None:
    # Without its reasoned exemption, the C++ type colour Morning Mist inherits from JetBrains' scheme (3.64:1)
    # must fail the inherited check.
    del scheme["contrastExempt"]["ReSharper.CPP_CLASS_IDENTIFIER"]


def mutate_dark_inherited_contrast(theme: dict, scheme: dict) -> None:
    # The dark path (Darcula platform default): without the shared dark exemption, ANSI "white" (black) output
    # on the dark console must fail the inherited check. A null entry removes the base map's entry.
    scheme["contrastExempt"]["CONSOLE_WHITE_OUTPUT"] = None


def mutate_wrong_platform_default(theme: dict, scheme: dict) -> None:
    # A light scheme whose base inherits from "Default" must not be checked against Darcula's attributes.
    scheme["platformDefault"] = "Darcula"


def mutate_kept_background(theme: dict, scheme: dict) -> None:
    # A mapped foreground over the background the base scheme keeps (Visual Studio Light's FFB496 brace fill)
    # must be checked on that background, not on the editor's.
    del scheme["attributes"]["UNMATCHED_BRACE_ATTRIBUTES"]["BACKGROUND"]


def mutate_kept_foreground(theme: dict, scheme: dict) -> None:
    # A mapped background under the foreground the base scheme keeps must be checked too.
    scheme["attributes"]["TODO_DEFAULT_ATTRIBUTES"] = {"BACKGROUND": "#0A246A", "element": "planted fault", "status": "inferred"}


def mutate_dark_caret_line(theme: dict, scheme: dict) -> None:
    # A caret line dark enough to sink the greys Morning Mist inherits must fail the caret-line rule.
    scheme["colors"]["CARET_ROW_COLOR"] = {"value": "#C8C8C8", "element": "planted fault", "status": "inferred"}


def mutate_dark_highlight(theme: dict, scheme: dict) -> None:
    # A background-only highlight dark enough to sink plain text must fail the highlight rule.
    scheme["attributes"]["IDENTIFIER_UNDER_CARET_ATTRIBUTES"] = {"BACKGROUND": "#505050", "element": "planted fault", "status": "inferred"}


Mutation = Callable[[dict, dict], None]
CASES: list[tuple[str, tuple[str, str, str], Mutation, str]] = [
    ("unknown UI key", COOL_BREEZE, mutate_unknown_ui_key, "UI key 'EditorTabs.backgroundColour' is not known"),
    ("deprecated UI key", COOL_BREEZE, mutate_deprecated_ui_key, "UI key 'CompletionPopup.selectionInfoForeground' is deprecated"),
    ("unknown colour token", COOL_BREEZE, mutate_unknown_token, "colour token 'tool-window-background' does not exist"),
    ("measurement off by one", COOL_BREEZE, mutate_wrong_measurement, "but the screenshot sample is #D3E3EE"),
    # Decoded data says "has no colour", the committed snapshot "is not in the snapshot"; both name the token.
    ("unknown VS colour", COOL_BREEZE, mutate_unknown_vs_colour, "'ShellInternal|EnvironmentBackgroundX"),
    ("unknown scheme attribute", COOL_BREEZE, mutate_unknown_scheme_attribute, "scheme attribute 'ReSharper.CSHARP_CLASS_IDENTIFIERS' is not used"),
    ("unknown scheme colour", COOL_BREEZE, mutate_unknown_scheme_colour, "scheme colour 'LINE_NUMBER_COLOR' is not used"),
    ("translucent scheme value", COOL_BREEZE, mutate_translucent_scheme_value, "must be opaque"),
    ("low contrast", COOL_BREEZE, mutate_low_contrast, "text-default: EEEEEE on FFFFFF is 1.16:1, below 4.5:1"),
    ("APCA floor", COOL_BREEZE, mutate_apca_floor, "text-default: 1B1B1B on FFFFFF is APCA Lc"),
    ("unreadable text on a fill", COOL_BREEZE, mutate_unreadable_fill, "tool-window-bg: FEFEFE on"),
    ("inherited low contrast", COOL_BREEZE, mutate_inherited_contrast, "ReSharper.CPP_CLASS_IDENTIFIER 2B91AF on FFFFFF"),
    ("inherited low contrast, dark", VS_DARK, mutate_dark_inherited_contrast, "CONSOLE_WHITE_OUTPUT"),
    ("platform default contradicts the base scheme", COOL_BREEZE, mutate_wrong_platform_default, "contradicts the base scheme's parent_scheme 'Default'"),
    ("foreground on a kept background", BLUE_MIST, mutate_kept_background, "UNMATCHED_BRACE_ATTRIBUTES: C50F1F on FFB496"),
    ("background under a kept foreground", BLUE_MIST, mutate_kept_foreground, "TODO_DEFAULT_ATTRIBUTES"),
    ("syntax colour on the caret line", COOL_BREEZE, mutate_dark_caret_line, "too weak on the caret line or selection"),
    ("plain text on a highlight", COOL_BREEZE, mutate_dark_highlight, "plain text 000000 on IDENTIFIER_UNDER_CARET_ATTRIBUTES 505050"),
]


def unchanged(theme: dict, scheme: dict) -> None:
    pass


def run_case(repo: Path, rider: Path, data: Path, evidence: Path, case_root: Path, family: tuple[str, str, str],
             mutate: Mutation) -> subprocess.CompletedProcess:
    if case_root.exists():
        shutil.rmtree(case_root)
    shutil.copytree(repo / "src", case_root / "src")
    folder, theme_stem, scheme_stem = family
    source = case_root / "src" / folder
    theme_path = source / "theme" / f"{theme_stem}.theme-map.json"
    scheme_path = source / "scheme" / f"{scheme_stem}.scheme-map.json"
    theme = json.loads(theme_path.read_text(encoding="utf-8"))
    scheme = json.loads(scheme_path.read_text(encoding="utf-8"))
    mutate(theme, scheme)
    theme_path.write_text(json.dumps(theme), encoding="utf-8", newline="\n")
    scheme_path.write_text(json.dumps(scheme), encoding="utf-8", newline="\n")
    return subprocess.run(
        [sys.executable, str(repo / "tools" / "generate.py"), str(case_root), str(rider), str(data), str(case_root / "out"),
         "--source", str(source), "--docs", str(case_root / "docs"), "--language-evidence", str(evidence)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    repo, rider, data, scratch, evidence = (Path(argument) for argument in argv)
    evidence = evidence.resolve()
    repo = repo.resolve()
    scratch = scratch.resolve()
    case_root = (scratch / "guard-case").resolve()
    if case_root.parent != scratch:
        raise ValueError("guard-case must remain inside the explicitly selected scratch directory")
    failures = 0
    try:
        for family in FAMILIES:
            run = run_case(repo, rider, data, evidence, case_root, family, unchanged)
            green = run.returncode == 0
            failures += 0 if green else 1
            print(f"{'GREEN as expected' if green else 'FIXTURE BROKEN':20} control: {family[1]} unchanged")
            if not green:
                print(f"    exit={run.returncode} stderr={run.stderr.strip()!r}")
        for label, family, mutate, expected in CASES:
            run = run_case(repo, rider, data, evidence, case_root, family, mutate)
            went_red = run.returncode == 1 and expected in run.stderr
            failures += 0 if went_red else 1
            print(f"{'RED as expected' if went_red else 'GUARD DID NOT FIRE':20} {label} ({family[1]})")
            if not went_red:
                print(f"    exit={run.returncode} stderr={run.stderr.strip()!r}")
    finally:
        shutil.rmtree(case_root, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
