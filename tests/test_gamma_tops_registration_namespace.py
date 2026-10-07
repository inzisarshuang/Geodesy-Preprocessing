"""Namespace and boundary tests for GAMMA TOPS registration."""

from __future__ import annotations

import dataclasses
import io
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_config import CoregConfigCase
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_plan import CoregPlan, build_coreg_plan
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_config import GeoRefConfigCase
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_plan import GeoRefPlan, build_geo_ref_plan
from geodesy_preprocessing.sar.rslc_registration.common.result import RegistrationResult
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_cli import (
    main as coreg_main,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_config import (
    GammaTopsCoregCase,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.coreg_plan import (
    GammaTopsCoregPlan,
    build_gamma_tops_coreg_plan,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_cli import (
    main as geo_ref_main,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_config import (
    GammaTopsGeoRefCase,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.tops.geo_ref_plan import (
    GammaTopsGeoRefPlan,
    build_gamma_tops_geo_ref_plan,
)


class GammaTopsRegistrationNamespaceTests(unittest.TestCase):
    """Require one implementation and backend-neutral published boundaries."""

    def test_canonical_aliases_share_one_implementation(self) -> None:
        self.assertIs(GammaTopsGeoRefCase, GeoRefConfigCase)
        self.assertIs(GammaTopsGeoRefPlan, GeoRefPlan)
        self.assertIs(build_gamma_tops_geo_ref_plan, build_geo_ref_plan)
        self.assertIs(GammaTopsCoregCase, CoregConfigCase)
        self.assertIs(GammaTopsCoregPlan, CoregPlan)
        self.assertIs(build_gamma_tops_coreg_plan, build_coreg_plan)

    def test_common_result_is_immutable_and_backend_neutral(self) -> None:
        result = RegistrationResult(
            manifest_path=Path("stack.json"),
            reference_date="20240101",
            dates=("20240101", "20240113"),
            quality_path=Path("quality/summary"),
            native_root=Path("RSLC"),
        )
        self.assertNotIn("gamma", {field.name for field in dataclasses.fields(result)})
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.reference_date = "20240113"  # type: ignore[misc]

    def test_new_cli_help_identifies_gamma_tops(self) -> None:
        for command in (geo_ref_main, coreg_main):
            with self.subTest(command=command.__module__):
                terminal = io.StringIO()
                with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
                    command(["--help"])
                self.assertEqual(stopped.exception.code, 0)
                self.assertIn("GAMMA", terminal.getvalue())
                self.assertIn("TOPS", terminal.getvalue())

    def test_commands_and_operational_templates_are_registered(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            scripts = tomllib.load(stream)["project"]["scripts"]
        self.assertIn("geodesy-pre-gamma-sar-tops-geo-ref", scripts)
        self.assertIn("geodesy-pre-gamma-sar-tops-coreg", scripts)
        templates = (
            "gamma_sar_tops_geo_ref.cfg",
            "gamma_sar_tops_coreg.cfg",
        )
        markers = (
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
            "reference",
            "range_looks",
            "azimuth_looks",
            "【必填】",
            "【常改】",
            "【通常不改】",
            "【谨慎修改】",
        )
        for name in templates:
            with self.subTest(template=name):
                contents = (root / "config_examples" / name).read_text(encoding="utf-8")
                for marker in markers:
                    self.assertIn(marker, contents)


if __name__ == "__main__":
    unittest.main()
