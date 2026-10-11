from .experiment_registry import write_json, read_json


def create_report(folder, experiment, comparison=None, diagnostics=None, failure=None):
    value = {"researchOnly": True, "hypothesis": experiment.get("hypothesis"),
             "baseline": experiment.get("parentVersion"), "candidate": experiment.get("candidateVersion"),
             "changes": experiment.get("changedVariables"), "state": experiment["state"],
             "training": "Reused immutable V07 population", "internalHoldout": "2024 only; quarterly selection",
             "developmentValidation": "2025; repeatedly observed, not final OOS",
             "generalization": diagnostics, "comparison": comparison, "failureReasons": [failure] if failure else [],
             "overfitRisk": experiment.get("riskOfOverfit"), "finalOosGate": "CLOSED",
             "nextRecommendedHypothesis": "Human review of structural failure diagnostics; no automatic 2025 numeric fitting"}
    write_json(folder / "report.json", value)
    safe = read_json(folder / "report.json")
    text = "# Autonomous AI Research\n\n" + "\n\n".join(f"**{k}**: {v}" for k, v in safe.items()) + "\n"
    (folder / "report.md").write_text(text, encoding="utf-8")
    return value
