import math
import statistics

from research.evolution.analyze_validation_v04 import _spearman, _pearson
from .experiment_registry import read_json


class BaselineCandidateError(ValueError):
    def __init__(self, code, *, stage=None, run_id=None, detail=None):
        self.code = code
        self.stage = stage
        self.run_id = run_id
        self.detail = detail
        parts = [code]
        if stage: parts.append("stage=" + str(stage))
        if run_id: parts.append("runId=" + str(run_id))
        if detail: parts.append(str(detail))
        super().__init__(": ".join(parts))

    def as_dict(self):
        return {"code": self.code, "stage": self.stage, "runId": self.run_id, "detail": self.detail}


def validation_metrics(summary, population, holdout):
    train = {r["bot_id"]: r for r in population}
    hold = {r["botId"]: r for r in holdout["bots"]}
    if len(train) != len(population) or len(hold) != len(holdout["bots"]) or len({r["botId"] for r in summary["bots"]}) != len(summary["bots"]):
        raise ValueError("Duplicate population/validation identity")
    valid = []
    for row in summary["bots"]:
        bot = train.get(row["botId"])
        previous = hold.get(row["botId"])
        if bot is None or previous is None or bot["genome_hash"] != row["genomeHash"] or previous["genomeHash"] != row["genomeHash"]:
            raise ValueError("Population/validation lineage mismatch")
        if row["invalidDays"] == 0 and row["validDays"] == summary["sessionsRequested"] and not summary["missingSessions"]:
            valid.append(row)
    values = [r["validationReturn"] for r in valid]
    trades = [r["trades"] for r in valid]
    costs = [r["transactionCost"] for r in valid]
    train_values = [train[r["botId"]]["lifetime_return"] for r in valid]
    hold_values = [hold[r["botId"]]["validationReturn"] for r in valid]
    if any(not math.isfinite(x) for seq in (values, trades, costs, train_values, hold_values) for x in seq):
        raise ValueError("Nonfinite diagnostics")
    return {"population": len(summary["bots"]), "validBots": len(valid),
            "invalidBots": len(summary["bots"]) - len(valid),
            "positiveBots": sum(v > 0 for v in values),
            "positiveShare": sum(v > 0 for v in values) / len(valid) if valid else None,
            "bestValidationReturn": max(values) if values else None,
            "meanValidationReturn": statistics.mean(values) if values else None,
            "medianValidationReturn": statistics.median(values) if values else None,
            "meanTradesPerBot": statistics.mean(trades) if trades else None,
            "meanTransactionCostPerBot": statistics.mean(costs) if costs else None,
            "totalTrades": sum(trades), "totalTransactionCost": sum(costs),
            "trainValidationSpearman": _spearman(train_values, values),
            "holdoutValidationSpearman": _spearman(hold_values, values),
            "tradesValidationPearson": _pearson(trades, values), "finalOosAllowed": False,
            "units": "additive daily net PnL / fixed daily capital; not compounded"}


def identify_baseline(runs):
    candidates = [(p, i) for p, i in runs if i.get("selectionMode") == "V07_INTERNAL_HOLDOUT_SHORTLIST"
                  and i.get("evaluationMode") == "FROZEN_VALIDATION"
                  and str(i.get("fromDate", "")).startswith("2025")
                  and str(i.get("toDate", "")).startswith("2025") and not i.get("smoke")]
    if not candidates:
        raise BaselineCandidateError("BASELINE_V07_DEVELOPMENT_ARTIFACT_REQUIRED")
    development, info = sorted(candidates, key=lambda x: (x[1].get("finishedAt", ""), x[1]["runId"]))[-1]
    by_id = {}
    for p, i in runs:
        if i["runId"] in by_id and by_id[i["runId"]][1] != i:
            raise ValueError("Ambiguous run identity")
        by_id[i["runId"]] = (p, i)
    try:
        shortlist, short_info = by_id[info["sourceRunId"]]
        holdout, hold_info = by_id[short_info["holdoutRunId"]]
        training, train_info = by_id[hold_info["sourceRunId"]]
    except KeyError as exc:
        missing = str(exc.args[0]) if exc.args else "unknown"
        raise BaselineCandidateError("V07_LINEAGE_ARTIFACTS_REQUIRED",
                                     stage="lineage", run_id=info.get("runId"),
                                     detail="missing=" + missing) from None
    from .safety_gate import period_gate
    for purpose, obj in (("training", train_info), ("internal_holdout", hold_info),
                         ("development_validation", info)):
        run_id = obj.get("runId")
        try:
            period_gate(obj["fromDate"], obj["toDate"], purpose)
        except (KeyError, ValueError) as exc:
            raise BaselineCandidateError("BASELINE_PERIOD_INVALID", stage=purpose,
                                         run_id=run_id, detail=str(exc)) from None
        if obj.get("researchOnly") is not True or obj.get("timestampKind") != "end":
            raise BaselineCandidateError("BASELINE_RESEARCH_TIMESTAMP_SEMANTICS_INVALID",
                                         stage=purpose, run_id=run_id)
        missing = obj.get("missingSessions") or []
        if missing or obj.get("resultsValid") is False:
            detail = "missingSessions=" + str(len(missing)) + ", resultsValid=" + str(obj.get("resultsValid"))
            raise BaselineCandidateError("BASELINE_INCOMPLETE_OR_INVALID",
                                         stage=purpose, run_id=run_id, detail=detail)
    if train_info.get("config") != short_info.get("config"):
        raise BaselineCandidateError("BASELINE_SHORTLIST_CONFIG_MISMATCH",
                                     stage="shortlist", run_id=short_info.get("runId"))
    try:
        baseline_metrics = validation_metrics(read_json(development / "validation_summary.json"),
                                             read_json(training / "final_population.json"),
                                             read_json(holdout / "validation_summary.json"))
    except (KeyError, TypeError, ValueError) as exc:
        raise BaselineCandidateError("BASELINE_METRICS_INVALID",
                                     stage="metrics", run_id=info.get("runId"), detail=str(exc)) from None
    return {"training": training, "holdout": holdout, "shortlist": shortlist, "development": development,
            "metrics": baseline_metrics, "info": info}


def failure_analysis(rows):
    return {"versions": rows, "baselinePolicy": "V07_PROTOCOL_NOT_BEST_2025_BOT",
            "observations": [{"runId": r.get("runId"), "meanValidation": r.get("meanValidation"),
                              "tradesValidationCorrelation": r.get("tradesValidationCorrelation"),
                              "verdict": r.get("verdict")} for r in rows],
            "limitations": ["2025 repeatedly observed: not untouched OOS", "No causal edge claim from correlations"],
            "nextHypothesis": "Quarterly 2024 shortlist stability; no parameter search"}
