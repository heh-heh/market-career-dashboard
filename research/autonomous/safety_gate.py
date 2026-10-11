"""Fail-closed research boundaries, independent of strategy fitness."""
from datetime import date
import math
import shutil
import time
import re
from pathlib import Path


def data_path_gate(path):
    if any(re.fullmatch(r"(?:202[6-9]|20[3-9]\d)(?:-\d{2}(?:-\d{2})?)?", part) for part in Path(path).parts):
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED")


def period_gate(first, last, purpose):
    a, b = date.fromisoformat(first), date.fromisoformat(last)
    if a.year >= 2026 or b.year >= 2026:
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED")
    limits = {"training": (2022, 2023), "internal_holdout": (2024, 2024),
              "development_validation": (2025, 2025)}
    lo, hi = limits[purpose]
    if b < a or not lo <= a.year <= b.year <= hi:
        raise ValueError("2025_LEAKAGE_BLOCKED: period/purpose mismatch")


def plan_gate(plan):
    if plan.get("researchOnly") is not True or plan.get("allowFinalOos") is not False:
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED")
    if plan.get("changedVariables") != ["shortlist_selection"]:
        raise ValueError("ONE_HYPOTHESIS_REQUIRED; no numeric fitting")
    if plan.get("candidateVersion") != "v08" or plan.get("selectionDataYear") != 2024:
        raise ValueError("2025_LEAKAGE_BLOCKED")
    if plan.get("selectionRule") != {"annualReturnAbove": 0, "positiveQuarterCountAtLeast": 3,
                                    "medianQuarterReturnAbove": 0}:
        raise ValueError("FROZEN_SELECTION_RULE_CHANGED")
    for purpose, dates in plan["periods"].items():
        period_gate(*dates, purpose)


def artifact_period_gate(info):
    # Config.final_oos declarations are metadata, not permission to evaluate it.
    first, last = info.get("fromDate"), info.get("toDate")
    if first and last and (str(first)[:4] >= "2026" or str(last)[:4] >= "2026"):
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED")
    if info.get("date") and str(info["date"])[:4] >= "2026":
        raise ValueError("FINAL_OOS_ACCESS_BLOCKED")


def resource_gate(config, snapshot=None, disk=None, expected_bytes=0):
    from research.evolution.config import Config
    from research.evolution.resource import sample_resources, decide_population
    if snapshot is None:
        sample_resources()  # Prime psutil's nonblocking CPU counter.
        time.sleep(.1)
        snapshot = sample_resources()
    decision = decide_population(20, snapshot, Config(), enabled=True)
    disk = shutil.disk_usage(config.registry) if disk is None else disk
    reserve = max(config.min_free_bytes, int(disk.total * config.disk_reserve_fraction))
    if decision["resourcePressure"] != "NORMAL" or disk.free < reserve + expected_bytes:
        raise ValueError("PAUSE_NEW_EXPERIMENT")
    return {"resources": snapshot, "diskFreeBytes": disk.free, "diskReserveBytes": reserve}


def promotion_gate(baseline, candidate):
    fields = ("meanValidationReturn", "medianValidationReturn", "positiveShare", "bestValidationReturn",
              "meanTradesPerBot", "meanTransactionCostPerBot", "trainValidationSpearman",
              "holdoutValidationSpearman", "invalidBots")
    compared = {key: {"baseline": baseline.get(key), "candidate": candidate.get(key)} for key in fields}
    complete = all(isinstance(x, (int, float)) and math.isfinite(x)
                   for values in compared.values() for x in values.values())
    decision = "INCONCLUSIVE"
    reasons = []
    if (candidate.get("validBots") or 0) < 2:
        reasons.append("INSUFFICIENT_VALID_BOTS_FOR_PROMOTION")
    elif not complete:
        reasons.append("MISSING_COMPARABLE_METRICS")
    elif candidate["invalidBots"] > baseline["invalidBots"]:
        decision = "REJECT_CANDIDATE"; reasons.append("MORE_INVALID_BOTS")
    elif candidate["meanValidationReturn"] < 0:
        decision = "KEEP_BASELINE"; reasons.append("NEGATIVE_DEVELOPMENT_MEAN")
    elif all(candidate[k] >= baseline[k] for k in fields if k not in
             ("meanTradesPerBot", "meanTransactionCostPerBot", "invalidBots")) and all(
            candidate[k] <= baseline[k] for k in ("meanTradesPerBot", "meanTransactionCostPerBot", "invalidBots")):
        decision = "PROMOTE_CANDIDATE"; reasons.append("REVIEW_RECOMMENDATION_ONLY")
    elif candidate["meanValidationReturn"] <= baseline["meanValidationReturn"]:
        decision = "REJECT_CANDIDATE"; reasons.append("NO_MEAN_IMPROVEMENT")
    else:
        reasons.append("METRIC_TRADEOFF_REQUIRES_REVIEW")
    return {"decision": decision, "metrics": compared, "reasons": reasons,
            "baselineChanged": False, "finalOosGate": "CLOSED",
            "finalOosAllowed": False, "developmentValidationNotIndependentOos": True}
