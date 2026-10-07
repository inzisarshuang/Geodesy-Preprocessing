"""Architecture checks for the standalone Preprocessing source tree."""

from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class ArchitectureTests(unittest.TestCase):
    """Prevent undocumented public APIs and legacy-source coupling."""

    def test_production_modules_document_public_symbols(self) -> None:
        modules = sorted(SOURCE_ROOT.rglob("*.py"))
        self.assertTrue(modules, "production source modules are missing")
        problems: list[str] = []
        for path in modules:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            docstring = ast.get_docstring(tree) or ""
            for heading in ("功能概述:", "函数说明:"):
                if heading not in docstring:
                    problems.append(f"{path.relative_to(REPOSITORY_ROOT)}: missing {heading}")
            for node in tree.body:
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    if not node.name.startswith("_") and f"``{node.name}``" not in docstring:
                        problems.append(
                            f"{path.relative_to(REPOSITORY_ROOT)}: "
                            f"public symbol {node.name} is not documented"
                        )
        self.assertEqual(problems, [], "\n".join(problems))

    def test_production_modules_do_not_depend_on_legacy_checkout(self) -> None:
        for path in SOURCE_ROOT.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertNotIn("GAMMA-InSAR", text)
                self.assertNotIn("sys.path", text)

    def test_package_version_and_config_entry_are_consistent(self) -> None:
        from geodesy_preprocessing import __version__

        metadata = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(__version__, "0.8.0")
        self.assertEqual(metadata["project"]["version"], "0.8.0")
        self.assertEqual(
            metadata["project"]["scripts"]["geodesy-pre-dem"],
            "geodesy_preprocessing.dem.cli:main",
        )
        self.assertIn("requests>=2.31", metadata["project"]["dependencies"])
        self.assertIn("rasterio>=1.3", metadata["project"]["dependencies"])
        legacy = {
            "geodesy-pre-s1-slc",
            "geodesy-pre-s1-geo-ref",
            "geodesy-pre-s1-coreg",
            "geodesy-pre-sar-slc",
        }
        self.assertTrue(legacy.isdisjoint(metadata["project"]["scripts"]))
        canonical = {
            "geodesy-pre-gamma-dem-convert",
            "geodesy-pre-gamma-sar-slc",
            "geodesy-pre-gamma-sar-tops-slc",
            "geodesy-pre-gamma-sar-tops-geo-ref",
            "geodesy-pre-gamma-sar-tops-coreg",
            "geodesy-pre-gamma-sar-rslc-crop",
        }
        self.assertTrue(canonical.issubset(metadata["project"]["scripts"]))
        self.assertEqual(
            metadata["tool"]["setuptools"]["data-files"][
                "share/geodesy-preprocessing/config_examples"
            ],
            ["config_examples/*.cfg"],
        )

    def test_documentation_places_analysis_after_registered_stack_publication(self) -> None:
        architecture = (REPOSITORY_ROOT / "docs" / "architecture.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("发布按研究区裁剪并验证的注册 RSLC 栈", architecture)
        self.assertIn("Geodesy-Analysis", architecture)
        self.assertIn("GAMMA", architecture)


if __name__ == "__main__":
    unittest.main()
