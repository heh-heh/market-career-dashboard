#!/usr/bin/env python3
"""Offline private collector inventory: actual files vs checkpoint metadata."""
from pathlib import Path
import tempfile
import json
from data_inventory import inventory


def run():
    with tempfile.TemporaryDirectory() as raw:
        root=Path(raw)
        history=root/"historical-50-2015-2026"
        history.mkdir()
        ticks=root/"ticks"
        ticks.mkdir()
        (history/"NVDA.sqlite3").write_bytes(b"SQLite temporary fixture")
        (history/"AMD.sqlite3").write_bytes(b"Another file")
        (ticks/"2026-10-28").mkdir()
        (ticks/"2026-10-29").mkdir()
        (ticks/"2026-10-29"/"NVDA.csv").write_bytes(b"date,price\n")
        (ticks/"2026-10-29"/"SOXL.csv").write_bytes(b"date,price\n2026-10-29,10\n")
        (ticks/"status.json").write_text(json.dumps({"state":"stopped","updatedAt":"2026-10-29T00:00:00Z"}))
        source={"symbols":[
            {"symbol":"NVDA","storedRows":110,"oldestTimestamp":"2015-01-01T00:00:00Z",
             "latestTimestamp":"2026-10-01T00:00:00Z","scanPercent":50,"missingTradingDays":12,"state":"checkpoint"},
            {"symbol":"AMD","storedRows":30,"oldestTimestamp":None,"latestTimestamp":None,
             "scanPercent":0,"missingTradingDays":None,"state":"queued"},
            {"symbol":"SOXL","storedRows":500,"scanPercent":90,"state":"collecting"}
        ]}
        snap=inventory(root,source)
        assert snap["history"]["targetCount"]==50
        assert snap["history"]["fileCount"]==2
        assert snap["history"]["checkpointRows"]==140, "Nonexistent SOXL DB must not count"
        assert snap["history"]["storedBytes"]>0
        nvda=snap["history"]["symbols"][0]
        soxl=snap["history"]["symbols"][2]
        assert nvda["checkpointRows"]==110 and nvda["bytes"]>0
        assert soxl["checkpointRows"] is None and not soxl["dbExists"]
        assert snap["ticks"]["sessionFolderCount"]==2
        assert snap["ticks"]["lastSession"]=="2026-10-29"
        assert len(snap["ticks"]["lastSessionFiles"])==2
        assert snap["ticks"]["status"]=="stopped"
        (history/"AMD.sqlite3").unlink()
        (history/"AMD.sqlite3").symlink_to(history/"NVDA.sqlite3")
        snap=inventory(root,source)
        assert snap["history"]["fileCount"]==1, "Never follow per-symbol symlinks"
    print("PRIVATE_STOCK_INVENTORY_TEST=PASS")


if __name__=="__main__":
    run()
