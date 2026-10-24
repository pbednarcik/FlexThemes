"""Run the generator guard proof (check_guards.py) as part of the unit suite.

It needs the JetBrains resources tools/build.py extracts from the installed Rider and the installed-IDE language
inventory it writes; without them the test is skipped with the reason, never passed.
"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

import check_guards
from generate import BuildError, platform_default_attributes

ROOT = Path(__file__).resolve().parents[1]


class PlatformDefaultTests(unittest.TestCase):
    """The inherited contrast check must fail closed when the platform default scheme is unusable."""

    def write(self, root: Path, body: str) -> Path:
        (root / "platform").mkdir()
        (root / "platform/DefaultColorSchemesManager.xml").write_text(
            f"<application><component>{body}</component></application>", encoding="utf-8", newline="\n"
        )
        return root

    def test_missing_empty_and_populated_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as folder, self.assertRaisesRegex(BuildError, "is missing"):
            platform_default_attributes(Path(folder), "Default")
        with tempfile.TemporaryDirectory() as folder:
            root = self.write(Path(folder), '<scheme name="Default"><attributes /></scheme>')
            with self.assertRaisesRegex(BuildError, "has no attributes"):
                platform_default_attributes(root, "Default")
        with tempfile.TemporaryDirectory() as folder:
            root = self.write(Path(folder), '<scheme name="Darcula"><attributes><option name="TEXT"><value>'
                                            '<option name="FOREGROUND" value="a9b7c6" /></value></option></attributes></scheme>')
            with self.assertRaisesRegex(BuildError, "is not in"):
                platform_default_attributes(root, "Default")
            self.assertEqual({"TEXT": {"FOREGROUND": "a9b7c6"}}, platform_default_attributes(root, "Darcula"))


def rider_resources() -> Path | None:
    configured = os.environ.get("VS_THEME_RIDER_RESOURCES")
    if configured:
        return Path(configured)
    # tools/build.py default --scratch is <temp>\FlexThemes-jetbrains; it extracts into rider-<build>.
    found = sorted((Path(tempfile.gettempdir()) / "FlexThemes-jetbrains").glob("rider-*"))
    return found[-1] if found else None


class GuardProofTests(unittest.TestCase):
    def test_every_planted_fault_fails_and_every_control_passes(self) -> None:
        rider = rider_resources()
        evidence = ROOT / "build" / "language-evidence.json"
        if rider is None or not rider.is_dir():
            self.skipTest("no extracted Rider resources; run tools/build.py once or set VS_THEME_RIDER_RESOURCES")
        if not evidence.is_file():
            self.skipTest("no build/language-evidence.json; run tools/build.py once")
        with tempfile.TemporaryDirectory() as scratch:
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                # A data folder that does not exist forces the committed snapshots, so the proof also runs
                # on a machine without Visual Studio.
                code = check_guards.main([str(ROOT), str(rider), str(Path(scratch) / "no-vs-data"), scratch, str(evidence)])
        report = output.getvalue()
        self.assertEqual(0, code, report)
        self.assertEqual(len(check_guards.FAMILIES), report.count("GREEN as expected"), report)
        self.assertEqual(len(check_guards.CASES), report.count("RED as expected"), report)


if __name__ == "__main__":
    unittest.main()
