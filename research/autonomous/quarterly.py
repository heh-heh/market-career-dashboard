"""V0.8: immutable V0.7 population, 2024-only quarterly shortlist selection."""
import math
from pathlib import Path
import statistics

from research.backtest_v4_strategies import exchange_sessions
from .experiment_registry import read_json, write_json
from .safety_gate import period_gate


def quarterly_metrics(training, holdout):
    train = read_json(Path(training) / "run.json")
    info = read_json(Path(holdout) / "run.json")
    summary = read_json(Path(holdout) / "validation_summary.json")
    period_gate(train["fromDate"], train["toDate"], "training")
    period_gate(info["fromDate"], info["toDate"], "internal_holdout")
    if not {"train", "validation", "initial_capital"}.issubset(train.get("config", {})):
        raise ValueError("TRAIN_CONFIG_REQUIRED")
    if info.get("phase") != "completed" or info.get("sourceRunId") != train["runId"] or info.get("timestampKind") != "end":
        raise ValueError("INVALID_HOLDOUT_LINEAGE_OR_STATE")
    if info.get("sourcePopulationVerified") is not True or info.get("inputHashesVerified") is not True:
        raise ValueError("UNVERIFIED_HOLDOUT")
    expected = set(exchange_sessions(info["fromDate"], info["toDate"]))
    if summary["missingSessions"] or summary["sessionsProcessed"] != len(expected) or summary["sessionsRequested"] != len(expected):
        raise ValueError("INCOMPLETE_INTERNAL_HOLDOUT")
    population = read_json(Path(training) / "final_population.json")
    by_id = {r["bot_id"]: r for r in population}
    annual = {r["botId"]: r for r in summary["bots"]}
    if len(by_id) != len(population) or len(annual) != len(summary["bots"]) or set(annual) != set(by_id):
        raise ValueError("Holdout population differs from fixed training population")
    capital = float(train["config"]["initial_capital"])
    totals = {bot: {q: {"pnl": 0., "trades": 0, "cost": 0., "days": 0, "invalid": 0} for q in range(1, 5)} for bot in by_id}
    seen = set()
    # At most one day of metrics in memory; never read trade prices or 2025 outcomes here.
    for path in sorted(Path(holdout).glob("validation_*/metrics.json")):
        data = read_json(path)
        rows = data["bots"]
        dates = {r["date"] for r in rows}
        if len(dates) != 1 or {r["botId"] for r in rows} != set(by_id) or len(rows) != len(by_id):
            raise ValueError("Missing/duplicate bot or date in daily holdout metrics")
        day = dates.pop()
        period_gate(day, day, "internal_holdout")
        if day not in expected or day in seen:
            raise ValueError("Unexpected/duplicate holdout date")
        seen.add(day)
        quarter = (int(day[5:7]) - 1) // 3 + 1
        for r in rows:
            bucket = totals[r["botId"]][quarter]
            bucket["days"] += 1
            bucket["trades"] += r["trades"]
            bucket["cost"] += r["transactionCost"]
            if r["evaluationValid"] is True:
                if not math.isfinite(r["dailyPnl"]):
                    raise ValueError("Invalid daily PnL")
                bucket["pnl"] += r["dailyPnl"]
            else:
                bucket["invalid"] += 1
    if seen != expected:
        raise ValueError("DAILY_HOLDOUT_ARTIFACTS_REQUIRED: summary export alone is insufficient")
    result = []
    for bot, quarters in totals.items():
        r = annual[bot]
        if r["genomeHash"] != by_id[bot]["genome_hash"]:
            raise ValueError("Genome hash mismatch")
        returns = [quarters[q]["pnl"] / capital for q in range(1, 5)]
        trades = [quarters[q]["trades"] for q in range(1, 5)]
        costs = [quarters[q]["cost"] for q in range(1, 5)]
        invalid = sum(x["invalid"] for x in quarters.values())
        if not math.isclose(sum(returns), r["validationReturn"], abs_tol=1e-8, rel_tol=1e-8) or sum(trades) != r["trades"] or not math.isclose(sum(costs), r["transactionCost"], abs_tol=1e-8, rel_tol=1e-8) or invalid != r["invalidDays"]:
            raise ValueError("Quarter/annual ledger mismatch")
        valid = invalid == 0 and r["validDays"] == len(expected) and all(x["days"] > 0 for x in quarters.values())
        result.append({"botId": bot, "genomeHash": r["genomeHash"], "mechanicallyValid": valid,
                       "annualReturn": r["validationReturn"], "quarterReturn": returns,
                       "positiveQuarterCount": sum(x > 0 for x in returns),
                       "medianQuarterReturn": statistics.median(returns), "worstQuarterReturn": min(returns),
                       "tradesPerQuarter": trades, "costPerQuarter": costs})
    return result


def select_candidates(rows, max_count=40, min_count=1):
    selected = [r for r in rows if r["mechanicallyValid"] and r["annualReturn"] > 0
                and r["positiveQuarterCount"] >= 3 and r["medianQuarterReturn"] > 0]
    # Stable ordering chosen before looking at 2025: median, worst, annual, ID.
    selected.sort(key=lambda r: (-r["medianQuarterReturn"], -r["worstQuarterReturn"], -r["annualReturn"], r["botId"]))
    selected = selected[:max_count]
    if len(selected) < min_count:
        raise ValueError("INSUFFICIENT_CANDIDATES")
    return selected


def build_shortlist_v08(training, holdout, out, max_count=40, min_count=1):
    rows = quarterly_metrics(training, holdout)
    chosen = select_candidates(rows, max_count, min_count)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    source = read_json(Path(training) / "run.json")
    population = read_json(Path(training) / "final_population.json")
    by_id = {r["bot_id"]: r for r in population}
    write_json(out / "final_population.json", [by_id[r["botId"]] for r in chosen])
    write_json(out / "shortlist.json", {"selectionBasis": "V08_QUARTERLY_INTERNAL_HOLDOUT_STABILITY",
               "maxCount": max_count, "minCount": min_count, "selectedBots": chosen, "allQuarterMetrics": rows,
               "selectionDataYear": 2024, "developmentDataUsed": False})
    info = dict(source)
    info.update(runId=out.name, phase="completed", version="evolution-v0.8",
                evaluationMode="SHORTLIST_ARTIFACT", selectionMode="V08_QUARTERLY_INTERNAL_HOLDOUT_STABILITY",
                shortlistCount=len(chosen), holdoutRunId=read_json(Path(holdout) / "run.json")["runId"],
                sourceTrainingRunId=source["runId"], sourceTrainingRunDir=str(training), finalOosAllowed=False)
    write_json(out / "run.json", info)
    return out
