"""Artifact-only Evolution summaries. Missing evidence stays null; never trains."""
import csv
import io
import json
import re
import statistics


def first(objects, *names):
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        for name in names:
            value = obj.get(name)
            if value is not None:
                return value
    return None


def layers(*objects):
    out = []
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        out.append(obj)
        for key in ("overall", "summary", "metrics", "config", "args", "diagnostics", "generalization", "correlations"):
            if isinstance(obj.get(key), dict):
                out.append(obj[key])
    return out


def period(objects, name):
    for obj in objects:
        for key in (name+"Period", name+"_period", name):
            value = obj.get(key)
            if isinstance(value, (list, tuple)) and len(value) == 2:
                return list(value)
            if isinstance(value, dict):
                lo = first([value], "from", "start", "fromDate", "startDate")
                hi = first([value], "to", "end", "toDate", "endDate")
                if lo is not None and hi is not None:
                    return [lo, hi]
        lo = first([obj], name+"From", name+"Start", name+"FromDate", name+"_from", name+"_start")
        hi = first([obj], name+"To", name+"End", name+"ToDate", name+"_to", name+"_end")
        if lo is not None and hi is not None:
            return [lo, hi]
    return None


def summarize_run(handle, files, documents):
    run = documents.get("run.json", {})
    status = documents.get("status.json", {})
    validation = documents.get("validation_summary.json", {})
    diag = documents.get("generalization_diagnostics.json", {})
    campaign = documents.get("campaign.json", {})
    config = documents.get("config.json", documents.get("default_config.json", {}))
    base = layers(run, campaign, status, config)
    # Frozen status contains last-day returns, not period-wide validation returns.
    # Aggregate only already stored valid bot records; no price/evaluation access.
    aggregate = {}
    bots = validation.get("bots") if isinstance(validation, dict) else None
    if isinstance(bots, list):
        valid_bots = [b for b in bots if isinstance(b, dict) and not b.get("invalidDays") and isinstance(b.get("validationReturn"), (int, float))]
        returns = [b["validationReturn"] for b in valid_bots]
        if returns:
            aggregate = dict(bestValidationReturn=max(returns), meanValidationReturn=statistics.mean(returns), medianValidationReturn=statistics.median(returns),
                             positiveBots=sum(x > 0 for x in returns), negativeBots=sum(x < 0 for x in returns),
                             totalTrades=sum(b.get("trades", 0) for b in valid_bots), totalTransactionCost=sum(b.get("transactionCost", 0) for b in valid_bots),
                             meanTradesPerBot=statistics.mean(b.get("trades", 0) for b in valid_bots))
    frozen = first(base, "evaluationMode") == "FROZEN_VALIDATION"
    vals = layers(diag, validation, aggregate, campaign, {} if frozen else status)
    version = first(base, "evolutionVersion", "researchVersion") or first(base, "version")
    fitness = first(base, "fitnessMode", "fitness_mode", "baseFitness")
    selection = first(base, "selectionMode", "selection_mode")
    stage = first(base, "stage", "evaluationLabel", "evaluationMode", "split", "mode")
    evaluation_period = [first(base, "fromDate", "from_date"), first(base, "toDate", "to_date")]
    if any(x is None for x in evaluation_period):
        evaluation_period = None
    train = period(base, "sourceTrain") or period(base, "train")
    valid = period(layers(diag, validation), "validation") or (evaluation_period if frozen else None) or period(base, "developmentValidation") or period(base, "validation")
    shortlist = documents.get("shortlist.json")
    count = first(layers(shortlist, run, status, diag), "shortlistCount", "shortlist_count", "selectedCount")
    if count is None and isinstance(shortlist, list):
        count = len(shortlist)
    if count is None and isinstance(shortlist, dict):
        for key in ("selectedBots", "bots", "shortlist", "selected", "botIds", "genomes"):
            if isinstance(shortlist.get(key), list):
                count = len(shortlist[key]); break
    population = first(layers(status, run, campaign, config), "evaluatedPopulation", "population", "population_size", "populationSize")
    if first(base, "evaluationMode") == "SHORTLIST_ARTIFACT" and count is not None: population = count
    if population is None and isinstance(documents.get("final_population.json"), list):
        population = len(documents["final_population.json"])
    row = dict(id=handle, runId=first(base, "runId", "run_id", "campaignId") or handle,
               version=version, fitnessMode=fitness, selectionMode=selection, stage=stage,
               trainPeriod=train, validationPeriod=valid, evaluationPeriod=evaluation_period,
               internalHoldoutPeriod=period(base, "internalHoldout"), developmentValidationPeriod=period(base, "developmentValidation"),
               generation=first(layers(status, run, campaign), "generation", "completedTrainGenerations"), population=population,
               shortlistCount=count,
               bestValidation=first(vals, "bestValidationReturn", "bestValidation", "bestReturn"),
               meanValidation=first(vals, "meanValidationReturn", "meanValidation", "meanReturn"),
               medianValidation=first(vals, "medianValidationReturn", "medianValidation", "medianReturn"),
               positiveBots=first(vals, "positiveBots", "positive_bots", "developmentPositiveBots"),
               negativeBots=first(vals, "negativeBots", "negative_bots"),
               tradeCount=first(vals, "validationTotalTrades", "totalTrades", "tradeCount", "trades"),
               tradeCost=first(vals, "totalTransactionCost", "totalTradeCost", "transactionCost", "tradeCost"),
               avgTradesPerBot=first(vals, "meanTradesPerBot", "avgTradesPerBot"),
               avgTradeCostPerBot=first(vals, "meanTransactionCostPerBot", "avgTransactionCostPerBot"),
               trainValidationCorrelation=first(vals, "trainValidationSpearman", "trainValidationCorrelation"),
               tradesValidationCorrelation=first(vals, "tradesValidationPearson", "tradesValidationCorrelation"),
               verdict=first(vals, "verdict"),
               finalOosAllowed=first(vals+layers(status), "finalOosAllowed", "finalOOSAllowed", "final_oos_allowed"),
               evaluationMode=first(base, "evaluationMode"), dataMode=first(base, "dataMode"), performanceClaim=first(base, "performanceClaim"),
               returnUnits=first(vals, "returnUnits", "return_units") or "AS_REPORTED_NO_UNIT_ASSUMPTION",
               files=len(files), estimatedBytes=sum(f["size"] for f in files),
               updatedAt=max((f["modifiedAt"] for f in files), default=None))
    # Labels come only from metadata, not directory names or remembered performance.
    text = " ".join(str(v) for v in (version, fitness, selection) if v is not None).lower()
    v07 = bool(re.search(r"(?:v0[._-]?7|0\.7)(?=$|[^a-z0-9])", text))
    if v07 and any(x in text for x in ("holdout", "shortlist")):
        row["label"] = "V0.7 Internal Holdout Shortlist"
    elif re.search(r"(?:v0[._-]?5|0\.5)(?=$|[^a-z0-9])", text) and "structural" in text:
        row["label"] = "V0.5 Structural"
    elif re.search(r"(?:v0[._-]?6|0\.6)(?=$|[^a-z0-9])", text) and "stability" in text:
        row["label"] = "V0.6 Stability"
    else:
        row["label"] = " · ".join(str(v) for v in (version, fitness, selection) if v is not None) or "Metadata version unavailable"
    row["isV07"] = v07
    row["validationMetricsBasis"] = "STORED_VALID_BOT_PERIOD_RETURNS" if aggregate else "ARTIFACT_DECLARED_NO_AGGREGATE"
    row["metadataEvidence"] = [name for name in documents if documents[name] is not None]
    return row


def summary_csv(rows):
    stream = io.StringIO(newline="")
    fields = list(dict.fromkeys(k for row in rows for k in row)) if rows else ["runId", "version", "fitnessMode", "trainPeriod", "validationPeriod", "verdict"]
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        values = {k: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v for k, v in row.items()}
        # Spreadsheet formula injection is independent of filesystem traversal.
        for k, v in values.items():
            if isinstance(v, str) and v.startswith(("=", "+", "-", "@", "\t", "\r")):
                values[k] = "'"+v
        writer.writerow(values)
    return stream.getvalue().encode("utf-8")
