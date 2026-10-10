#!/usr/bin/env python3
"""Read-only descriptive V4 result analysis; never launches a backtest.

Accept JSON or the existing admin ZIP. Cost counterfactuals keep actual trade
selection/times/quantity/R denominator fixed: they are NOT strategy reruns.
"""
import argparse
import copy
import hashlib
import json
import statistics
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

try:
    from .intraday_v4_metrics import summarize, grouped, time_bucket
    from .backtest_intraday_v4 import atomic_json
except ImportError:
    from intraday_v4_metrics import summarize, grouped, time_bucket
    from backtest_intraday_v4 import atomic_json

MAX_JSON_BYTES = 64 * 1024 * 1024


def input_hash(path):
    digest=hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""): digest.update(block)
    return digest.hexdigest()


def load(path):
    path = Path(path)
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            members = [i for i in z.infolist() if i.filename.endswith("_result.json") or Path(i.filename).name == "result.json"]
            if len(members) != 1: raise ValueError("ZIP must contain exactly one result JSON")
            if members[0].file_size > MAX_JSON_BYTES: raise ValueError("Result JSON exceeds size limit")
            result = json.loads(z.read(members[0]))
            audits = [i for i in z.infolist() if i.filename.endswith("_data_audit.json") or Path(i.filename).name == "audit.json"]
            audit = json.loads(z.read(audits[0])) if len(audits) == 1 and audits[0].file_size <= MAX_JSON_BYTES else None
    else:
        if path.stat().st_size > MAX_JSON_BYTES: raise ValueError("Result JSON exceeds size limit")
        result = json.loads(path.read_text())
        audit_path = Path(result.get("data", {}).get("auditPath", ""))
        audit = json.loads(audit_path.read_text()) if audit_path.is_file() else None
    if not isinstance(result.get("trades"), list): raise ValueError("Result has no trade records")
    return result, audit


def chronological(trades):
    for t in trades:
        if datetime.fromisoformat(t["exitTimestamp"]).tzinfo is None:
            raise ValueError("Ambiguous naive trade timestamp")
    return sorted(trades, key=lambda t: (datetime.fromisoformat(t["exitTimestamp"]).astimezone(timezone.utc), t["strategy"], t["symbol"]))


def cost_counterfactual(trades, slippage_bps, half_spread_bps):
    out = []
    cost = (slippage_bps + half_spread_bps) / 10000
    for source in trades:
        t = copy.deepcopy(source)
        E = t["entryMarketPrice"] * (1 + cost)
        X = t["exitMarketPrice"] * (1 - cost)
        risk = source["entryPrice"] - source["initialStop"]
        if risk <= 0: raise ValueError("Nonpositive original risk")
        t.update(returnPct=100*(X/E-1), pnlUsd=t["quantity"]*(X-E), netR=(X-E)/risk)
        out.append(t)
    return out


def concentration(trades, key):
    pnl = defaultdict(float)
    counts = defaultdict(int)
    for t in trades:
        label = key(t)
        pnl[label] += t["pnlUsd"]
        counts[label] += 1
    positive = {k: v for k, v in pnl.items() if v > 0}
    total = sum(positive.values())
    return dict(tradeCounts=dict(sorted(counts.items())), netPnlUsd=dict(sorted(pnl.items())),
                largestPositiveNetPnlShare=max(positive.values())/total if total else None,
                note="Share of positive group net PnL; not share of overall net PnL")


def concentration_stress(trades,cash):
    out={}
    for name,key in (("symbol",lambda t:t["symbol"]),("year",lambda t:t["date"][:4]),("month",lambda t:t["date"][:7])):
        groups=defaultdict(float)
        for t in trades: groups[key(t)]+=t["pnlUsd"]
        best=max(groups,key=lambda k:(groups[k],k)) if groups else None
        out["removeBestNetPnl"+name.title()]=dict(removedGroup=best,
            metrics=summarize([t for t in trades if key(t)!=best],cash))
    winners=sorted((i for i,t in enumerate(trades) if t["pnlUsd"]>0),key=lambda i:-trades[i]["pnlUsd"])[:3]
    out["removeUpToThreeLargestWinners"]=dict(removedTrades=len(winners),
        metrics=summarize([t for i,t in enumerate(trades) if i not in winners],cash))
    return dict(semantics="POST_HOC_DIAGNOSTIC_NOT_A_UNIVERSE_FILTER_OR_STRATEGY_RERUN",scenarios=out)


def folds(result, trades, cash):
    out = []
    for f in result.get("chronologicalEvaluation", {}).get("folds", []):
        row = {k: f[k] for k in ("trainPeriod", "validationPeriod", "oosPeriod", "embargoSession") if k in f}
        for kind in ("train", "validation", "oos"):
            lo, hi = f[kind + "Period"]
            selected = [t for t in trades if lo <= t["date"] < hi and t["date"] != f.get("embargoSession") and lo <= t["exitTimestamp"][:10] < hi]
            row[kind] = summarize(selected, cash)
        out.append(row)
    expectancies = [f["oos"]["expectancyR"] for f in out if f["oos"]["trades"]]
    factors = [f["oos"]["profitFactorR"] for f in out if f["oos"]["profitFactorR"] is not None]
    return dict(folds=out, positiveOOSFolds=sum(x > 0 for x in expectancies), nonemptyOOSFolds=len(expectancies),
                totalOOSFolds=len(out), oosMedianExpectancyR=statistics.median(expectancies) if expectancies else None,
                oosMedianPF_R=statistics.median(factors) if factors else None,
                note="Rolling OOS windows; empty folds excluded from medians but explicitly counted; zero-loss PF is null/infinite, not dropped from fold records")


def analyze(result):
    strategies = result["configuration"]["strategy"]
    cash = result["configuration"].get("initialCashPerStrategy", 10000) * len(strategies)
    trades = chronological(result["trades"])
    curve = []
    equity = peak = cash
    exits = defaultdict(float)
    for t in trades: exits[t["exitTimestamp"]] += t["pnlUsd"]
    for ts in sorted(exits, key=lambda x: datetime.fromisoformat(x).astimezone(timezone.utc)):
        equity += exits[ts]; peak = max(peak, equity)
        curve.append(dict(timestamp=ts, equityUsd=equity, realizedDrawdownPct=100*(peak-equity)/peak))
    by_strategy = grouped(trades, lambda t: t["strategy"], cash / len(strategies))
    for strategy in strategies: by_strategy.setdefault(strategy, summarize([], cash / len(strategies)))
    groups = dict(bySymbol=grouped(trades, lambda t: t["symbol"], cash),
                  byYear=grouped(trades, lambda t: t["date"][:4], cash),
                  byMonth=grouped(trades, lambda t: t["date"][:7], cash),
                  bySession=grouped(trades, lambda t: t["session"], cash),
                  byExitReason=grouped(trades, lambda t: t["exitReason"], cash),
                  byRegime=grouped(trades, lambda t: t["marketRegime"]["direction"] + "/" + t["marketRegime"]["volatility"], cash),
                  byTimeOfDay=grouped(trades, time_bucket, cash))
    costs = {name: summarize(cost_counterfactual(trades, slip, spread), cash)
             for name, slip, spread in (("zeroCostSameObservedMarks", 0, 0), ("2bpsPlus1bpsSpread", 2, 1), ("5bpsPlus1bpsSpread", 5, 1))}
    return dict(overall=summarize(trades, cash), byStrategy=by_strategy, **groups,
                chronologicalEvaluation=folds(result, trades, cash),
                foldsByStrategy={s: folds(result, [t for t in trades if t["strategy"] == s], cash / len(strategies)) for s in strategies},
                equityCurve=dict(initialCashUsd=cash, marks=curve, semantics="REALIZED_EXIT_ONLY_NOT_MARK_TO_MARKET"),
                tradeDistribution=[dict(strategy=t["strategy"], symbol=t["symbol"], date=t["date"], netR=t["netR"], returnPct=t["returnPct"],
                                        exitReason=t["exitReason"], MFE_R=t.get("MFE_R"), MAE_R=t.get("MAE_R"),
                                        holdSeconds=t["holdDurationSeconds"], entryToMFESeconds=t.get("entryToMFESeconds"),
                                        entryToMAESeconds=t.get("entryToMAESeconds"), diagnostics=t.get("diagnostics", {})) for t in trades],
                concentration=dict(year=concentration(trades, lambda t:t["date"][:4]),
                                   month=concentration(trades, lambda t:t["date"][:7]),
                                   symbol=concentration(trades, lambda t:t["symbol"])),
                concentrationStress=concentration_stress(trades,cash),
                robustnessCounterfactuals=dict(model="FIXED_OBSERVED_TRADE_TIMES_QUANTITY_AND_ORIGINAL_R_DENOMINATOR_NOT_A_STRATEGY_RERUN",
                    feeUSD=0, feeVerified=False, quoteVerified=False, scenarios=costs,
                    warning="Signals, cost guard, sizing, stop path and universe would differ in a real rerun; no delayed-execution claim"),
                funnel=result.get("funnel", {}), diagnostics=result.get("diagnostics", {}),
                unresolvedPositions=result.get("unresolvedPositions", []),
                sampleVerdict="INSUFFICIENT_SAMPLE" if len(trades) < 50 else "DESCRIPTIVE_REQUIRES_OOS_REVIEW",
                dataMode=result["configuration"].get("dataMode", result.get("data", {}).get("reviewStatus", "UNKNOWN")),
                warnings=["No profitability or significance claim", "Present-day seed universe retains survivorship bias",
                          "Group/fold account percentages use the declared capital, not summed trade returns",
                          "Only completed-close observations; intrabar highs/lows and quote executability are not fills"])


def comparable(baseline, candidate, audits):
    errors = []
    for section, keys in (("configuration", ("contract", "strategy", "parameters", "selectedSymbols", "fromDate", "toDate", "dataMode", "specificationSha256", "initialCashPerStrategy", "notionalUSD")),
                          ("execution", ("model", "timestampKind", "slippageBpsPerSide", "halfSpreadProxyBpsPerSide", "feesUSD"))):
        for key in keys:
            if baseline.get(section, {}).get(key) != candidate.get(section, {}).get(key): errors.append(section + "." + key)
    if any(a is None for a in audits): errors.append("Missing audit: cannot verify same source files")
    else:
        hashes = [{s: x["sha256"] for s, x in a["symbols"].items()} for a in audits]
        if hashes[0] != hashes[1]: errors.append("Source symbol/file hashes differ")
    return dict(comparable=not errors, differences=errors)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--baseline", type=Path, required=True)
    p.add_argument("--candidate", type=Path)
    p.add_argument("--out", type=Path, default=Path("research/results/v4_comparison.json"))
    p.add_argument("--report", type=Path, default=Path("research/results/v4_comparison.md"))
    args = p.parse_args(argv)
    inputs = {x.resolve() for x in (args.baseline, args.candidate) if x}
    if args.out.resolve() in inputs or args.report.resolve() in inputs or args.out.resolve() == args.report.resolve():
        p.error("Outputs cannot overwrite inputs or each other")
    result, audit = load(args.baseline)
    report = dict(version=1, generatedAt=datetime.now(timezone.utc).isoformat(),
                  baselineSource=dict(path=str(args.baseline), sha256=input_hash(args.baseline),
                                      runId=result.get("runId"), sourceImplementation=result["configuration"].get("implementationVersion"),
                                      configuration=result["configuration"],execution=result.get("execution"),data=result.get("data"),
                                      inputFiles={s:{k:x.get(k) for k in ("path","sha256","rows","firstTimestamp","lastTimestamp","timestamp_kind","coverageCheck")} for s,x in (audit or {}).get("symbols",{}).items()}),
                  baseline=analyze(result), candidate=dict(status="NOT_RUN", reason="Actual historical data/EC2 execution still required"),
                  comparison=dict(comparable=False, reason="No candidate result supplied"))
    if args.candidate:
        other, other_audit = load(args.candidate)
        report["candidate"] = analyze(other)
        report["candidateSource"] = dict(path=str(args.candidate),sha256=input_hash(args.candidate),runId=other.get("runId"),researchVariant=other["configuration"].get("researchVariant"))
        report["comparison"] = comparable(result, other, (audit, other_audit))
    atomic_json(args.out, report)
    rows = ["# V4 baseline / candidate comparison", "", "Descriptive only. Archived results are not a new historical rerun.", "",
            "| Version / strategy | Trades | Win % | Expectancy R | PF R | MDD R | Sum trade return % |",
            "|---|---:|---:|---:|---:|---:|---:|"]
    def fmt(x): return "—" if x is None else f"{x:.4f}"
    for label in ("baseline", "candidate"):
        analysis = report[label]
        if "byStrategy" not in analysis: continue
        for kind, m in analysis["byStrategy"].items():
            rows.append(f"| {label} {kind} | {m['trades']} | {fmt(m['winRatePct'])} | {fmt(m['expectancyR'])} | {fmt(m['profitFactorR'])} | {fmt(m['maxDrawdownR'])} | {fmt(m['sumTradeReturnPct'])} |")
    rows += ["", "Baseline data mode: `" + report["baseline"]["dataMode"] + "`.",
             "Candidate: `" + (report["candidate"].get("status", "DESCRIPTIVE_RESULT")) + "`.",
             "No winner selected. See JSON for every fold, excursions, concentration, costs and compatibility checks.",
             "Cost scenarios are fixed-trade accounting counterfactuals, not ideal-execution or strategy backtests.",
             "Zero-loss PF is null with an explicit infinite flag; it does not establish an edge."]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(rows) + "\n")
    print(json.dumps(dict(out=str(args.out), report=str(args.report), candidate=report["candidate"].get("status", "DESCRIPTIVE_RESULT"))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
