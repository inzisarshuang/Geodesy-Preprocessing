"""Contract tests for the durable, non-duplicated user documentation."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / "docs/workflows"
DURABLE = (
    ROOT / "README.md",
    ROOT / "docs/architecture.md",
    WORKFLOWS / "README.md",
    WORKFLOWS / "dem.md",
    WORKFLOWS / "sentinel1_tops.md",
    WORKFLOWS / "radar_grid.md",
    WORKFLOWS / "nisar_rslc.md",
    WORKFLOWS / "nisar_gslc.md",
    ROOT / "docs/validation/preprocessing_acceptance.md",
)


class DocumentationLayoutTests(unittest.TestCase):
    def test_only_durable_manuals_remain(self) -> None:
        for path in DURABLE:
            self.assertTrue(path.is_file(), path)
        for removed in (
            ROOT / "使用说明.md",
            ROOT / "docs/STATUS.md",
            ROOT / "MIGRATION_PROVENANCE.md",
            ROOT / "docs/migrations",
            ROOT / "docs/superpowers",
        ):
            self.assertFalse(removed.exists(), removed)

    def test_local_markdown_links_resolve(self) -> None:
        pattern = re.compile(r"\[[^]]+\]\((?!https?://|#)([^)]+)\)")
        problems: list[str] = []
        for document in DURABLE:
            for target in pattern.findall(document.read_text(encoding="utf-8")):
                path = (document.parent / target.split("#", 1)[0]).resolve()
                if not path.exists():
                    problems.append(f"{document.relative_to(ROOT)} -> {target}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_workflows_use_named_stages_not_numbered_steps(self) -> None:
        numbered_heading = re.compile(r"^#{1,6}\s+\d+[.)、]\s*", re.MULTILINE)
        for document in WORKFLOWS.glob("*.md"):
            self.assertIsNone(numbered_heading.search(document.read_text(encoding="utf-8")), document)

    def test_durable_guides_do_not_publish_stale_names_or_credentials(self) -> None:
        texts = [path.read_text(encoding="utf-8") for path in DURABLE]
        texts.extend(path.read_text(encoding="utf-8") for path in (ROOT / "config_examples").glob("*.cfg"))
        contents = "\n".join(texts)
        for stale in (
            "geodesy_preprocessing.sar.registration",
            "geodesy_preprocessing.sar.subsetting",
            "geodesy_preprocessing.sar.preparation",
            "geodesy-pre-s1-",
        ):
            self.assertNotIn(stale, contents)
        self.assertIn(".netrc", contents)
        self.assertIn("EARTHDATA_TOKEN", contents)
        self.assertNotRegex(contents, r"(?im)^\s*earthdata_(?:username|password|token)\s*=")


if __name__ == "__main__":
    unittest.main()
