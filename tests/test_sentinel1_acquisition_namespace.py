"""Canonical namespace tests for backend-neutral Sentinel-1 acquisition tools."""

from __future__ import annotations

import unittest

from geodesy_preprocessing.sar.acquisition.sentinel1 import burst_table
from geodesy_preprocessing.sar.acquisition.sentinel1 import burst_table_cli
from geodesy_preprocessing.sar.acquisition.sentinel1 import coverage
from geodesy_preprocessing.sar.acquisition.nisar import cli as nisar_cli
from geodesy_preprocessing.sar.acquisition.nisar import coverage as nisar_coverage
from geodesy_preprocessing.sar.acquisition.nisar import download as nisar_download
from geodesy_preprocessing.sar.acquisition.nisar import hdf5 as nisar_hdf5
from geodesy_preprocessing.sar.acquisition.nisar import manifest as nisar_manifest
from geodesy_preprocessing.sar.acquisition.nisar import plotting as nisar_plotting
from geodesy_preprocessing.sar.acquisition.nisar import query as nisar_query
from geodesy_preprocessing.sar.acquisition.nisar import search as nisar_search


class Sentinel1AcquisitionNamespaceTests(unittest.TestCase):
    """Exercise the stage-first Sentinel-1 acquisition namespace."""

    def test_every_declared_public_object_is_available(self) -> None:
        for module in (
            burst_table,
            burst_table_cli,
            coverage,
            nisar_cli,
            nisar_coverage,
            nisar_download,
            nisar_hdf5,
            nisar_manifest,
            nisar_plotting,
            nisar_query,
            nisar_search,
        ):
            for name in module.__all__:
                with self.subTest(module=module.__name__, name=name):
                    self.assertTrue(hasattr(module, name))


if __name__ == "__main__":
    unittest.main()
