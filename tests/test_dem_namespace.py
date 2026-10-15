"""Tests for the permanent DEM acquisition and conversion namespaces."""

from __future__ import annotations

import unittest
import tomllib
from importlib.util import find_spec
from pathlib import Path

from geodesy_preprocessing.dem.config import (
    ALLOWED_CONFIG_KEYS,
    load_config,
    validate_config_keys,
)
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class DemNamespaceTests(unittest.TestCase):
    """Ensure DEM stages expose only their permanent namespaces."""

    def test_obsolete_compatibility_modules_are_absent(self) -> None:
        self.assertIsNone(find_spec("geodesy_preprocessing.dem.opentopography"))
        self.assertIsNone(find_spec("geodesy_preprocessing.dem.gamma"))

    def test_installed_command_points_to_gamma_conversion_cli(self) -> None:
        metadata = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata["project"]["scripts"]["geodesy-pre-gamma-dem-convert"],
            "geodesy_preprocessing.dem.conversion.gamma.cli:main",
        )

    def test_download_template_supports_the_complete_dem_workflow(self) -> None:
        acquisition_template = REPOSITORY_ROOT / "config_examples/opentopo_dem_download.cfg"
        text = acquisition_template.read_text(encoding="utf-8")
        for marker in (
            "# 功能：",
            "# 输入：",
            "# 输出：",
            "# 边界：",
            "# 查看帮助：",
            "# 正式运行：",
            "【必填】",
            "【常改】",
            "【通常不改】",
            "【谨慎修改】",
            "# 结果和日志",
            "# 完成判断",
            "# 重跑规则",
            "# 常见问题",
            "# 凭据安全",
        ):
            self.assertIn(marker, text)

        acquisition_values = load_config(acquisition_template)
        validate_config_keys(acquisition_values, ALLOWED_CONFIG_KEYS, acquisition_template)
        self.assertEqual(acquisition_values["run_srtm2dem"], "true")
        self.assertEqual(acquisition_values["project_root"], "../..")
        self.assertEqual(acquisition_values["topography_dataset_id"], "area_cop30")
        self.assertNotIn("output_dir", acquisition_values)
        self.assertNotIn("srtm2dem_output_dir", acquisition_values)
        self.assertNotIn("api_key", acquisition_values)


if __name__ == "__main__":
    unittest.main()
