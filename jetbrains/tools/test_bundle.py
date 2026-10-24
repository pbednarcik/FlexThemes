"""Exercise real bundle contents and conservative release cleanup planning."""

from __future__ import annotations

import copy
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from built import requires_build

from bundle import collect, config, obsolete_jars, release_name, validate_bundle

ROOT = Path(__file__).resolve().parents[1]


@requires_build
class BundleTests(unittest.TestCase):
    def test_independent_package_versions_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "shared root VERSION"):
            release_name(ROOT, {"Version": "src/bundle/VERSION", "Jar": "drift"})

    @classmethod
    def setUpClass(cls) -> None:
        cls.expected = collect(ROOT)
        with zipfile.ZipFile(
            ROOT / "dist" / release_name(ROOT, config(ROOT))
        ) as archive:
            cls.actual = {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/")
            }

    def test_complete_theme_only_bundle_matches_verified_releases(self) -> None:
        validate_bundle(self.actual, self.expected)
        descriptor = ET.fromstring(self.actual["META-INF/plugin.xml"])
        self.assertEqual(9, len(descriptor.findall("./extensions/themeProvider")))
        self.assertEqual(8, len(descriptor.findall("./extensions/bundledColorScheme")))
        self.assertEqual(7, len(descriptor.findall("incompatible-with")))
        self.assertFalse(any(name.endswith(".class") for name in self.actual))

    def test_missing_alternative_scheme_fails(self) -> None:
        broken = dict(self.actual)
        del broken["colorSchemes/LavenderMistPurple.xml"]
        with self.assertRaisesRegex(ValueError, "resource catalogue"):
            validate_bundle(broken, self.expected)

    def test_palette_mutation_fails(self) -> None:
        broken = dict(self.actual)
        key = "colorSchemes/PeriwinkleIslands.xml"
        broken[key] = broken[key].replace(b"EEF0FA", b"FFFFFF", 1)
        self.assertNotEqual(broken[key], self.actual[key])
        with self.assertRaisesRegex(ValueError, "resource differs"):
            validate_bundle(broken, self.expected)

    def test_misdirected_provider_and_missing_conflict_fail(self) -> None:
        for mutation in ("provider", "conflict"):
            with self.subTest(mutation=mutation):
                broken = dict(self.actual)
                descriptor = ET.fromstring(broken["META-INF/plugin.xml"])
                if mutation == "provider":
                    descriptor.find("./extensions/themeProvider").set(
                        "path", "/themes/missing.theme.json"
                    )
                else:
                    descriptor.remove(descriptor.find("incompatible-with"))
                broken["META-INF/plugin.xml"] = ET.tostring(descriptor)
                with self.assertRaisesRegex(ValueError, "plugin.xml"):
                    validate_bundle(broken, self.expected)

    def test_unexpected_code_and_stale_provenance_fail(self) -> None:
        for entry in ("runtime/Marker.class", "META-INF/theme-bundle.json"):
            with self.subTest(entry=entry):
                broken = copy.deepcopy(self.actual)
                broken[entry] = b"unreviewed"
                with self.assertRaises(ValueError):
                    validate_bundle(broken, self.expected)


class ReleaseCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        parent = ROOT / "build/bundle-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.folder = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.folder.cleanup)
        self.dist = Path(self.folder.name)
        self.releases = {"example-theme": "example-theme-0.5.10.jar"}

    def touch(self, *names: str) -> None:
        for name in names:
            (self.dist / name).write_bytes(b"fixture")

    def test_only_older_numeric_releases_are_selected_without_deleting(self) -> None:
        self.touch(
            "example-theme-0.5.10.jar",
            "example-theme-0.5.9.jar",
            "example-theme-0.5.8-personal.jar",
            "other-0.1.0.jar",
            "notes.jar",
        )
        self.assertEqual(
            ["example-theme-0.5.9.jar"], obsolete_jars(self.dist, self.releases)
        )
        self.assertEqual(5, len(list(self.dist.iterdir())))

    def test_missing_current_release_cannot_start_cleanup(self) -> None:
        self.touch("example-theme-0.5.9.jar")
        with self.assertRaisesRegex(ValueError, "without current release"):
            obsolete_jars(self.dist, self.releases)

    def test_newer_unconfigured_release_requires_review(self) -> None:
        self.touch("example-theme-0.5.10.jar", "example-theme-0.6.0.jar")
        with self.assertRaisesRegex(ValueError, "newer release"):
            obsolete_jars(self.dist, self.releases)

    def test_release_like_directory_is_ignored(self) -> None:
        self.touch("example-theme-0.5.10.jar")
        (self.dist / "example-theme-0.5.9.jar").mkdir()
        self.assertEqual([], obsolete_jars(self.dist, self.releases))


if __name__ == "__main__":
    unittest.main()
