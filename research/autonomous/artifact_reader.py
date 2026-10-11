"""Manifest-verified, bounded ZIP extraction; cached originals never rewritten."""
import hashlib
import json
from pathlib import Path
import re
import stat
import zipfile
import uuid
import os

from research_export import safe_relative, allowed_name, sanitize, SafeRoot, SECRET_TEXT
from .experiment_registry import read_json
from .safety_gate import artifact_period_gate


def sha256(path):
    value = hashlib.sha256()
    path = Path(path).absolute()
    with SafeRoot(path.parent).directory() as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
        with os.fdopen(fd, "rb") as f:
            meta = os.fstat(f.fileno())
            if not stat.S_ISREG(meta.st_mode) or meta.st_nlink != 1:
                raise ValueError("Unsafe checksum input")
            for block in iter(lambda: f.read(1024 * 1024), b""):
                value.update(block)
    return value.hexdigest()


def verify_archive(path, max_bytes=4 * 1024**3):
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            names = [i.filename for i in infos]
            if len(infos) > 50000 or len(set(names)) != len(names):
                raise ValueError("Duplicate/excess archive entries")
            if "export_manifest.json" not in names:
                raise ValueError("Missing export manifest")
            if sum(i.file_size for i in infos) > max_bytes:
                raise ValueError("Archive exceeds extraction limit")
            for entry in infos:
                safe_relative(entry.filename)
                if not allowed_name(entry.filename) or stat.S_ISLNK(entry.external_attr >> 16) or entry.is_dir():
                    raise ValueError("Unsafe/secret archive entry")
                if entry.flag_bits & 1 or entry.file_size > 512 * 1024**2:
                    raise ValueError("Encrypted/oversized archive entry")
            if archive.getinfo("export_manifest.json").file_size > 16 * 1024**2:
                raise ValueError("Oversized manifest")
            raw = archive.read("export_manifest.json")
            manifest = json.loads(raw)
            if manifest.get("archiveBytes") != Path(path).stat().st_size or manifest.get("version") != 1:
                raise ValueError("Archive size/schema mismatch")
            entries = manifest.get("files", [])
            listed = [e["path"] for e in entries]
            if len(set(listed)) != len(listed) or set(listed) != set(names) - {"export_manifest.json"}:
                raise ValueError("Manifest file set mismatch")
            for entry in entries:
                info = archive.getinfo(entry["path"])
                if info.file_size != entry["exportedBytes"] or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]):
                    raise ValueError("Manifest file size/hash invalid")
                h = hashlib.sha256()
                with archive.open(info) as f:
                    for block in iter(lambda: f.read(1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest() != entry["sha256"]:
                    raise ValueError("ARCHIVE_SHA256_MISMATCH")
                if entry["path"].endswith(".json"):
                    if info.file_size > 32 * 1024**2:
                        raise ValueError("JSON artifact too large")
                    data = json.loads(archive.read(info))
                    if sanitize(data) != data:
                        raise ValueError("SECRET_IN_ARTIFACT")
                    if isinstance(data, dict):
                        artifact_period_gate(data)
                elif entry["path"].endswith((".md", ".txt", ".patch", ".jsonl")):
                    with archive.open(info) as stream:
                        for line in iter(lambda: stream.readline(1024 * 1024 + 1), b""):
                            if len(line) > 1024 * 1024 or SECRET_TEXT.search(line.decode("utf-8")):
                                raise ValueError("SECRET_OR_OVERSIZED_TEXT_ARTIFACT")
            return manifest, hashlib.sha256(raw).hexdigest()
    except (zipfile.BadZipFile, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("CORRUPTED_ARCHIVE") from exc


def verify_cache(folder):
    folder = Path(folder).absolute()
    if folder.resolve() != folder:
        raise ValueError("Unsafe cache directory")
    manifest = read_json(folder / "export_manifest.json")
    expected = {e["path"] for e in manifest["files"]} | {"export_manifest.json"}
    observed = set()
    for root, dirs, files in os.walk(folder, followlinks=False):
        if any((Path(root) / d).is_symlink() for d in dirs):
            raise ValueError("Unsafe cache symlink")
        observed.update(str((Path(root) / f).relative_to(folder)) for f in files)
        if len(observed) > 50000:
            raise ValueError("Too many cache files")
    if observed != expected:
        raise ValueError("CACHE_FILE_SET_MISMATCH")
    for entry in manifest["files"]:
        safe_relative(entry["path"])
        p = folder / entry["path"]
        if p.is_symlink() or p.resolve() != p.absolute() or p.stat().st_size != entry["exportedBytes"] or sha256(p) != entry["sha256"]:
            raise ValueError("CACHE_SHA256_MISMATCH")
    return manifest


def extract_verified(path, cache, max_bytes=4 * 1024**3):
    manifest, digest = verify_archive(path, max_bytes)
    cache = Path(cache)
    if cache.resolve() != cache.absolute():
        raise ValueError("Unsafe cache path")
    cache.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = cache / digest
    if target.exists():
        verify_cache(target)
        return target, digest
    # Interrupted partial work is retained; retries cannot overwrite it.
    temp = cache / (digest + "." + uuid.uuid4().hex + ".extracting")
    temp.mkdir(mode=0o700)
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            out = temp / info.filename
            out.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, out.open("xb") as dest:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    dest.write(block)
    verify_cache(temp)
    temp.rename(target)
    return target, digest


def discover_runs(folder):
    runs = []
    for path in sorted(Path(folder).rglob("run.json")):
        if path.is_symlink():
            raise ValueError("Symlink artifact")
        info = read_json(path)
        artifact_period_gate(info)
        if info.get("phase") == "completed":
            runs.append((path.parent, info))
    return runs
