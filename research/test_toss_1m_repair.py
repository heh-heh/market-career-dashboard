"""Tiny offline repair fixtures. No API calls, EC2 mutation or evolution training."""
from contextlib import closing
from contextlib import redirect_stdout
import io
import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from research.backtest_v4_strategies import MINUTE, exchange_sessions, file_sha256
from research.toss_1m_repair import (FIELDS, audit, collect, validate_stage, checked_stage,
    inspect_rows, prepare_merge, apply_merge, recover_merge, snapshot, write_csv,
    sessions_checked, disk_guard, save, readonly_db, verify_snapshot, adopt_staging)


class RepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.day = "2024-07-26"
        cls.calendar = sessions_checked([cls.day])
        a,b = cls.calendar[cls.day]
        cls.rows = [dict(zip(FIELDS, ((a+(i+1)*MINUTE).isoformat(), "100", "101", "99", "100.5", "10", "USD"))) for i in range(int((b-a)/MINUTE))]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.prod = self.root/"production"; self.prod.mkdir()
        self.guard = patch("research.toss_1m_repair.disk_guard"); self.guard.start(); self.addCleanup(self.guard.stop)
        self.stage = self.root/"staging"
        self.operation = self.root/"operation"
        self.source = self.prod/"TQQQ.csv.gz"
        write_csv(self.source, [])

    def stage_rows(self, rows=None, symbols=("TQQQ",)):
        self.stage.mkdir()
        for symbol in symbols: write_csv(self.stage/(symbol+".csv.gz"), self.rows if rows is None else rows)
        save(self.stage/"staging.json", dict(phase="collected",operationId="fixture",symbols=list(symbols),
             dates=[self.day],calendar="XNYS",timestampKind="end",productionRoot=str(self.prod)))
        return validate_stage(self.stage)

    def set_source(self, rows):
        replacement = self.root/"source.csv.gz"; write_csv(replacement, rows); replacement.replace(self.source)

    def db(self, rows):
        path = self.prod/"TQQQ.sqlite3"
        with closing(sqlite3.connect(path)) as conn:
            conn.execute("CREATE TABLE candles(timestamp TEXT PRIMARY KEY,open TEXT,high TEXT,low TEXT,close TEXT,volume TEXT,currency TEXT)")
            conn.executemany("INSERT INTO candles VALUES (?,?,?,?,?,?,?)", [tuple(row[k] for k in FIELDS) for row in rows]); conn.commit()
        return path

    def test_audit_shm_mtime_only_does_not_block(self):
        # Simulate a read-only SQLite connection touching ephemeral WAL-index
        # metadata. This is not evidence that the database/WAL changed.
        import research.toss_1m_repair as repair
        path = self.db([])
        real_signature = repair.db_signature
        calls = [0]

        def simulated_signature(p):
            signature = real_signature(p)
            calls[0] += 1
            signature[path.name + "-shm"] = [1, 2, 32768, calls[0]]
            return signature

        with patch.object(repair, "db_signature", side_effect=simulated_signature):
            report = audit(self.prod, ["TQQQ"], [self.day])
        self.assertTrue(report["symbols"]["TQQQ"]["sqlite"]["shmMetadataChangedDuringAudit"])

    def test_audit_db_change_still_blocks(self):
        import research.toss_1m_repair as repair
        self.db([])
        original = repair.db_signature
        calls = [0]

        def changed_signature(p):
            result = original(p)
            calls[0] += 1
            if calls[0] >= 2:
                key = p.name
                result[key] = result[key][:]
                result[key][-1] += 1
            return result

        with patch.object(repair, "db_signature", side_effect=changed_signature):
            with self.assertRaisesRegex(ValueError, "SQLITE_CHANGED_DURING_AUDIT"):
                audit(self.prod, ["TQQQ"], [self.day])

    def test_xnys_full_session_and_end_labels(self):
        report, rows = inspect_rows(iter(self.rows),"TQQQ",self.calendar)
        self.assertTrue(report["passed"])
        day = report["sessions"][0]
        self.assertEqual(day["actualRegularMinutes"],390)
        self.assertTrue(day["firstRthBarEnd"].endswith("09:31:00-04:00"))
        self.assertTrue(day["lastRthBarEnd"].endswith("16:00:00-04:00"))
        self.assertEqual(day["lastRthBarStart"],"2024-07-26T19:59:00+00:00")

    def test_holiday_weekend_and_2026_block(self):
        for day,reason in (("2024-07-04","NOT_XNYS"),("2024-07-27","NOT_XNYS"),("2026-01-02","FINAL_OOS")):
            with self.assertRaisesRegex(ValueError,reason): sessions_checked([day])

    def test_early_close_and_dst(self):
        for day,expected,offset in (("2024-07-03",210,"-04:00"),("2024-03-08",390,"-05:00"),("2024-03-11",390,"-04:00")):
            a,b=sessions_checked([day])[day]
            self.assertEqual((b-a)/MINUTE,expected); self.assertTrue(a.isoformat().endswith(offset))

    def test_duplicate_and_conflict_distinguished(self):
        report,_=inspect_rows(iter([self.rows[0]]+self.rows),"TQQQ",self.calendar)
        self.assertTrue(report["passed"]); self.assertEqual(report["sessions"][0]["duplicates"],1)
        report,_=inspect_rows(iter([dict(self.rows[0],close="100.6")]+self.rows),"TQQQ",self.calendar)
        self.assertFalse(report["passed"]); self.assertEqual(report["sessions"][0]["conflictingDuplicates"],1)

    def test_invalid_ohlcv_currency_timezone_and_order(self):
        for change in (dict(high="98"),dict(high="1e501",close="1e500"),dict(currency="KRW"),dict(timestamp="2024-07-26T09:31:00")):
            report,_=inspect_rows(iter([dict(self.rows[0],**change)]+self.rows[1:]),"TQQQ",self.calendar)
            self.assertFalse(report["passed"])
        report,_=inspect_rows(iter([self.rows[1],self.rows[0]]+self.rows[2:]),"TQQQ",self.calendar)
        self.assertFalse(report["passed"])

    def test_incomplete_stage_blocks_merge(self):
        result=self.stage_rows(self.rows[:-1]); self.assertFalse(result["validationPassed"])
        with self.assertRaisesRegex(ValueError,"STAGING_VALIDATION_REQUIRED"):
            prepare_merge(self.prod,self.stage,self.operation,"TQQQ")

    def test_out_of_range_stage_blocked(self):
        other=dict(self.rows[0],timestamp="2024-07-29T09:31:00-04:00")
        result=self.stage_rows(self.rows+[other]); self.assertFalse(result["validationPassed"])
        self.assertEqual(result["symbols"]["TQQQ"]["outOfScopeRows"],1)

    def test_hash_mutation_blocks_merge(self):
        self.stage_rows()
        with (self.stage/"TQQQ.csv.gz").open("ab") as stream: stream.write(b"mutated")
        with self.assertRaisesRegex(ValueError,"STAGING_HASH_MISMATCH"): checked_stage(self.stage)

    def test_read_only_sqlite_csv_disagreement(self):
        path=self.db(self.rows[:3]); before=file_sha256(path); csv_before=file_sha256(self.source)
        facts=audit(self.prod,["TQQQ"],[self.day])["symbols"]["TQQQ"]
        self.assertEqual(facts["comparison"][0]["sqliteOnly"],3)
        self.assertEqual(facts["sqlite"]["sessions"][0]["actualRegularMinutes"],3)
        self.assertEqual(file_sha256(path),before); self.assertEqual(file_sha256(self.source),csv_before)
        with closing(readonly_db(path)) as conn:
            with self.assertRaises(sqlite3.OperationalError): conn.execute("DELETE FROM candles")

    def test_csv_without_sqlite_is_valid_source(self):
        self.set_source(self.rows)
        out=audit(self.prod,["TQQQ"],[self.day])["symbols"]["TQQQ"]
        self.assertTrue(out["csv"]["passed"]); self.assertIsNone(out["sqlite"])

    def test_prepare_does_not_modify_original_and_apply_requires_confirmation(self):
        self.stage_rows(); before=file_sha256(self.source)
        plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(file_sha256(self.source),before)
        self.assertEqual(plan["files"][0]["insertedRows"],390)
        with self.assertRaisesRegex(ValueError,"EXPLICIT_OFFLINE"):
            apply_merge(self.operation)
        applied=apply_merge(self.operation,confirmed=True)
        self.assertEqual(applied["phase"],"APPLIED")
        self.assertTrue(audit(self.prod,["TQQQ"],[self.day])["symbols"]["TQQQ"]["csv"]["passed"])
        self.assertEqual(file_sha256(self.operation/applied["files"][0]["backup"]),before)

    def test_existing_history_and_future_opaque_bytes_preserved(self):
        old=dict(self.rows[0],timestamp="2022-01-03T09:31:00-05:00")
        future=dict(self.rows[0],timestamp="2026-01-02T09:31:00-05:00",open="FUTURE_NOT_EVALUATED",high="FUTURE_NOT_EVALUATED")
        self.set_source([old]+self.rows[:2]+[future]); self.stage_rows()
        plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        apply_merge(self.operation,confirmed=True)
        with gzip.open(self.source,"rt") as stream:
            content=stream.read()
        self.assertIn("2022-01-03T09:31:00-05:00",content)
        self.assertIn("FUTURE_NOT_EVALUATED,FUTURE_NOT_EVALUATED",content)
        self.assertEqual(plan["files"][0]["insertedRows"],388)

    def test_identical_production_duplicate_deduped(self):
        self.set_source([self.rows[0]]+self.rows); self.stage_rows()
        plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(plan["files"][0]["identicalDuplicatesRemoved"],1)
        self.assertEqual(plan["files"][0]["insertedRows"],0)

    def test_production_conflict_never_overwritten(self):
        self.set_source([dict(self.rows[0],close="100.6")]); self.stage_rows(); before=file_sha256(self.source)
        with self.assertRaisesRegex(ValueError,"CONFLICTING_PRODUCTION_ROW"):
            prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(file_sha256(self.source),before)

    def test_changed_production_blocks_apply(self):
        self.stage_rows(); prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.set_source(self.rows[:1]); before=file_sha256(self.source)
        with self.assertRaisesRegex(ValueError,"PRODUCTION_CHANGED"):
            apply_merge(self.operation,confirmed=True)
        self.assertEqual(file_sha256(self.source),before)

    def test_sqlite_pair_insert_only_and_consistency(self):
        self.stage_rows(); path=self.db(self.rows[:2]); before=file_sha256(path)
        plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(file_sha256(path),before)
        self.assertEqual(plan["files"][1]["insertedRows"],388)
        apply_merge(self.operation,confirmed=True)
        out=audit(self.prod,["TQQQ"],[self.day])["symbols"]["TQQQ"]
        self.assertEqual(out["comparison"][0]["same"],390)
        self.assertEqual(out["comparison"][0]["conflicting"],0)

    def test_sqlite_conflict_fail_closed(self):
        self.stage_rows(); path=self.db([dict(self.rows[0],close="100.6")]); before=file_sha256(path)
        with self.assertRaisesRegex(ValueError,"CONFLICTING_SQLITE_ROW"):
            prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(file_sha256(path),before)

    def test_active_wal_blocks_preparation(self):
        self.stage_rows(); path=self.db(self.rows[:1]); Path(str(path)+"-wal").write_bytes(b"active")
        with self.assertRaisesRegex(ValueError,"SQLITE_OFFLINE_PREPARATION"):
            prepare_merge(self.prod,self.stage,self.operation,"TQQQ")

    def test_interrupted_paired_replace_restores_original(self):
        self.stage_rows(); db=self.db(self.rows[:2]); csv_hash=file_sha256(self.source); db_hash=file_sha256(db)
        prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        real_replace=__import__("os").replace
        def crash(source,destination):
            if Path(destination)==db and str(source).endswith(".tmp"):
                raise OSError("simulated paired replace interruption")
            return real_replace(source,destination)
        with patch("research.toss_1m_repair.os.replace",side_effect=crash):
            with self.assertRaises(OSError): apply_merge(self.operation,confirmed=True)
        self.assertEqual(file_sha256(self.source),csv_hash); self.assertEqual(file_sha256(db),db_hash)
        self.assertEqual(json.loads((self.operation/"merge.json").read_text())["phase"],"ROLLED_BACK")

    def test_power_loss_requires_explicit_recovery(self):
        self.stage_rows(); old=file_sha256(self.source)
        plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        plan["phase"]="APPLYING"; save(self.operation/"merge.json",plan)
        (self.prod/(".toss-repair-"+plan["operationId"]+".json")).write_text("{}")
        self.source.write_bytes((self.operation/plan["files"][0]["prepared"]).read_bytes())
        with self.assertRaisesRegex(ValueError,"EXPLICIT_OFFLINE"): recover_merge(self.operation)
        recover_merge(self.operation,confirmed=True)
        self.assertEqual(file_sha256(self.source),old)

    def test_disk_guard_and_destination_symlink(self):
        self.guard.stop()
        with patch("research.toss_1m_repair.shutil.disk_usage",return_value=(100*1024**3,99*1024**3,1024**3)):
            with self.assertRaisesRegex(ValueError,"DISK_RESERVE"): disk_guard(self.root)
        self.stage.symlink_to(self.prod, target_is_directory=True)
        with self.assertRaisesRegex(ValueError,"STAGING_MUST_BE_SEPARATE"):
            collect(self.stage,self.prod,["TQQQ"],[self.day],fetch=Mock(),get_token=Mock())

    def test_targeted_collect_isolated_real_shape_pagination(self):
        before=file_sha256(self.source)
        candles=[dict(timestamp=r["timestamp"],openPrice=r["open"],highPrice=r["high"],lowPrice=r["low"],closePrice=r["close"],volume=r["volume"],currency=r["currency"]) for r in reversed(self.rows)]
        responses=[]
        for i in range(0,len(candles),100):
            batch=candles[i:i+100]
            responses.append(dict(candles=batch,nextBefore=batch[-1]["timestamp"]))
        fetch=Mock(side_effect=responses)
        result=collect(self.stage,self.prod,["TQQQ"],[self.day],fetch=fetch,get_token=Mock(return_value="not-a-real-token"),sleep=0)
        self.assertTrue(result["validationPassed"]); self.assertEqual(fetch.call_count,4)
        self.assertEqual(file_sha256(self.source),before)
        self.assertTrue(all("error_recorder" in c.kwargs for c in fetch.call_args_list))
        self.assertNotIn("not-a-real-token",(self.stage/"staging.json").read_text())

    def test_api_out_of_scope_rows_never_persisted_and_failure_not_production_logged(self):
        extra=dict(timestamp="2024-07-29T09:31:00-04:00",openPrice="100",highPrice="101",lowPrice="99",closePrice="100",volume="10",currency="USD")
        first=dict(extra,timestamp=self.rows[0]["timestamp"])
        result=collect(self.stage,self.prod,["TQQQ"],[self.day],fetch=Mock(return_value=dict(candles=[extra,first])),get_token=Mock(return_value="x"))
        self.assertFalse(result["validationPassed"])
        self.assertNotIn(extra["timestamp"],gzip.open(self.stage/"TQQQ.csv.gz","rt").read())

    def test_old_failed_campaign_preserved_and_snapshot_requires_full_2024(self):
        old=self.root/"failed-campaign.json"; old.write_text('{"phase":"error","resultsValid":false}')
        before=old.read_bytes(); self.stage_rows(); prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        apply_merge(self.operation,confirmed=True)
        for symbol in ("SPY","QQQ","SOXX"): write_csv(self.prod/(symbol+".csv.gz"),self.rows)
        with self.assertRaisesRegex(ValueError,"FULL_2024_COVERAGE_REQUIRED"):
            snapshot(self.prod,self.root/"new-snapshot",["TQQQ","SPY","QQQ","SOXX"],[self.operation])
        self.assertEqual(old.read_bytes(),before)

    def test_snapshot_fences_future_and_records_new_identity(self):
        self.stage_rows(); plan=prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        plan=apply_merge(self.operation,confirmed=True)
        future=dict(self.rows[0],timestamp="2026-01-02T09:31:00-05:00",open="FORBIDDEN")
        self.set_source(self.rows+[future])
        for symbol in ("SPY","QQQ","SOXX"): write_csv(self.prod/(symbol+".csv.gz"),self.rows+[future])
        plan["files"][0]["newSha256"]=file_sha256(self.source); save(self.operation/"merge.json",plan)
        coverage=dict(symbols={s:dict(csv=dict(passed=True)) for s in ("TQQQ","SPY","QQQ","SOXX")})
        with patch("research.toss_1m_repair.audit",return_value=coverage):
            result=snapshot(self.prod,self.root/"snapshot",["TQQQ","SPY","QQQ","SOXX"],[self.operation])
        self.assertEqual(result["finalOosGate"],"CLOSED")
        self.assertNotIn("FORBIDDEN",gzip.open(self.root/"snapshot/TQQQ.csv.gz","rt").read())
        self.assertEqual(result["timestampKind"],"end")
        self.assertIn("snapshotId",result)

    def test_timestamp_audit_catches_disorder_after_target_range(self):
        later=dict(self.rows[0],timestamp="2025-01-02T09:31:00-05:00",open="NOT_PARSED")
        report,_=inspect_rows(iter(self.rows+[later,self.rows[-1]]),"TQQQ",self.calendar)
        self.assertFalse(report["passed"])
        self.assertEqual(report["outOfOrderTransitions"],1)

    def test_sqlite_extended_rows_not_misreported_as_rth(self):
        pre=dict(self.rows[0],timestamp="2024-07-26T09:00:00-04:00")
        self.db([pre]+self.rows[:2])
        out=audit(self.prod,["TQQQ"],[self.day])["symbols"]["TQQQ"]
        self.assertEqual(out["sqlite"]["sessions"][0]["extendedRows"],1)
        self.assertEqual(out["sqlite"]["sessions"][0]["actualRegularMinutes"],2)

    def test_missing_reference_snapshot_blocked(self):
        with self.assertRaisesRegex(ValueError,"SNAPSHOT_REQUIRED_REFERENCES"):
            snapshot(self.prod,self.root/"snapshot",["TQQQ"],[self.operation])

    def test_api_failure_secrets_not_logged_and_original_unchanged(self):
        original=file_sha256(self.source)
        def fail(*args,**kwargs):
            kwargs["error_recorder"]("TQQQ","test",0,"password=SECRET",401,"Authorization: SECRET")
            raise RuntimeError("token=SECRET")
        with self.assertRaisesRegex(RuntimeError,"STAGING_BACKFILL_FAILED"):
            collect(self.stage,self.prod,["TQQQ"],[self.day],fetch=fail,get_token=Mock(return_value="SECRET"))
        self.assertNotIn("SECRET",(self.stage/"request_errors.jsonl").read_text())
        self.assertNotIn("SECRET",(self.stage/"staging.json").read_text())
        self.assertEqual(file_sha256(self.source),original)

    def test_repaired_snapshot_new_campaign_provenance_old_campaign_immutable(self):
        from research.evolution import run_v07_campaign as campaign
        self.stage_rows(); prepare_merge(self.prod,self.stage,self.operation,"TQQQ"); apply_merge(self.operation,confirmed=True)
        for s in ("AAPL","SPY","QQQ","SOXX"): write_csv(self.prod/(s+".csv.gz"),self.rows)
        coverage=dict(symbols={s:dict(csv=dict(passed=True)) for s in ("TQQQ","AAPL","SPY","QQQ","SOXX")})
        dest=self.root/"campaign-input"
        with patch("research.toss_1m_repair.audit",return_value=coverage): snapshot(self.prod,dest,list(coverage["symbols"]),[self.operation])
        manifest=dest/"repair_snapshot.json"
        self.assertEqual(verify_snapshot(manifest,dest,["TQQQ","AAPL"])["finalOosGate"],"CLOSED")
        results=self.root/"runs"; results.mkdir()
        old=results/"campaign-old"; old.mkdir(); (old/"campaign.json").write_text('{"phase":"error"}')
        before=(old/"campaign.json").read_bytes()
        def shortlist(train,hold,out,count):
            out.mkdir(); save(out/"shortlist.json",dict(selectedBots=[dict(botId="test")],eligiblePositiveBots=1))
        with patch.object(campaign,"train_main",return_value=results/"new-train") as train, \
             patch.object(campaign,"eval_main",side_effect=[results/"new-hold",results/"new-dev"]) as evaluate, \
             patch.object(campaign,"build_shortlist",side_effect=shortlist), \
             patch.object(campaign,"analyze",return_value=dict(verdict="INCONCLUSIVE")), redirect_stdout(io.StringIO()):
            new=campaign.main(["--data-dir",str(dest),"--results-dir",str(results),"--dataset-snapshot",str(manifest)])
        self.assertNotEqual(new,old); self.assertTrue((new/"dataset_snapshot.json").is_file())
        self.assertEqual((old/"campaign.json").read_bytes(),before)
        self.assertEqual(json.loads((new/"campaign.json").read_text())["finalOosAllowed"],False)
        for call in [train.call_args,*evaluate.call_args_list]:
            argv=call.args[0]
            self.assertEqual(argv[argv.index("--data-dir")+1],str(dest))
            self.assertEqual(argv[argv.index("--timestamp-kind")+1],"end")
            self.assertFalse(any(x.startswith("2026-") for x in argv))
        (dest/"AAPL.csv.gz").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError,"SNAPSHOT_HASH_MISMATCH"):
            verify_snapshot(manifest,dest,["TQQQ","AAPL"])

    def test_existing_remote_collector_staging_adopted_without_api_or_mutation(self):
        existing=self.root/"prior-staging/TQQQ"; existing.mkdir(parents=True)
        path=existing/"TQQQ.csv.gz"; write_csv(path,self.rows)
        before=file_sha256(path)
        with patch("scripts.collect_toss_1m.fetch_page",side_effect=AssertionError("must not fetch")):
            result=adopt_staging(existing.parent,self.stage,self.prod,["TQQQ"],[self.day])
        self.assertTrue(result["validationPassed"]); self.assertEqual(file_sha256(path),before)
        self.assertEqual(json.loads((self.stage/"staging.json").read_text())["sourceMode"],"ADOPTED_UNVERIFIED_STAGING")

    def test_adopt_existing_conflicts_not_hidden(self):
        existing=self.root/"prior-staging"; existing.mkdir()
        path=existing/"TQQQ.csv.gz"; write_csv(path,[dict(self.rows[0],close="100.6")]+self.rows)
        before=file_sha256(path)
        with self.assertRaisesRegex(ValueError,"EXISTING_STAGING_INVALID_OR_CONFLICTING"):
            adopt_staging(existing,self.stage,self.prod,["TQQQ"],[self.day])
        self.assertEqual(file_sha256(path),before)

    def test_mixed_offset_sqlite_future_export_disorder_blocks_publish(self):
        # Existing ET row is later in real time than a new UTC early row, but
        # TEXT ORDER BY would put the ET 10:11 label before UTC 13:31.
        from datetime import timezone, datetime
        self.stage_rows([dict(r,timestamp=datetime.fromisoformat(r["timestamp"]).astimezone(timezone.utc).isoformat()) for r in self.rows])
        path=self.db([self.rows[40]]); before=file_sha256(path)
        with self.assertRaisesRegex(ValueError,"SQLITE_TEXT_EXPORT_ORDER_INVALID"):
            prepare_merge(self.prod,self.stage,self.operation,"TQQQ")
        self.assertEqual(file_sha256(path),before)


if __name__=="__main__":
    unittest.main()
