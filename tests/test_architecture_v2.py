"""Architecture tests for backend-neutral and backend-specific namespaces."""

from __future__ import annotations

import ast
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
BACKENDS = {"gamma", "isce3"}
ANALYSIS_ROOTS = {"geodesy_analysis", "mintpy", "dsinsar"}


def _module_name(source_root: Path, path: Path) -> str:
    return ".".join(path.relative_to(source_root).with_suffix("").parts)


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    return tuple(imported)


def _dependency_problems(source_root: Path) -> list[str]:
    problems: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        module = _module_name(source_root, path)
        parts = module.split(".")
        is_common = parts[1:2] == ["common"] or "common" in parts[2:]
        source_backends = BACKENDS.intersection(parts)
        for imported in _imports(path):
            imported_parts = imported.lower().split(".")
            imported_backends = BACKENDS.intersection(imported_parts)
            if imported_parts[0] in ANALYSIS_ROOTS:
                problems.append(f"{module} imports Analysis dependency {imported}")
            if is_common and imported_backends:
                problems.append(f"{module} common code imports backend {imported}")
            if source_backends and imported_backends - source_backends:
                problems.append(f"{module} imports sibling backend {imported}")
    return problems


class ArchitectureV2Tests(unittest.TestCase):
    """Keep common code independent from processing backends and Analysis."""

    def test_current_source_obeys_dependency_direction(self) -> None:
        self.assertEqual(_dependency_problems(SOURCE_ROOT), [])

    def test_common_to_backend_import_is_rejected(self) -> None:
        problems = self._fixture_problems(
            "geodesy_preprocessing/sar/common/bad.py",
            "from geodesy_preprocessing.sar.rslc_registration.gamma import tops\n",
        )
        self.assertRegex("\n".join(problems), "common code imports backend")

    def test_backend_to_sibling_backend_import_is_rejected(self) -> None:
        problems = self._fixture_problems(
            "geodesy_preprocessing/sar/rslc_registration/gamma/bad.py",
            "from geodesy_preprocessing.sar.rslc_registration.isce3 import stripmap\n",
        )
        self.assertRegex("\n".join(problems), "imports sibling backend")

    def test_preprocessing_to_analysis_import_is_rejected(self) -> None:
        for dependency in ("geodesy_analysis", "mintpy", "dsinsar"):
            with self.subTest(dependency=dependency):
                problems = self._fixture_problems(
                    "geodesy_preprocessing/sar/validation/bad.py",
                    f"import {dependency}\n",
                )
                self.assertRegex("\n".join(problems), "Analysis dependency")

    def test_compatibility_only_top_level_modules_are_absent(self) -> None:
        package = SOURCE_ROOT / "geodesy_preprocessing"
        for removed in (
            "progress.py",
            "dem/gamma.py",
            "dem/opentopography.py",
        ):
            self.assertFalse((package / removed).exists(), removed)

    def test_gamma_radar_grid_registration_is_backend_scoped(self) -> None:
        root = (
            SOURCE_ROOT
            / "geodesy_preprocessing/sar/rslc_registration/gamma/radar_grid"
        )
        self.assertTrue((root / "geo_ref_cli.py").is_file())
        self.assertTrue((root / "coreg_cli.py").is_file())
        self.assertFalse(
            (SOURCE_ROOT / "geodesy_preprocessing/sar/rslc_registration/radar_grid").exists()
        )

    def test_nisar_acquisition_is_backend_neutral_and_gamma_import_is_scoped(self) -> None:
        acquisition = SOURCE_ROOT / "geodesy_preprocessing/sar/acquisition/nisar"
        adapter = (
            SOURCE_ROOT
            / "geodesy_preprocessing/sar/slc_generation/gamma/radar_grid/adapters/nisar.py"
        )
        self.assertTrue((acquisition / "query.py").is_file())
        self.assertTrue((acquisition / "download.py").is_file())
        self.assertTrue(adapter.is_file())
        for path in acquisition.glob("*.py"):
            self.assertFalse(
                any(".gamma" in imported for imported in _imports(path)),
                path,
            )

    def test_nisar_gslc_subsetting_is_not_inside_a_processing_backend(self) -> None:
        native = SOURCE_ROOT / "geodesy_preprocessing/sar/gslc_subsetting/nisar"
        self.assertTrue((native / "cli.py").is_file())
        self.assertTrue((native / "runner.py").is_file())
        for path in native.glob("*.py"):
            self.assertFalse(any(".gamma" in imported for imported in _imports(path)), path)

    def test_removed_stage_names_are_absent(self) -> None:
        sar = SOURCE_ROOT / "geodesy_preprocessing/sar"
        for removed in ("registration", "subsetting", "preparation"):
            self.assertFalse((sar / removed).exists(), removed)

    def _fixture_problems(self, relative_path: str, source: str) -> list[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source_root = Path(temporary_directory) / "src"
            path = source_root / relative_path
            path.parent.mkdir(parents=True)
            path.write_text(source, encoding="utf-8")
            return _dependency_problems(source_root)


if __name__ == "__main__":
    unittest.main()
