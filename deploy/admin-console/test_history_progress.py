#!/usr/bin/env python3
"""Offline 50-symbol dashboard progress test; no market data/network required."""
from __future__ import annotations
import json
import tempfile
from pathlib import Path
from history_progress import progress_snapshot

SYMBOLS="AAPL MSFT NVDA AMD INTC AVGO TSM MU QCOM AMAT LRCX KLAC ASML MRVL ARM GOOGL GOOG AMZN META TSLA NFLX PLTR ORCL ADBE CRM NOW SNOW UBER COIN HOOD JPM BAC GS V MA WMT COST UNH LLY XOM SPY QQQ IWM DIA SOXX SMH SOXL SOXS TQQQ SQQQ".split()

def test():
    assert len(SYMBOLS)==50
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw)
        h=root/"history"
        h.mkdir()
        symbols=root/"symbols.json"
        control=root/"control.json"
        symbols.write_text(json.dumps({"symbols":SYMBOLS}))
        control.write_text(json.dumps({"enabled":False}))
        res=progress_snapshot(h,symbols,control)
        assert res["configured"] and res["symbolCount"]==50
        assert not res["enabled"]
        assert res["scanPercent"]==0
        assert len(res["symbols"])==50
        assert res["totalStoredRows"]==0
        assert all(row["verifiedDaysPercent"] is None for row in res["symbols"])
        (h/"_collection_progress.json").write_text(json.dumps({
            "NVDA":{"status":"collecting","historyCursor":"2020-01-01T00:00:00-05:00",
                    "stored":100,"page":10,"verifiedTradingDays":800,"missingTradingDays":1700},
            "SOXL":{"status":"collection_pass_finished_unverified",
                    "historyCursor":"2015-01-01T00:00:00-05:00",
                    "stored":500,"page":20,"verifiedTradingDays":1200,"missingTradingDays":1200},
            "AMD":{"status":"error","errorCode":"RATE_LIMIT_RETRY_DEFERRED","stored":0}
        }))
        res=progress_snapshot(h,symbols,control)
        assert res["totalStoredRows"]==600
        assert res["totalPages"]==30
        nvda=next(x for x in res["symbols"] if x["symbol"]=="NVDA")
        soxl=next(x for x in res["symbols"] if x["symbol"]=="SOXL")
        amd=next(x for x in res["symbols"] if x["symbol"]=="AMD")
        assert 50<nvda["scanPercent"]<60
        assert nvda["verifiedDaysPercent"] is None, "stale audits must not imply completion"
        assert soxl["scanPercent"]>99.9
        assert soxl["verifiedDaysPercent"]==50
        assert amd["errorCode"]=="RATE_LIMIT_RETRY_DEFERRED"
        assert res["counts"]["error"]==1
        assert res["counts"]["collection_pass_finished_unverified"]==1
        assert res["scanPercent"]<10,"average must include queued zero-progress symbols"
        control.write_text(json.dumps({"enabled":True}))
        assert progress_snapshot(h,symbols,control)["enabled"]
        symbols.write_text(json.dumps({"symbols":SYMBOLS[:4]}))
        assert progress_snapshot(h,symbols,control)["configured"] is False
    print("HISTORY_50_PROGRESS_TEST=PASS")

if __name__=="__main__":
    test()
