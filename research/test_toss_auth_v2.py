"""Offline tests for Toss Auth V2. No network, real credentials or production changes."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import toss_auth


class AuthV2Tests(unittest.TestCase):
    def test_broker_mode_fails_closed_without_local_mint(self):
        with patch.dict(os.environ, {"TOSS_TOKEN_SOCKET": "/nonexistent/toss-auth-v2.sock"}):
            with patch("toss_auth.urllib.request.urlopen", side_effect=AssertionError("must not mint")):
                with self.assertRaises((OSError, RuntimeError)):
                    toss_auth.get_token({"toss": {"app_key": "test", "app_secret": "test"}})

    def test_broker_mode_does_not_require_local_client_secrets(self):
        with patch.dict(os.environ, {"TOSS_TOKEN_SOCKET": "/test.sock"}):
            with patch("toss_auth.broker_token", return_value="shared-token") as broker:
                self.assertEqual(toss_auth.get_token({}, force=True, rejected_token="old"), "shared-token")
                broker.assert_called_once_with("/test.sock", force=True, rejected_token="old")

    def test_same_cache_reuses_rotated_token_instead_of_minting(self):
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"TOSS_TOKEN_SOCKET": ""}):
            cache = Path(d) / "cache.json"
            lock = Path(d) / "cache.lock"
            with patch.object(toss_auth, "CACHE", cache), patch.object(toss_auth, "LOCK", lock):
                cache.write_text(json.dumps({
                    "access_token": "new", "expires_at": 9999999999, "issued_at": 100
                }))
                with patch("toss_auth.urllib.request.urlopen", side_effect=AssertionError("must not mint")):
                    self.assertEqual(toss_auth.get_token({"toss": {"app_key": "a", "app_secret": "b"}},
                        force=True, rejected_token="old"), "new")


if __name__ == "__main__":
    unittest.main()
