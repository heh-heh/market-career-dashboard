"""Authenticated same-origin API client. Credentials remain in memory only."""
import hashlib
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from .artifact_reader import extract_verified, verify_cache, sha256
from .experiment_registry import read_json, write_json


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError("EXPORT_REDIRECT_BLOCKED")


def extraction_disk_reserve(job, config):
    """Disk working-set estimate; security extraction cap remains max_extracted_bytes."""
    archive = job.get("archiveBytes")
    payload = job.get("bytesProcessed")
    if not isinstance(archive, int) or archive <= 0:
        raise ValueError("Invalid export archive size")
    if not isinstance(payload, int) or payload < 0:
        payload = job.get("estimatedBytes")
    if not isinstance(payload, int) or payload < 0:
        # Fail closed when a completed export does not report any bounded size evidence.
        return archive + config.max_extracted_bytes

    # bytesProcessed is the exporter's completed sanitized/uncompressed payload.
    # Keep 64 MiB plus 25% headroom for generated manifests/summaries and filesystem
    # overhead, while preserving max_extracted_bytes as the hard ZIP verification cap.
    headroom = 64 * 1024**2
    extraction = min(config.max_extracted_bytes, max(headroom, payload + payload // 4 + headroom))
    return archive + extraction


class ExportClient:
    def __init__(self, config, *, opener=None):
        self.config = config
        self.base = os.environ.get("RESEARCH_EXPORT_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
        self.token = os.environ.get("RESEARCH_EXPORT_TOKEN", "")
        url = urllib.parse.urlsplit(self.base)
        if not self.token:
            raise ValueError("RESEARCH_EXPORT_TOKEN_REQUIRED")
        if url.scheme not in ("http", "https") or url.username or url.password or url.query or url.fragment or url.path:
            raise ValueError("Invalid export API origin")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise ValueError("Remote export API requires HTTPS")
        self.opener = opener or urllib.request.build_opener(NoRedirect())

    def open(self, path, body=None):
        if not path.startswith("/api/research/export/") or ".." in path:
            raise ValueError("Export API path not allowlisted")
        req = urllib.request.Request(self.base + path,
               data=None if body is None else json.dumps(body).encode(),
               headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"})
        try:
            return self.opener.open(req, timeout=60)
        except urllib.error.HTTPError as exc:
            # Allow only a known disabled-feature enum, never a raw response body.
            if exc.code == 503:
                try:
                    body=json.loads(exc.read(4096))
                    if body.get("error") in {"API_DISABLED","DEPENDENCY_DISABLED"} and body.get("feature",body.get("dependency")) == "research_export":
                        raise ValueError("API_DEPENDENCY_DISABLED: dependency=research_export")
                except (json.JSONDecodeError, UnicodeDecodeError, TypeError, AttributeError):
                    pass
            raise ValueError(f"EXPORT_HTTP_{exc.code}") from None
        except urllib.error.URLError:
            raise ValueError("EXPORT_NETWORK_ERROR") from None

    def json(self, path, body=None):
        with self.open(path, body) as response:
            raw = response.read(16 * 1024**2 + 1)
        if len(raw) > 16 * 1024**2:
            raise ValueError("API response too large")
        value = json.loads(raw)
        if value.get("ok") is False:
            raise ValueError("EXPORT_API_ERROR")
        return value

    def collect(self, export_type, ids, catalog):
        selected = [r for r in catalog["runs"] if r["id"] in ids]
        # Catalog changes invalidate this pointer; every cache hit is rehashed.
        key = hashlib.sha256(json.dumps({"type": export_type, "rows": selected, "boundary": "PRE2026_V1"}, sort_keys=True).encode()).hexdigest()
        cache = self.config.registry / "cache"
        cache.mkdir(parents=True, exist_ok=True, mode=0o700)
        pointer = cache / ("request-" + key + ".json")
        if pointer.exists():
            entry = read_json(pointer)
            digest = entry["manifestHash"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise ValueError("Invalid cache reference")
            folder = cache / digest
            manifest = verify_cache(folder)
            if manifest.get("type") != export_type or manifest.get("selection") != {"type": export_type, "runIds": ids, "researchOnly": True}:
                raise ValueError("CACHE_RESEARCH_BOUNDARY_MISMATCH")
            if hashlib.sha256((folder / "export_manifest.json").read_bytes()).hexdigest() != digest:
                raise ValueError("CACHE_MANIFEST_MISMATCH")
            return folder, digest, manifest
        request = {"type": export_type, "runIds": ids, "researchOnly": True}
        pending = cache / ("pending-" + key + ".json")
        if pending.exists():
            previous = read_json(pending).get("jobId", "")
            if not re.fullmatch(r"[0-9a-f]{32}", previous):
                raise ValueError("Invalid pending export reference")
            try:
                job = self.json("/api/research/export/jobs/" + previous)
            except ValueError as exc:
                if str(exc) != "EXPORT_HTTP_404": raise
                job = self.json("/api/research/export/jobs", request)
            else:
                if job.get("selection") != request:
                    raise ValueError("PENDING_EXPORT_SELECTION_MISMATCH")
                if job.get("state") == "failed":
                    job = self.json("/api/research/export/jobs", request)
        else:
            job = self.json("/api/research/export/jobs", request)
        job_id = job.get("jobId", "")
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("Invalid export job identifier")
        write_json(pending, {"jobId": job_id, "type": export_type})
        deadline = time.monotonic() + self.config.export_timeout_seconds
        last_progress=None
        print(json.dumps({
            "event":"export_job","jobId":job_id,"type":export_type,
            "state":job.get("state"),"progressPct":job.get("progressPct"),
        },sort_keys=True),flush=True)
        while job.get("state") in ("queued", "running"):
            if time.monotonic() > deadline:
                raise ValueError("EXPORT_TIMEOUT")
            time.sleep(min(self.config.poll_seconds, 10))
            job = self.json("/api/research/export/jobs/" + job_id)
            progress=(job.get("state"),job.get("progressPct"),job.get("filesProcessed"),job.get("bytesProcessed"))
            if progress!=last_progress:
                print(json.dumps({
                    "event":"export_job","jobId":job_id,"type":export_type,
                    "state":job.get("state"),"progressPct":job.get("progressPct"),
                    "filesProcessed":job.get("filesProcessed"),"totalFiles":job.get("totalFiles"),
                    "bytesProcessed":job.get("bytesProcessed"),"estimatedBytes":job.get("estimatedBytes"),
                },sort_keys=True),flush=True)
                last_progress=progress
        if job.get("state") != "completed":
            raise ValueError("EXPORT_JOB_FAILED")
        expected = job.get("archiveBytes")
        if not isinstance(expected, int) or not 0 < expected <= self.config.max_archive_bytes:
            raise ValueError("Invalid/oversized export")
        from .safety_gate import resource_gate
        working_set = extraction_disk_reserve(job, self.config)
        resource_gate(self.config, expected_bytes=working_set, observer=getattr(self,"resource_observer",None))
        print(json.dumps({
            "event":"export_disk_guard",
            "jobId":job_id,
            "archiveBytes":expected,
            "payloadBytes":job.get("bytesProcessed"),
            "workingSetBytes":working_set,
            "hardExtractionLimitBytes":self.config.max_extracted_bytes,
        },sort_keys=True),flush=True)
        path = cache / (uuid.uuid4().hex + ".zip")
        size = 0
        next_report=16*1024*1024
        print(json.dumps({"event":"export_download","jobId":job_id,"bytes":0,"expectedBytes":expected},sort_keys=True),flush=True)
        with self.open("/api/research/export/jobs/" + job_id + "/download") as response, path.open("xb") as dest:
            for block in iter(lambda: response.read(1024 * 1024), b""):
                size += len(block)
                if size > expected:
                    raise ValueError("Archive larger than declared size")
                dest.write(block)
                if size>=next_report or size==expected:
                    print(json.dumps({
                        "event":"export_download","jobId":job_id,"bytes":size,"expectedBytes":expected,
                        "progressPct":round((size/expected)*100,1) if expected else None,
                    },sort_keys=True),flush=True)
                    next_report+=16*1024*1024
        if size != expected:
            raise ValueError("Incomplete archive download")
        # Server estimates are not a substitute for the ZIP's actual decoded size.
        # Check space again on the cache filesystem before any extraction writes.
        with zipfile.ZipFile(path) as archive:
            decoded=sum(entry.file_size for entry in archive.infolist())
        if decoded > self.config.max_extracted_bytes:
            raise ValueError("Archive exceeds extraction limit")
        actual_set=extraction_disk_reserve({"archiveBytes":size,"bytesProcessed":decoded},self.config)
        resource_gate(self.config, expected_bytes=actual_set, observer=getattr(self,"resource_observer",None))
        folder, digest = extract_verified(path, cache, self.config.max_extracted_bytes)
        manifest = verify_cache(folder)
        if manifest.get("type") != export_type or manifest.get("selection", {}).get("researchOnly") is not True or sorted(manifest.get("selection", {}).get("runIds", [])) != sorted(ids):
            raise ValueError("EXPORT_SELECTION_MISMATCH")
        write_json(pointer, {"manifestHash": digest, "archiveSha256": sha256(path)})
        return folder, digest, manifest
