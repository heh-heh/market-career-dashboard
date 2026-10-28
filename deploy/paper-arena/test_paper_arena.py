#!/usr/bin/env python3
"""Pure offline test: fake quotes & CSV, no broker, no network, no real ticks."""
from __future__ import annotations
import csv
import json
import sqlite3
import tempfile
from datetime import datetime, timezone, timedelta
from pathlib import Path
import paper_arena as app


def main():
    fixed = datetime(2026, 10, 28, 15, 0, 0, tzinfo=timezone.utc)
    with tempfile.TemporaryDirectory(prefix="paper-arena-test-") as d:
        root = Path(d)
        app.BASE = root / "paper"
        app.TICKS = root / "ticks"
        app.BASE.mkdir()
        app.TICKS.mkdir()
        app.utcnow = lambda: fixed
        day = "2026-10-28"
        folder = app.TICKS / day
        folder.mkdir()
        status = {
            "state": "collecting", "marketSession": "REGULAR",
            "sessionDate": day,
            "openAt": (fixed-timedelta(hours=1)).isoformat(),
            "closeAt": (fixed+timedelta(hours=1)).isoformat()
        }
        app.atomic_json(app.TICKS / "status.json", status)
        app.atomic_json(app.BASE / "control.json", {"paused": True})
        target=folder/"SOXL.csv"
        with target.open("w",newline="") as f:
            csv.writer(f).writerow(app.SCHEMA)
        db=sqlite3.connect(app.BASE/"ledger.sqlite3")
        app.init_db(db)
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==20
        app.cycle(db)
        assert app.read_json(app.BASE/"status.json")["state"]=="PAUSED"
        assert all(a["state"] == "PAUSED" for a in app.read_json(app.BASE/"status.json")["agents"])
        app.atomic_json(app.BASE/"control.json",{"paused":False})
        app.cycle(db)  # first encounter: start at EOF, don't trade on old rows
        with target.open("a",newline="") as f:
            writer=csv.writer(f)
            for i in range(55):
                writer.writerow([
                    (fixed-timedelta(seconds=3)+timedelta(milliseconds=i*10)).isoformat(),
                    (fixed-timedelta(seconds=2)+timedelta(milliseconds=i*10)).isoformat(),
                    "100.00" if i<15 else "105.00",
                    "2","USD","fixture",str(i+1)])
        app.cycle(db)
        status=app.read_json(app.BASE/"status.json")
        count=status["tradeCount"]
        assert status["realOrdersEnabled"] is False
        assert status["mode"]=="RECEIVED_TICK_PAPER_PILOT"
        assert count > 0, status
        assert status["bots"] == 20
        assert status["equityUsd"] > 0
        assert status["cashUsd"] < 20000
        assert status["uniqueHoldings"] >= 1
        agents=status.get("agents", [])
        assert len(agents) == 20, "one card per paper account"
        assert len({agent["id"] for agent in agents}) == 20, "unique bot IDs"
        assert sum(round(agent["cashUsd"]*100) for agent in agents) == round(status["cashUsd"]*100)
        assert sum(round(agent["equityUsd"]*100) for agent in agents) == round(status["equityUsd"]*100)
        assert sum(round(agent["investedUsd"]*100) for agent in agents) == round(status["investedUsd"]*100)
        assert all(agent["initialCapitalUsd"] == 1000 for agent in agents)
        assert all(agent["strategy"] in ("MOMENTUM","MEAN_REVERSION") for agent in agents)
        assert all(len(agent["holdings"]) <= 3 for agent in agents)
        assert any(agent["lastTrade"] is not None for agent in agents)
        assert any(agent["state"] == "HOLDING" for agent in agents)
        app.cycle(db)
        assert app.read_json(app.BASE/"status.json")["tradeCount"]==count, "duplicate trade on poll"
        db.close()
        db=sqlite3.connect(app.BASE/"ledger.sqlite3")
        app.init_db(db)
        app.cycle(db)
        assert app.read_json(app.BASE/"status.json")["tradeCount"]==count, "duplicate trade on restart"
        assert len(app.read_json(app.BASE/"status.json")["agents"])==20, "agents persist across restart"
        app.atomic_json(app.BASE/"control.json",{"paused":True})
        app.cycle(db)
        assert app.read_json(app.BASE/"status.json")["state"]=="PAUSED"
        assert db.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]==20
        db.close()
    print("PAPER_OFFLINE_TEST=PASS")
    print("FIXTURE_TRADES="+str(count))
    print("REAL_BROKER_API_CALLS=0")


if __name__ == "__main__":
    main()
