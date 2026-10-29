#!/usr/bin/env python3
"""Host telemetry collector: small, public SAFE snapshot; no credentials/network API.

Writes only aggregate utilization & graph samples into the dashboard's existing
read-only data volume. All measurements come from kernel counters. No shell,
hostname, NIC names, process names, IPs, mount paths or secrets are published.
"""
from __future__ import annotations
import argparse
import json
import math
import os
import secrets
import shutil
import signal
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

OUTPUT=Path(os.environ.get("TELEMETRY_OUTPUT","/srv/ai-data/market-dashboard/server-telemetry.json"))
INTERVAL=60
HISTORY_LIMIT=720
RUN=True

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def cpu_counters():
    with open("/proc/stat",encoding="ascii") as f:
        parts=f.readline().split()
    if not parts or parts[0]!="cpu":
        raise ValueError("PROC_STAT_NOT_AVAILABLE")
    values=[int(x) for x in parts[1:9]]
    idle=values[3]+values[4]
    return sum(values), idle

def mem_percent():
    info={}
    with open("/proc/meminfo",encoding="ascii") as f:
        for line in f:
            k,_,v=line.partition(":")
            if k in ("MemTotal","MemAvailable"):
                info[k]=int(v.strip().split()[0])*1024
    total=info["MemTotal"]
    avail=info["MemAvailable"]
    return round((total-avail)*100/total,1),round(total/1024**3,2),round((total-avail)/1024**3,2)

def network_counters():
    # Hardware-backed UP interfaces only; don't sum a physical NIC + VPN/bridge.
    root=Path("/sys/class/net")
    rx=tx=0
    found=0
    for interface in root.iterdir():
        name=interface.name
        if name=="lo" or not (interface/"device").exists():
            continue
        try:
            if (interface/"operstate").read_text().strip()!="up":
                continue
            rx+=int((interface/"statistics/rx_bytes").read_text())
            tx+=int((interface/"statistics/tx_bytes").read_text())
            found+=1
        except (OSError,ValueError):
            continue
    return (rx,tx) if found else None

def disk_usage(root):
    stat=shutil.disk_usage(root)
    return round(stat.used*100/stat.total,1),round(stat.used/1024**3,2),round(stat.total/1024**3,2)

def find_rapl():
    # energy_uj is CPU/package energy; not power measured at wall outlet.
    root=Path("/sys/class/powercap")
    if not root.exists():
        return None
    for entry in root.glob("intel-rapl:*"):
        if not (entry/"energy_uj").is_file() or not (entry/"max_energy_range_uj").is_file():
            continue
        try:
            label=(entry/"name").read_text().strip().lower()
            if "package" in label:
                return entry
        except OSError:
            continue
    return None

def rapl_counters(folder):
    if folder is None:
        return None
    try:
        return (int((folder/"energy_uj").read_text()),
                int((folder/"max_energy_range_uj").read_text()))
    except (OSError,ValueError):
        return None

def compute_cpu(prev,curr):
    if prev is None:
        return None
    diff=curr[0]-prev[0]
    idle=curr[1]-prev[1]
    if diff<=0 or idle<0:
        return None
    return round(max(0,min(100,100*(diff-idle)/diff)),1)

def compute_rate(prev,curr,elapsed):
    if prev is None or curr is None or elapsed<=0:
        return None,None
    if curr[0]<prev[0] or curr[1]<prev[1]:
        return None,None
    # Units: Mbps, measured bytes of physical UP interfaces.
    return round((curr[0]-prev[0])*8/elapsed/1e6,3),round((curr[1]-prev[1])*8/elapsed/1e6,3)

def compute_package_power(prev,curr,elapsed):
    if prev is None or curr is None or elapsed<=0:
        return None
    old,old_range=prev
    new,new_range=curr
    if old_range!=new_range or new_range<=0:
        return None
    difference=new-old
    if difference<0:
        difference+=new_range
    if difference<0:
        return None
    watts=difference/1e6/elapsed
    return round(watts,2) if math.isfinite(watts) and 0<=watts<=300 else None

def read_previous():
    try:
        if OUTPUT.is_symlink():
            return []
        data=json.loads(OUTPUT.read_text(encoding="utf-8"))
        rows=data.get("history",[]) if isinstance(data,dict) else []
        if not isinstance(rows,list):
            return []
        return rows[-HISTORY_LIMIT:]
    except (OSError,ValueError):
        return []

def write_atomic(obj):
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    if OUTPUT.is_symlink():
        raise RuntimeError("OUTPUT_SYMLINK_REJECTED")
    tmp=OUTPUT.with_name(OUTPUT.name+"."+secrets.token_hex(8)+".tmp")
    fd=os.open(tmp,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as f:
            json.dump(obj,f,ensure_ascii=False,allow_nan=False,separators=(",",":"))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp,OUTPUT)
        os.chmod(OUTPUT,0o644) # mounted public data inside read-only API container
    finally:
        tmp.unlink(missing_ok=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--once",action="store_true")
    parser.add_argument("--output",default=None)
    args=parser.parse_args()
    global OUTPUT,RUN
    if args.output:
        OUTPUT=Path(args.output)
    history=deque(read_previous(),maxlen=HISTORY_LIMIT)
    prev_cpu=prev_net=prev_energy=None
    prev_clock=None
    power_source=find_rapl()
    def stop(*_):
        global RUN
        RUN=False
    for signum in (signal.SIGINT,signal.SIGTERM):
        signal.signal(signum,stop)
    while RUN:
        started=time.monotonic()
        try:
            current_cpu=cpu_counters()
            current_net=network_counters()
            current_energy=rapl_counters(power_source)
            cpu=compute_cpu(prev_cpu,current_cpu)
            elapsed=started-prev_clock if prev_clock is not None else 0
            rx,tx=compute_rate(prev_net,current_net,elapsed)
            package_w=compute_package_power(prev_energy,current_energy,elapsed)
            memory_pct,memory_total,memory_used=mem_percent()
            disk_pct,disk_used,disk_total=disk_usage("/")
            data_pct,data_used,data_total=disk_usage("/srv/ai-data")
            sample={
                "at":now_iso(),
                "cpuPct":cpu,"memoryPct":memory_pct,
                "networkRxMbps":rx,"networkTxMbps":tx,
                "diskPct":disk_pct,"dataDiskPct":data_pct,
                "cpuPackageWatts":package_w
            }
            history.append(sample)
            snapshot={
                "schemaVersion":1,"mode":"PUBLIC_READ_ONLY_AGGREGATED",
                "updatedAt":sample["at"],"samplingSeconds":INTERVAL,
                "metrics":{
                    **sample,
                    "memoryUsedGiB":memory_used,"memoryTotalGiB":memory_total,
                    "diskUsedGiB":disk_used,"diskTotalGiB":disk_total,
                    "dataDiskUsedGiB":data_used,"dataDiskTotalGiB":data_total,
                    "systemWatts":None,
                    "systemPowerNote":"전체 서버 소비전력은 스마트플러그/계측기 연결 전까지 측정 불가",
                    "cpuPowerNote":"CPU 패키지 RAPL 추정 전력(가능할 때만 표시). 시스템 전체 전력 아님"
                },
                "history":list(history)
            }
            # Only after an unambiguous sample.
            write_atomic(snapshot)
            prev_cpu,prev_net,prev_energy,prev_clock=current_cpu,current_net,current_energy,started
        except (OSError,ValueError,KeyError) as exc:
            print("TELEMETRY_COLLECT_ERROR="+type(exc).__name__,flush=True)
        if args.once:break
        time.sleep(max(0.1,INTERVAL-(time.monotonic()-started)))

if __name__=="__main__":
    main()
