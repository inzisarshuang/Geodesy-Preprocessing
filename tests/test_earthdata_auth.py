"""Tests for shared Earthdata credentials without CFG secrets."""

from __future__ import annotations

import os
import tempfile
import traceback
import unittest
from pathlib import Path
from unittest import mock

from geodesy_preprocessing.sar.acquisition.earthdata import (
    earthdata_session_from_environment,
)


class _Session:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def auth_with_token(self, token):
        self.calls.append(("token", token))
        if self.fail:
            raise RuntimeError(f"bad {token}")
        return self

    def auth_with_creds(self, username, password):
        self.calls.append(("creds", username, password))
        if self.fail:
            raise RuntimeError(f"bad {username}:{password}")
        return self


class EarthdataAuthTests(unittest.TestCase):
    def test_token_and_paired_credentials_are_supported(self) -> None:
        session = _Session()
        with mock.patch.dict(os.environ, {"EARTHDATA_TOKEN": "TOKEN"}, clear=True):
            self.assertIs(earthdata_session_from_environment(session_factory=lambda: session), session)
        self.assertEqual(session.calls, [("token", "TOKEN")])

        session = _Session()
        with mock.patch.dict(os.environ, {"EARTHDATA_USERNAME": "USER", "EARTHDATA_PASSWORD": "PASS"}, clear=True):
            earthdata_session_from_environment(session_factory=lambda: session)
        self.assertEqual(session.calls, [("creds", "USER", "PASS")])

    def test_conflicting_or_incomplete_environment_is_rejected(self) -> None:
        invalid = (
            {"EARTHDATA_TOKEN": "T", "EARTHDATA_USERNAME": "U", "EARTHDATA_PASSWORD": "P"},
            {"EARTHDATA_USERNAME": "U"},
            {"EARTHDATA_PASSWORD": "P"},
        )
        for values in invalid:
            with self.subTest(values=tuple(values)), mock.patch.dict(os.environ, values, clear=True), self.assertRaises(ValueError):
                earthdata_session_from_environment(session_factory=_Session)

    def test_netrc_is_used_only_when_environment_is_absent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".netrc"
            path.write_text("machine urs.earthdata.nasa.gov login NETUSER password NETPASS\n", encoding="utf-8")
            path.chmod(0o600)
            session = _Session()
            with mock.patch.dict(os.environ, {}, clear=True):
                earthdata_session_from_environment(netrc_path=path, session_factory=lambda: session)
        self.assertEqual(session.calls, [("creds", "NETUSER", "NETPASS")])

    def test_absent_credentials_and_authentication_errors_are_secret_free(self) -> None:
        with tempfile.TemporaryDirectory() as directory, mock.patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(ValueError, "credentials"):
            earthdata_session_from_environment(netrc_path=Path(directory) / "missing", session_factory=_Session)
        with mock.patch.dict(os.environ, {"EARTHDATA_TOKEN": "TOKEN-SECRET"}, clear=True), self.assertRaises(RuntimeError) as raised:
            earthdata_session_from_environment(session_factory=lambda: _Session(fail=True))
        trace = "".join(traceback.format_exception(type(raised.exception), raised.exception, raised.exception.__traceback__))
        self.assertNotIn("TOKEN-SECRET", trace)
        self.assertIn("authentication failed", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
