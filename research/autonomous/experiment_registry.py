import contextlib
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import uuid

from research_export import sanitize, SafeRoot


def utc():
    return datetime.now(timezone.utc).isoformat()


def write_json(path, value):
    path = Path(path)
    if path.is_symlink() or path.parent.resolve() != path.parent.absolute():
        raise ValueError("Unsafe output path")
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    safe = sanitize(value)
    credential = os.environ.get("RESEARCH_EXPORT_TOKEN")
    # Defense in depth for a credential accidentally echoed under another key.
    def redact(x):
        if isinstance(x, dict): return {k: redact(v) for k, v in x.items()}
        if isinstance(x, list): return [redact(v) for v in x]
        if credential and isinstance(x, str): return x.replace(credential, "[REDACTED]")
        return x
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(redact(safe), f, ensure_ascii=False, allow_nan=False, indent=2)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def read_json(path):
    path = Path(path).absolute()
    return SafeRoot(path.parent).json(path.name)


@contextlib.contextmanager
def exclusive_lock(path, inherited_fd=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.resolve() != path.parent.absolute():
        raise ValueError("Unsafe lock path")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600) if inherited_fd is None else inherited_fd
    try:
        expected = path.stat(follow_symlinks=False)
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino) or path.is_symlink():
            raise ValueError("Invalid inherited research lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("RESEARCH_LOOP_ALREADY_LOCKED") from None
        yield fd
    finally:
        os.close(fd)


class Registry:
    def __init__(self, root):
        self.root = Path(root).absolute()
        if self.root.resolve() != self.root:
            raise ValueError("Unsafe registry root")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = self.root / "owner.json"
        if marker.exists():
            if read_json(marker) != {"owner": "autonomous-research-v0.1"}:
                raise ValueError("Registry belongs to another application")
        else:
            if any(p.name != "cache" for p in self.root.iterdir()):
                raise ValueError("REGISTRY_UNOWNED: refusing to overwrite existing research artifacts")
            write_json(marker, {"owner": "autonomous-research-v0.1"})

    def iterations(self):
        return sorted(p for p in self.root.glob("iteration-*") if
                      re.fullmatch(r"iteration-\d{4,}", p.name) and p.is_dir() and not p.is_symlink())

    def create(self):
        number = max((int(p.name.split("-")[1]) for p in self.iterations()), default=0) + 1
        folder = self.root / f"iteration-{number:04d}"
        folder.mkdir(mode=0o700)
        write_json(folder / "experiment.json", {"iteration": number, "researchOnly": True,
                   "state": "queued", "startedAt": utc(), "finalOosGate": "CLOSED"})
        return folder

    def update(self, folder, state, **fields):
        if folder.parent != self.root or folder not in self.iterations():
            raise ValueError("Iteration outside registry")
        value = read_json(folder / "experiment.json")
        if value.get("state") == "completed" and value.get("dryRun") is True:
            raise ValueError("COMPLETED_DRY_RUN_IMMUTABLE: create a separate actual iteration")
        value.update(fields, state=state, updatedAt=utc(), researchOnly=True, finalOosGate="CLOSED")
        write_json(folder / "experiment.json", value)
        write_json(self.root / "status.json", value)
        event={
            "event":"autonomous_state",
            "iteration":value.get("iteration"),
            "state":state,
            "updatedAt":value.get("updatedAt"),
            "failureCode":value.get("failureCode"),
            "trainingProgress":value.get("trainingProgress"),
            "holdoutProgress":value.get("holdoutProgress"),
            "validationProgress":value.get("validationProgress"),
            "selectedCandidates":value.get("selectedCandidates"),
        }
        print(json.dumps(event,ensure_ascii=False,allow_nan=False,sort_keys=True),flush=True)
        return value
