# Autonomous operational audit / recovery

Research only. Logical paths stay unchanged; bind mounts are supported. No
collector, feature, genome, fitness, mutation or execution rule is changed by this
operational patch. END-labelled completed bars and subsequent completed-close
execution remain the existing model. Frozen evaluators never evolve the population.
2025 remains development validation, and Final OOS is CLOSED.

## Findings and evidence limits

The inspected starting commit was `67a4537`. It already uses completed Export
payload size plus bounded headroom rather than always reserving 4GiB, and retries
SOFT resource pressure for 30 seconds. Existing Adaptive Resource Guard thresholds
are unchanged. HARD pressure and insufficient disk reserve stop immediately.

Confirmed code defects:

* Frozen evaluation did not explicitly finalize `resultsValid` and did not record
  per-symbol/reference session coverage. A date present for one symbol could hide
  another missing symbol. New runs store bounded coverage samples, exact counts
  and explicit validity. Missing regular minutes invalidate coverage; no fill or
  repair is performed. Individual invalid bots remain excluded by their existing
  validity rules; their PnL does not become valid merely because coverage is valid.
* V0.7 shortlist construction previously ignored missing sessions and incomplete
  valid-day counts. It now refuses incomplete holdout metadata and excludes bots
  that did not complete the requested XNYS sessions. The positive-return ranking
  is unchanged. New shortlist run IDs include the campaign name: the repeated
  literal `shortlist` ID previously made multiple campaigns ambiguous.
* Diagnostics previously conflated calendar/data/count/result failures. They now
  include `rootCause`, XNYS expected count, missing and non-trading dates, lineage
  IDs and bounded symbol coverage. They preserve the existing error `code` for
  consumers. The runner reports the common specific root cause if all candidates
  fail for the same reason. Actual missing dates are never automatically forgiven.
* Completed Dry Run state is now immutable through Registry.update. Actual work
  clones the completed proof into a new iteration; failed attempts remain intact.
* Malformed persistent API switch settings formerly reverted to enabled defaults.
  Live and paper now load OFF on parse/schema failure. Engine OFF is attempted
  independently even if DISARM's status call errors. No order test is involved.
* The existing storage monitor measured the checkout filesystem instead of the
  minute-data mount. It now measures the minute-data path. New authenticated AI
  status reports every research filesystem separately using disk_usage and
  `/proc/self/mountinfo`, including bind source/root. No recursive size scan is
  added. Directory sizes are explicitly NOT_SCANNED rather than fabricated.

No EC2 access was available. The reported two internal-holdout failures cannot be
attributed to actual missing candles versus stale metadata from this workspace.
The metadata audit command below supplies that distinction on EC2. Historical
artifacts are never rewritten or upgraded to valid. Legacy artifacts without
per-symbol coverage retain their existing metadata checks; this is not evidence
that their raw data is complete. To establish that, use the existing read-only
minute/clock audit on the flagged dates. Exchange-calendar library version should
be checked if non-trading dates disagree with XNYS. Regenerate a new campaign only
after evidence is resolved; preserve the original campaign.

## Safe deployment (operator commands; not executed by Codex)

```bash
cd /home/ubuntu/market-career-dashboard
git status --short
git branch --show-current
git log -1 --oneline
pgrep -af '[r]un_research_loop.py|[r]un_v07_campaign.py|[e]valuate_population_v03.py|[s]tage_worker.py|[r]un_evolution_v0.py'
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git switch strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
```

If fast-forward is refused due to local commits, inspect both sides then preserve
them with `git merge --no-ff --no-edit origin/strategy-engine-v3`. Resolve conflicts
without touching runtime data. If tracked local edits block the merge, preserve
and review those edits first; do not reset/clean. Do not restart during active AI
work. After a successful merge and only when the process check is empty:

```bash
sudo systemctl restart market-career-dashboard.service
sudo systemctl status market-career-dashboard.service --no-pager
.venv/bin/python research/autonomous/audit_v07_baseline.py --results-dir /home/ubuntu/evolution-results
findmnt -T /home/ubuntu/evolution-autonomous
findmnt -T /home/ubuntu/research-exports
findmnt -T /home/ubuntu/market-career-dashboard/data/toss_1m
findmnt -T /home/ubuntu/market-career-dashboard/data/toss_ticks
df -h /home/ubuntu/evolution-autonomous /home/ubuntu/research-exports data/toss_1m data/toss_ticks
```

Audit output goes to stdout, not existing research artifacts. If it reports
BASELINE_DATA_MISSING, identify the exact dates/symbols with the read-only coverage
audit. Do not call the existing collector in place as a speculative backfill: its
current CLI does not provide a separate scratch output directory. Acquire any
replacement data into a distinct reviewed staging destination in a separately
authorized operation, verify identity/timestamps/splits/hashes, and create a new
data snapshot/campaign. No automated backfill or rewrite is provided here.

## Swap: explicit separate operator operation

```bash
bash ops/setup-research-swap.sh --check
sudo bash ops/setup-research-swap.sh --apply
swapon --show
sysctl vm.swappiness
```

The script requires an ext4 mount outside the root filesystem, 16GiB plus reserve
space, and refuses symlinks or unknown existing files. Existing other swap stays
enabled; the research swap is added once with mode 600 and swappiness 10. Existing
conflicting fstab/sysctl configuration is preserved and rejected. A partial file
from interrupted creation is also preserved for explicit inspection; the script
will not automatically format it on retry. Actual swapon and reboot persistence
require EC2 verification; only shell syntax was tested here.

## Dry Run and actual research

Prefer the authenticated admin AI Research tab. Ensure autonomous_ai and
research_export are ON. Their status/log/control-plane views remain reachable
when execution is disabled. The CLI also checks the local persisted switches;
Export HTTP 503 with API_DISABLED is translated to API_DEPENDENCY_DISABLED without
echoing any raw server response or credential.

With existing admin session token supplied securely via environment:

```bash
export RESEARCH_EXPORT_BASE_URL=http://127.0.0.1:8080
.venv/bin/python research/autonomous/run_research_loop.py --dry-run --max-iterations 1
```

Review baseline_selection.json, resource_guard.json, quarterly_diagnostics.json and
plan.json in the new iteration. If they pass, actual research is a SEPARATE later
operator action, not part of this audit:

```bash
.venv/bin/python research/autonomous/run_research_loop.py --resume --max-iterations 1 --data-manifest research/v4_data_manifest.json
```

Use `--provisional` instead of a reviewed manifest only when deliberately choosing
the existing unreviewed mode; never forge provenance flags. Missing candidates do
not relax the fixed V0.8 annual-positive / 3-positive-quarters / median-positive
selection rules. No automatic baseline replacement or 2026 evaluation follows.

## Admin verification

AI Research: baseline rejection root cause/dates/counts, per-symbol coverage,
normal/paused Resource Guard with sample count, filesystem device/mount/free space,
API dependency state, pipeline, process RAM, checkpoints, sanitized failure/logs,
old failed iterations, immutable completed Dry Run and CLOSED Final OOS.

API settings: authenticated changes persist after restart, OFF returns explicit
API_DISABLED/DEPENDENCY_DISABLED, live OFF disarms/turns engine and auto OFF,
paper auto stops, realtime and dashboard updater idle. No real order is needed
or permitted to verify these checks. Main/Pages deployment is separate: only
admin.html is a UI change in this patch; no Python belongs on main.
