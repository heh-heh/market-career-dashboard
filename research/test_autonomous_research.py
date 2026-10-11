"""Small offline artifacts / localhost mocks only; no historical research execution."""
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import zipfile

from research_export import finalize_manifest, ResearchExporter
from research.backtest_v4_strategies import exchange_sessions
from research.autonomous.artifact_reader import verify_archive, extract_verified, verify_cache, sha256
from research.autonomous.config import LoopConfig
from research.autonomous.experiment_registry import Registry, write_json, read_json, exclusive_lock
from research.autonomous.export_client import ExportClient, NoRedirect, extraction_disk_reserve
from research.autonomous.quarterly import quarterly_metrics, select_candidates, build_shortlist_v08
from research.autonomous.safety_gate import period_gate, plan_gate, resource_gate, promotion_gate, ResourcePauseError
from research.autonomous.runner import loop, process_identity, active_stage, execute_iteration, run_validation
from research.autonomous.monitor import status, iteration_id
from research.autonomous.stage_worker import bounded_day_stream, prepare_bounded_inputs

REPO = Path(__file__).resolve().parents[1]


def make_zip(path, documents, export_type="evolution_summary", ids=None):
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
        entries = []
        for name, value in documents.items():
            raw = json.dumps(value).encode() if isinstance(value, (dict, list)) else value.encode()
            z.writestr(name, raw)
            entries.append({"path": name, "exportedBytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        finalize_manifest(z, {"version": 1, "files": entries, "type": export_type,
                              "selection": {"type": export_type, "runIds": ids or [], "researchOnly": True}, "runs": []})
    return path


def fixed_artifacts(base):
    from research.evolution.config import Config
    config = replace(Config(), train=("2022-01-01", "2023-12-31"), validation=("2024-01-01", "2024-12-31")).snapshot()
    train = base / "evolution/train"; hold = base / "evolution/hold"; short = base / "evolution/short"; dev = base / "evolution/dev"
    for p in (train, hold, short, dev): p.mkdir(parents=True)
    info = {"researchOnly": True, "timestampKind": "end", "phase": "completed", "config": config,
            "version": "evolution-v0.7", "symbols": ["TQQQ"], "missingSessions": [],
            "sourcePopulationVerified": True, "inputHashesVerified": True}
    write_json(train / "run.json", dict(info, runId="train", fromDate="2022-01-03", toDate="2023-12-29"))
    population = [{"bot_id": "a", "genome_hash": "a" * 64, "lifetime_return": .1},
                  {"bot_id": "b", "genome_hash": "b" * 64, "lifetime_return": .2}]
    write_json(train / "final_population.json", population)
    write_json(short / "run.json", dict(info, runId="short", fromDate="2022-01-03", toDate="2023-12-29",
                                       holdoutRunId="hold", sourceTrainingRunId="train"))
    write_json(short / "final_population.json", population)
    write_json(hold / "run.json", dict(info, runId="hold", sourceRunId="train", fromDate="2024-01-02", toDate="2024-12-31"))
    totals = {b: {"pnl": 0., "trades": 0, "cost": 0.} for b in ("a", "b")}
    dates = list(exchange_sessions("2024-01-02", "2024-12-31"))
    for number, day in enumerate(dates):
        quarter = (int(day[5:7]) - 1) // 3
        rows = []
        for bot in ("a", "b"):
            pnl = ([.1, .1, .1, -.01] if bot == "a" else [.5, -.02, -.02, -.02])[quarter]
            row = {"botId": bot, "date": day, "evaluationValid": True, "dailyPnl": pnl,
                   "trades": 1, "transactionCost": .01}
            rows.append(row)
            totals[bot]["pnl"] += pnl; totals[bot]["trades"] += 1; totals[bot]["cost"] += .01
        d = hold / f"validation_{number+1:04d}"; d.mkdir()
        write_json(d / "metrics.json", {"bots": rows})
    rows = [{"botId": bot, "genomeHash": bot * 64, "invalidDays": 0, "validDays": len(dates),
             "validationReturn": total["pnl"] / 100, "pnlUsd": total["pnl"], "trades": total["trades"],
             "transactionCost": total["cost"]} for bot, total in totals.items()]
    write_json(hold / "validation_summary.json", {"bots": rows, "missingSessions": [], "sessionsProcessed": len(dates), "sessionsRequested": len(dates)})
    write_json(dev / "run.json", dict(info, runId="dev", sourceRunId="short", fromDate="2025-01-02", toDate="2025-12-31",
                                     selectionMode="V07_INTERNAL_HOLDOUT_SHORTLIST", evaluationMode="FROZEN_VALIDATION"))
    dev_rows = [dict(r, validationReturn=.05 if r["botId"] == "a" else -.1) for r in rows]
    write_json(dev / "validation_summary.json", {"bots": dev_rows, "missingSessions": [], "sessionsRequested": len(dates)})
    return train, hold, short, dev


class AutonomousTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.base = Path(self.temp.name)
        self.config = LoopConfig(registry=self.base / "registry", lock=self.base / "lock", min_free_bytes=0, disk_reserve_fraction=0)
    def tearDown(self): self.temp.cleanup()

    def test_archive_verified_and_cache_reused(self):
        p = make_zip(self.base / "a.zip", {"evolution/a/run.json": {"fromDate": "2024-01-02", "toDate": "2024-12-31"}})
        first, digest = extract_verified(p, self.base / "cache")
        second, again = extract_verified(p, self.base / "cache")
        self.assertEqual((first, digest), (second, again)); verify_cache(first)
        (first / "evolution/a/run.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "MISMATCH"): verify_cache(first)

    def test_corrupted_archive(self):
        p = self.base / "a.zip"; p.write_bytes(b"not a zip")
        with self.assertRaisesRegex(ValueError, "CORRUPTED"): verify_archive(p)

    def test_path_traversal_secret_and_symlink(self):
        for name, doc in (("../escape.json", {}), ("/outside.json", {}), (".env", "x"),
                          ("run.json", {"access_token": "hidden"}), ("README.md", "Bearer hidden")):
            p = make_zip(self.base / "a.zip", {name: doc})
            with self.assertRaises(ValueError): verify_archive(p)
        p = make_zip(self.base / "a.zip", {"run.json": {}})
        with zipfile.ZipFile(p, "a") as archive:
            link = zipfile.ZipInfo("link.json"); link.external_attr = (0o120777 << 16)
            archive.writestr(link, "../outside")
        with self.assertRaises(ValueError): verify_archive(p)

    def test_archive_hash_mismatch(self):
        p = make_zip(self.base / "a.zip", {"run.json": {"x": 1}})
        with zipfile.ZipFile(p) as z:
            m = json.loads(z.read("export_manifest.json"))
        m["files"][0]["sha256"] = "0" * 64
        with zipfile.ZipFile(self.base / "bad.zip", "w") as z:
            z.writestr("run.json", '{"x": 1}'); finalize_manifest(z, m)
        with self.assertRaisesRegex(ValueError, "SHA256"): verify_archive(self.base / "bad.zip")

    def test_unlisted_cache_file_rejected(self):
        p = make_zip(self.base / "a.zip", {"run.json": {}})
        folder, _ = extract_verified(p, self.base / "cache")
        (folder / "other.json").write_text('{}')
        with self.assertRaisesRegex(ValueError, "FILE_SET"): verify_cache(folder)

    def test_final_oos_and_2025_leakage(self):
        with self.assertRaisesRegex(ValueError, 'FINAL_OOS'):
            replace(self.config, data_dir=self.base/'2026')
        for first, last, purpose in (("2026-01-01", "2026-02-01", "training"),
                                      ("2025-01-01", "2025-12-31", "internal_holdout")):
            with self.assertRaises(ValueError): period_gate(first, last, purpose)
        p = make_zip(self.base / "a.zip", {"run.json": {"fromDate": "2026-01-01", "toDate": "2026-12-31"}})
        with self.assertRaisesRegex(ValueError, "FINAL_OOS"): verify_archive(p)

    def test_registry_atomic_resume_and_lock(self):
        r = Registry(self.config.registry); folder = r.create(); r.update(folder, "failed", error="test")
        restored = Registry(self.config.registry)
        self.assertEqual(restored.iterations(), [folder]); self.assertEqual(read_json(folder / "experiment.json")["state"], "failed")
        with exclusive_lock(self.config.lock):
            with self.assertRaisesRegex(ValueError, "LOCKED"):
                with exclusive_lock(self.config.lock): pass
        with exclusive_lock(self.config.lock): pass

    def test_lock_survives_detached_worker_handoff(self):
        with exclusive_lock(self.config.lock) as fd:
            code = 'import fcntl,sys;fcntl.flock(int(sys.argv[1]),fcntl.LOCK_EX|fcntl.LOCK_NB);print("ready",flush=True);sys.stdin.read(1)'
            child = subprocess.Popen([sys.executable, '-c', code, str(fd)], pass_fds=(fd,),
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
            self.assertEqual(child.stdout.readline().strip(), 'ready')
        try:
            with self.assertRaisesRegex(ValueError, 'LOCKED'):
                with exclusive_lock(self.config.lock): pass
        finally:
            child.communicate('x', timeout=5)
        with exclusive_lock(self.config.lock): pass

    def test_resource_guard(self):
        self.config.registry.mkdir()
        okay = {"cpuPct": 0, "memoryPct": 20, "swapPct": 0, "memoryAvailableGb": 1,
                "swapUsedGb": 0, "swapTotalGb": 1, "processRssMb": 10}
        disk = shutil._ntuple_diskusage(100, 20, 80)
        resource_gate(self.config, okay, disk)
        with self.assertRaises(ResourcePauseError) as caught:
            resource_gate(self.config, dict(okay, cpuPct=99), disk)
        self.assertEqual(caught.exception.facts["reason"], "SOFT_RESOURCE_PRESSURE")
        self.assertEqual(caught.exception.facts["cpuPct"], 99.0)
        with self.assertRaises(ResourcePauseError) as caught:
            resource_gate(replace(self.config, min_free_bytes=90), okay, disk)
        self.assertEqual(caught.exception.facts["reason"], "DISK_RESERVE")

    def test_resource_guard_retries_transient_soft_pressure(self):
        self.config.registry.mkdir()
        busy = {"cpuPct": 99, "memoryPct": 20, "swapPct": 0, "memoryAvailableGb": 1,
                "swapUsedGb": 0, "swapTotalGb": 1, "processRssMb": 10}
        okay = dict(busy, cpuPct=10)
        disk = shutil._ntuple_diskusage(100, 20, 80)
        with patch("research.evolution.resource.sample_resources", side_effect=[busy, busy, okay]), \
             patch("research.autonomous.safety_gate.time.sleep"):
            result = resource_gate(self.config, snapshot=None, disk=disk,
                                   soft_wait_seconds=5, sample_interval_seconds=.1)
        self.assertEqual(result["reason"], "NORMAL")
        self.assertEqual(result["samples"], 2)

    def test_quarterly_selection_and_immutable_source(self):
        train, hold, _, _ = fixed_artifacts(self.base / "fixed")
        original = sha256(train / "final_population.json")
        rows = quarterly_metrics(train, hold)
        self.assertEqual(rows[0]["positiveQuarterCount"], 3); self.assertEqual(rows[1]["positiveQuarterCount"], 1)
        selected = select_candidates(rows)
        self.assertEqual([r["botId"] for r in selected], ["a"])
        out = build_shortlist_v08(train, hold, self.base / "shortlist")
        self.assertEqual(len(read_json(out / "final_population.json")), 1)
        self.assertEqual(sha256(train / "final_population.json"), original)

    def test_missing_quarterly_artifact_fails_closed(self):
        train, hold, _, _ = fixed_artifacts(self.base / "fixed")
        (hold / "validation_0001/metrics.json").rename(hold / "validation_0001/retained.json")
        with self.assertRaisesRegex(ValueError, "DAILY_HOLDOUT"): quarterly_metrics(train, hold)

    def test_insufficient_no_threshold_relaxation(self):
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_CANDIDATES"):
            select_candidates([{"mechanicallyValid": True, "annualReturn": .1, "positiveQuarterCount": 2, "medianQuarterReturn": .01}])

    def test_promotion_negative_mean_and_missing_metrics(self):
        baseline = {"meanValidationReturn": -.08, "medianValidationReturn": -.02, "positiveShare": .2,
                    "bestValidationReturn": .13, "meanTradesPerBot": 266, "meanTransactionCostPerBot": 3,
                    "trainValidationSpearman": .8, "holdoutValidationSpearman": .5, "invalidBots": 0, "validBots": 10}
        candidate = dict(baseline, meanValidationReturn=-.01)
        decision = promotion_gate(baseline, candidate)
        self.assertEqual(decision["decision"], "KEEP_BASELINE"); self.assertFalse(decision["baselineChanged"])
        self.assertEqual(decision["finalOosGate"], "CLOSED")
        self.assertEqual(promotion_gate({}, {})["decision"], "INCONCLUSIVE")
        positive = dict(baseline, meanValidationReturn=.01, medianValidationReturn=.01)
        self.assertEqual(promotion_gate(baseline, positive)["decision"], "PROMOTE_CANDIDATE")

    def test_max_iterations(self):
        for count in (0, -1, 101):
            with self.assertRaises(ValueError): replace(self.config, max_iterations=count)
        with self.assertRaisesRegex(ValueError, 'DRY_RUN_REVIEW_REQUIRED'):
            loop(self.config)

    def test_process_identity_not_pid_only(self):
        r = Registry(self.config.registry); folder = r.create()
        identity = process_identity(os.getpid())
        write_json(folder / "stage_process.json", {"identity": identity})
        self.assertIsNotNone(active_stage(folder))
        identity["startTicks"] = "incorrect"
        write_json(folder / "stage_process.json", {"identity": identity})
        self.assertIsNone(active_stage(folder))

    def test_bounded_reader_never_parses_2026_prices(self):
        path = self.base / "TQQQ.csv"
        path.write_text('timestamp,open,high,low,close,volume\n2025-12-31T15:59:00-05:00,100,101,99,100,20\n2026-01-02T09:31:00-05:00,INVALID,INVALID,INVALID,INVALID,INVALID\n')
        manifest = {"symbols": {"TQQQ": {"naive_timezone": None}}}
        days = list(bounded_day_stream({"TQQQ": path}, manifest))
        self.assertEqual([d for d, _ in days], ["2025-12-31"])
        with self.assertRaisesRegex(ValueError, "FINAL_OOS"):
            list(bounded_day_stream({"TQQQ": path}, manifest, "2026-01-01", "2026-12-31"))

    def test_dry_run_offline_pipeline_and_resume(self):
        fixed = self.base / "fixed"; fixed_artifacts(fixed)
        documents = {str(p.relative_to(fixed)): read_json(p) for p in fixed.rglob("*.json")}
        archive = make_zip(self.base / "bundle.zip", documents)
        cache, digest = extract_verified(archive, self.config.registry / "cache")
        catalog = {"runs": [{"id": "a" * 24, "runId": "dev", "isV07": True,
                    "selectionMode": "V07_INTERNAL_HOLDOUT_SHORTLIST", "evaluationPeriod": ["2025-01-02", "2025-12-31"]}],
                   "researchBoundary": "FINAL_OOS_EXCLUDED"}
        class FakeClient:
            def json(self, path): return catalog if path.split('?')[0].endswith('catalog') else {"rows": catalog["runs"], "researchBoundary": "FINAL_OOS_EXCLUDED"}
            def collect(self, *args): return cache, digest, read_json(cache / "export_manifest.json")
        with patch('research.autonomous.runner.resource_gate'), patch('research.autonomous.runner.execute_iteration') as execute:
            result = loop(self.config, dry_run=True, client=FakeClient())
            self.assertTrue(result[0]["dryRun"]); execute.assert_not_called()
            registry = Registry(self.config.registry); folder = registry.iterations()[0]
            self.assertTrue((folder / "plan.json").exists()); self.assertFalse((folder / "shortlist-v08").exists())
            self.assertFalse((folder / "test_results.json").exists())
            execute.return_value = {"state": "completed"}
            loop(self.config, resume=True, client=FakeClient())
            execute.assert_called_once()
            iterations = registry.iterations()
            self.assertEqual(len(iterations), 2)
            dry = read_json(iterations[0] / "experiment.json")
            actual = read_json(iterations[1] / "experiment.json")
            self.assertEqual(dry["state"], "completed")
            self.assertTrue(dry["dryRun"])
            self.assertTrue(actual["dryRunReviewed"])
            self.assertEqual(actual["reviewedDryRunIteration"], dry["iteration"])
            self.assertTrue((iterations[1] / "plan.json").exists())

    def test_resume_finds_completed_dry_run_behind_failed_attempt(self):
        r = Registry(self.config.registry)
        dry = r.create()
        write_json(dry / "plan.json", {"candidateVersion":"v08","selectionDataYear":2024,
                   "hypothesis":"quarterly stability shortlist",
                   "selectionRule":{"annualReturnAbove":0,"positiveQuarterCountAtLeast":3,"medianQuarterReturnAbove":0},
                   "changedVariables":["shortlist_selection"],"periods":{"internal_holdout":["2024-01-02","2024-12-31"]},
                   "researchOnly":True,"allowFinalOos":False,"expectedChangedFiles":["research/autonomous/candidates/v08.json"]})
        for name,value in {
            "source_manifest.json":{"cacheDirectory":str(self.config.registry/"cache"),"manifestHash":"x"},
            "source_runs.json":{},"baseline_metrics.json":{},"quarterly_diagnostics.json":{"bots":[]},
            "config.json":{},
        }.items():
            write_json(dry / name,value)
        r.update(dry,"completed",dryRun=True,candidateVersion="v08",decision="DRY_RUN_ONLY")
        failed = r.create()
        r.update(failed,"failed",failureCode="RESEARCH_FAILURE")
        with patch('research.autonomous.runner.execute_iteration', return_value={"state":"completed"}) as execute:
            loop(self.config,resume=True)
        execute.assert_called_once()
        newest = r.iterations()[-1]
        self.assertNotEqual(newest, failed)
        info = read_json(newest/"experiment.json")
        self.assertTrue(info["dryRunReviewed"])
        self.assertEqual(info["reviewedDryRunIteration"], read_json(dry/"experiment.json")["iteration"])

    def test_failed_experiment_recovery(self):
        with patch('research.autonomous.runner.prepare_iteration', side_effect=ValueError('offline failure')):
            with self.assertRaisesRegex(ValueError, 'offline failure'): loop(self.config, dry_run=True)
        r = Registry(self.config.registry)
        self.assertEqual(read_json(r.iterations()[0] / "experiment.json")["state"], "failed")
        self.assertTrue((r.iterations()[0] / "report.json").exists())

    def test_client_auth_origin_and_no_redirect(self):
        with patch.dict(os.environ, {"RESEARCH_EXPORT_TOKEN": "offline-secret", "RESEARCH_EXPORT_BASE_URL": "http://127.0.0.1:8080"}):
            class Opener:
                def open(self, req, timeout):
                    self.request = req; return io.BytesIO(b'{"ok":true,"rows":[]}')
            opener = Opener(); client = ExportClient(self.config, opener=opener)
            self.assertEqual(client.json('/api/research/export/summary?format=json')['rows'], [])
            self.assertEqual(opener.request.get_header('Authorization'), 'Bearer offline-secret')
            with self.assertRaises(ValueError): client.open('/api/trading/order')
            with self.assertRaisesRegex(ValueError, 'REDIRECT'):
                NoRedirect().redirect_request(None, None, None, None, None, None)
        with patch.dict(os.environ, {"RESEARCH_EXPORT_TOKEN": "offline", "RESEARCH_EXPORT_BASE_URL": "http://remote.invalid"}):
            with self.assertRaisesRegex(ValueError, 'HTTPS'): ExportClient(self.config)

    def test_export_disk_reserve_uses_completed_payload(self):
        mib = 1024**2
        gib = 1024**3
        cfg = replace(self.config, max_extracted_bytes=4*gib)
        small = extraction_disk_reserve(
            {"archiveBytes": 8*mib, "bytesProcessed": 6*mib, "estimatedBytes": 7*mib}, cfg
        )
        # Working reserve is based on the completed payload plus bounded headroom,
        # not the unrelated 4 GiB security extraction ceiling.
        self.assertLess(small, 128*mib)
        self.assertGreaterEqual(small, 8*mib + 64*mib)

        capped = extraction_disk_reserve(
            {"archiveBytes": 100*mib, "bytesProcessed": 8*gib}, cfg
        )
        self.assertEqual(capped, 100*mib + 4*gib)

        fallback = extraction_disk_reserve({"archiveBytes": 8*mib}, cfg)
        self.assertEqual(fallback, 8*mib + 4*gib)

    def test_export_client_download_and_cache(self):
        ids = ['a' * 24]
        archive = make_zip(self.base / 'api.zip', {'run.json': {}}, ids=ids)
        payload = archive.read_bytes(); calls = []
        class Opener:
            def open(self, req, timeout):
                calls.append(req.full_url)
                if req.full_url.endswith('/download'): return io.BytesIO(payload)
                return io.BytesIO(json.dumps({'ok': True, 'jobId': 'b' * 32, 'state': 'completed', 'archiveBytes': len(payload)}).encode())
        with patch.dict(os.environ, {'RESEARCH_EXPORT_TOKEN': 'offline', 'RESEARCH_EXPORT_BASE_URL': 'http://127.0.0.1:8080'}), patch('research.autonomous.safety_gate.resource_gate'):
            client = ExportClient(self.config, opener=Opener()); catalog = {'runs': [{'id': ids[0], 'updatedAt': 'now'}]}
            first = client.collect('evolution_summary', ids, catalog); count = len(calls)
            second = client.collect('evolution_summary', ids, catalog)
            self.assertEqual(first[1], second[1]); self.assertEqual(len(calls), count)

    def test_iteration_export_excludes_cache_and_secrets(self):
        r = Registry(self.config.registry); folder = r.create(); r.update(folder, 'completed', candidateVersion='v08')
        write_json(folder / 'report.json', {'researchOnly': True, 'Authorization': 'secret'})
        (folder / 'code_diff.patch').write_text('+ quarterly selection\n')
        cache = self.config.registry / 'cache'; cache.mkdir(); (cache / 'private.json').write_text('{}')
        m = ResearchExporter(REPO, roots={'autonomous': self.config.registry}, export_dir=self.base / 'exports', min_free_bytes=0, reserve_fraction=0)
        try:
            catalog = m.catalog()['public']; identifier = catalog['iterations'][0]['id']
            files, _, _ = m.plan({'type': 'autonomous_iteration', 'runIds': [identifier]})
            self.assertTrue(any(f['relative'].endswith('code_diff.patch') for f in files))
            self.assertFalse(any('cache' in f['relative'] for f in files))
            self.assertEqual(identifier, iteration_id(folder.name))
            monitor = status(self.config.registry)
            self.assertNotIn('Authorization', json.dumps(monitor)); self.assertEqual(monitor['current']['finalOosGate'], 'CLOSED')
        finally: m.close()

    def test_plan_rejects_multiple_changes_and_2025_selection(self):
        plan = {"researchOnly": True, "allowFinalOos": False, "candidateVersion": "v08", "selectionDataYear": 2024,
                "selectionRule": {"annualReturnAbove": 0, "positiveQuarterCountAtLeast": 3, "medianQuarterReturnAbove": 0},
                "changedVariables": ["shortlist_selection"], "periods": {"internal_holdout": ["2024-01-02", "2024-12-31"]}}
        plan_gate(plan)
        for change in ({"selectionDataYear": 2025}, {"changedVariables": ["shortlist_selection", "fitness"]}, {"allowFinalOos": True}):
            with self.assertRaises(ValueError): plan_gate(dict(plan, **change))

    def test_provisional_audit_rejects_conflicting_quote_duplicates(self):
        p = self.base / 'SPY.csv'
        p.write_text('timestamp,open,high,low,close,volume,bid,ask\n2025-01-02T09:31:00-05:00,100,101,99,100,20,99,101\n2025-01-02T09:31:00-05:00,100,101,99,100,20,98,101\n')
        with self.assertRaisesRegex(ValueError, 'Conflicting'):
            list(bounded_day_stream({'SPY': p}, {'symbols': {'SPY': {'naive_timezone': None}}}))

    def test_provisional_audit_and_reviewed_guard(self):
        from argparse import Namespace
        args = Namespace(data_manifest=None, provisional=False, data_dir=self.base, naive_timezone=None)
        with self.assertRaisesRegex(ValueError, 'REVIEWED_MANIFEST_REQUIRED'): prepare_bounded_inputs(args, ['TQQQ'])
        args.data_manifest = self.base / 'missing.json'
        with self.assertRaises(FileNotFoundError): prepare_bounded_inputs(args, ['TQQQ'])
        args.data_manifest = None; args.provisional = True
        for s in ('TQQQ', 'SPY', 'QQQ', 'SOXX'):
            (self.base / (s+'.csv')).write_text('timestamp,open,high,low,close,volume\n2025-01-02T09:31:00-05:00,100,101,99,100,20\n2026-01-02T09:31:00-05:00,INVALID,INVALID,INVALID,INVALID,INVALID\n')
        paths, manifest, mode = prepare_bounded_inputs(args, ['TQQQ'])
        self.assertEqual(mode, 'PROVISIONAL_UNREVIEWED_DATA'); self.assertFalse(manifest['priceAdjustmentConfirmed'])
        self.assertEqual(manifest['symbols']['TQQQ']['rowsAudited'], 1)

    def test_completed_attempt_recovers_without_relaunch(self):
        r = Registry(self.config.registry); folder = r.create()
        attempted = folder / 'validation-attempts/retained'; attempted.mkdir(parents=True)
        write_json(attempted / 'run.json', {'phase': 'completed', 'runId': 'retained', 'evaluationMode': 'FROZEN_VALIDATION',
                   'sourceRunId': 'shortlist-v08', 'fromDate': '2025-01-02', 'toDate': '2025-12-31'})
        with patch('research.autonomous.runner.subprocess.Popen') as popen:
            recovered = run_validation(r, folder, self.config)
            self.assertEqual(recovered, attempted); popen.assert_not_called()
            self.assertTrue((folder / 'validation_run.json').exists())

    def test_invalid_checkpoint_is_not_completed(self):
        r = Registry(self.config.registry); folder = r.create()
        attempt = folder / 'validation-attempts/future'; attempt.mkdir(parents=True)
        write_json(attempt / 'run.json', {'phase': 'completed', 'fromDate': '2026-01-01', 'toDate': '2026-12-31'})
        write_json(folder / 'validation_run.json', {'path': str(attempt)})
        with self.assertRaises(ValueError): run_validation(r, folder, self.config)

    def test_monitor_interrupted_worker_no_pid_fabrication(self):
        r = Registry(self.config.registry); folder = r.create()
        r.update(folder, 'development_validation', workerIdentity={'pid': 999999999, 'startTicks': '0', 'bootId': 'bad', 'commandHash': 'bad'})
        self.assertEqual(status(self.config.registry)['current']['failureCode'], 'WORKER_INTERRUPTED_RESUME_REQUIRED')

    def test_offline_iteration_artifacts_and_provenance(self):
        fixed = self.base / 'fixed'; _, _, _, dev = fixed_artifacts(fixed)
        documents = {str(p.relative_to(fixed)): read_json(p) for p in fixed.rglob('*.json')}
        archive = make_zip(self.base / 'bundle.zip', documents)
        cache, digest = extract_verified(archive, self.config.registry / 'cache')
        catalog = {'runs': [{'id': 'a'*24, 'runId': 'dev', 'isV07': True, 'selectionMode': 'V07_INTERNAL_HOLDOUT_SHORTLIST', 'evaluationPeriod': ['2025-01-02', '2025-12-31']}],
                   'researchBoundary': 'FINAL_OOS_EXCLUDED'}
        class Client:
            def json(self, path): return catalog if path.split('?')[0].endswith('catalog') else {'rows': catalog['runs'], 'researchBoundary': 'FINAL_OOS_EXCLUDED'}
            def collect(self, *args): return cache, digest, read_json(cache/'export_manifest.json')
        with patch('research.autonomous.runner.resource_gate'):
            loop(self.config, dry_run=True, client=Client())
        r = Registry(self.config.registry); folder = r.iterations()[0]
        evaluation = folder / 'validation-attempts/mock'; evaluation.mkdir(parents=True)
        write_json(evaluation/'validation_summary.json', {'bots': [read_json(dev/'validation_summary.json')['bots'][0]], 'sessionsRequested': len(exchange_sessions('2024-01-02','2024-12-31')), 'missingSessions': []})
        def evaluation_stub(*args):
            write_json(evaluation/'run.json', {'sourcePopulationFileHash': sha256(folder/'shortlist-v08/final_population.json'),
                       'sourcePopulationVerified': True, 'inputHashesVerified': True, 'timestampKind': 'end', 'symbols': ['TQQQ']})
            return evaluation
        with patch('research.autonomous.runner.resource_gate'), patch('research.autonomous.runner.candidate_git'), patch('research.autonomous.runner.run_tests'), patch('research.autonomous.runner.run_validation', side_effect=evaluation_stub):
            result = execute_iteration(r, folder, self.config)
        self.assertEqual(result['state'], 'completed'); self.assertFalse(result['dryRun'])
        for name in ('training_run.json','holdout_run.json','diagnostics.json','comparison.json','decision.json','report.json'):
            self.assertTrue((folder/name).exists(),name)
        self.assertEqual(read_json(folder/'decision.json')['finalOosGate'], 'CLOSED')

    def test_real_localhost_export_api_and_monitor(self):
        import server
        from http.server import ThreadingHTTPServer
        fixed = self.base / 'fixed'; fixed_artifacts(fixed)
        r = Registry(self.config.registry); folder = r.create(); r.update(folder, 'completed', dryRun=True)
        manager = ResearchExporter(REPO, roots={'evolution': fixed/'evolution', 'autonomous': self.config.registry},
                                   export_dir=self.base/'exports', min_free_bytes=0, reserve_fraction=0)
        token = 'offline-localhost-test-only'
        with patch.object(server, 'RESEARCH_EXPORTER', manager), patch.object(server.Handler, 'log_message', lambda *a: None), patch.dict(server.SESSIONS, {token: time.time()+60}):
            http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
            thread = threading.Thread(target=http.serve_forever, daemon=True); thread.start()
            try:
                with patch.dict(os.environ, {'RESEARCH_EXPORT_TOKEN': token,
                                             'RESEARCH_EXPORT_BASE_URL': 'http://127.0.0.1:'+str(http.server_port),
                                             'EVOLUTION_AUTONOMOUS_DIR': str(self.config.registry)}), patch('research.autonomous.safety_gate.resource_gate'):
                    client = ExportClient(replace(self.config, poll_seconds=.01))
                    catalog = client.json('/api/research/export/catalog')
                    summary = client.json('/api/research/export/summary?format=json')
                    self.assertEqual(len(summary['rows']), 4)
                    dev = next(row for row in catalog['runs'] if row['runId'] == 'dev')
                    cache, digest, manifest = client.collect('V07_FULL_RESEARCH_BUNDLE', [dev['id']], catalog)
                    from research.autonomous.diagnostics import identify_baseline
                    from research.autonomous.artifact_reader import discover_runs
                    baseline = identify_baseline(discover_runs(cache))
                    self.assertEqual(baseline['info']['runId'], 'dev')
                    self.assertTrue(list(baseline['holdout'].glob('validation_*/metrics.json')))
                    from research.autonomous.run_research_loop import main
                    with patch('research.autonomous.runner.resource_gate'), patch('research.autonomous.runner.execute_iteration') as actual:
                        main(['--dry-run', '--max-iterations', '1', '--registry-dir', str(self.config.registry),
                              '--lock-file', str(self.config.lock)])
                        actual.assert_not_called()
                    planned = Registry(self.config.registry).iterations()[-1]
                    self.assertTrue((planned/'report.json').exists())
                    self.assertTrue(read_json(planned/'experiment.json')['dryRun'])
                    request = __import__('urllib.request', fromlist=['urlopen'])
                    with request.urlopen('http://127.0.0.1:'+str(http.server_port)+'/api/research/autonomous/status') as response:
                        observed = json.load(response)
                    self.assertEqual(observed['current']['finalOosGate'], 'CLOSED')
                    self.assertNotIn(token, json.dumps(observed))
                    job = client.json('/api/research/export/jobs', {'type': 'autonomous_iteration', 'runIds': [iteration_id(folder.name)]})
                    for _ in range(100):
                        if job['state'] not in ('queued','running'): break
                        time.sleep(.01); job = client.json('/api/research/export/jobs/'+job['jobId'])
                    self.assertEqual(job['state'], 'completed')
                    with client.open('/api/research/export/jobs/'+job['jobId']+'/download') as response:
                        archive = zipfile.ZipFile(io.BytesIO(response.read()))
                        self.assertIn('autonomous/'+folder.name+'/experiment.json', archive.namelist())
            finally:
                http.shutdown(); http.server_close(); thread.join(); manager.close()

    def test_baseline_selection_skips_invalid_newest_without_relaxing(self):
        from research.autonomous.artifact_reader import discover_runs
        from research.autonomous.diagnostics import identify_baseline
        from research.autonomous.runner import choose_valid_v07_baseline

        good = self.base / "good-bundle"
        bad = self.base / "bad-bundle"
        fixed_artifacts(good)
        fixed_artifacts(bad)
        bad_dev = bad / "evolution" / "dev" / "run.json"
        bad_info = read_json(bad_dev)
        bad_info["missingSessions"] = ["2025-07-04"]
        bad_info["resultsValid"] = False
        write_json(bad_dev, bad_info)

        with self.assertRaisesRegex(
            ValueError,
            r"BASELINE_INCOMPLETE_OR_INVALID: stage=development_validation: runId=dev: baseline completeness check failed",
        ):
            identify_baseline(discover_runs(bad))

        older = {"id": "a" * 24, "runId": "dev-good", "updatedAt": "2026-01-01T00:00:00Z"}
        newer = {"id": "b" * 24, "runId": "dev-bad", "updatedAt": "2026-02-01T00:00:00Z"}
        catalog = {"runs": [older, newer]}

        class FakeClient:
            def collect(self, export_type, ids, _catalog):
                folder = bad if ids == [newer["id"]] else good
                return folder, ("b" if folder == bad else "a") * 64, {
                    "type": export_type,
                    "selection": {"type": export_type, "runIds": ids, "researchOnly": True},
                }

        choice = choose_valid_v07_baseline(FakeClient(), catalog, [older, newer])
        self.assertEqual(choice["candidate"]["runId"], "dev-good")
        self.assertEqual(choice["baseline"]["info"]["runId"], "dev")
        self.assertEqual(len(choice["rejected"]), 1)
        self.assertEqual(choice["rejected"][0]["runId"], "dev")
        self.assertEqual(choice["rejected"][0]["code"], "BASELINE_INCOMPLETE_OR_INVALID")
        self.assertEqual(choice["rejected"][0]["stage"], "development_validation")
        self.assertEqual(choice["rejected"][0]["facts"]["missingCount"], 1)
        self.assertEqual(choice["rejected"][0]["facts"]["missingDates"], ["2025-07-04"])
        self.assertFalse(choice["rejected"][0]["facts"]["resultsValid"])

    def test_secret_redacted_even_under_other_key(self):
        p = self.base/'artifact.json'
        with patch.dict(os.environ, {'RESEARCH_EXPORT_TOKEN': 'actual-test-secret'}):
            write_json(p, {'note': 'echo actual-test-secret', 'password': 'also-secret'})
        self.assertNotIn('actual-test-secret', p.read_text()); self.assertNotIn('also-secret', p.read_text())

    def test_unowned_registry_never_overwrites_results(self):
        self.config.registry.mkdir()
        p = self.config.registry/'status.json'; p.write_text('original')
        with self.assertRaisesRegex(ValueError, 'REGISTRY_UNOWNED'): Registry(self.config.registry)
        self.assertEqual(p.read_text(), 'original')

    def test_export_boundary_excludes_future_descendants_and_generated_summaries(self):
        fixed = self.base/'fixed'; train, _, _, _ = fixed_artifacts(fixed)
        future = train/'future'; future.mkdir()
        write_json(future/'run.json', {'runId': 'reserved-oos', 'version': 'evolution-v0.7',
                   'fromDate': '2026-01-02', 'toDate': '2026-12-31', 'evaluationMode': 'FROZEN_VALIDATION', 'phase': 'completed'})
        write_json(future/'validation_summary.json', {'bots': [{'botId': 'future', 'validationReturn': 999, 'invalidDays': 0}]})
        m = ResearchExporter(REPO, roots={'evolution': fixed/'evolution'}, export_dir=self.base/'exports', min_free_bytes=0, reserve_fraction=0)
        try:
            catalog = m.catalog()['public']
            source = next(r for r in catalog['runs'] if r['runId'] == 'train')
            files, _, _ = m.plan({'type': 'evolution_summary', 'runIds': [source['id']], 'researchOnly': True})
            self.assertFalse(any('/future/' in f['relative'] for f in files))
            summary = json.loads(m.summary(research_only=True))
            self.assertFalse(any(r['runId'] == 'reserved-oos' for r in summary['rows']))
            self.assertEqual(summary['researchBoundary'], 'FINAL_OOS_EXCLUDED')
        finally: m.close()


if __name__ == '__main__': unittest.main()
