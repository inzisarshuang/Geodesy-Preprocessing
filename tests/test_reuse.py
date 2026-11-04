"""Tests for completion-aware reuse of expensive preprocessing outputs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from geodesy_preprocessing.common.reuse import ReuseState, classify_output


class ReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output = self.root / "RSLC"

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _manifest(self, **updates: object) -> None:
        payload: dict[str, object] = {
            "status": "complete",
            "input_signature": "same-inputs",
            "required_products": ["registered_stack.json", "20240101/scene.rslc"],
        }
        payload.update(updates)
        (self.output / "completion.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    @staticmethod
    def _validate(output: Path, manifest: dict[str, object]) -> bool:
        return all(
            (output / str(name)).is_file()
            and (output / str(name)).stat().st_size > 0
            for name in manifest["required_products"]  # type: ignore[index]
        )

    def test_absent_output_may_run(self) -> None:
        decision = classify_output(
            self.output, "completion.json", "same-inputs", self._validate
        )
        self.assertEqual(decision.state, ReuseState.ABSENT)

    def test_complete_matching_output_is_reusable(self) -> None:
        (self.output / "20240101").mkdir(parents=True)
        (self.output / "registered_stack.json").write_text("{}", encoding="utf-8")
        (self.output / "20240101/scene.rslc").write_bytes(b"science")
        self._manifest()

        decision = classify_output(
            self.output, "completion.json", "same-inputs", self._validate
        )

        self.assertEqual(decision.state, ReuseState.COMPLETE)
        self.assertTrue(decision.reusable)

    def test_missing_product_is_incomplete_with_delete_or_replace_guidance(self) -> None:
        self.output.mkdir()
        (self.output / "registered_stack.json").write_text("{}", encoding="utf-8")
        self._manifest()

        decision = classify_output(
            self.output, "completion.json", "same-inputs", self._validate
        )

        self.assertEqual(decision.state, ReuseState.INCOMPLETE)
        self.assertIn(str(self.output), decision.guidance)
        self.assertIn("delete", decision.guidance.lower())

    def test_missing_or_malformed_manifest_is_incomplete(self) -> None:
        for malformed in (None, "not-json"):
            with self.subTest(malformed=malformed):
                self.output.mkdir(exist_ok=True)
                if malformed is not None:
                    (self.output / "completion.json").write_text(
                        malformed, encoding="utf-8"
                    )
                decision = classify_output(
                    self.output, "completion.json", "same-inputs", self._validate
                )
                self.assertEqual(decision.state, ReuseState.INCOMPLETE)
                (self.output / "completion.json").unlink(missing_ok=True)

    def test_changed_signature_is_mismatch(self) -> None:
        self.output.mkdir()
        self._manifest(input_signature="old-inputs")

        decision = classify_output(
            self.output, "completion.json", "new-inputs", self._validate
        )

        self.assertEqual(decision.state, ReuseState.MISMATCH)
        self.assertIn("signature", decision.reason)

    def test_symlink_output_is_never_reused(self) -> None:
        target = self.root / "target"
        target.mkdir()
        self.output.symlink_to(target, target_is_directory=True)

        decision = classify_output(
            self.output, "completion.json", "same-inputs", self._validate
        )

        self.assertEqual(decision.state, ReuseState.MISMATCH)
        self.assertIn("symbolic link", decision.reason)


if __name__ == "__main__":
    unittest.main()
