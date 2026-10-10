"""Resource-aware population scaling for Evolution V0.1.

This module never creates swap or changes OS settings. It only observes host
resources and returns a bounded population recommendation.
"""
from __future__ import annotations

import math
import os

import psutil


def sample_resources(session_runtime_seconds=None):
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    process = psutil.Process(os.getpid())
    with process.oneshot():
        rss = process.memory_info().rss
        process_cpu = process.cpu_percent(interval=None)
    return {
        "cpuPct": float(psutil.cpu_percent(interval=None)),
        "processCpuPct": float(process_cpu),
        "memoryPct": float(vm.percent),
        "memoryAvailableGb": float(vm.available) / (1024 ** 3),
        "swapPct": float(swap.percent) if swap.total else 0.0,
        "swapUsedGb": float(swap.used) / (1024 ** 3),
        "swapTotalGb": float(swap.total) / (1024 ** 3),
        "processRssMb": float(rss) / (1024 ** 2),
        "sessionRuntimeSeconds": None if session_runtime_seconds is None else float(session_runtime_seconds),
    }


def decide_population(current_population, snapshot, config, *, enabled):
    current = int(current_population)
    if current < 1:
        raise ValueError("current_population must be positive")
    if not enabled:
        return {
            "resourceDecision": "HOLD_DISABLED",
            "nextPopulation": current,
            "resourcePressure": "DISABLED",
        }

    memory = float(snapshot.get("memoryPct") or 0.0)
    swap = float(snapshot.get("swapPct") or 0.0)
    cpu = float(snapshot.get("cpuPct") or 0.0)

    hard = memory >= config.memory_hard_limit_pct or swap >= config.swap_hard_limit_pct
    soft = memory >= config.memory_soft_limit_pct or swap >= config.swap_soft_limit_pct
    cpu_busy = cpu >= config.cpu_soft_limit_pct

    if hard:
        target = max(config.min_population, int(math.floor(current * config.scale_down_factor)))
        if target >= current and current > config.min_population:
            target = current - 1
        target = max(config.min_population, min(config.max_population, target))
        return {
            "resourceDecision": "SCALE_DOWN",
            "nextPopulation": target,
            "resourcePressure": "HARD",
        }

    if soft or cpu_busy:
        return {
            "resourceDecision": "HOLD",
            "nextPopulation": current,
            "resourcePressure": "SOFT",
        }

    if current >= config.max_population:
        return {
            "resourceDecision": "HOLD_MAX",
            "nextPopulation": current,
            "resourcePressure": "NORMAL",
        }

    target = int(math.ceil(current * config.scale_up_factor))
    target = max(current + 1, config.min_population, target)
    target = min(config.max_population, target)
    return {
        "resourceDecision": "SCALE_UP",
        "nextPopulation": target,
        "resourcePressure": "NORMAL",
    }
