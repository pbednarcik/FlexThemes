"""The published overrides example builds for the family the docs name, and the contrast checks still apply to it."""

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_check_guards import rider_resources

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "docs/overrides.example.json"
EVIDENCE = ROOT / "build/language-evidence.json"


def documented_family() -> str:
    """The family named in the example's own instructions; the README must name the same one."""
    about = json.loads(EXAMPLE.read_text(encoding="utf-8"))["about"]
    family = re.search(r"--plugin (\w+) --overrides", about).group(1)
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    if f"--plugin {family} --overrides" not in readme:
        raise AssertionError(f"README does not tell readers to build {family} with overrides")
    return family


def generate(family: str) -> subprocess.CompletedProcess:
    source = ROOT / json.loads((ROOT / "plugins.json").read_text(encoding="utf-8"))[family]["Source"]
    with tempfile.TemporaryDirectory() as temp:
        return subprocess.run(
            [sys.executable, str(ROOT / "tools/generate.py"), str(ROOT), str(rider_resources()),
             str(ROOT / "data/none"), str(Path(temp) / "out"), "--source", str(source),
             "--docs", str(Path(temp) / "docs"), "--language-evidence", str(EVIDENCE), "--overrides", str(EXAMPLE)],
            capture_output=True, text=True, encoding="utf-8", check=False,
        )


class OverridesExampleTests(unittest.TestCase):
    def setUp(self):
        if rider_resources() is None:
            self.skipTest("no extracted Rider resources; run tools/build.py once or set VS_THEME_RIDER_RESOURCES")
        if not EVIDENCE.is_file():
            self.skipTest("no build/language-evidence.json; run tools/build.py once")

    def test_documented_family_builds_with_the_example(self):
        family = documented_family()
        result = generate(family)
        self.assertEqual(result.returncode, 0, f"{family}: {result.stderr or result.stdout}")

    def test_contrast_checks_still_apply_to_overrides(self):
        """A dark family refuses the light example: proof that overrides do not bypass the checks."""
        result = generate("Graphite")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("below 4.5:1", result.stderr + result.stdout)


if __name__ == "__main__":
    unittest.main()
