"""Namespace and task-identity tests for GAMMA TOPS SLC generation."""

from __future__ import annotations

import io
import tomllib
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import SlcConfigCase
from geodesy_preprocessing.sar.slc_generation.gamma.tops.plan import SlcBatchPlan, build_slc_plan
from geodesy_preprocessing.sar.slc_generation.gamma.tops.cli import main
from geodesy_preprocessing.sar.slc_generation.gamma.tops.config import (
    GammaTopsSlcCase,
)
from geodesy_preprocessing.sar.slc_generation.gamma.tops.plan import (
    GammaTopsSlcPlan,
    build_gamma_tops_slc_plan,
)
from geodesy_preprocessing.sar.slc_generation.gamma.tops.runner import (
    DEFAULT_GAMMA_TOPS_SLC_TASK,
    run_gamma_tops_slc,
)


class GammaTopsSlcNamespaceTests(unittest.TestCase):
    """Keep one backend-qualified TOPS implementation and public command."""

    def test_canonical_aliases_share_one_implementation(self) -> None:
        self.assertIs(GammaTopsSlcCase, SlcConfigCase)
        self.assertIs(GammaTopsSlcPlan, SlcBatchPlan)
        self.assertIs(build_gamma_tops_slc_plan, build_slc_plan)

    def test_new_runner_has_backend_qualified_default_task(self) -> None:
        self.assertEqual(DEFAULT_GAMMA_TOPS_SLC_TASK, "PRE_GAMMA_SAR_TOPS_SLC")
        self.assertTrue(callable(run_gamma_tops_slc))

    def test_new_cli_help_names_gamma_tops(self) -> None:
        terminal = io.StringIO()
        with self.assertRaises(SystemExit) as stopped, redirect_stdout(terminal):
            main(["--help"])
        self.assertEqual(stopped.exception.code, 0)
        self.assertIn("GAMMA", terminal.getvalue())
        self.assertIn("TOPS", terminal.getvalue())

    def test_backend_qualified_command_is_registered(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with (root / "pyproject.toml").open("rb") as stream:
            project = tomllib.load(stream)
        scripts = project["project"]["scripts"]
        self.assertEqual(
            scripts["geodesy-pre-gamma-sar-tops-slc"],
            "geodesy_preprocessing.sar.slc_generation.gamma.tops.config_cli:main",
        )

    def test_canonical_template_is_operationally_documented(self) -> None:
        root = Path(__file__).resolve().parents[1]
        template = root / "config_examples" / "gamma_sar_tops_slc.cfg"
        required_text = (
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
            "[project]",
            "[defaults]",
            "[case:main]",
        )
        contents = template.read_text(encoding="utf-8")
        for marker in required_text:
            self.assertIn(marker, contents)
        self.assertIn("geodesy-pre-gamma-sar-tops-slc", contents)


if __name__ == "__main__":
    unittest.main()
