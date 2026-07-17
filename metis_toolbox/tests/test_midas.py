"""
test_midas.py — unit tests for tools/midas.py's Vault-backed Finnhub key.

Hermetic: the network seam (requests.get) and the Cerberus-key seam
(midas._finnhub_key / cerberus.is_unlocked) are mocked, so no test touches the
network or the real Cerberus Vault. Covers the migration off .env/env onto the
Cerberus Vault (mirrors test_callimachus.py's _brave_key coverage):

    python -X utf8 -m unittest tests.test_midas
"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cerberus
from tools import midas


class _FakeResp:
    """Stand-in for requests.get's return value: .json(), .raise_for_status()."""

    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self._status = status

    def raise_for_status(self) -> None:
        if self._status >= 400:
            raise Exception(f"HTTP {self._status}")

    def json(self) -> dict:
        return self._payload


class TestFetchOneVaultKey(unittest.TestCase):
    def setUp(self) -> None:
        self._unlocked = mock.patch.object(cerberus, "is_unlocked", return_value=True)
        self._unlocked.start()
        self.addCleanup(self._unlocked.stop)

    def test_locked_vault_is_vault_locked(self) -> None:
        with mock.patch.object(cerberus, "is_unlocked", return_value=False):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_VAULT_LOCKED})

    def test_unlocked_but_no_key_is_no_key(self) -> None:
        with mock.patch.object(midas, "_finnhub_key",
                               side_effect=cerberus.VaultError("no vault entry")):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_NO_KEY})

    def test_success_shapes_result(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("requests.get",
                           return_value=_FakeResp({"c": 105.0, "pc": 100.0})) as get:
            out = midas._fetch_one("AAPL")
        self.assertNotIn("error", out)
        self.assertEqual(out["direction"], "up")
        self.assertEqual(out["pct"], 5.0)
        # The Vault key is passed as the request token, never hardcoded.
        self.assertEqual(get.call_args.kwargs["params"]["token"], "fake-key")

    def test_zero_fields_is_no_data(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("requests.get", return_value=_FakeResp({"c": 0, "pc": 0})):
            out = midas._fetch_one("BADSYM")
        self.assertEqual(out, {"symbol": "BADSYM", "error": midas.ERR_NO_DATA})

    def test_request_exception_is_fetch_failed(self) -> None:
        with mock.patch.object(midas, "_finnhub_key", return_value="fake-key"), \
                mock.patch("requests.get", side_effect=Exception("boom")):
            out = midas._fetch_one("AAPL")
        self.assertEqual(out, {"symbol": "AAPL", "error": midas.ERR_FETCH_FAILED})

    def test_key_never_cached_across_calls(self) -> None:
        # Each fetch re-derives the key at call time — never memoized on the
        # module — so a key rotated mid-session takes effect on the next call.
        with mock.patch.object(midas, "_finnhub_key",
                               side_effect=["key-1", "key-2"]) as key_fn, \
                mock.patch("requests.get",
                           return_value=_FakeResp({"c": 1.0, "pc": 1.0})):
            midas._fetch_one("AAPL")
            midas._fetch_one("AAPL")
        self.assertEqual(key_fn.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
