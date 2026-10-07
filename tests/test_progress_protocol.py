"""Tests for the language-neutral Workflow progress producer."""

from __future__ import annotations

import unittest

from geodesy_preprocessing.common.progress import emit_progress
from geodesy_preprocessing.common.progress import emit_progress as emit_shared_progress


class ProgressProtocolTests(unittest.TestCase):
    """Keep emitted lines parseable, deterministic, and credential-free."""

    def test_emits_compact_sorted_literal_protocol_line(self) -> None:
        self.assertEqual(
            emit_progress(
                event="item_started",
                task="PRE_S1_SLC",
                item="20240929",
                index=3,
                total=18,
                stage="import",
                status="running",
                timestamp="2026-09-18T10:00:00Z",
                orbit="154",
            ),
            '@@GEODESY_PROGRESS {"event":"item_started","index":3,'
            '"item":"20240929","orbit":"154","stage":"import",'
            '"status":"running","task":"PRE_S1_SLC",'
            '"timestamp":"2026-09-18T10:00:00Z","total":18}',
        )

    def test_rejects_secret_like_detail_keys(self) -> None:
        for key in ("password", "api_key", "access_token", "Authorization"):
            with self.subTest(key=key):
                with self.assertRaisesRegex(ValueError, "secret"):
                    emit_progress(
                        event="run_started",
                        task="PRE_S1_SLC",
                        timestamp="2026-09-18T10:00:00Z",
                        **{key: "must-not-appear"},
                )

    def test_shared_and_legacy_import_paths_emit_identical_lines(self) -> None:
        arguments = {
            "event": "item",
            "task": "PRE_OTHER_SAR_SLC",
            "timestamp": "2026-09-22T10:00:00Z",
            "item": "20260922",
            "index": 1,
            "total": 2,
            "stage": "import",
            "status": "running",
        }
        self.assertEqual(emit_shared_progress(**arguments), emit_progress(**arguments))

    def test_shared_protocol_rejects_secret_like_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "secret-like"):
            emit_shared_progress(
                event="item",
                task="PRE_OTHER_SAR_SLC",
                api_token="do-not-log",
            )


if __name__ == "__main__":
    unittest.main()
