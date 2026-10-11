from .hypothesis import hypothesis
from .experiment_registry import read_json
from .safety_gate import plan_gate


def make_plan(baseline, config):
    plan = dict(hypothesis(), parentVersion="v07", candidateVersion="v08", researchOnly=True,
                allowFinalOos=False, selectionDataYear=2024,
                selectionRule={"annualReturnAbove": 0, "positiveQuarterCountAtLeast": 3, "medianQuarterReturnAbove": 0},
                minCandidates=config.min_candidates, maxCandidates=config.max_candidates,
                periods={"training": ["2022-01-03", "2023-12-29"],
                         "internal_holdout": ["2024-01-02", "2024-12-31"],
                         "development_validation": ["2025-01-02", "2025-12-31"]},
                trainingMode="REUSE_VERIFIED_V07_FIXED_POPULATION",
                holdoutMode="REUSE_VERIFIED_V07_DAILY_HOLDOUT",
                reasonForReuse="Isolate shortlist_selection; new stochastic/adaptive training would change another variable",
                expectedChangedFiles=["research/autonomous/candidates/v08_selection.json"],
                requiredDailyArtifacts="2024 validation_*/metrics.json; fail closed if missing",
                finalOosGate="CLOSED", nextIteration="STOP_HYPOTHESIS_REQUIRED")
    for name in ("training", "holdout", "development"):
        info = read_json(baseline[name] / "run.json")
        key = {"training": "training", "holdout": "internal_holdout", "development": "development_validation"}[name]
        if [info["fromDate"], info["toDate"]] != plan["periods"][key]:
            raise ValueError("BASELINE_PERIOD_DIFFERS_FROM_FIXED_V07_PROTOCOL")
    plan_gate(plan)
    return plan
