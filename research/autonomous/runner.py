import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time
import traceback

from .config import ROOT, LoopConfig
from .artifact_reader import discover_runs, sha256, verify_cache
from .diagnostics import BaselineCandidateError, identify_baseline, failure_analysis, validation_metrics
from .experiment_planner import make_plan
from .experiment_registry import Registry, read_json, write_json, utc
from .export_client import ExportClient
from .quarterly import build_shortlist_v08
from .report import create_report
from .safety_gate import plan_gate, resource_gate, promotion_gate
from research_export import sanitize


def git(*args):
    proc = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=True)
    return proc.stdout.strip()


def clean_environment():
    env = dict(os.environ)
    env.pop("RESEARCH_EXPORT_TOKEN", None)
    return env


def code_fingerprints():
    paths = list((ROOT / "research/evolution").glob("*.py")) + list((ROOT / "research/autonomous").glob("*.py"))
    paths += [ROOT / p for p in ("research/backtest_intraday_v4.py", "research/backtest_v4_strategies.py",
                                "research/build_v4_data_manifest.py", "research/intraday_v4_engine.py")]
    return {str(p.relative_to(ROOT)): sha256(p) for p in sorted(paths)}


def process_identity(pid):
    try:
        # starttime + boot ID + command, not a PID-only liveness check.
        root = Path(f"/proc/{int(pid)}")
        fields = (root / "stat").read_text().rsplit(") ", 1)[1].split()
        if fields[0] == "Z": return None
        return {"pid": int(pid), "startTicks": fields[19],
                "bootId": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                "commandHash": __import__("hashlib").sha256((root / "cmdline").read_bytes()).hexdigest()}
    except (OSError, ValueError, IndexError):
        return None


def active_stage(folder):
    record = folder / "stage_process.json"
    if not record.exists(): return None
    old = read_json(record)
    return old if old.get("identity") and process_identity(old["identity"]["pid"]) == old["identity"] else None


def choose_valid_v07_baseline(client, catalog, candidates):
    rejected = []
    ordered = sorted(candidates, key=lambda row: (row.get("updatedAt") or "", row.get("id") or ""), reverse=True)
    for candidate in ordered:
        bundle, bundle_hash, manifest = client.collect("V07_FULL_RESEARCH_BUNDLE", [candidate["id"]], catalog)
        try:
            baseline = identify_baseline(discover_runs(bundle))
        except BaselineCandidateError as exc:
            rejected.append({
                "catalogId": candidate.get("id"),
                "runId": candidate.get("runId"),
                "updatedAt": candidate.get("updatedAt"),
                **exc.as_dict(),
            })
            continue
        return {
            "candidate": candidate,
            "bundle": bundle,
            "bundleHash": bundle_hash,
            "manifest": manifest,
            "baseline": baseline,
            "rejected": rejected,
        }
    return {"candidate": None, "bundle": None, "bundleHash": None, "manifest": None,
            "baseline": None, "rejected": rejected}


def prepare_iteration(registry, folder, config, client=None):
    from .hypothesis import hypothesis
    registry.update(folder, "downloading", **hypothesis(), parentVersion="v07", candidateVersion="v08")
    resource_gate(config)
    client = client or ExportClient(config)
    catalog = client.json("/api/research/export/catalog?researchOnly=true")
    summary = client.json("/api/research/export/summary?format=json&researchOnly=true")
    if any(v.get("researchBoundary") != "FINAL_OOS_EXCLUDED" for v in (catalog, summary)):
        raise ValueError("EXPORT_BACKEND_RESEARCH_BOUNDARY_REQUIRED")
    allowed = [r for r in catalog.get("runs", []) if not any(
        p and str(p[1])[:4] >= "2026" for p in [r.get("evaluationPeriod"), r.get("validationPeriod")])]
    if not allowed:
        raise ValueError("NO_PRE_2026_EVOLUTION_ARTIFACTS")
    ids = [r["id"] for r in allowed]
    summary_folder, summary_hash, _ = client.collect("evolution_summary", ids, catalog)
    runs = discover_runs(summary_folder)
    completed_ids = {info["runId"] for _, info in runs}
    # Daily holdout metrics are intentionally absent from Summary Export.
    v07 = [r for r in allowed if r.get("isV07") and r.get("runId") in completed_ids and str((r.get("evaluationPeriod") or [""])[0]).startswith("2025")
           and r.get("selectionMode") == "V07_INTERNAL_HOLDOUT_SHORTLIST"]
    if not v07:
        raise ValueError("V07_FULL_RESEARCH_BUNDLE_REQUIRED")
    choice = choose_valid_v07_baseline(client, catalog, v07)
    write_json(folder / "baseline_selection.json", {
        "policy": "NEWEST_STRICTLY_VALID_V07_LINEAGE",
        "candidateCount": len(v07),
        "selectedRunId": choice["candidate"].get("runId") if choice["candidate"] else None,
        "selectedCatalogId": choice["candidate"].get("id") if choice["candidate"] else None,
        "rejected": choice["rejected"],
        "criteriaRelaxed": False,
    })
    if choice["baseline"] is None:
        registry.update(folder, "analyzing", baselineCandidates=len(v07),
                        baselineRejected=len(choice["rejected"]))
        raise ValueError("NO_VALID_V07_BASELINE")
    bundle, bundle_hash, manifest = choice["bundle"], choice["bundleHash"], choice["manifest"]
    baseline = choice["baseline"]
    registry.update(folder, "analyzing", parentVersion="v07", candidateVersion="v08",
                    baselineRunId=choice["candidate"].get("runId"),
                    baselineCandidates=len(v07), baselineRejected=len(choice["rejected"]))
    write_json(folder / "diagnostics.json", failure_analysis([r for r in summary.get("rows", []) if r.get("id") in ids]))
    write_json(folder / "source_manifest.json", {"manifestHash": bundle_hash, "summaryManifestHash": summary_hash,
               "cacheDirectory": str(bundle), "manifest": manifest})
    source_hashes = {}
    for stage in ("training", "holdout", "development"):
        source_hashes[stage] = {"path": str(baseline[stage]),
                                "runSha256": sha256(baseline[stage] / "run.json")}
    write_json(folder / "source_runs.json", source_hashes)
    write_json(folder / "baseline_metrics.json", baseline["metrics"])
    registry.update(folder, "planning")
    plan = make_plan(baseline, config)
    write_json(folder / "plan.json", plan)
    input_manifest = read_json(baseline["training"] / "run.json").get("inputManifest")
    manifest_hash = __import__("hashlib").sha256(json.dumps(input_manifest, sort_keys=True).encode()).hexdigest() if input_manifest else None
    registry.update(folder, "planning", **plan, gitCommitBefore=git("rev-parse", "HEAD"),
                    dataManifestHash=manifest_hash, artifactManifestHash=bundle_hash)
    # Dry-run may compute quarterly diagnostics but must not create a shortlist/code.
    from .quarterly import quarterly_metrics, select_candidates
    quarter_rows = quarterly_metrics(baseline["training"], baseline["holdout"])
    write_json(folder / "quarterly_diagnostics.json", {"bots": quarter_rows})
    selected = select_candidates(quarter_rows, config.max_candidates, config.min_candidates)
    return registry.update(folder, "planning", selectedCandidates=len(selected))


def verify_sources(folder):
    source = read_json(folder / "source_manifest.json")
    cache = Path(source["cacheDirectory"])
    if sha256(cache / "export_manifest.json") != source["manifestHash"]:
        raise ValueError("SOURCE_MANIFEST_CHANGED")
    verify_cache(cache)
    stages = read_json(folder / "source_runs.json")
    for row in stages.values():
        p = Path(row["path"])
        if cache not in p.parents or sha256(p / "run.json") != row["runSha256"]:
            raise ValueError("SOURCE_ARTIFACT_CHANGED")
    return {key: Path(row["path"]) for key, row in stages.items()}


def run_tests(folder):
    started = time.monotonic()
    command = [sys.executable, "-m", "unittest", "discover", "-s", "research", "-p", "test_*.py"]
    commands = [command] + [["node", str(p.relative_to(ROOT))] for p in sorted((ROOT / "research").glob("test_*_smoke.cjs"))]
    records = []
    for cmd in commands:
        proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True, env=clean_environment())
        records.append({"command": cmd, "returnCode": proc.returncode, "output": proc.stdout + proc.stderr})
        if proc.returncode: break
    raw = "\n".join(r["output"] for r in records)
    token = os.environ.get("RESEARCH_EXPORT_TOKEN")
    if token: raw = raw.replace(token, "[REDACTED]")
    write_json(folder / "test_results.json", {"commands": [r["command"] for r in records], "returnCode": proc.returncode,
               "runtimeSeconds": time.monotonic() - started, "output": sanitize(raw[-64000:]),
               "gitCommit": git("rev-parse", "HEAD")})
    if proc.returncode:
        raise ValueError("TESTS_FAILED")


def run_validation(registry, folder, config):
    output = folder / "validation_run.json"
    if output.exists():
        record = read_json(output)
        path = Path(record["path"])
        info = read_json(path / "run.json")
        if folder not in path.parents or info.get("phase") != "completed" or info.get("sourceRunId") != "shortlist-v08" or info.get("fromDate") != "2025-01-02" or info.get("toDate") != "2025-12-31":
            raise ValueError("Invalid validation checkpoint")
        return path
    # A completed attempt may survive a worker crash before the pointer write.
    completed = [(p, i) for p, i in discover_runs(folder / "validation-attempts")
                 if i.get("evaluationMode") == "FROZEN_VALIDATION"]
    if len(completed) > 1:
        raise ValueError("MULTIPLE_COMPLETED_ATTEMPTS_REVIEW_REQUIRED")
    if completed:
        p, i = completed[0]
        if i.get("sourceRunId") != "shortlist-v08" or i.get("fromDate") != "2025-01-02" or i.get("toDate") != "2025-12-31":
            raise ValueError("Recovered validation lineage/period mismatch")
        write_json(output, {"path": str(p), "run": i})
        return p
    resource_gate(config)
    if not config.data_manifest and not config.provisional:
        raise ValueError("REVIEWED_MANIFEST_REQUIRED")
    active = active_stage(folder)
    if active:
        child = None
        identity = active["identity"]
    else:
        attempt = len(list((folder / "validation-attempts").glob("*/run.json"))) + 1
        log = folder / f"stage-{attempt:04d}.log"
        with log.open("xb") as stream:
            child = subprocess.Popen([sys.executable, str(ROOT / "research/autonomous/stage_worker.py"),
                                     "--iteration", str(folder)], cwd=ROOT,
                                     stdout=stream, stderr=subprocess.STDOUT, env=clean_environment(), start_new_session=True)
        identity = process_identity(child.pid)
        write_json(folder / "stage_process.json", {"identity": identity, "startedAt": utc(), "attempt": attempt})
    registry.update(folder, "development_validation", validationProgress=0)
    while (child.poll() is None if child else process_identity(identity["pid"]) == identity):
        attempts = sorted((folder / "validation-attempts").glob("*/status.json"))
        if attempts:
            state = read_json(attempts[-1])
            requested = state.get("requestedSessions") or 0
            registry.update(folder, "development_validation", validationProgress=
                            100 * (state.get("completedSessions") or 0) / requested if requested else 0)
        time.sleep(5)
    if not output.exists():
        # Never overwrite failed attempt directories on retry.
        raise ValueError("DEVELOPMENT_VALIDATION_FAILED: inspect retained stage log/attempt; --resume retries in a fresh directory")
    return Path(read_json(output)["path"])


def candidate_git(folder, plan, *, commit=False):
    if git("branch", "--show-current") != "strategy-engine-v3":
        raise ValueError("RESEARCH_BRANCH_REQUIRED")
    relative = plan["expectedChangedFiles"][0]
    p = ROOT / relative
    if not commit:
        # Only the fixed template is allowed to change; never overwrite a version.
        p.parent.mkdir(exist_ok=True)
        template = {k: plan[k] for k in ("candidateVersion", "selectionDataYear", "selectionRule", "changedVariables")}
        if p.exists() and read_json(p) != template:
            raise ValueError("CANDIDATE_CONFIG_ALREADY_EXISTS_WITH_DIFFERENT_RULE")
        if not p.exists(): write_json(p, template)
        proc = subprocess.run(["git", "diff", "--no-index", "/dev/null", str(p)], text=True, capture_output=True)
        (folder / "code_diff.patch").write_text(proc.stdout, encoding="utf-8")
        return
    status = git("status", "--porcelain")
    for line in status.splitlines():
        if line[3:] not in (relative, "research/autonomous/candidates/"):
            raise ValueError("UNRELATED_WORKTREE_CHANGES: commit/push paused; research artifacts preserved")
    # Empty index only; never include user-staged changes.
    if git("diff", "--cached", "--name-only"):
        raise ValueError("USER_STAGED_CHANGES: automatic commit blocked")
    if git("status", "--porcelain", "--", relative):
        git("add", "--", relative)
        git("commit", "-m", "research: evolution v08 quarterly holdout stability", "--", relative)
    write_json(folder / "git_result.json", {"gitCommitAfter": git("rev-parse", "HEAD"), "uiStatus": "UI_UPDATE_READY"})
    git("push", "origin", "HEAD:refs/heads/strategy-engine-v3")


def latest_completed_dry_run(registry):
    for folder in reversed(registry.iterations()):
        info=read_json(folder / "experiment.json")
        if info.get("state")=="completed" and info.get("dryRun") is True:
            return folder,info
    return None,None


def clone_reviewed_dry_run(registry, source):
    src_info=read_json(source / "experiment.json")
    if src_info.get("state")!="completed" or src_info.get("dryRun") is not True:
        raise ValueError("DRY_RUN_REVIEW_REQUIRED")
    folder=registry.create()
    for name in (
        "config.json","source_manifest.json","source_runs.json","baseline_metrics.json",
        "plan.json","quarterly_diagnostics.json","hypothesis.md",
    ):
        src=source/name
        if src.is_file() and not src.is_symlink():
            shutil.copy2(src,folder/name)
    fields={
        "dryRun":False,
        "dryRunReviewed":True,
        "reviewedDryRunIteration":src_info.get("iteration"),
        "parentVersion":src_info.get("parentVersion"),
        "candidateVersion":src_info.get("candidateVersion"),
        "hypothesis":src_info.get("hypothesis"),
        "decision":None,
        "error":None,
        "failureCode":None,
        "finishedAt":None,
    }
    return folder,registry.update(folder,"queued",**fields)


def execute_iteration(registry, folder, config):
    plan = read_json(folder / "plan.json")
    plan_gate(plan)
    sources = verify_sources(folder)
    fingerprints = code_fingerprints()
    code_record = folder / "code_fingerprints.json"
    if code_record.exists() and read_json(code_record) != fingerprints:
        raise ValueError("RESEARCH_CODE_CHANGED: resume requires the exact tested implementation")
    if not code_record.exists(): write_json(code_record, fingerprints)
    if git("branch", "--show-current") != "strategy-engine-v3":
        raise ValueError("RESEARCH_BRANCH_REQUIRED")
    resource_gate(config)
    registry.update(folder, "implementing", dryRun=False, codeChanged=True)
    candidate_git(folder, plan)
    registry.update(folder, "testing")
    run_tests(folder)
    registry.update(folder, "training", trainingProgress=100)
    write_json(folder / "training_run.json", {"mode": "REUSED_FIXED_V07", "path": str(sources["training"]),
               "sha256": sha256(sources["training"] / "final_population.json")})
    registry.update(folder, "internal_holdout", holdoutProgress=100)
    write_json(folder / "holdout_run.json", {"mode": "REUSED_VERIFIED_DAILY_2024", "path": str(sources["holdout"])})
    registry.update(folder, "shortlisting")
    shortlist = folder / "shortlist-v08"
    if not shortlist.exists():
        build_shortlist_v08(sources["training"], sources["holdout"], shortlist,
                            config.max_candidates, config.min_candidates)
    elif not (shortlist / "run.json").exists():
        raise ValueError("INTERRUPTED_SHORTLIST: retain partial directory; create a new iteration after inspection")
    else:
        from .quarterly import quarterly_metrics, select_candidates
        rows = select_candidates(quarterly_metrics(sources["training"], sources["holdout"]),
                                 config.max_candidates, config.min_candidates)
        by_id = {r["bot_id"]: r for r in read_json(sources["training"] / "final_population.json")}
        if read_json(shortlist / "final_population.json") != [by_id[r["botId"]] for r in rows]:
            raise ValueError("SHORTLIST_CHECKPOINT_CHANGED")
    validation = run_validation(registry, folder, config)
    if code_fingerprints() != fingerprints:
        raise ValueError("RESEARCH_CODE_CHANGED_DURING_EXPERIMENT")
    registry.update(folder, "diagnostics")
    validation_info = read_json(validation / "run.json")
    if validation_info.get("sourcePopulationFileHash") != sha256(shortlist / "final_population.json") or validation_info.get("sourcePopulationVerified") is not True or validation_info.get("inputHashesVerified") is not True:
        raise ValueError("VALIDATION_PROVENANCE_MISMATCH")
    diag = validation_metrics(read_json(validation / "validation_summary.json"),
                              read_json(shortlist / "final_population.json"),
                              read_json(sources["holdout"] / "validation_summary.json"))
    write_json(folder / "diagnostics.json", diag)
    registry.update(folder, "comparing")
    comparison = promotion_gate(read_json(folder / "baseline_metrics.json"), diag)
    baseline_info = read_json(sources["development"] / "run.json")
    if validation_info.get("dataMode") != baseline_info.get("dataMode") or validation_info.get("symbols") != baseline_info.get("symbols") or validation_info.get("timestampKind") != baseline_info.get("timestampKind"):
        comparison.update(decision="INCONCLUSIVE", reasons=["INCOMPARABLE_DATA_MODE_UNIVERSE_OR_TIMESTAMP"])
    write_json(folder / "comparison.json", comparison)
    write_json(folder / "decision.json", comparison)
    candidate_git(folder, plan, commit=True)
    final = registry.update(folder, "completed", decision=comparison["decision"],
                            positiveBots=diag["positiveBots"], validationMean=diag["meanValidationReturn"],
                            validationMedian=diag["medianValidationReturn"], avgTrades=diag["meanTradesPerBot"],
                            finishedAt=utc(), gitCommitAfter=git("rev-parse", "HEAD"))
    create_report(folder, final, comparison, diag)
    return final


def loop(config, *, dry_run=False, resume=False, client=None):
    registry = Registry(config.registry)
    if not dry_run and not resume:
        raise ValueError("DRY_RUN_REVIEW_REQUIRED: complete --dry-run, then explicitly --resume")
    completed = []
    for _ in range(config.max_iterations):
        old = registry.iterations()
        if not dry_run and any(
            read_json(p / "experiment.json").get("candidateVersion") == "v08"
            and read_json(p / "experiment.json").get("state") == "completed"
            and not read_json(p / "experiment.json").get("dryRun")
            for p in old
        ):
            break  # No repeated mining of the same 2025 interval.
        if dry_run:
            folder = registry.create()
        elif resume:
            latest = old[-1] if old else None
            prior = read_json(latest / "experiment.json") if latest else {}
            if latest and prior.get("dryRunReviewed") and prior.get("dryRun") is not True and prior.get("state") in {
                "queued","implementing","testing","training","internal_holdout","shortlisting",
                "development_validation","diagnostics","comparing","failed"
            }:
                folder=latest
                registry.update(folder,"queued",dryRunReviewed=True)
            else:
                dry_folder,dry_info=latest_completed_dry_run(registry)
                if dry_folder is None:
                    raise ValueError("DRY_RUN_REVIEW_REQUIRED")
                folder,_=clone_reviewed_dry_run(registry,dry_folder)
        else:
            raise ValueError("DRY_RUN_REVIEW_REQUIRED")
        try:
            settings = {"dataDir": str(config.data_dir),
                       "dataManifest": str(config.data_manifest) if config.data_manifest else None,
                       "provisional": config.provisional, "naiveTimezone": config.naive_timezone,
                       "minCandidates": config.min_candidates, "maxCandidates": config.max_candidates,
                       "researchOnly": True}
            if (folder / "stage_process.json").exists() and read_json(folder / "config.json") != settings:
                raise ValueError("RESUME_CONFIG_CHANGED_AFTER_VALIDATION_LAUNCH")
            write_json(folder / "config.json", settings)
            registry.update(folder, read_json(folder / "experiment.json")["state"], workerIdentity=process_identity(os.getpid()))
            if not (folder / "plan.json").exists():
                prepare_iteration(registry, folder, config, client)
            plan_gate(read_json(folder / "plan.json"))
            (folder / "hypothesis.md").write_text(read_json(folder / "plan.json")["hypothesis"] + "\n", encoding="utf-8")
            if dry_run:
                value = registry.update(folder, "completed", dryRun=True, decision="DRY_RUN_ONLY",
                                        finishedAt=utc(), codeChanged=False, trainingStarted=False)
                create_report(folder, value)
                completed.append(value)
                break
            value = execute_iteration(registry, folder, config)
            completed.append(value)
            # Only V08 is registered. Additional iterations need a distinct,
            # reviewed structural implementation, never automatic threshold search.
            write_json(folder / "next_iteration.json", {"decision": "STOP_HYPOTHESIS_REQUIRED"})
            break
        except Exception as exc:
            token = os.environ.get("RESEARCH_EXPORT_TOKEN")
            reason = str(exc).replace(token, "[REDACTED]") if token else str(exc)
            prefix = reason.split(":", 1)[0]
            failure_code = prefix if __import__("re").fullmatch(r"[A-Z][A-Z0-9_]+", prefix) else "RESEARCH_FAILURE"
            previous_state = read_json(folder / "experiment.json").get("state")
            trace = traceback.format_exc()
            if token:
                trace = trace.replace(token, "[REDACTED]")
            trace = sanitize(trace[-32000:])
            try:
                (folder / "error_traceback.log").write_text(str(trace), encoding="utf-8")
            except OSError:
                pass
            value = registry.update(folder, "failed", error=sanitize(reason), failureCode=failure_code,
                                    lastSuccessfulState=previous_state, finishedAt=utc())
            print(json.dumps({
                "event":"autonomous_failure",
                "iteration":value.get("iteration"),
                "failureCode":failure_code,
                "lastSuccessfulState":previous_state,
                "error":sanitize(reason),
            },ensure_ascii=False,allow_nan=False,sort_keys=True),flush=True)
            create_report(folder, value, failure=reason)
            raise
    return completed
