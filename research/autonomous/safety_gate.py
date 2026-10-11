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


class ResourcePauseError(ValueError):
    def __init__(self, facts):
        self.facts=dict(facts or {})
        reason=self.facts.get("reason") or "RESOURCE_PRESSURE"
        msg=(
            "PAUSE_NEW_EXPERIMENT: reason="+str(reason)+
            " pressure="+str(self.facts.get("resourcePressure"))+
            " cpuPct="+str(self.facts.get("cpuPct"))+
            " memoryPct="+str(self.facts.get("memoryPct"))+
            " swapPct="+str(self.facts.get("swapPct"))+
            " diskFreeGb="+str(self.facts.get("diskFreeGb"))+
            " requiredFreeGb="+str(self.facts.get("requiredFreeGb"))
        )
        super().__init__(msg)


def resource_gate(config, snapshot=None, disk=None, expected_bytes=0, *, soft_wait_seconds=30.0,
                  sample_interval_seconds=2.0):
    from research.evolution.config import Config
    from research.evolution.resource import sample_resources, decide_population

    explicit_snapshot = snapshot is not None
    live_disk = disk is None

    def evaluate(current_snapshot, current_disk):
        decision = decide_population(20, current_snapshot, Config(), enabled=True)
        reserve = max(config.min_free_bytes, int(current_disk.total * config.disk_reserve_fraction))
        required = reserve + expected_bytes
        facts = {
            "resourceDecision": decision.get("resourceDecision"),
            "resourcePressure": decision.get("resourcePressure"),
            "cpuPct": round(float(current_snapshot.get("cpuPct") or 0.0), 1),
            "memoryPct": round(float(current_snapshot.get("memoryPct") or 0.0), 1),
            "memoryAvailableGb": round(float(current_snapshot.get("memoryAvailableGb") or 0.0), 2),
            "swapPct": round(float(current_snapshot.get("swapPct") or 0.0), 1),
            "swapUsedGb": round(float(current_snapshot.get("swapUsedGb") or 0.0), 2),
            "swapTotalGb": round(float(current_snapshot.get("swapTotalGb") or 0.0), 2),
            "processRssMb": round(float(current_snapshot.get("processRssMb") or 0.0), 1),
            "diskFreeBytes": int(current_disk.free),
            "diskReserveBytes": int(reserve),
            "expectedBytes": int(expected_bytes),
            "diskFreeGb": round(current_disk.free / (1024**3), 2),
            "requiredFreeGb": round(required / (1024**3), 2),
        }
        if current_disk.free < required:
            facts["reason"] = "DISK_RESERVE"
        elif decision.get("resourcePressure") == "HARD":
            facts["reason"] = "HARD_RESOURCE_PRESSURE"
        elif decision.get("resourcePressure") != "NORMAL":
            facts["reason"] = "SOFT_RESOURCE_PRESSURE"
        else:
            facts["reason"] = "NORMAL"
        return facts

    if snapshot is None:
        sample_resources()  # Prime psutil's nonblocking CPU counter.
        time.sleep(.1)
        snapshot = sample_resources()
    if disk is None:
        disk = shutil.disk_usage(config.registry)

    facts = evaluate(snapshot, disk)
    if facts["reason"] in {"DISK_RESERVE", "HARD_RESOURCE_PRESSURE"}:
        raise ResourcePauseError(facts)
    if facts["reason"] == "NORMAL":
        return {"resources": snapshot, **facts}

    # A transient CPU spike or soft RAM/swap pressure must not fail an entire dry-run immediately.
    # Explicit snapshots are test/diagnostic inputs and remain fail-fast/deterministic.
    if explicit_snapshot or soft_wait_seconds <= 0:
        raise ResourcePauseError(facts)

    deadline = time.monotonic() + max(0.0, float(soft_wait_seconds))
    samples = 1
    while time.monotonic() < deadline:
        time.sleep(max(0.1, min(float(sample_interval_seconds), 5.0)))
        snapshot = sample_resources()
        if live_disk:
            disk = shutil.disk_usage(config.registry)
        facts = evaluate(snapshot, disk)
        samples += 1
        facts["samples"] = samples
        if facts["reason"] == "NORMAL":
            return {"resources": snapshot, **facts}
        if facts["reason"] in {"DISK_RESERVE", "HARD_RESOURCE_PRESSURE"}:
            raise ResourcePauseError(facts)
    facts["reason"] = "SOFT_RESOURCE_PRESSURE_TIMEOUT"
    raise ResourcePauseError(facts)


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
