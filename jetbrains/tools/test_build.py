"""The cross-platform build: IDE homes on each system, reproducible jars and change notes."""

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import ide_home

import build


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class IdeHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_windows_and_linux_folder(self):
        home = self.base / "Rider"
        write(home / "product-info.json", json.dumps({"productCode": "RD"}))
        self.assertEqual(ide_home.resolve(home), home)
        self.assertEqual(ide_home.product_info(home)["productCode"], "RD")

    def test_macos_app_bundle_resolves_to_contents(self):
        app = self.base / "GoLand.app"
        write(app / "Contents/Resources/product-info.json", json.dumps({"productCode": "GO"}))
        (app / "Contents/lib").mkdir()
        self.assertEqual(ide_home.resolve(app), app / "Contents")
        self.assertEqual(ide_home.product_info(app)["productCode"], "GO")
        self.assertEqual(ide_home.parse_homes([f"GO={app}"]), {"GO": app / "Contents"})

    def test_missing_ide_and_bad_argument_fail(self):
        with self.assertRaises(FileNotFoundError):
            ide_home.product_info(self.base / "nothing")
        with self.assertRaises(ValueError):
            ide_home.parse_homes(["RD"])

    def test_default_candidates_per_system(self):
        windows = ide_home.candidates("IU", "win32", {"LOCALAPPDATA": "/local"})
        self.assertEqual(windows[0], Path("/local/Programs/IntelliJ IDEA Ultimate"))
        mac = ide_home.candidates("RD", "darwin", {})
        self.assertEqual(mac[0], Path("/Applications/Rider.app"))
        self.assertEqual(ide_home.candidates("RD", "linux", {}), [])

    def test_default_home_prefers_an_installed_candidate(self):
        programs = self.base / "Programs"
        write(programs / "IntelliJ IDEA/product-info.json", "{}")
        found = ide_home.default_home("IU", "win32", {"LOCALAPPDATA": str(self.base)})
        self.assertEqual(found, programs / "IntelliJ IDEA")


class JarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.resources = self.base / "resources"
        write(self.resources / "META-INF/plugin.xml", "<idea-plugin/>\n")
        write(self.resources / "themes/A.theme.json", "{}\n")
        write(self.resources / "colorSchemes/A.xml", "<scheme/>\n")

    def tearDown(self):
        self.temp.cleanup()

    def test_same_inputs_give_the_same_bytes(self):
        first, second = self.base / "one.jar", self.base / "two.jar"
        build.write_jar(self.resources, first)
        (self.resources / "themes/A.theme.json").touch()  # a newer file time must not matter
        build.write_jar(self.resources, second)
        self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_entries_order_dates_and_manifest(self):
        jar = self.base / "out.jar"
        build.write_jar(self.resources, jar)
        with zipfile.ZipFile(jar) as archive:
            self.assertEqual(archive.namelist(), [
                "META-INF/", "META-INF/MANIFEST.MF", "META-INF/plugin.xml",
                "colorSchemes/", "colorSchemes/A.xml", "themes/", "themes/A.theme.json",
            ])
            self.assertEqual({info.date_time for info in archive.infolist()}, {build.JAR_DATE})
            self.assertEqual(archive.read("META-INF/MANIFEST.MF"), build.MANIFEST)

    def test_own_manifest_is_refused(self):
        write(self.resources / "META-INF/MANIFEST.MF", "x\n")
        with self.assertRaises(build.BuildError):
            build.write_jar(self.resources, self.base / "out.jar")


class ChangeNotesTests(unittest.TestCase):
    def test_newest_section_only_encoded_like_dotnet(self):
        with tempfile.TemporaryDirectory() as temp:
            changelog = Path(temp) / "CHANGELOG.md"
            changelog.write_bytes(
                b"# Changelog\r\n\r\n## 0.7.0\r\n\r\n- Blue's <b> & \"x\" caf\xc3\xa9\r\n  - nested\r\n\r\n## 0.6.0\r\n\r\n- old\r\n"
            )
            self.assertEqual(
                build.change_notes(changelog),
                "<ul><li>Blue&#39;s &lt;b&gt; &amp; &quot;x&quot; caf&#233;</li><li>nested</li></ul>",
            )

    def test_changelog_without_version_section_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            changelog = Path(temp) / "CHANGELOG.md"
            changelog.write_text("# Changelog\n", encoding="utf-8")
            with self.assertRaises(build.BuildError):
                build.change_notes(changelog)


if __name__ == "__main__":
    unittest.main()
