"""Authenticated generic backtest integration; subprocesses are never run."""
import io
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock,patch

_import_tmp=tempfile.TemporaryDirectory()
with patch.dict(os.environ,{"PAPER_V3_DIR":_import_tmp.name}):
    import server
from backtest_manager import BacktestManager,BACKTEST_ENGINES


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.manager=BacktestManager(self.root,self.root/"results")

    def test_exact_six_engine_commands_allowlisted(self):
        self.assertEqual(set(BACKTEST_ENGINES),{"simple-v1","v3","v4-ir1","v4-ir2","v4-ir3","v4-all"})
        for k in BACKTEST_ENGINES:
            cmd=self.manager.command(k)
            self.assertEqual(Path(cmd[1]).name,BACKTEST_ENGINES[k]["script"])
            if k.startswith("v4-"):
                self.assertEqual(cmd[cmd.index("--strategy")+1],k.removeprefix("v4-"))
            self.assertNotIn("shell",cmd)
        with self.assertRaises(ValueError):self.manager.command("../../orders")

    def test_conflict_blocks_every_engine_and_legacy_simple(self):
        with patch.object(self.manager,"active_job",return_value="v3"),patch("backtest_manager.subprocess.Popen") as popen:
            for k in BACKTEST_ENGINES:
                with self.assertRaisesRegex(ValueError,"Another historical"):self.manager.start(k)
            popen.assert_not_called()

    def test_launch_uses_selected_script_and_preserves_child_state(self):
        data=self.root/"data/toss_1m";data.mkdir(parents=True);(data/"QQQ.csv").write_text("synthetic")
        research=self.root/"research";research.mkdir();(research/"backtest_intraday_v4.py").write_text("test")
        (research/"v4_data_manifest.json").write_text("{}")
        def launch(cmd,**kw):
            path=Path(cmd[cmd.index("--state")+1]);path.write_text(json.dumps(dict(pid=123,phase="running",progress=37)))
            return Mock(pid=123)
        with patch.object(self.manager,"active_job",return_value=None),patch("backtest_manager.subprocess.Popen",side_effect=launch) as proc,patch.object(self.manager,"running",return_value=True):
            status=self.manager.start("v4-ir2")
            self.assertEqual(status["progress"],37)
            self.assertEqual(proc.call_args.args[0][proc.call_args.args[0].index("--strategy")+1],"ir2")
            self.assertFalse(proc.call_args.kwargs.get("shell",False))

    def test_missing_manifest_fails_before_any_subprocess(self):
        data=self.root/"data/toss_1m";data.mkdir(parents=True);(data/"QQQ.csv").write_text("test")
        research=self.root/"research";research.mkdir();(research/"backtest_intraday_v4.py").write_text("test")
        with patch.object(self.manager,"active_job",return_value=None),patch("backtest_manager.subprocess.Popen") as proc:
            with self.assertRaisesRegex(RuntimeError,"manifest"):self.manager.start("v4-ir3")
            proc.assert_not_called()

    def test_interrupted_state_is_visible_on_new_manager_instance(self):
        paths=self.manager.paths("v4-ir1");self.manager.directory.mkdir()
        paths["state"].write_text(json.dumps(dict(pid=99999999,running=True,phase="running",progress=52)))
        recreated=BacktestManager(self.root,self.manager.directory)
        status=recreated.status("v4-ir1")
        self.assertEqual(status["phase"],"interrupted");self.assertEqual(status["progress"],52)

    def test_previous_result_not_reused_for_failed_new_run(self):
        paths=self.manager.paths("v4-ir1");self.manager.directory.mkdir()
        paths["state"].write_text(json.dumps(dict(runId="new",running=False,phase="error")))
        paths["result"].write_text(json.dumps(dict(runId="old",overall=dict(trades=999))))
        status=self.manager.status("v4-ir1")
        self.assertFalse(status["downloadAvailable"]);self.assertEqual(status["summary"],{})
        with self.assertRaises(FileNotFoundError):self.manager.download("v4-ir1")

    def test_download_contains_only_selected_engine_allowlisted_artifacts(self):
        self.manager.directory.mkdir()
        for k in BACKTEST_ENGINES:
            for key,p in self.manager.paths(k).items():
                p.write_text('{"overall":{"trades":2}}' if key=="result" else "{}" if key=="state" else "fixture")
        (self.manager.directory/"server_secrets.json").write_text("secret")
        for k in BACKTEST_ENGINES:
            with zipfile.ZipFile(io.BytesIO(self.manager.download(k))) as z:
                names=z.namelist();prefix=BACKTEST_ENGINES[k]["prefix"]
                self.assertTrue(all(n.startswith(prefix) for n in names))
                self.assertFalse(any("secrets" in n for n in names))
                self.assertIn(prefix+"_result.json",names)
                if k.startswith("v4-"):
                    for suffix in ("_trades.csv","_state.json",".log","_data_audit.json"):self.assertIn(prefix+suffix,names)


class HTTPTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.manager=BacktestManager(server.ROOT,self.tmp.name)
        self.patch=patch.object(server,"BACKTEST_MANAGER",self.manager);self.patch.start();self.addCleanup(self.patch.stop)
        self.token="backtest-unit-test";server.SESSIONS[self.token]=time.time()+60
        self.addCleanup(lambda:server.SESSIONS.pop(self.token,None))
        self.http=ThreadingHTTPServer(("127.0.0.1",0),server.Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop)

    def stop(self):self.http.shutdown();self.thread.join();self.http.server_close()
    def req(self,path,body=None,auth=True):
        headers={"Authorization":"Bearer "+self.token} if auth else {}
        request=urllib.request.Request(f"http://127.0.0.1:{self.http.server_port}"+path,
            data=json.dumps(body).encode() if body is not None else None,headers=headers)
        try:
            with urllib.request.urlopen(request) as r:return r.status,r.read()
        except urllib.error.HTTPError as exc:return exc.code,exc.read()

    def test_unauthorized_every_engine_start_status_download(self):
        with patch.object(self.manager,"start",side_effect=AssertionError("unauthorized subprocess")):
            for k in BACKTEST_ENGINES:
                for path,body in (("/api/backtest/start",dict(engine=k)),("/api/backtest/status?engine="+k,None),("/api/backtest/download?engine="+k,None)):
                    self.assertEqual(self.req(path,body,False)[0],401)
            self.assertEqual(self.req("/api/backtest/engines",auth=False)[0],401)

    def test_authenticated_registry_and_selected_engine_routing_no_live(self):
        code,raw=self.req("/api/backtest/engines")
        self.assertEqual(code,200);self.assertEqual(len(json.loads(raw)["engines"]),6)
        with patch.object(server.LIVE_TRADER,"_api",side_effect=AssertionError("live forbidden")),patch.object(self.manager,"start",return_value=dict(running=True)) as start:
            for k in BACKTEST_ENGINES:
                self.assertEqual(self.req("/api/backtest/start",dict(engine=k))[0],200)
                self.assertEqual(start.call_args.args[0],k)

    def test_arbitrary_subprocess_extra_arguments_rejected(self):
        for body in (dict(engine="not-allowed"),dict(engine=["v4-ir1"]),dict(engine="v4-ir1",args="--optimize")):
            self.assertEqual(self.req("/api/backtest/start",body)[0],400)

    def test_conflict_is_409(self):
        with patch.object(self.manager,"start",side_effect=ValueError("Another historical backtest is running")):
            self.assertEqual(self.req("/api/backtest/start",dict(engine="v4-ir1"))[0],409)

    def test_status_and_download_survive_server_reconstruction(self):
        paths=self.manager.paths("v4-ir3");paths["result"].write_text(json.dumps(dict(overall=dict(trades=2))))
        paths["state"].write_text(json.dumps(dict(phase="completed",running=False,progress=100)))
        code,raw=self.req("/api/backtest/status?engine=v4-ir3")
        self.assertEqual(code,200);self.assertEqual(json.loads(raw)["summary"]["trades"],2)
        code,raw=self.req("/api/backtest/download?engine=v4-ir3")
        self.assertEqual(code,200)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:self.assertIn(paths["result"].name,z.namelist())


if __name__=="__main__":unittest.main()
