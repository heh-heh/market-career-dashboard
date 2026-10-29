#!/usr/bin/env python3
"""Four switch combinations and REST authorization around session transitions."""
from __future__ import annotations
import json
import tempfile
from datetime import datetime,timedelta,timezone
from pathlib import Path

from collector_policy import choose_worker,enabled,rest_allowed

NOW=datetime(2026,10,29,16,25,tzinfo=timezone.utc)

def decision(mode,confirmed=True,reason=None):
    return {"mode":mode,"calendarConfirmed":confirmed,"reason":reason,
            "openAt":"2026-10-29T13:30:00+00:00",
            "closeAt":"2026-10-29T20:00:00+00:00"}

def permit(mode,reason,confirmed=True,secs=4,deadline=4):
    return {"mode":mode,"reason":reason,"calendarConfirmed":confirmed,
            "leaseExpiresAt":(NOW+timedelta(seconds=secs)).isoformat(),
            "requestDeadline":(NOW+timedelta(seconds=deadline)).isoformat()}

def check():
    for tick in (False,True):
        for history in (False,True):
            role,info=choose_worker(decision("MARKET_OPEN"),ticks=tick,history=history)
            expected="tick" if tick else ("backfill" if history else None)
            assert role==expected,(tick,history,"open",info)
            if history and not tick: assert info["mode"]=="HISTORY_ONLY_RUNNING"
            role,info=choose_worker(decision("MARKET_CLOSED"),ticks=tick,history=history)
            expected="backfill" if history else None
            assert role==expected,(tick,history,"closed",info)
            if role=="backfill":assert info["mode"]=="MARKET_CLOSED"
            role,info=choose_worker(decision("SWITCHING",reason="PREOPEN_REQUEST_GUARD"),
                                     ticks=tick,history=history)
            expected="backfill" if history and not tick else None
            assert role==expected,(tick,history,"preopen",info)
            for blocked in ("ERROR","PAUSED"):
                role,_=choose_worker(decision(blocked),ticks=tick,history=history)
                assert role is None,(tick,history,blocked)
    role,info=choose_worker(decision("MARKET_OPEN",confirmed=False),ticks=False,history=True)
    assert role is None and info["mode"]=="ERROR"
    mode="HISTORY_ONLY_RUNNING"
    p=permit(mode,"HISTORY_ONLY_DURING_MARKET_OPEN")
    assert rest_allowed(p,NOW,is_market_open=True,ticks=False,history=True)
    assert not rest_allowed(p,NOW,is_market_open=True,ticks=True,history=True)
    assert not rest_allowed(p,NOW,is_market_open=True,ticks=False,history=False)
    assert not rest_allowed({**p,"calendarConfirmed":False},NOW,is_market_open=True,ticks=False,history=True)
    assert not rest_allowed(permit(mode,"HISTORY_ONLY_DURING_MARKET_OPEN",secs=-1),
                            NOW,is_market_open=True,ticks=False,history=True)
    assert not rest_allowed({**p,"mode":"SWITCHING","reason":"WAITING_FOR_WORKER_FLUSH"},
                            NOW,is_market_open=True,ticks=False,history=True)
    closed=permit("MARKET_CLOSED",None)
    assert rest_allowed(closed,NOW,is_market_open=False,ticks=True,history=True)
    assert not rest_allowed(closed,NOW,is_market_open=True,ticks=True,history=True)
    assert not rest_allowed(permit("MARKET_CLOSED",None,deadline=-1),
                            NOW,is_market_open=False,ticks=False,history=True)
    with tempfile.TemporaryDirectory() as temp:
        c=Path(temp)/"tick.json"
        assert not enabled(str(c)) and not enabled(None)
        c.write_text(json.dumps({"enabled":True}))
        assert enabled(str(c))
        c.write_text('{"enabled":false}')
        assert not enabled(str(c))
        c.write_text('garbled')
        assert not enabled(str(c))
        target=Path(temp)/"value"
        target.write_text('{"enabled":true}')
        c.unlink()
        c.symlink_to(target)
        assert not enabled(str(c))
    print("STOCK_TOGGLE_POLICY_4X_SESSION_MODES=PASS")

if __name__=="__main__":check()
