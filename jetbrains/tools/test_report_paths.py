"""Generated reports never land in the public docs/ folder, whichever tool writes them."""

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import report_family
from built import requires_build
from report_paths import family_reports, reports_root, source_reports
from test_check_guards import rider_resources

import bundle

ROOT = Path(__file__).resolve().parents[1]


class ReportPathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        (self.repo / "src/lavender").mkdir(parents=True)

    def tearDown(self):
        self.temp.cleanup()

    def assert_outside_docs(self, path):
        docs = (self.repo / "docs").resolve()
        self.assertNotEqual(path.resolve(), docs)
        self.assertNotIn(docs, path.resolve().parents)

    def test_public_checkout_writes_to_ignored_build(self):
        self.assertEqual(reports_root(self.repo), self.repo / "build/reports")
        self.assertEqual(source_reports(self.repo, self.repo / "src"), self.repo / "build/reports")
        self.assertEqual(source_reports(self.repo, self.repo / "src/lavender"), self.repo / "build/reports/lavender")

    def test_private_checkout_keeps_them_with_the_private_folder(self):
        private = self.repo / "qa"
        private.mkdir()
        lavender = source_reports(self.repo, self.repo / "src/lavender")
        self.assertIn(private, lavender.parents)
        self.assertEqual(lavender, reports_root(self.repo) / "lavender")

    def test_no_family_and_no_source_reaches_public_docs(self):
        for with_qa in (False, True):
            if with_qa:
                (self.repo / "qa").mkdir()
            for source in (self.repo / "src", self.repo / "src/lavender", Path(self.temp.name) / "elsewhere"):
                self.assert_outside_docs(source_reports(self.repo, source))
            for spec in json.loads((ROOT / "plugins.json").read_text(encoding="utf-8")).values():
                self.assert_outside_docs(family_reports(self.repo, spec))

    def test_family_folders_match_their_source_folders(self):
        """generate.py's default (from --source) and build.py's (from plugins.json) name the same folder."""
        for spec in json.loads((ROOT / "plugins.json").read_text(encoding="utf-8")).values():
            self.assertEqual(source_reports(ROOT, ROOT / spec["Source"]), family_reports(ROOT, spec))


def tree_digest(folder: Path) -> dict[str, str]:
    return {path.relative_to(folder).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(folder.rglob("*")) if path.is_file()}


@requires_build
class ReportWriterTests(unittest.TestCase):
    """The real writers, run against a copy of the project: the generator without --docs, the bundle report and the
    family index. A path helper test alone would pass even if a writer went back to docs/."""

    def setUp(self):
        if rider_resources() is None:
            self.skipTest("no extracted Rider resources; run tools/build.py once or set VS_THEME_RIDER_RESOURCES")
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "jetbrains"
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(
            "qa", "build", "__pycache__", ".ruff_cache", "personal", "data"))
        self.docs_before = tree_digest(self.repo / "docs")

    def tearDown(self):
        self.temp.cleanup()

    def write_all_reports(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / "tools/generate.py"), str(self.repo), str(rider_resources()),
             str(self.repo / "data/none"), str(Path(self.temp.name) / "out"),
             "--language-evidence", str(ROOT / "build/language-evidence.json")],
            capture_output=True, text=True, encoding="utf-8", check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        bundle.check(self.repo, report=True)
        report_family.main(self.repo)

    def assert_reports_in(self, folder: Path):
        self.assertEqual(tree_digest(self.repo / "docs"), self.docs_before, "a report was written into docs/")
        for name in ("Colours.md", "images/MorningMist.svg", "BundleVerification.md", "ThemeVerification.md"):
            self.assertTrue((folder / name).is_file(), f"{name} missing from {folder}")

    def test_public_checkout_reports_go_to_build(self):
        self.write_all_reports()
        self.assert_reports_in(self.repo / "build/reports")

    def test_private_checkout_reports_stay_with_the_private_folder(self):
        (self.repo / "qa").mkdir()
        self.write_all_reports()
        self.assert_reports_in(reports_root(self.repo))
        self.assertFalse((self.repo / "build/reports").exists())


if __name__ == "__main__":
    unittest.main()
