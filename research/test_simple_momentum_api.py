"""Real local HTTP handlers with synthetic paper state, never broker access."""
import copy
import io
import json
import os
import zipfile
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

# Import-time V3 construction must never touch production persistence in tests.
_import_storage = tempfile.TemporaryDirectory()
with patch.dict(os.environ, {"PAPER_V3_DIR": _import_storage.name}):
    import server
import paper_trader
import simple_momentum_v1 as simple


class PaperAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = simple.SimpleMomentumPaper(Path(self.tmp.name)/"simple", None, simple.Config())
        self.addCleanup(self.engine.close)
        self.addCleanup(patch.stopall)
        patch.object(server, "SIMPLE_PAPER", self.engine).start()
        with patch.dict(os.environ, {"PAPER_V3_DIR": str(Path(self.tmp.name)/"v3")}):
            self.v3 = paper_trader.PaperV3Trader(self.tmp.name, lambda symbol: {
                "session": "REGULAR", "candidates": []})
        patch.object(server, "PAPER_V3", self.v3).start()
        patch.object(server.Handler, "log_message", lambda *args: None).start()
        self.token = "synthetic-admin-session"
        server.SESSIONS[self.token] = time.time()+60
        self.addCleanup(lambda: server.SESSIONS.pop(self.token, None))
        self.http = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)
        self.base = "http://127.0.0.1:"+str(self.http.server_port)

    def shutdown(self):
        self.http.shutdown()
        self.thread.join(timeout=2)
        self.http.server_close()

    def request(self, route, body=None, auth=True, engine="simple-v1"):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Bearer "+self.token
        req = urllib.request.Request(self.base+"/api/trading/paper/"+engine+"/"+route,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers=headers, method="GET" if body is None else "POST")
        try:
            with urllib.request.urlopen(req, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_all_three_endpoints_require_existing_admin_auth_before_initialization(self):
        with patch.object(server, "simple_paper", side_effect=AssertionError("unauthorized initialization")):
            for route, body in (("status", None), ("auto", {"enabled": True}), ("scan", {})):
                code, result = self.request(route, body, auth=False)
                self.assertEqual(code, 401)
                self.assertEqual(result["error"], "unauthorized")

    def test_authorized_controls_and_scan_never_touch_live_trader(self):
        initial_flags = copy.deepcopy(server.TRADING_STATE)
        live_flags = (server.LIVE_TRADER.engine_enabled, server.LIVE_TRADER.live_armed, server.LIVE_TRADER.auto_enabled)
        with patch.object(server.LIVE_TRADER, "_api", side_effect=AssertionError("live API forbidden")), \
             patch.object(server.LIVE_TRADER, "arm", side_effect=AssertionError("ARM forbidden")), \
             patch.object(self.engine, "scan", return_value=self.engine.status()) as scan:
            self.assertEqual(self.request("status")[0], 200)
            code, result = self.request("auto", {"enabled": True})
            self.assertEqual(code, 200)
            self.assertTrue(result["enabled"])
            self.assertEqual(result["mode"], "paper")
            self.assertFalse(result["liveOrderCapability"])
            self.assertEqual(self.request("scan", {})[0], 200)
            scan.assert_called_once()
            self.assertEqual(self.request("auto", {"enabled": False})[0], 200)
        self.assertEqual(server.TRADING_STATE, initial_flags)
        self.assertEqual((server.LIVE_TRADER.engine_enabled, server.LIVE_TRADER.live_armed, server.LIVE_TRADER.auto_enabled), live_flags)

    def test_invalid_enabled_values_cannot_turn_on_paper(self):
        for value in ("false", 1, None):
            self.assertEqual(self.request("auto", {"enabled": value})[0], 400)
            self.assertFalse(self.engine.state["enabled"])

    def test_v3_endpoints_require_unchanged_admin_auth(self):
        for route, body in (("status", None), ("auto", {"enabled": True}), ("scan", {})):
            self.assertEqual(self.request(route, body, auth=False, engine="v3")[0], 401)
        self.assertFalse(self.v3.enabled)

    def test_real_v3_runner_survives_and_accounts_controls_are_independent(self):
        live_flags = (server.LIVE_TRADER.engine_enabled, server.LIVE_TRADER.live_armed,
                      server.LIVE_TRADER.auto_enabled)
        self.assertNotEqual(self.v3.state_path, self.engine.state_path)
        with patch.object(server.LIVE_TRADER, "_api", side_effect=AssertionError("live orders forbidden")), \
             patch.object(server.LIVE_TRADER, "arm", side_effect=AssertionError("ARM forbidden")):
            self.assertEqual(self.request("auto", {"enabled": True}, engine="v3")[0], 200)
            self.assertFalse(self.engine.state["enabled"])
            self.assertTrue(self.v3.enabled)
            self.assertEqual(self.request("auto", {"enabled": True})[0], 200)
            self.assertTrue(self.engine.state["enabled"])
            simple_before = copy.deepcopy(self.engine.state)
            candidate = dict(symbol="V3ONLY", price=100, stopPrice=98, targetPrice=105,
                             bestStrategy="VWAP_MEAN_REVERSION", signal="BUY")
            self.v3.market_snapshot = lambda symbol: dict(session="REGULAR", candidates=[candidate])
            self.assertEqual(self.request("scan", {}, engine="v3")[0], 200)
            self.assertEqual(self.v3.position["symbol"], "V3ONLY")
            self.assertEqual(self.engine.state, simple_before)
            self.v3.market_snapshot = lambda symbol: dict(session="REGULAR", candidates=[],
                                                        managed=dict(symbol=symbol, price=96))
            self.assertEqual(self.request("scan", {}, engine="v3")[0], 200)
            self.assertEqual(self.v3.closed_count, 1)
            self.assertIsNone(self.v3.position)
            self.assertEqual(self.engine.state, simple_before)
            self.assertEqual(self.engine.recent_trades, [])
            self.assertTrue(self.v3.trades_path.exists())
            self.assertFalse((self.engine.directory/"trades.jsonl").exists())
            self.assertEqual(self.request("auto", {"enabled": False}, engine="v3")[0], 200)
            self.assertTrue(self.engine.state["enabled"])
            self.assertFalse(self.v3.enabled)
            self.assertEqual(self.request("status", engine="v3")[1]["engine"], "strategy-engine-v3")
        self.assertEqual((server.LIVE_TRADER.engine_enabled, server.LIVE_TRADER.live_armed,
                          server.LIVE_TRADER.auto_enabled), live_flags)
        with patch.dict(os.environ, {"PAPER_V3_DIR": str(self.v3.data_dir)}):
            restored = paper_trader.PaperV3Trader(self.tmp.name, self.v3.market_snapshot)
        self.assertEqual(restored.closed_count, 1)
        self.assertEqual(restored.cash, self.v3.cash)
        self.assertEqual(restored.recent_trades, self.v3.recent_trades)

    def test_authenticated_paper_log_download_contains_v3_and_simple_files(self):
        self.v3._save_state()
        (self.v3.trades_path).write_text('{"strategy":"v3"}\n', encoding="utf-8")
        (self.v3.decisions_path).write_text('{"decision":"v3"}\n', encoding="utf-8")
        self.engine._save()
        (self.engine.directory/"decisions.jsonl").write_text('{"strategy":"simple"}\n', encoding="utf-8")
        req = urllib.request.Request(self.base+"/api/trading/paper/logs/download",
                                     headers={"Authorization": "Bearer "+self.token}, method="GET")
        with urllib.request.urlopen(req, timeout=3) as response:
            self.assertEqual(response.status, 200)
            self.assertEqual(response.headers.get_content_type(), "application/zip")
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = set(archive.namelist())
        self.assertIn("manifest.json", names)
        self.assertIn("v3/state.json", names)
        self.assertIn("v3/trades.jsonl", names)
        self.assertIn("v3/decisions.jsonl", names)
        self.assertIn("simple_v1/state.json", names)
        self.assertIn("simple_v1/decisions.jsonl", names)

    def test_paper_log_download_requires_admin_auth(self):
        req = urllib.request.Request(self.base+"/api/trading/paper/logs/download", method="GET")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=3)
        self.assertEqual(caught.exception.code, 401)

    def test_same_storage_configuration_fails_before_initialization(self):
        with patch.object(server, "SIMPLE_PAPER", None), \
             patch.object(server, "persistent_directory", return_value=self.v3.data_dir):
            with self.assertRaisesRegex(ValueError, "must be different"):
                server.simple_paper()

    def test_simple_execution_does_not_change_real_v3_account(self):
        from research.test_simple_momentum_v1 import candidate, valid_bars, moment, session, observation
        self.v3.set_enabled(True)
        v3_before = copy.deepcopy(self.v3.status())
        self.engine.set_enabled(True)
        with patch.object(server.LIVE_TRADER, "_api", side_effect=AssertionError("live orders forbidden")):
            self.engine.process_snapshot([candidate()], {"ABC": valid_bars()}, moment(), session())
            self.engine.process_snapshot([], {}, moment(10, 1), session(),
                                         observations={"ABC": observation(104, moment(10, 1))})
            self.assertIsNotNone(self.engine.state["openPosition"])
            self.engine.process_snapshot([], {}, moment(10, 2), session(),
                                         observations={"ABC": observation(97, moment(10, 2))})
        self.assertEqual(self.engine.state["completedTrades"], 1)
        self.assertEqual(self.v3.status(), v3_before)
        self.assertEqual(self.v3.recent_trades, [])


if __name__ == "__main__":
    unittest.main()
