"""Persistent, bounded and sanitized PUBLIC collection activity events.

This is not a raw application/system log. It never stores credentials, URLs,
HTTP bodies, exception messages, filesystem paths or research data rows.
The standalone worker and scheduler share a small append-only JSONL file.
"""
import fcntl
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

KINDS = frozenset({
    "MODE_CHANGED", "WORKER_STARTED", "WORKER_EXITED", "WORKER_ERROR",
    "CANDLE_PAGE", "CANDLE_ERROR", "SCHEDULER_STOPPED",
})
FIELDS = ("mode", "worker", "symbol", "code", "page", "added", "stored", "exitCode")
CURRENT = "_collection_events.jsonl"
PREVIOUS = "_collection_events.jsonl.1"
LOCK = "_collection_events.lock"
MAX_BYTES = 1024 * 1024  # two retained segments, never an unbounded public log
TAIL_BYTES = 128 * 1024


def _safe_event(kind, at=None, **values):
    if kind not in KINDS:
        raise ValueError("UNKNOWN_COLLECTION_EVENT")
    event = {"kind": kind, "at": at or datetime.now(timezone.utc).isoformat()}
    for field in ("mode", "worker", "symbol", "code"):
        v = values.get(field)
        if isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9_]{1,48}", v):
            if field != "symbol" or re.fullmatch(r"[A-Z0-9]{1,8}", v):
                event[field] = v
    for field in ("page", "added", "stored", "exitCode"):
        v = values.get(field)
        if type(v) is int and -2147483648 <= v <= 2147483647:
            event[field] = v
    return event


def emit(root, kind, **fields):
    """Never let observability failure stop a live market-data worker."""
    if root is None:
        return False
    try:
        directory = Path(root)
        if not directory.is_dir() or directory.is_symlink():
            return False
        lock = directory / LOCK
        current = directory / CURRENT
        previous = directory / PREVIOUS
        if any(p.is_symlink() for p in (lock, current, previous)):
            return False
        payload = (json.dumps(_safe_event(kind, **fields), separators=(",", ":"),
                              ensure_ascii=False) + "\n").encode("utf-8")
        lock_fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o640)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
            if current.is_symlink() or previous.is_symlink():
                return False
            if current.exists() and current.stat().st_size + len(payload) > MAX_BYTES:
                os.replace(current, previous)
            out_fd = os.open(current, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o640)
            try:
                os.write(out_fd, payload)
            finally:
                os.close(out_fd)
        finally:
            os.close(lock_fd)
        return True
    except (OSError, ValueError, TypeError):
        return False


def recent(root, limit=120):
    """Fixed-file bounded tail, strict field allowlist for public display."""
    limit = max(1, min(120, int(limit)))
    entries = []
    directory = Path(root)
    if directory.is_symlink():
        return []
    for name in (PREVIOUS, CURRENT):
        path = directory / name
        if not path.is_file() or path.is_symlink():
            continue
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                end = stream.seek(0, os.SEEK_END)
                start = max(0, end - TAIL_BYTES)
                stream.seek(start)
                if start:
                    stream.readline()  # partial JSONL row
                for line in stream:
                    if len(line) > 2048:
                        continue
                    try:
                        original = json.loads(line)
                        if not isinstance(original, dict):
                            continue
                        timestamp = str(original.get("at", ""))
                        if len(timestamp) > 40:
                            continue
                        dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            continue
                        row = _safe_event(original.get("kind"), at=dt.astimezone(timezone.utc).isoformat(),
                                          **{name: original.get(name) for name in FIELDS})
                        entries.append(row)
                    except (TypeError, ValueError, KeyError):
                        continue
        except OSError:
            continue
    return entries[-limit:]
