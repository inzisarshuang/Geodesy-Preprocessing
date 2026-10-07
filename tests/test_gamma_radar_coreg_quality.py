"""Tests for strict GAMMA SLC_coreg.py quality parsing and classification."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.coreg_config import (
    GammaRadarQualityThresholds,
)
from geodesy_preprocessing.sar.rslc_registration.gamma.radar_grid.quality import (
    classify_coreg_quality,
    parse_coreg_quality,
)


QUALITY = """SLC coregistration quality file
final solution: 3418 offset estimates accepted out of 4096 samples
final range offset poly. coeff.: -0.04622 2.9e-7
final azimuth offset poly. coeff.: -0.01461 -1.0e-7
final model fit std. dev. (samples) range: 0.0242 azimuth: 0.0204
end of SLC_coreg.py
"""


class GammaRadarCoregQualityTests(unittest.TestCase):
    """Parse the final fit only and block absent or hard-failure metrics."""

    def _quality(self, text: str = QUALITY):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / "scene.coreg_quality"
        path.write_text(text, encoding="utf-8")
        return parse_coreg_quality(path)

    def test_parses_accepted_counts_fit_deviations_and_residual_offsets(self) -> None:
        quality = self._quality()
        self.assertEqual((quality.accepted_offsets, quality.total_offsets), (3418, 4096))
        self.assertEqual((quality.range_stddev, quality.azimuth_stddev), (0.0242, 0.0204))
        self.assertEqual((quality.range_residual, quality.azimuth_residual), (-0.04622, -0.01461))

    def test_unparseable_quality_blocks_stack_publication(self) -> None:
        for text in ("", QUALITY.replace("final solution:", "solution:"), QUALITY.replace("end of SLC_coreg.py", "")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "quality"):
                    self._quality(text)

    def test_quality_thresholds_distinguish_warning_and_failure(self) -> None:
        thresholds = GammaRadarQualityThresholds(0.10, 0.30, 0.20, 0.50)
        self.assertEqual(classify_coreg_quality(self._quality(), thresholds), "complete")
        warning = self._quality(QUALITY.replace("range: 0.0242", "range: 0.15"))
        self.assertEqual(classify_coreg_quality(warning, thresholds), "warning")
        failed = self._quality(QUALITY.replace("-0.04622", "-0.60"))
        self.assertEqual(classify_coreg_quality(failed, thresholds), "failed")


if __name__ == "__main__":
    unittest.main()
