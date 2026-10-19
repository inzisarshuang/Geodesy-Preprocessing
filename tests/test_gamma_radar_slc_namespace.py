"""Parity tests for the permanent GAMMA radar-grid SLC namespace."""

from __future__ import annotations

import io
import os
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from fixtures.other_sar import write_cosmo_product
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    OtherSarSlcCase,
    load_other_sar_slc_config,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.plan import build_slc_plan
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.runner import run_slc_batch
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.cli import main
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.config import (
    GammaRadarSlcCase,
    load_gamma_radar_slc_config,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.plan import (
    DEFAULT_ADAPTERS,
    build_gamma_radar_slc_plan,
)
from geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.runner import (
    run_gamma_radar_slc,
)


FIXTURE = Path(__file__).parent / "fixtures/fake_other_sar_command.py"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class GammaRadarSlcNamespaceTests(unittest.TestCase):
    """Exercise the permanent GAMMA radar-grid SLC namespace."""

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "RAW/main").mkdir(parents=True)
        write_cosmo_product(self.root / "RAW/main", sensor="csg", date="20260922")
        self.config = self.root / "gamma_slc_grid.cfg"
        self.config.write_text(
            f"""[project]
root = {self.root}
[defaults]
range_looks = 10
azimuth_looks = 10
[case:main]
sensor = csg
raw_dir = RAW/main
slc_dir = SLC/main
import_command = {sys.executable} {FIXTURE} cosmo
""",
            encoding="utf-8",
        )

    def test_canonical_aliases_share_one_implementation(self) -> None:
        self.assertIs(GammaRadarSlcCase, OtherSarSlcCase)
        self.assertIs(build_gamma_radar_slc_plan, build_slc_plan)
        self.assertIs(run_gamma_radar_slc, run_slc_batch)
        self.assertEqual(
            set(DEFAULT_ADAPTERS),
            {"alos2", "csg", "csks", "lt1", "rsat2", "saocom", "nisar"},
        )

    def test_loader_uses_backend_qualified_task_identity(self) -> None:
        case = load_gamma_radar_slc_config(self.config)[0]
        self.assertEqual(case.workflow_task, "PRE_GAMMA_SAR_SLC")
        self.assertTrue(build_gamma_radar_slc_plan(case).products)

    def test_new_cli_identifies_gamma_and_emits_gamma_task_by_default(self) -> None:
        terminal = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("GAMMA", terminal.getvalue())

        terminal = io.StringIO()
        with patch.dict(os.environ, {"GEODESY_PROGRESS_PROTOCOL": "1"}), redirect_stdout(terminal):
            self.assertEqual(main(["--config", str(self.config)]), 0)
        self.assertIn('"task":"PRE_GAMMA_SAR_SLC"', terminal.getvalue())

    def test_command_and_template_match_their_entry_point(self) -> None:
        metadata = tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )
        self.assertEqual(
            metadata["project"]["scripts"]["geodesy-pre-gamma-slc-grid"],
            "geodesy_preprocessing.sar.slc_generation.gamma.radar_grid.cli:main",
        )
        self.assertNotIn(
            "geodesy-pre-gamma-sar-slc", metadata["project"]["scripts"]
        )
        templates = (("gamma_slc_grid.cfg", "geodesy-pre-gamma-slc-grid", "PRE_GAMMA_SAR_SLC"),)
        for filename, command, task in templates:
            path = REPOSITORY_ROOT / "config_examples/preprocessing" / filename
            text = path.read_text(encoding="utf-8")
            with self.subTest(filename=filename):
                for marker in (
                    "# 功能：",
                    "# 输入：",
                    "# 输出：",
                    "# 边界：",
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
                self.assertIn(command, text)
                self.assertIn(f"workflow_task = {task}", text)


if __name__ == "__main__":
    unittest.main()
