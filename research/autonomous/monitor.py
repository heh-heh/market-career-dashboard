"""Small public projection; never expose filesystem paths, credentials or logs."""
import hashlib
from pathlib import Path
import re

from .experiment_registry import read_json

PUBLIC = {"iteration", "candidateVersion", "parentVersion", "state", "hypothesis", "dryRun",
          "trainingProgress", "holdoutProgress", "validationProgress", "positiveBots",
          "validationMean", "validationMedian", "avgTrades", "decision", "finalOosGate",
          "startedAt", "finishedAt", "updatedAt", "selectedCandidates", "researchOnly", "failureCode"}


def iteration_id(name):
    return hashlib.sha256(("autonomous:" + name).encode()).hexdigest()[:24]


def status(root):
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("Unsafe autonomous registry")
    recent = []
    if root.exists():
        for p in sorted(root.glob("iteration-*/experiment.json"), reverse=True):
            if not re.fullmatch(r"iteration-\d{4,}", p.parent.name): continue
            try:
                doc = read_json(p)
                row = {k: v for k, v in doc.items() if k in PUBLIC}
                if doc.get("state") not in ("completed", "failed", "waiting") and doc.get("workerIdentity"):
                    from .runner import process_identity, active_stage
                    identity = doc["workerIdentity"]
                    if process_identity(identity["pid"]) != identity and not active_stage(p.parent):
                        row.update(state="failed", failureCode="WORKER_INTERRUPTED_RESUME_REQUIRED")
                row["id"] = iteration_id(p.parent.name)
                recent.append(row)
            except (ValueError, OSError):
                continue
            if len(recent) >= 5: break
    current = recent[0] if recent else {"state": "waiting", "researchOnly": True, "finalOosGate": "CLOSED"}
    return {"current": current, "recent": recent, "uiStatus": "UI_UPDATE_READY"}
