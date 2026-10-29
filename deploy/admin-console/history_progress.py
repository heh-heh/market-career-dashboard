"""Restricted, read-only historical backfill progress for the private admin.

The scan % measures the requested time interval crossed by the stored pagination
cursor. It is NOT completeness of the 1-minute OHLCV dataset. Audit ratios are
shown only on a finished pass and are not final data-quality certification.
"""
from __future__ import annotations
import json
import math
from datetime import datetime, timezone
from pathlib import Path

START="2015-01-01T00:00:00-05:00"
END="2026-10-01T23:59:59-04:00"
ROOT=Path("/srv/ai-data/stock-collection/historical-50-2015-2026")
CONFIG=Path("/srv/apps/_deploy/stock-collection/private/historical-symbols.json")
CONTROL=Path("/srv/ai-data/stock-collection/runtime/collection/historical-control.json")

def parse_stamp(raw):
    try:
        d=datetime.fromisoformat(str(raw).replace("Z","+00:00"))
        return d.astimezone(timezone.utc) if d.tzinfo else None
    except (ValueError,TypeError,OverflowError):
        return None

def safe_read(path, limit=2_000_000):
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size>limit:
            return {}
        data=json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data,dict) else {}
    except (OSError,ValueError):
        return {}

def safe_int(value):
    if isinstance(value,bool):
        return None
    try:
        n=int(value)
        return n if 0<=n<=1_000_000_000 else None
    except (ValueError,TypeError,OverflowError):
        return None

def progress_snapshot(root=ROOT,symbols_path=CONFIG,control_path=CONTROL):
    config=safe_read(symbols_path)
    raw=config.get("symbols")
    if not isinstance(raw,list) or len(raw)!=50 or len(set(
            x for x in raw if isinstance(x,str)))!=50:
        return {"configured":False,"enabled":False,"symbols":[],"symbolCount":50,
                "startDate":"2015-01-01","endDate":"2026-10-01","scanPercent":None,
                "error":"HISTORICAL_50_NOT_CONFIGURED"}
    controls=safe_read(control_path)
    records=safe_read(root/"_collection_progress.json")
    start=parse_stamp(START)
    end=parse_stamp(END)
    span=(end-start).total_seconds()
    out=[]
    for symbol in raw:
        if not isinstance(symbol,str) or not symbol.isalnum() or len(symbol)>8:
            continue
        state=records.get(symbol,{})
        if not isinstance(state,dict):
            state={}
        status=str(state.get("status") or "queued")[:65]
        if status not in ("queued","collecting","checkpoint","error","collection_pass_finished_unverified"):
            status="unknown"
        cursor=parse_stamp(state.get("historyCursor"))
        if cursor:
            scan=round(max(0.0,min(100.0,(end-cursor).total_seconds()/span*100.0)),2)
        else:
            # No checkpointed cursor means no provable scanned portion; report
            # the conservative 0%, even for errors, rather than invent coverage.
            scan=0.0
        verified=safe_int(state.get("verifiedTradingDays"))
        missing=safe_int(state.get("missingTradingDays"))
        completed=(status=="collection_pass_finished_unverified")
        verified_ratio=(round(verified/(verified+missing)*100,2)
                        if completed and verified is not None and missing is not None
                        and verified+missing>0 else None)
        code=state.get("errorCode")
        code=str(code)[:70] if isinstance(code,str) and code.replace("_","").isalnum() else None
        out.append({
            "symbol":symbol,"state":status,"scanPercent":scan,
            "verifiedDaysPercent":verified_ratio,"verifiedTradingDays":verified,
            "missingTradingDays":missing,"pages":safe_int(state.get("page")) or 0,
            "storedRows":safe_int(state.get("stored")) or 0,
            "http429Count":safe_int(state.get("http429Count")) or 0,
            "errorCode":code,"oldestTimestamp":(str(state["oldestTimestamp"])[:35]
                if isinstance(state.get("oldestTimestamp"),str) else None),
            "updatedAt":state.get("updatedAt") if isinstance(state.get("updatedAt"),(float,int)) else None
        })
    known=[x["scanPercent"] for x in out if x["scanPercent"] is not None]
    mean=round(sum(known)/len(out),2) if len(known)==len(out) and out else None
    counts={state:sum(item["state"]==state for item in out) for state in
            ("queued","collecting","checkpoint","error","collection_pass_finished_unverified")}
    return {
        "configured":len(out)==50,"enabled":controls.get("enabled") is True,
        "controlReason":str(controls.get("reason") or "NO_CONTROL_REASON")[:70],
        "controlUpdatedAt":str(controls.get("updatedAt") or "")[:40] or None,
        "symbolCount":50,"startDate":"2015-01-01","endDate":"2026-10-01",
        "scanPercent":mean,"scanKnownCount":len(known),
        "totalStoredRows":sum(x["storedRows"] for x in out),
        "totalPages":sum(x["pages"] for x in out),
        "counts":counts,"symbols":out,
        "progressBasis":"CALENDAR_CURSOR_NOT_COMPLETENESS",
        "verificationPolicy":"RTH_VERIFIED_SESSION_RATIO_ONLY_AFTER_PASS",
        "warning":"상장 전 기간, API 보존 한계 및 결측으로 100% 확보가 불가능할 수 있음"
    }
