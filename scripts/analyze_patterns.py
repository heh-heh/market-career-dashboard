#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pattern_engine import aggregate_3m, common_precondition, evaluate_signals, scan_patterns
DATA = ROOT / "data" / "dashboard.json"


def pct(values):
    vals=[x for x in values if x is not None]
    return round(sum(vals)/len(vals),3) if vals else None


def summarize(rows):
    grouped=defaultdict(list)
    for r in rows:
        grouped[r["pattern"]].append(r)
    out=[]
    for pattern, items in sorted(grouped.items()):
        returns3=[x["return3Pct"] for x in items]
        returns5=[x["return5Pct"] for x in items]
        returns10=[x["return10Pct"] for x in items]
        def win(vals):
            vals=[v for v in vals if v is not None]
            return round(sum(v>0 for v in vals)/len(vals)*100,1) if vals else None
        out.append({
            "pattern":pattern,
            "signals":len(items),
            "avg9mReturnPct":pct(returns3),
            "win9mPct":win(returns3),
            "avg15mReturnPct":pct(returns5),
            "win15mPct":win(returns5),
            "avg30mReturnPct":pct(returns10),
            "win30mPct":win(returns10),
            "stopWithin10BarsPct":round(sum(bool(x["stopWithin10Bars"]) for x in items)/len(items)*100,1) if items else None,
        })
    return out


def main():
    data=json.loads(DATA.read_text(encoding="utf-8"))
    source_items=data.get("stocks",[])
    report={
        "generatedAt":data.get("updatedAt"),
        "source":"data/dashboard.json",
        "scope":[],
        "notes":[
            "현재 dashboard.json의 저장 캔들만 사용한 검증입니다.",
            "미국주식 데이터는 Yahoo Finance 1분봉을 3분봉으로 합성합니다.",
            "공통 조건의 '당일 거래대금 순위 <=150'은 dashboard.json에 순위가 없어 완전 검증할 수 없습니다.",
            "3분봉 20억원 조건은 환율 기본값 1 USD=1400 KRW로 참고 계산합니다.",
            "Pattern 3, 4, 5는 원문 수식과 수정 수식을 각각 비교합니다.",
            "이 데이터셋은 종목당 최근 하루 수준의 1분봉이어서 통계적 백테스트 결론으로 사용하면 안 됩니다."
        ],
        "preconditions":[],
        "signals":[],
        "summary":[]
    }

    all_signal_rows=[]
    for item in source_items:
        candles=item.get("candles",{}).get("1m",[])
        if not candles:
            continue
        bars=aggregate_3m(candles)
        if len(bars)<25:
            continue
        history=[x for x in item.get("history",[]) if isinstance(x,(int,float))]
        prev_close=history[-2] if len(history)>=2 else None
        pre=common_precondition(bars,prev_close,trading_rank=None,fx_krw_per_usd=1400.0)
        pre["symbol"]=item.get("symbol")
        pre["name"]=item.get("name")
        report["preconditions"].append(pre)
        report["scope"].append({
            "symbol":item.get("symbol"),
            "name":item.get("name"),
            "candles1m":len(candles),
            "bars3m":len(bars),
            "signalCount":len(scan_patterns(bars))
        })
        sigs=evaluate_signals(bars,scan_patterns(bars))
        for s in sigs:
            s["symbol"]=item.get("symbol")
            s["name"]=item.get("name")
        all_signal_rows.extend(sigs)

    report["signals"]=all_signal_rows
    report["summary"]=summarize(all_signal_rows)

    # Print concise human-readable evidence in Actions logs.
    print("=== 3-MIN PATTERN ANALYSIS ===")
    print("symbols=",len(report["scope"]), "signals=",len(all_signal_rows))
    for r in report["summary"]:
        print(
            f'{r["pattern"]}: signals={r["signals"]} '
            f'avg9m={r["avg9mReturnPct"]}% win9m={r["win9mPct"]}% '
            f'avg15m={r["avg15mReturnPct"]}% win15m={r["win15mPct"]}% '
            f'avg30m={r["avg30mReturnPct"]}% win30m={r["win30mPct"]}% '
            f'stop10={r["stopWithin10BarsPct"]}%'
        )

    out=ROOT/"pattern_analysis_report.json"
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print("report=",out)
    print("=== PRECONDITION COVERAGE ===")
    for p in report["preconditions"]:
        print(
            p["symbol"],
            "max3mValueKRW=", round(p["maxThreeMinuteValueKrw"]),
            "maxIntradayReturn=", None if p["intradayMaxReturnPct"] is None else round(p["intradayMaxReturnPct"],2),
            "rank=UNKNOWN"
        )


if __name__=="__main__":
    main()
