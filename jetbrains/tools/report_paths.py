"""Where the build writes its generated reports: colour tables, previews, language and UI verification, indexes.

They are working evidence for the maintainers, not user documentation. With the private QA folder present they are
kept with it; in any other checkout they go to build/reports/, which git ignores.
"""

from __future__ import annotations

from pathlib import Path


def reports_root(repo: Path) -> Path:
    return repo / "qa/docs/generated" if (repo / "qa").is_dir() else repo / "build/reports"


def family_reports(repo: Path, spec: dict) -> Path:
    """The report folder of one plugin family: spec["Reports"] in plugins.json, relative to the reports root."""
    return reports_root(repo) / spec["Reports"]


def source_reports(repo: Path, source: Path) -> Path:
    """The report folder for a source folder: src/ maps to the reports root, src/<family> to <family>."""
    try:
        relative = Path(source).resolve().relative_to((Path(repo) / "src").resolve())
    except ValueError:
        relative = Path(Path(source).name)
    return reports_root(Path(repo)) / relative
