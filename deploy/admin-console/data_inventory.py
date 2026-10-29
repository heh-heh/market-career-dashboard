"""Bounded, read-only inventory of data files owned by the stock collector.

Uses metadata/checkpoints and os.stat, not COUNT(*) on dozens of large SQLite
files or a recursive scan. Values from progress JSON are explicitly labeled as
last recorded checkpoint counts, NOT a fresh SQL integrity or completeness test.
"""
from __future__ import annotations

import re
from pathlib import Path

from history_progress import progress_snapshot, safe_read

ROOT=Path("/srv/ai-data/stock-collection")
HISTORY=ROOT/"historical-50-2015-2026"
TICKS=ROOT/"ticks"
DAY=re.compile(r"20[0-9]{2}-[01][0-9]-[0-3][0-9]")
SYMBOL=re.compile(r"[A-Z0-9]{1,8}")


def file_size(path: Path):
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return max(0,path.stat().st_size)
    except OSError:
        return None


def inventory(root=ROOT, progress=None):
    root=Path(root)
    historic=root/"historical-50-2015-2026"
    tick_root=root/"ticks"
    progress=progress if progress is not None else progress_snapshot(root=historic)
    data=[]
    for item in progress.get("symbols",[])[:50]:
        if not isinstance(item,dict):
            continue
        symbol=item.get("symbol")
        if not isinstance(symbol,str) or not SYMBOL.fullmatch(symbol):
            continue
        db_bytes=file_size(historic/(symbol+".sqlite3"))
        checkpoint_rows=item.get("storedRows") if db_bytes is not None else None
        data.append({
            "symbol":symbol,
            "dbExists":db_bytes is not None,
            "bytes":db_bytes,
            "checkpointRows":checkpoint_rows,
            "oldest":item.get("oldestTimestamp") if db_bytes is not None else None,
            "newest":item.get("latestTimestamp") if db_bytes is not None else None,
            "scanPercent":item.get("scanPercent"),
            "verifiedDaysPercent":item.get("verifiedDaysPercent"),
            "missingDays":item.get("missingTradingDays"),
            "state":item.get("state"),
            "lastProgressAt":item.get("updatedAt")
        })
    # One bounded directory listing, no recursive walk or file-content reads.
    dates=[]
    try:
        if not tick_root.is_symlink() and tick_root.is_dir():
            dates=[item for item in tick_root.iterdir()
                   if DAY.fullmatch(item.name) and not item.is_symlink() and item.is_dir()]
    except OSError:
        dates=[]
    dates.sort(key=lambda item:item.name,reverse=True)
    latest=dates[0] if dates else None
    latest_files=[]
    if latest:
        try:
            for p in latest.iterdir():
                if not p.name.endswith(".csv") or not SYMBOL.fullmatch(p.name[:-4]):
                    continue
                n=file_size(p)
                if n is not None:
                    latest_files.append({"symbol":p.name[:-4],"bytes":n})
        except OSError:
            latest_files=[]
    tick_status=safe_read(tick_root/"status.json")
    return {
        "kind":"PRIVATE_COLLECTOR_STORAGE_INVENTORY",
        "basis":"FILE_METADATA_AND_LAST_COLLECTOR_CHECKPOINT",
        "history":{
            "targetCount":50,
            "fileCount":sum(bool(x["dbExists"]) for x in data),
            "storedBytes":sum(x["bytes"] or 0 for x in data),
            "checkpointRows":sum(x["checkpointRows"] or 0 for x in data),
            "rowsCountIsLive":False,
            "startDate":"2015-01-01","endDate":"2026-10-01",
            "symbols":data
        },
        "ticks":{
            "sessionFolderCount":len(dates),
            "lastSession":latest.name if latest else None,
            "lastSessionFiles":sorted(latest_files,key=lambda x:x["symbol"]),
            "lastSessionBytes":sum(x["bytes"] for x in latest_files),
            "status":str(tick_status.get("state") or "NO_TICK_STATUS")[:72],
            "lastUpdate":str(tick_status.get("updatedAt") or "")[:45] or None
        },
        "notice":"행 수와 수집 기간은 마지막 체크포인트 기록이며 DB 정합성·모든 거래일의 확보를 의미하지 않습니다."
    }
