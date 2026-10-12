"""Read-only, bounded Linux host diagnostics for authenticated dashboard admins.

No cloud credentials, shell evaluation, file contents from app secrets, process
command lines or arbitrary path queries. Gracefully reports unavailable metrics.
"""
import datetime
import os
import re
import subprocess
import time
from pathlib import Path

SERVICE_NAMES = (
    "market-career-dashboard.service",
    "research-collection-scheduler.service",
    "toss-token-broker.service",
    "collect-toss-ticks.service",
    "collect-expanded-universe.service",
)
PSEUDO_FS = frozenset(("tmpfs", "devtmpfs", "squashfs", "proc", "sysfs", "devpts",
                       "cgroup", "cgroup2", "securityfs", "pstore", "debugfs",
                       "tracefs", "configfs", "mqueue", "hugetlbfs", "ramfs"))
TIMEOUT = 3


def _run(*args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT, check=False)
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _meminfo():
    raw = Path("/proc/meminfo").read_text(encoding="ascii")
    values = {}
    for line in raw.splitlines():
        match = re.match(r"^([A-Za-z_()]+):\s+(\d+)\s+kB$", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    sw_total = values.get("SwapTotal", 0)
    sw_free = values.get("SwapFree", 0)
    return {
        "totalBytes": total,
        "availableBytes": available,
        "usedBytes": max(0, total - available),
        "usedPct": round(100 * (total - available) / total, 1) if total else None,
        "swapTotalBytes": sw_total,
        "swapUsedBytes": max(0, sw_total - sw_free),
        "swapUsedPct": round(100 * (sw_total - sw_free) / sw_total, 1) if sw_total else None,
    }


def _cpu_times():
    first = Path("/proc/stat").read_text(encoding="ascii").splitlines()[0]
    parts = first.split()
    if parts[0] != "cpu" or len(parts) < 5:
        raise ValueError("no CPU counters")
    counters = [int(x) for x in parts[1:]]
    total = sum(counters)
    idle = counters[3] + (counters[4] if len(counters) > 4 else 0)
    return total, idle


def _cpu_pct():
    before, before_idle = _cpu_times()
    time.sleep(0.15)
    after, after_idle = _cpu_times()
    elapsed = after - before
    if elapsed <= 0:
        return None
    return round(max(0, min(100, (elapsed - (after_idle - before_idle)) * 100 / elapsed)), 1)


def _mounts():
    """All mounted local filesystems, not only '/' or one research folder."""
    output = _run("df", "-P", "-B1", "-T", "-l")
    records = []
    for line in output.splitlines()[1:]:
        parts = line.split()
        # coreutils df: Filesystem Type 1B-blocks Used Available Capacity Mounted-on
        if len(parts) != 7:
            continue
        device, fstype, total, used, available, percent, mount = parts
        if fstype in PSEUDO_FS:
            continue
        if not (total.isdecimal() and used.isdecimal() and available.isdecimal()):
            continue
        if not re.fullmatch(r"\d+(?:\.\d+)?%", percent):
            continue
        if not mount.startswith("/") or mount.startswith(("/proc/", "/sys/", "/run/")):
            continue
        records.append({
            "device": device[:100], "type": fstype[:40], "mount": mount[:160],
            "totalBytes": int(total), "usedBytes": int(used),
            "availableBytes": int(available), "usedPct": float(percent[:-1])
        })
        if len(records) >= 32:
            break
    return records


def _block_devices():
    """Include attached but not mounted disks as separate rows."""
    output = _run("lsblk", "-b", "-n", "-P", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT")
    items = []
    for line in output.splitlines():
        fields = dict(re.findall(r'([A-Z]+)="([^"]*)"', line))
        if fields.get("TYPE") not in ("disk", "part", "lvm"):
            continue
        try:
            size = int(fields.get("SIZE") or "0")
        except ValueError:
            continue
        if size <= 0:
            continue
        items.append({
            "name": fields.get("NAME", "")[:80],
            "type": fields.get("TYPE", "")[:16],
            "filesystem": fields.get("FSTYPE", "")[:32],
            "mount": fields.get("MOUNTPOINT", "")[:160],
            "sizeBytes": size,
        })
        if len(items) >= 32:
            break
    return items


def _services():
    services = []
    for name in SERVICE_NAMES:
        state = _run("systemctl", "is-active", name).strip()
        # systemctl is-active exits nonzero for inactive services, so read stdout
        # from the subprocess separately instead of treating them as missing.
        if not state:
            try:
                result = subprocess.run(["systemctl", "is-active", name],
                                        capture_output=True, text=True,
                                        timeout=TIMEOUT, check=False)
                state = result.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                state = "unknown"
        if state not in ("active", "inactive", "failed", "activating", "deactivating"):
            state = "unknown"
        services.append({"name": name, "state": state})
    return services


def _network():
    rx = tx = 0
    try:
        lines = Path("/proc/net/dev").read_text(encoding="ascii").splitlines()[2:]
    except OSError:
        return {"receivedBytes": None, "sentBytes": None}
    for line in lines:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        name = name.strip()
        if name == "lo" or name.startswith(("veth", "docker", "br-", "tun", "tap")):
            continue
        columns = values.split()
        if len(columns) >= 16:
            try:
                rx += int(columns[0])
                tx += int(columns[8])
            except ValueError:
                pass
    return {"receivedBytes": rx, "sentBytes": tx}


def snapshot():
    """No mutation, no AWS calls, no app/database reads; admin auth is enforced by caller."""
    errors = []
    try:
        cpu_percent = _cpu_pct()
    except (OSError, ValueError):
        cpu_percent = None
        errors.append("CPU_METRICS_UNAVAILABLE")
    try:
        load = [round(v, 2) for v in os.getloadavg()]
    except OSError:
        load = []
    try:
        memory = _meminfo()
    except (OSError, ValueError):
        memory = {}
        errors.append("MEMORY_METRICS_UNAVAILABLE")
    try:
        uptime = max(0, int(float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])))
    except (OSError, ValueError, IndexError):
        uptime = None
    disks = _mounts()
    if not disks:
        errors.append("MOUNT_METRICS_UNAVAILABLE")
    devices = _block_devices()
    warnings = []
    if memory.get("usedPct") is not None and memory["usedPct"] >= 90:
        warnings.append("MEMORY_PRESSURE")
    if cpu_percent is not None and cpu_percent >= 90:
        warnings.append("HIGH_CPU_SAMPLE")
    for mount in disks:
        if mount["usedPct"] >= 85:
            warnings.append("DISK_PRESSURE")
            break
    return {
        "ok": True, "sampledAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "cpu": {"usedPct": cpu_percent, "cores": os.cpu_count(), "loadAverage": load},
        "memory": memory, "uptimeSeconds": uptime,
        "mounts": disks, "devices": devices, "services": _services(),
        "network": _network(), "warnings": warnings, "unavailable": errors,
        "note": "CPU is a short host sample; network values are since boot; disk volumes are Linux mounts/devices, not AWS cloud utilization.",
    }
