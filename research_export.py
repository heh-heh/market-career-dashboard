"""Authenticated callers only. Read-only source exports; no trading/research execution.

ZIP payloads stream from bounded snapshots. Exported bytes are sanitized and hashed.
Only this module's UUID-owned output files can expire; source roots are never written.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import csv
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import secrets
import stat
import subprocess
import threading
import time
import uuid
import zipfile
from zoneinfo import ZoneInfo

from research_export_metadata import summarize_run, summary_csv, layers

VERSION = "research-export-v1"
TYPES = {"evolution_full", "evolution_summary", "evolution_population", "evolution_trades",
         "V07_FULL_RESEARCH_BUNDLE", "v4_full", "research_metadata", "toss_1m", "toss_ticks"}
SUMMARY_NAMES = {"run.json", "status.json", "final_population.json", "validation_summary.json",
                 "generalization_diagnostics.json", "shortlist.json", "campaign.json", "config.json",
                 "default_config.json", "evaluation_summary.json"}
EXTENSIONS = {".json", ".jsonl", ".csv", ".md", ".txt", ".toml", ".yaml", ".yml", ".ini"}
SECRET_KEY = re.compile(r"token|secret|password|credential|authorization|cookie|private.?key|access.?key|api.?key|app.?key|session(?:id|token|key|cookie)|^sessions?$", re.I)
SECRET_TEXT = re.compile(r"Bearer\s+\S+|(?:AKIA|ASIA)[A-Z0-9]{16}|[\w-]*(?:token|secret|password|authorization|cookie|credential|session|access[_-]?key|api[_-]?key|app[_-]?key)[\w-]*\s*[:=]\s*\S+|-----BEGIN[^\n]*PRIVATE KEY", re.I)
NY = ZoneInfo("America/New_York")
MiB = 1024**2
GiB = 1024**3


def utc():
    return datetime.now(timezone.utc).isoformat()


def safe_relative(value):
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ValueError("Invalid relative identifier")
    path = PurePosixPath(value)
    if path.is_absolute() or any(p in ("", ".", "..") for p in value.split("/")):
        raise ValueError("Path traversal/absolute paths are forbidden")
    return path.parts


def allowed_name(relative):
    parts = safe_relative(relative)
    for p in parts:
        low = p.lower()
        if low.startswith(".") or re.search(r"oauth|token|secret|password|credential|authorization|cookie|private[-_]?key|access[-_]?key|api[-_]?key|app[-_]?key|^sessions?(?:\.|_)|^id_rsa|(?:server|access|error)[._-]?log|^logs?$", low):
            return False
        if low.endswith((".key", ".pem", ".log", ".sqlite", ".db", ".zip", ".tmp")):
            return False
    name = parts[-1]
    plain = name[:-3] if name.endswith(".gz") else name
    return Path(plain).suffix.lower() in EXTENSIONS


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items() if not SECRET_KEY.search(str(k))}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                nested = json.loads(value)
                return json.dumps(sanitize(nested), ensure_ascii=False)
            except (ValueError, TypeError):
                pass
        if SECRET_TEXT.search(value):
            return "[REDACTED_SENSITIVE_VALUE]"
    return value


class SafeRoot:
    def __init__(self, path):
        self.path = Path(path).absolute()

    @contextmanager
    def directory(self, relative=None):
        parts = safe_relative(relative) if relative else ()
        # Reject even configured roots reached through symlinks.
        if self.path.resolve() != self.path:
            raise ValueError("Source root contains a symlink")
        fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for p in parts:
                new = os.open(p, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd); fd = new
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def file(self, relative):
        parts = safe_relative(relative)
        if not allowed_name(relative):
            raise ValueError("Sensitive/unsupported source file")
        with self.directory("/".join(parts[:-1]) or None) as parent:
            fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                meta = os.fstat(fd)
                if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                    raise ValueError("Nonregular/hardlinked source file")
                with os.fdopen(fd, "rb", closefd=False) as stream:
                    yield stream, meta
            finally:
                os.close(fd)

    def walk(self, prefix=None, limit=50000):
        stack = [prefix]
        count = 0
        while stack:
            directory = stack.pop()
            with self.directory(directory) as fd:
                with os.scandir(fd) as entries:
                    for entry in entries:
                        count += 1
                        if count > limit:
                            raise ValueError("Catalog file/directory limit exceeded")
                        rel = (directory+"/" if directory else "")+entry.name
                        if entry.is_symlink() or entry.name.startswith("."):
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not re.search(r"token|secret|credential|authorization|cookie", entry.name, re.I):
                                stack.append(rel)
                        elif entry.is_file(follow_symlinks=False) and allowed_name(rel):
                            s = entry.stat(follow_symlinks=False)
                            if s.st_nlink == 1:
                                yield dict(relative=rel, size=s.st_size, modifiedAt=datetime.fromtimestamp(s.st_mtime, timezone.utc).isoformat())

    def json(self, relative, max_bytes=32*MiB):
        with self.file(relative) as (stream, meta):
            if meta.st_size > max_bytes:
                raise ValueError("JSON exceeds bounded metadata limit")
            data = stream.read(max_bytes+1)
            if len(data) != meta.st_size or (os.fstat(stream.fileno()).st_mtime_ns, os.fstat(stream.fileno()).st_size) != (meta.st_mtime_ns, meta.st_size):
                raise ValueError("Metadata changed while reading; retry")
            return sanitize(json.loads(data))


class Snapshot(io.RawIOBase):
    """Never read bytes appended after opening; track original snapshot digest."""
    def __init__(self, stream, size):
        self.stream, self.remaining = stream, size
        self.digest = hashlib.sha256()
    def readable(self): return True
    def readinto(self, buffer):
        if self.remaining <= 0: return 0
        data = self.stream.read(min(len(buffer), self.remaining))
        self.remaining -= len(data)
        self.digest.update(data)
        buffer[:len(data)] = data
        return len(data)


class OffsetBuffer(io.BytesIO):
    """Virtual absolute file offsets, without allocating the preceding ZIP payload."""
    def __init__(self, base):
        super().__init__(); self.base = base
    def tell(self): return self.base+super().tell()
    def seek(self, offset, whence=0):
        return self.base+super().seek(offset-self.base if whence == 0 else offset, whence)


def stream_json(text, emit):
    """Bounded JSON tokenizer/parser; never materializes a population or source file."""
    chunk = ""; index = 0; lookahead = None
    def character():
        nonlocal chunk, index
        if index == len(chunk): chunk, index = text.read(8192), 0
        if not chunk: return ""
        c = chunk[index]; index += 1; return c
    def token():
        nonlocal lookahead, index
        if lookahead is not None:
            t, lookahead = lookahead, None; return t
        c = character()
        while c and c.isspace(): c = character()
        if not c: return None
        if c in "{}[],:": return c
        chars = [c]
        if c == '"':
            escaped = False
            while True:
                c = character()
                if not c: raise ValueError("Incomplete JSON string")
                chars.append(c)
                if len(chars) > MiB: raise ValueError("Oversized JSON string")
                if c == '"' and not escaped: break
                escaped = c == "\\" and not escaped
        else:
            while True:
                c = character()
                if not c: break
                if c.isspace() or c in "{}[],:":
                    index -= 1; break
                chars.append(c)
                if len(chars) > MiB: raise ValueError("Oversized JSON literal")
        return "".join(chars)
    def output(s, enabled):
        if enabled: emit(s.encode("utf-8"))
    def value(t, enabled=True, depth=0):
        if depth > 100: raise ValueError("JSON nesting limit exceeded")
        if t == "{":
            output("{", enabled); seen = set(); comma = False
            t = token()
            if t != "}":
                while True:
                    if t is None or not t.startswith('"'): raise ValueError("Invalid JSON object key")
                    key = json.loads(t)
                    if key in seen or len(seen) >= 100000: raise ValueError("Duplicate/oversized JSON object keys")
                    seen.add(key)
                    if token() != ":": raise ValueError("Invalid JSON separator")
                    keep = not SECRET_KEY.search(key)
                    if keep:
                        if comma: output(",", enabled)
                        output(json.dumps(key, ensure_ascii=False)+":", enabled); comma = True
                    value(token(), enabled and keep, depth+1)
                    t = token()
                    if t == "}": break
                    if t != ",": raise ValueError("Invalid JSON object")
                    t = token()
            output("}", enabled)
        elif t == "[":
            output("[", enabled); t = token(); comma = False
            while t != "]":
                if comma: output(",", enabled)
                value(t, enabled, depth+1); comma = True
                t = token()
                if t == "]": break
                if t != ",": raise ValueError("Invalid JSON array")
                t = token()
                if t == "]": raise ValueError("Trailing comma in JSON array")
            output("]", enabled)
        elif t and t.startswith('"'):
            output(json.dumps(sanitize(json.loads(t)), ensure_ascii=False), enabled)
        elif t and re.fullmatch(r"(?:true|false|null|-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?)", t):
            obj = json.loads(t)
            if isinstance(obj, float) and not math.isfinite(obj): raise ValueError("Nonfinite JSON")
            output(t, enabled)
        else: raise ValueError("Invalid JSON value")
    value(token())
    if token() is not None: raise ValueError("Trailing JSON content")


def finalize_manifest(archive, manifest):
    # Payload is already compressed. Simulate only the small manifest+central tail,
    # including ZIP64, until archiveBytes is an exact fixed point. Manifest STORED.
    manifest = dict(manifest, archiveBytes=0)
    for _ in range(8):
        data = json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False).encode()
        tail = OffsetBuffer(archive.fp.tell())
        with zipfile.ZipFile(tail, "w", allowZip64=True) as dry:
            dry.filelist = list(archive.filelist)
            dry.NameToInfo = dict(archive.NameToInfo)
            dry.writestr("export_manifest.json", data, compress_type=zipfile.ZIP_STORED)
        predicted = tail.tell()
        if predicted == manifest["archiveBytes"]:
            archive.writestr("export_manifest.json", data, compress_type=zipfile.ZIP_STORED)
            return predicted
        manifest["archiveBytes"] = predicted
    raise RuntimeError("Archive size manifest did not converge")


class ResearchExporter:
    def __init__(self, repo, *, roots=None, export_dir=None, min_free_bytes=10*GiB, reserve_fraction=.1,
                 max_archive_bytes=2*GiB, summary_bytes=64*MiB, ttl_days=7):
        self.repo = Path(repo).resolve()
        defaults = dict(evolution=Path("/home/ubuntu/evolution-results"), v4=Path("/home/ubuntu/v4-research"),
                        results=self.repo/"research/results", minutes=self.repo/"data/toss_1m",
                        ticks=self.repo/"data/toss_ticks", metadata=self.repo/"research/evolution")
        self.roots = {k: SafeRoot(p) for k, p in (roots or defaults).items()}
        self.output = Path(export_dir or "/home/ubuntu/research-exports").absolute()
        if self.output.resolve() != self.output:
            raise ValueError("Export output must not use symlinks")
        for root in self.roots.values():
            if self.output == root.path or self.output in root.path.parents or root.path in self.output.parents:
                raise ValueError("Export directory must be disjoint from every source root")
        self.min_free, self.reserve_fraction = min_free_bytes, reserve_fraction
        self.max_archive, self.summary_limit, self.ttl = max_archive_bytes, summary_bytes, ttl_days*86400
        self.symbols = set(json.loads((self.repo/"research/universe_v3.json").read_text())["symbols"])
        self.lock = threading.RLock()
        self.jobs = {}
        self.tickets = {}
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="research-export")
        self.cached = None
        self.output.mkdir(parents=True, exist_ok=True, mode=0o700)
        marker = self.output/".research_export_owner.json"
        if marker.is_symlink(): raise ValueError("Invalid export ownership marker")
        if not marker.exists():
            with marker.open("x") as f: json.dump({"owner": VERSION}, f)
        if json.loads(marker.read_text()).get("owner") != VERSION:
            raise ValueError("Output directory belongs to another application")
        self._recover()

    def _write(self, job):
        path = self.output/(job["jobId"]+".job.json")
        tmp = self.output/(job["jobId"]+".state.tmp")
        if self.output.resolve() != self.output: raise ValueError("Output directory changed to symlink")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(job, f, ensure_ascii=False, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        fd = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try: os.fsync(fd)
        finally: os.close(fd)

    def _recover(self):
        for p in self.output.glob("*.job.json"):
            if p.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}\.job\.json", p.name): continue
            try:
                job = json.loads(p.read_text())
                if not isinstance(job, dict) or job.get("state") not in ("queued", "running", "completed", "failed") or not isinstance(job.get("createdAt"), str): continue
                if job.get("owner") != VERSION or p.name != job.get("jobId", "")+".job.json": continue
                datetime.fromisoformat(job.get("finishedAt") or job["createdAt"])
                if job.get("state") in ("queued", "running"):
                    job.update(state="failed", error="INTERRUPTED_EXPORT: service restarted; source data unchanged", finishedAt=utc())
                    for suffix in (".tmp", ".zip", ".state.tmp"):
                        temp = self.output/(job["jobId"]+suffix)
                        if temp.is_file() and not temp.is_symlink(): temp.unlink()
                    self._write(job)
                self.jobs[job["jobId"]] = job
            except (ValueError, OSError, TypeError, KeyError): continue
        self.cleanup()

    def cleanup(self):
        with self.lock:
            if self.output.resolve() != self.output: raise ValueError("Unsafe export cleanup directory")
            for key, job in list(self.jobs.items()):
                if job["state"] in ("running", "queued"): continue
                age = time.time()-datetime.fromisoformat(job.get("finishedAt") or job["createdAt"]).timestamp()
                if age <= self.ttl: continue
                for suffix in (".zip", ".tmp", ".job.json", ".state.tmp"):
                    p = self.output/(key+suffix)
                    if p.is_file() and not p.is_symlink(): p.unlink()
                self.jobs.pop(key, None)

    def close(self):
        self.executor.shutdown(wait=True)

    def catalog(self, refresh=False):
        self.cleanup()
        with self.lock:
            if not refresh and self.cached and time.monotonic()-self.cached[0] < 30:
                return self.cached[1]
        sources = {}; runs = {}; warnings = []
        for kind, root in self.roots.items():
            try:
                files = list(root.walk()) if root.path.exists() else []
            except (ValueError, OSError) as exc:
                files = []; warnings.append(f"{kind}: {exc}")
            sources[kind] = dict(id=kind, available=bool(files), files=len(files), estimatedBytes=sum(f["size"] for f in files),
                                 updatedAt=max((f["modifiedAt"] for f in files), default=None), entries=files)
            if kind not in ("evolution", "results"): continue
            by_dir = {}
            for f in files:
                parent = str(PurePosixPath(f["relative"]).parent)
                if PurePosixPath(f["relative"]).name in ("run.json", "campaign.json"):
                    by_dir.setdefault(parent, [])
            for directory in by_dir:
                docs = {}
                for name in SUMMARY_NAMES-{"final_population.json"}:
                    rel = name if directory == "." else directory+"/"+name
                    try: docs[name] = root.json(rel, max_bytes=MiB)
                    except FileNotFoundError: continue
                    except (ValueError, OSError) as exc: warnings.append(f"{kind}/{directory}/{name}: {exc}")
                text = json.dumps({k: docs.get(k) for k in ("run.json", "campaign.json")}, ensure_ascii=False).lower()
                if kind == "results" and "evolution" not in text and not (directory == "evolution" or directory.startswith("evolution/")):
                    continue
                subset = [f for f in files if directory == "." or f["relative"].startswith(directory+"/")]
                key = hashlib.sha256((kind+":"+directory).encode()).hexdigest()[:24]
                summary = summarize_run(key, subset, docs)
                references = []
                for doc in layers(*docs.values()):
                    for field, value in doc.items():
                        if re.search(r"(?:source|parent|train|holdout|shortlist|validation).*(?:run(?:Id|Dir)?|population(?:File|Path)?|path|dir)$", field, re.I):
                            references.extend((field, ref) for ref in (value if isinstance(value, list) else [value]) if isinstance(ref, str))
                runs[key] = dict(root=kind, directory=directory, files=subset, references=references[:1000], summary=summary)
        rows = sorted((r["summary"] for r in runs.values()), key=lambda r: r["updatedAt"] or "", reverse=True)
        public = dict(sources=[{k: v for k, v in s.items() if k != "entries"} for s in sources.values()],
                      runs=rows, symbols=sorted(self.symbols), warnings=warnings[:30],
                      limits=dict(maxArchiveBytes=self.max_archive, summaryBytes=self.summary_limit,
                                  minFreeBytes=self.min_free, diskReserveFraction=self.reserve_fraction, maxTickDays=31),
                      jobs=[dict(j) for j in sorted(self.jobs.values(), key=lambda j: j["createdAt"], reverse=True)[:20]],
                      security="ADMIN_AUTH_REQUIRED; sources read-only; secrets sanitized; no raw server logs")
        value = dict(public=public, sources=sources, runs=runs)
        with self.lock: self.cached = (time.monotonic(), value)
        return value

    def summary(self, fmt="json"):
        rows = self.catalog()["public"]["runs"]
        if fmt == "csv": return summary_csv(rows)
        return json.dumps(dict(exportedAt=utc(), rows=rows, missingValues="null; no inferred performance"), ensure_ascii=False, indent=2, allow_nan=False).encode()

    def validate_request(self, body):
        if not isinstance(body, dict) or set(body)-{"type", "runIds", "symbols", "from", "to"}:
            raise ValueError("Only type/runIds/symbols/from/to are allowed; filesystem paths are forbidden")
        if body.get("type") not in TYPES: raise ValueError("Unknown export type")
        if body["type"] in ("toss_1m", "toss_ticks"):
            if "runIds" in body: raise ValueError("runIds not allowed for raw data")
            syms = body.get("symbols")
            if not isinstance(syms, list) or not syms or len(syms) > 30 or any(not isinstance(s, str) or s not in self.symbols for s in syms):
                raise ValueError("Explicit allowlisted symbols required")
            for key in ("from", "to"):
                if not isinstance(body.get(key), str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", body[key]):
                    raise ValueError("Explicit ISO from/to dates required")
            a, b = date.fromisoformat(body["from"]), date.fromisoformat(body["to"])
            if b < a or (b-a).days > (30 if body["type"] == "toss_ticks" else 3660):
                raise ValueError("Invalid/oversized date range; ticks limited to 31 days")
        else:
            if set(body) & {"symbols", "from", "to"}: raise ValueError("Raw-data selectors not allowed for artifacts")
            if "runIds" in body:
                if not (body["type"].startswith("evolution") or body["type"] == "V07_FULL_RESEARCH_BUNDLE"):
                    raise ValueError("runIds supported only for Evolution exports")
                if not isinstance(body["runIds"], list) or not body["runIds"] or len(body["runIds"]) > 100 or any(not isinstance(x, str) or not re.fullmatch(r"[0-9a-f]{24}", x) for x in body["runIds"]):
                    raise ValueError("runIds must be opaque catalog IDs, never paths")
        return body

    def create(self, body):
        body = self.validate_request(body)
        with self.lock:
            self.cleanup()
            if sum(j["state"] in ("queued", "running") for j in self.jobs.values()) >= 3:
                raise ValueError("Export queue full; wait for current jobs")
            key = uuid.uuid4().hex
            job = dict(owner=VERSION, jobId=key, type=body["type"], selection=body, state="queued", createdAt=utc(),
                       progressPct=0, filesProcessed=0, totalFiles=0, bytesProcessed=0, estimatedBytes=0, archiveBytes=0)
            self.jobs[key] = job; self._write(job)
            self.executor.submit(self._work, key)
            return dict(job)

    def get(self, key):
        if not re.fullmatch(r"[0-9a-f]{32}", key): raise ValueError("Invalid job ID")
        with self.lock:
            if key not in self.jobs: raise FileNotFoundError("Export job not found/expired")
            return dict(self.jobs[key])

    def download(self, key):
        job = self.get(key)
        if job["state"] != "completed": raise ValueError("Export is not completed")
        p = self.output/(key+".zip")
        if p.is_symlink() or not p.is_file(): raise FileNotFoundError("Archive missing/expired")
        if not re.fullmatch(r"[a-zA-Z0-9-]+\.zip", job.get("downloadName", "")): raise ValueError("Invalid download filename")
        return p, job["downloadName"]

    def ticket(self, key):
        self.download(key)  # authenticated caller can issue only completed exports
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.tickets = {k: v for k, v in self.tickets.items() if v[1] > time.time()}
            if len(self.tickets) >= 100: raise ValueError("Too many pending download tickets")
            self.tickets[token] = (key, time.time()+45)
        return token

    def consume_ticket(self, token, key):
        with self.lock:
            info = self.tickets.pop(token, None)
        return bool(info and info[0] == key and info[1] > time.time())

    def update(self, key, **values):
        with self.lock:
            self.jobs[key].update(values, updatedAt=utc()); self._write(self.jobs[key])

    def disk_guard(self, expected=0):
        disk = shutil.disk_usage(self.output)
        reserve = max(self.min_free, int(disk.total*self.reserve_fraction))
        if disk.free < reserve+expected:
            raise ValueError(f"DISK_SPACE_GUARD: free={disk.free}, reserve={reserve}, required={expected}; sources unchanged")

    def plan(self, body):
        catalog = self.catalog(refresh=True); sources, runs = catalog["sources"], catalog["runs"]
        kind = body["type"]; selected = []; chosen = []; warnings = []
        if kind.startswith("evolution") or kind == "V07_FULL_RESEARCH_BUNDLE":
            ids = body.get("runIds", [k for k in runs if kind != "V07_FULL_RESEARCH_BUNDLE" or runs[k]["summary"]["isV07"]])
            if not ids: raise ValueError("No matching Evolution runs/metadata found")
            if any(k not in runs for k in ids): raise ValueError("Run ID missing from current catalog")
            if kind == "V07_FULL_RESEARCH_BUNDLE":
                if not any(runs[k]["summary"]["isV07"] for k in ids): raise ValueError("V0.7 metadata required")
                # Follow only references that resolve to already cataloged allowlisted runs.
                todo = list(ids); linked = set(ids)
                while todo:
                    current = runs[todo.pop()]
                    for field, ref in current["references"]:
                        matches = [k for k, r in runs.items() if ref in (k, r["summary"]["runId"], r["directory"]) or
                                   ref == str(self.roots[r["root"]].path/r["directory"]) or
                                   ref == str(self.roots[r["root"]].path/r["directory"]/"final_population.json")]
                        if len(matches) == 1:
                            if matches[0] not in linked: linked.add(matches[0]); todo.append(matches[0])
                        else: warnings.append("Unresolved lineage reference: "+field)
                ids = sorted(linked)
            chosen = [runs[k]["summary"] for k in ids]
            for k in ids:
                r = runs[k]
                for f in r["files"]:
                    name = PurePosixPath(f["relative"]).name
                    use = (kind in ("evolution_full", "V07_FULL_RESEARCH_BUNDLE") or
                           kind == "evolution_summary" and (name in SUMMARY_NAMES or name.lower().startswith(("config", "readme"))) or
                           kind == "evolution_population" and ("population" in name or name in ("shortlist.json", "run.json", "config.json")) or
                           kind == "evolution_trades" and name.endswith((".csv", ".csv.gz")) and "trade" in name.lower())
                    if use: selected.append(dict(f, root=r["root"]))
        elif kind == "v4_full": selected = [dict(f, root="v4") for f in sources.get("v4", {}).get("entries", [])]
        elif kind == "research_metadata":
            for root, s in sources.items():
                if root in ("minutes", "ticks"): continue
                selected.extend(dict(f, root=root) for f in s["entries"] if PurePosixPath(f["relative"]).name in SUMMARY_NAMES or f["relative"].lower().endswith(".md"))
        else:
            root = "minutes" if kind == "toss_1m" else "ticks"
            for f in sources[root]["entries"]:
                plain = PurePosixPath(f["relative"]).name.removesuffix(".gz")
                if not plain.endswith(".csv"): continue
                symbol = plain[:-4]
                if symbol not in body["symbols"]: continue
                if kind == "toss_ticks":
                    dates = [p for p in PurePosixPath(f["relative"]).parts[:-1] if re.fullmatch(r"\d{4}-\d{2}-\d{2}", p)]
                    if len(dates) != 1 or not body["from"] <= dates[0] <= body["to"]: continue
                selected.append(dict(f, root=root, symbol=symbol))
            for symbol in body["symbols"]:
                matches = [f for f in selected if f["symbol"] == symbol]
                if not matches: raise ValueError(f"{symbol}: no CSV/CSV.GZ for requested source/date partitions")
                plain_paths = [f["relative"].removesuffix(".gz") for f in matches]
                if len(set(plain_paths)) != len(plain_paths): raise ValueError(f"{symbol}: both CSV and CSV.GZ present; wait for finalized rotation")
        if "metadata" in sources:
            selected.extend(dict(f, root="metadata") for f in sources["metadata"]["entries"] if f["relative"].lower().endswith((".md", "_config.json")) or PurePosixPath(f["relative"]).name == "config.json")
        selected = sorted({(f["root"], f["relative"]): f for f in selected}.values(), key=lambda f: (f["root"], f["relative"]))
        names = [(f["root"], f["relative"].removesuffix(".gz")) for f in selected]
        if len(set(names)) != len(names): raise ValueError("Ambiguous compressed/plain artifact names; wait for finalized files")
        if not selected: raise ValueError("No supported non-sensitive artifacts found")
        estimate = sum(f["size"] for f in selected)
        if kind == "evolution_summary" and estimate > self.summary_limit:
            raise ValueError("Summary exceeds mobile size limit; select fewer runs")
        return selected, chosen, warnings

    def _work(self, key):
        job = self.get(key); body = job["selection"]; tmp = self.output/(key+".tmp")
        try:
            self.update(key, state="running", stage="planning", startedAt=utc())
            files, runs, warnings = self.plan(body)
            estimate = sum(f["size"] for f in files)
            self.disk_guard(min(estimate+4*MiB, self.max_archive))
            self.update(key, stage="archiving", totalFiles=len(files), estimatedBytes=estimate)
            entries = []; processed = 0
            git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True)
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(fd, "w+b") as destination, zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=3, allowZip64=True) as archive:
                for i, f in enumerate(files):
                    def progress(written, fraction):
                        self.update(key, bytesProcessed=processed+written, progressPct=round(95*(i+fraction)/len(files), 1), archiveBytes=destination.tell())
                    result = self._add_source(archive, f, body, progress)
                    entries.append(result); processed += result["exportedBytes"]
                    if body["type"] == "evolution_summary" and processed > self.summary_limit:
                        raise ValueError("Summary exceeds mobile export limit")
                    if destination.tell() > self.max_archive: raise ValueError("Archive exceeds configured maximum size")
                    self.disk_guard()
                    self.update(key, filesProcessed=i+1, bytesProcessed=processed, progressPct=round(95*(i+1)/len(files), 1), archiveBytes=destination.tell())
                readme = ("# Research Data Export\n\nRead-only, sanitized research snapshots. Not an atomic training checkpoint.\n"
                          "export_manifest.json records selected runs, provenance, sizes and SHA256 of every exported file.\n"
                          "sourceSha256 hashes source snapshot bytes; sha256 hashes sanitized archived bytes.\n"
                          "Evolution summary values come from artifacts; null means evidence unavailable.\n"
                          "V07 bundle completeEvidence=false means incomplete or unverified lineage/stage coverage.\n"
                          "1m timestamps: exclusive END; raw tick timestamps preserved, no invented timezone or history.\n"
                          "Tick streams are LOSSY; duplicate-looking trades remain separate deliveries.\n"
                          "No live orders, data adjustments, model training or Final OOS evaluation were performed.\n").encode()
                for name, content in (("README.md", readme), ("evolution_research_summary.json", self.summary()), ("evolution_research_summary.csv", self.summary("csv"))):
                    archive.writestr(name, content)
                    entries.append(dict(path=name, exportedBytes=len(content), sha256=hashlib.sha256(content).hexdigest(), generated=True))
                names = [x["path"] for x in entries]
                bundle = None
                if body["type"] == "V07_FULL_RESEARCH_BUNDLE":
                    bundle = dict(shortlistIncluded=any(p.endswith("/shortlist.json") for p in names),
                                  validationSummaries=sum(p.endswith("/validation_summary.json") for p in names),
                                  populations=sum(p.endswith("/final_population.json") for p in names),
                                  diagnosticsIncluded=any(p.endswith("/generalization_diagnostics.json") for p in names))
                    bundle["completeEvidence"] = (bundle["shortlistIncluded"] and bundle["diagnosticsIncluded"] and bundle["validationSummaries"] >= 2 and bundle["populations"] >= 2 and not warnings)
                    periods = [r.get("evaluationPeriod") or r.get("validationPeriod") for r in runs]
                    bundle["holdout2024Evidence"] = any(p and str(p[0]).startswith("2024") and str(p[1]).startswith("2024") for p in periods)
                    bundle["development2025Evidence"] = any(p and str(p[0]).startswith("2025") and str(p[1]).startswith("2025") for p in periods)
                    bundle["training2022To2023Evidence"] = any(p and str(p[0]).startswith("2022") and str(p[1]).startswith("2023") for p in [r.get("trainPeriod") or r.get("evaluationPeriod") for r in runs])
                    bundle["completeEvidence"] = bundle["completeEvidence"] and all(bundle[k] for k in ("holdout2024Evidence", "development2025Evidence", "training2022To2023Evidence"))
                manifest = dict(version=1, exporterVersion=VERSION, exportedAt=utc(), gitCommit=git.stdout.strip(),
                                jobId=key, type=body["type"], selection=body, runs=runs, files=entries,
                                warnings=warnings[:100], v07Bundle=bundle,
                                sourcePolicy="READ_ONLY; bounded per-file snapshots, not a cross-run atomic training checkpoint",
                                hashBasis="sha256 of exported sanitized bytes; sourceSha256 is snapshot bytes",
                                timestampKind="end" if body["type"] == "toss_1m" else None,
                                dateBasis="America/New_York bar-start trading date" if body["type"] == "toss_1m" else "collector partition date; source timestamp preserved, not inferred" if body["type"] == "toss_ticks" else None,
                                naiveTimezone=os.getenv("RESEARCH_EXPORT_1M_TIMEZONE") if body["type"] == "toss_1m" else None,
                                tickCompleteness="LOSSY delivered stream; no deduplication or complete-exchange-tape claim" if body["type"] == "toss_ticks" else None,
                                secretPolicy="sensitive filenames excluded; secret JSON keys/CSV columns removed; suspicious string values redacted; raw logs excluded")
                expected = finalize_manifest(archive, manifest)
            if tmp.stat().st_size != expected: raise RuntimeError("Archive size disagrees with manifest")
            if expected > self.max_archive: raise ValueError("Archive exceeds configured maximum size")
            self.disk_guard()
            with tmp.open("rb") as completed: os.fsync(completed.fileno())
            final = self.output/(key+".zip")
            if final.exists() or final.is_symlink(): raise ValueError("Archive UUID collision")
            os.replace(tmp, final)
            directory_fd = os.open(self.output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try: os.fsync(directory_fd)
            finally: os.close(directory_fd)
            name = body["type"].lower().replace("_", "-")+"-export-"+datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+key[:8]+".zip"
            self.update(key, state="completed", stage="completed", progressPct=100, archiveBytes=expected, downloadName=name,
                        finishedAt=utc(), bundleEvidence=bundle, warnings=warnings[:30])
        except Exception as exc:
            if tmp.is_file() and not tmp.is_symlink(): tmp.unlink()
            self.update(key, state="failed", error=str(exc), finishedAt=utc())

    def _add_source(self, archive, entry, body, progress=None):
        root, rel = self.roots[entry["root"]], entry["relative"]
        name = entry["root"]+"/"+rel.removesuffix(".gz")
        exported_hash = hashlib.sha256(); written = 0
        with root.file(rel) as (source, before):
            size = before.st_size
            # Append-only CSV/JSONL snapshot ends at a newline, never a partial tick.
            suffix = Path(rel.removesuffix(".gz")).suffix.lower()
            if not rel.endswith(".gz") and suffix in (".csv", ".jsonl") and size:
                source.seek(max(0, size-MiB)); tail = source.read(MiB)
                if not tail.endswith(b"\n"):
                    cut = tail.rfind(b"\n")
                    if cut < 0: raise ValueError("No complete record at snapshot boundary")
                    size = max(0, size-MiB)+cut+1
                source.seek(0)
            snapshot = Snapshot(source, size)
            buffered = io.BufferedReader(snapshot)
            decoded = gzip.GzipFile(fileobj=buffered) if rel.endswith(".gz") else buffered
            text = io.TextIOWrapper(decoded, encoding="utf-8", newline="")
            rows = 0; removed = []; last_check = time.monotonic()
            with archive.open(name, "w", force_zip64=True) as target:
                def emit(data):
                    nonlocal written, last_check
                    exported_hash.update(data); written += len(data); target.write(data)
                    if written > 4*GiB: raise ValueError("Per-file decoded export limit exceeded")
                    if body["type"] == "evolution_summary" and written > self.summary_limit: raise ValueError("Summary exceeds mobile export limit")
                    if time.monotonic()-last_check > 1:
                        self.disk_guard()
                        if archive.fp.tell() > self.max_archive: raise ValueError("Archive size limit exceeded")
                        if progress: progress(written, 1-snapshot.remaining/max(size, 1))
                        last_check = time.monotonic()
                if suffix == ".json":
                    pending = bytearray()
                    def buffered_emit(data):
                        pending.extend(data)
                        if len(pending) >= 65536:
                            emit(pending); pending.clear()
                    stream_json(text, buffered_emit)
                    if pending: emit(pending)
                elif suffix == ".jsonl":
                    for line in iter(lambda: text.readline(MiB+1), ""):
                        if len(line) > MiB: raise ValueError("JSONL record too large")
                        emit((json.dumps(sanitize(json.loads(line)), ensure_ascii=False, allow_nan=False)+"\n").encode()); rows += 1
                elif suffix == ".csv":
                    csv.field_size_limit(MiB)
                    record_size = 0
                    def bounded_lines():
                        nonlocal record_size
                        for line in iter(lambda: text.readline(MiB+1), ""):
                            record_size += len(line)
                            if len(line) > MiB or record_size > 2*MiB: raise ValueError("CSV record exceeds bounded memory limit")
                            yield line
                    reader = csv.DictReader(bounded_lines(), strict=True)
                    headers = reader.fieldnames or []
                    record_size = 0
                    if len(headers) > 512: raise ValueError("CSV column limit exceeded")
                    if len(headers) != len(set(headers)): raise ValueError("Duplicate CSV columns")
                    fields = [x for x in headers if not SECRET_KEY.search(x)]
                    removed = sorted(set(headers)-set(fields))
                    if not fields: raise ValueError("No non-sensitive CSV columns")
                    def record(row):
                        buffer = io.StringIO(newline=""); writer = csv.DictWriter(buffer, fieldnames=fields)
                        values = {k: sanitize(row.get(k, "")) for k in fields}
                        for k, v in values.items():
                            if isinstance(v, str) and (v.startswith(("=", "+", "@", "\t", "\r")) or v.startswith("-") and not re.fullmatch(r"-\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", v)):
                                values[k] = "'"+v
                        writer.writerow(values); emit(buffer.getvalue().encode())
                    record(dict(zip(fields, fields)))
                    for row in reader:
                        record_size = 0
                        if time.monotonic()-last_check > 1:
                            self.disk_guard()
                            if progress: progress(written, 1-snapshot.remaining/max(size, 1))
                            last_check = time.monotonic()
                        if None in row or any(row.get(k) is None for k in headers): raise ValueError("Malformed/incomplete CSV record")
                        if body["type"] in ("toss_1m", "toss_ticks"):
                            for identity in ("symbol", "ticker"):
                                if row.get(identity) and row[identity] != entry["symbol"]: raise ValueError("CSV symbol identity mismatch")
                        if body["type"] == "toss_1m" and entry["root"] == "minutes":
                            stamp = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                            if stamp.second or stamp.microsecond: raise ValueError("Minute timestamps must be exact exclusive END boundaries")
                            if stamp.tzinfo is None:
                                zone = os.getenv("RESEARCH_EXPORT_1M_TIMEZONE")
                                if zone not in ("UTC", "America/New_York"): raise ValueError("Timezone-naive minute source: declare RESEARCH_EXPORT_1M_TIMEZONE; no guessing")
                                from research.backtest_v4_strategies import normalize_minute_timestamp
                                start = normalize_minute_timestamp(row["timestamp"], "end", zone)
                            else: start = stamp.astimezone(NY)-timedelta(minutes=1)
                            if not body["from"] <= start.date().isoformat() <= body["to"]: continue
                        record(row); rows += 1
                    if body["type"] == "toss_1m" and entry["root"] == "minutes" and rows == 0:
                        raise ValueError(entry["symbol"]+": no candles in requested date range")
                else:
                    private_block = False
                    for line in iter(lambda: text.readline(MiB+1), ""):
                        if len(line) > MiB: raise ValueError("Text line too large")
                        if re.search(r"-----BEGIN.*PRIVATE KEY", line): private_block = True
                        if private_block:
                            if re.search(r"-----END.*PRIVATE KEY", line): private_block = False
                            emit(b"[REDACTED_PRIVATE_KEY_BLOCK]\n"); continue
                        emit(str(sanitize(line)).encode())
                # Drain compressed snapshot so source digest covers exact snapshot bytes.
                while buffered.read(MiB): pass
                after = os.fstat(source.fileno())
                immutable = rel.endswith(".gz") or suffix not in (".csv", ".jsonl")
                if after.st_size < before.st_size or immutable and (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    raise ValueError("Source mutated while reading; retry a completed run")
                if not immutable and (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                    source.seek(0); remaining = size; check = hashlib.sha256()
                    while remaining:
                        data = source.read(min(MiB, remaining))
                        if not data: raise ValueError("Snapshot source truncated")
                        check.update(data); remaining -= len(data)
                    if check.digest() != snapshot.digest.digest(): raise ValueError("Snapshot prefix modified; retry a stable source")
            text.close()
        return dict(path=name, root=entry["root"], sourceRelative=rel, sourceBytes=before.st_size,
                    snapshotBytes=size, exportedBytes=written, sha256=exported_hash.hexdigest(),
                    sourceSha256=snapshot.digest.hexdigest(), rows=rows, removedColumns=removed,
                    snapshotModifiedAt=datetime.fromtimestamp(before.st_mtime, timezone.utc).isoformat(),
                    appendDuringRead=after.st_size > before.st_size, sanitationApplied=True)
