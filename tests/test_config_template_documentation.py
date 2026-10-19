"""Repository-wide acceptance checks for installed CFG templates."""

from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = tuple(sorted((ROOT / "config_examples").rglob("*.cfg")))


class ConfigTemplateDocumentationTests(unittest.TestCase):
    """Keep every installed CFG usable as a plain-language operating guide."""

    def test_every_template_contains_the_operational_sections_and_markers(self) -> None:
        required = (
            "功能用途",
            "适用范围",
            "不适用范围",
            "上一步",
            "下一步",
            "--help",
            "正式运行",
            "结果保存",
            "日志",
            "完成判断",
            "重新运行",
            "【必填】",
            "【常改】",
            "【通常不改】",
            "【谨慎修改】",
            "凭据安全",
        )
        self.assertTrue(TEMPLATES)
        problems: list[str] = []
        for template in TEMPLATES:
            contents = template.read_text(encoding="utf-8")
            for marker in required:
                if marker not in contents:
                    problems.append(f"{template.name}: missing {marker}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_documented_help_commands_are_installed(self) -> None:
        with (ROOT / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        documented: set[str] = set()
        pattern = re.compile(r"\b(geodesy-pre-[a-z0-9-]+)\s+--help\b")
        for template in TEMPLATES:
            documented.update(pattern.findall(template.read_text(encoding="utf-8")))
        self.assertTrue(documented)
        self.assertEqual(sorted(documented - scripts.keys()), [])

    def test_templates_do_not_store_secret_values(self) -> None:
        assignment = re.compile(
            r"^\s*(?P<key>[^#;=]*(?:password|token|secret|api_key)[^=]*)"
            r"\s*=\s*(?P<value>.+?)\s*$",
            re.IGNORECASE | re.MULTILINE,
        )
        problems: list[str] = []
        for template in TEMPLATES:
            for match in assignment.finditer(template.read_text(encoding="utf-8")):
                key = match.group("key").strip().lower()
                value = match.group("value").strip()
                if key.endswith("_env") or value in {"", "-"}:
                    continue
                problems.append(f"{template.name}: secret-like assignment {key}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_gamma_radar_geo_ref_template_documents_its_operational_identity(self) -> None:
        contents = (ROOT / "config_examples/preprocessing/gamma_geo_ref_grid.cfg").read_text(
            encoding="utf-8"
        )
        for marker in (
            "PRE_GAMMA_SAR_RADAR_GEO_REF",
            "LOG/PRE/GAMMA/SAR_RADAR_GRID_GEO_REF",
            "geodesy-pre-gamma-geo-ref-grid --config",
            "--case",
            "--dry-run",
            "reference_geometry.json",
            "WGS84",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contents)

    def test_nisar_templates_separate_rslc_and_gslc_acquisition(self) -> None:
        self.assertFalse((ROOT / "config_examples/nisar_download.cfg").exists())
        expectations = {
            "nisar_rslc_download.cfg": (
                "product_type = RSLC",
                "NISAR_RSLC_footprints_all.png",
                "INPUT/NISAR/RSLC",
            ),
            "nisar_gslc_download.cfg": (
                "product_type = GSLC",
                "NISAR_GSLC_footprints_all.png",
                "INPUT/NISAR/GSLC",
            ),
        }
        common = (
            "PRE_COMMON_NISAR_ACQUISITION",
            "geodesy-pre-sar-nisar-acquire --config",
            "--query",
            "--download",
            "query_manifest.json",
            "source_manifest.json",
            "main_only",
            "main_plus_auxiliary",
            ".netrc",
            "EARTHDATA_TOKEN",
            "LOG/PRE/COMMON/NISAR_ACQUISITION",
        )
        for filename, markers in expectations.items():
            contents = (ROOT / "config_examples/acquisition" / filename).read_text(
                encoding="utf-8"
            )
            for marker in common + markers:
                with self.subTest(filename=filename, marker=marker):
                    self.assertIn(marker, contents)

    def test_nisar_gslc_stack_template_documents_native_la_lb_handoff(self) -> None:
        contents = (ROOT / "config_examples/preprocessing/nisar_gslc_stack.cfg").read_text(
            encoding="utf-8"
        )
        for marker in (
            "PRE_NISAR_GSLC",
            "Native，不使用 GAMMA",
            "source_manifest = INPUT/NISAR/GSLC/source_manifest.json",
            "[case:la]",
            "frequency = A",
            "[case:lb]",
            "frequency = B",
            "--plan-only",
            "complex64",
            "registered_stack.json",
            "LOG/PRE/NISAR/GSLC",
            "不重投影或重采样复数相位",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contents)

    def test_sentinel1_download_template_documents_acquisition_and_safe_handoff(self) -> None:
        contents = (ROOT / "config_examples/acquisition/sentinel1_download.cfg").read_text(
            encoding="utf-8"
        )
        for marker in (
            "PRE_COMMON_SENTINEL1_ACQUISITION",
            "geodesy-pre-sar-sentinel1-acquire --config",
            "--query",
            "--download",
            "--merge",
            "product_type = BURST",
            "BURST、SLC、GRD",
            "query_manifest.json",
            "source_manifest.json",
            "safe_manifest.json",
            "download_layout = orbit_subdirectories",
            "images_NNN",
            ".netrc",
            "EARTHDATA_TOKEN",
            "LOG/PRE/COMMON/SENTINEL1_ACQUISITION",
        ):
            with self.subTest(marker=marker):
                self.assertIn(marker, contents)


if __name__ == "__main__":
    unittest.main()
