# Toss targeted repair: six missing 2024 sessions

## Evidence and limits

The operator's EC2 audit reports TQQQ/AAPL finalized CSVs with **0/390 RTH
minutes** on July 26, 29, 30, 31 and August 1, 2, 2024. Both prior V07 holdouts
requested 252 XNYS sessions but completed 246. These are real trading days,
not calendar holidays. This checkout has no EC2 data or credentials; no repair,
Toss request, service change, training, or baseline validation was performed here.

Code evidence:

* `research/evolution/run_evolution_v0.py:prepare_inputs` resolves finalized CSV
  via `research/build_v4_data_manifest.py:source_file` (or reviewed manifest).
  Exactly one `SYMBOL.csv.gz` or `SYMBOL.csv` is required. It never consumes
  SQLite as an evaluation input. `evaluate_population_v03.py` reuses this loader.
  `run_v07_campaign.py` defaults to `TQQQ,AAPL`, END timestamps and explicit
  provisional mechanical validation. SPY/QQQ/SOXX references are also required.
* `scripts/collect_toss_1m.py:collect` uses `existing_oldest` to resume backwards.
  It does not detect or repair holes inside an existing history. `coveragePct`
  measures extrema, then is written as 100 at completion: it does not prove
  session or minute completeness.
* The collector commits SQLite pages, but exports CSV only at the normal end.
  An exception after a DB checkpoint can leave an older CSV in place. Its CSV
  import is skipped when the DB already has any row. Restart therefore does not
  reconcile independent CSV/DB changes. `INSERT OR IGNORE` also cannot explain
  conflicting data or validate missing sessions.

Answers to the eight source questions:

1. **Why precisely these six days disappeared:** not established locally. An
   interior hole can survive the resume algorithm; the original creation cause
   requires EC2 progress/error/cursor history. Do not attribute it to an API
   outage or export bug without those records.
2. **421 SQLite rows vs zero CSV RTH:** not established. Total rows are not RTH
   coverage. The new audit reports extended rows, exact normalized RTH rows,
   timestamp offsets, source endpoints, and CSV-only/DB-only/conflicting rows.
   It can distinguish a stale export from extended-hours/date-boundary rows.
3. **AAPL CSV without DB:** supported by evaluator design. It may be an export
   from another collection/storage path. Its actual origin is unknown; do not
   fabricate or automatically recreate that production DB.
4. **Evaluator input:** finalized CSV, not DB (see loader above).
5. **CSV stale:** possible, not yet proved. Metadata/file age alone is not proof;
   require same-symbol/time/value comparison. Audit records CSV SHA256/signature,
   DB signature/count/extrema and RTH differences.
6. **Canonical source:** SQLite is a mutable collector checkpoint; CSV is the
   finalized evaluator artifact. Neither is automatically market truth.
7. **Consistency after repair:** add only validated requested RTH minutes to
   CSV and an existing SQLite copy; compare overlapping values using Decimal,
   never overwrite conflicting rows. Publish both only during an offline
   window. Do not create an absent AAPL DB. Pre-existing extended-hours or
   out-of-scope differences remain documented, not silently reconciled.
   The prepared DB's original `ORDER BY timestamp` export must also be
   chronological after END normalization; mixed UTC/ET text ordering fails
   with `SQLITE_TEXT_EXPORT_ORDER_INVALID`, never silent timestamp rewriting.
8. **Six days imply 252 complete sessions:** no. `snapshot` requires the entire
   2024 calendar to pass for symbols AND references; the newly run frozen
   evaluator must then independently report 252/252, no incomplete sessions.

## Tools and invariants

`scripts/repair_toss_1m.py` subcommands:

| Command | Effect |
|---|---|
| `audit` | Read-only CSV/SQLite comparison, bounded to at most 366 selected sessions. |
| `collect` | Fresh, exclusive external staging directory; never resumes a production DB. |
| `adopt` | Read-only import of an existing preview-first collector staging export into a NEW validated bundle; no duplicate API collection. |
| `validate` | 390/390 for these full sessions, aware timezone, exact exclusive END labels, OHLCV, USD, duplicate/conflict/order/scope checks. |
| `prepare` | Immutable original backups, separately prepared CSV and existing SQLite, hashes/counts/provenance; no production mutation. |
| `apply` | Explicit offline acknowledgement, all hashes rechecked, atomic per-file replacement with fsync and recovery journal. |
| `recover` | Explicit rollback of interrupted publication; unknown changed bytes fail closed. |
| `snapshot` | New immutable-by-convention 2022–2025 CSV-only campaign input and repair provenance after full 2024 coverage passes. |

The dedicated staging receiver reuses OAuth, rate limiter and REST candle page
logic (`GET /api/v1/candles`, `adjusted=true`, existing `nextBefore` protocol).
The only existing collector change is an optional error-recorder argument;
its default behavior is unchanged. The newer remote `scripts/backfill_toss_1m_staging.py`
(commits a95dd3a/2bc6788) is preserved unchanged; its unverified per-symbol output
can use `adopt` below. Staging errors go to staging and contain
only timestamp/symbol/cursor/attempt/HTTP code, never response bodies or tokens.
No broker-order imports or calls, tick collector changes, threshold changes,
mutation changes, fee changes, or new manifest review flags.

Each requested date starts its own cursor at RTH close + 1 minute, queried as
UTC. Only rows whose END-normalized start lies inside that exact XNYS session
are persisted. Pagination stops at session open, is capped, and must move
backwards. The API may return overlapping/outside page rows; those are not
stored. Incomplete history is a failed validation, never a reason to relax it.
Timestamps must already be timezone-aware, exact minute boundaries; no naive
timezone guessing or inclusive `:59` interpretation. END is an explicit operator
declaration, not a claim that OHLCV can prove Toss's timestamp convention.

Validation/manifests remain **PROVISIONAL_UNREVIEWED_DATA**. `adjusted=true` is
recorded, not proof of split/dividend adjustment compatibility. Before apply,
the operator must establish that new and existing adjusted prices use a
compatible basis (including corporate-action revisions). If a completely
missing session has no overlap, this cannot be proved from that session alone.
No auto-adjustment or final reviewed manifest creation occurs.

Raw source hashes/backups preserve opaque bytes of the whole existing file,
including any future partition already present. Timestamp-only ordering audit
can inspect labels across that file, but **no 2026 price/volume is interpreted,
evaluated, normalized or selected**. Snapshot stops before constructing any
2026 price record. No future bar enters a simulation or repair decision.

Memory is bounded by requested RTH minutes per symbol (six sessions: 2,340).
Audit may select a full year (approximately 98,000 RTH rows/symbol), one symbol
at a time; samples cap at ten. CSV input/merge is streaming. SQLite sorting uses
the database engine. `disk_guard` measures the actual destination filesystem,
including bind mounts, and preserves max(10 GiB, 10% of filesystem capacity).
Prepare conservatively estimates gzip expansion and backups. It can refuse a
large operation; never lower the reserve to make it run.

Two-file CSV/SQLite publication is **not a filesystem-wide atomic transaction**.
Stop writers/readers first and do not restart them while a `.toss-repair-*.json`
journal exists. SIGTERM/Python exceptions roll back using durable exact backups.
Power loss/SIGKILL leaves the journal; explicit `recover` restores both. Unknown
post-crash writes are a conflict and require manual investigation. A persistent
production WAL blocks prepare/apply: explicitly close/checkpoint it in maintenance
after confirming no processes are using it; the tool never deletes WAL/SHM.
No collector is stopped or restarted by the tool. Its flock prevents concurrent
repair operations, not uncooperative external writers. `--confirm-no-writers`
is a real maintenance acknowledgement, not a substitute for stopping writers.

Preparation backups, failed operation artifacts and old campaigns are retained.
Only this tool's temporary output files and resolved repair journals are removed.
No automatic original/backup/campaign/staging cleanup occurs.

## EC2 operator sequence (not executed in Codex)

First preserve local changes and identify running experiments. Do not pull over
active Python runs. A fast-forward failure is a divergence requiring a normal
reviewed merge; never reset/clean/force push.

```bash
cd /home/ubuntu/market-career-dashboard
git status --short
git branch --show-current
pgrep -af '[r]un_research_loop.py|[r]un_v07_campaign.py|[e]valuate_population_v03.py|[r]un_evolution_v0.py'
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git switch strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
findmnt -T /mnt/research
findmnt -T data/toss_1m
df -h /mnt/research data/toss_1m

REPAIR_ID=$(date -u +%Y%m%dT%H%M%SZ)-$RANDOM
REPAIR_ROOT=/mnt/research/staging/toss_1m_backfill/$REPAIR_ID
mkdir -p "$REPAIR_ROOT"
.venv/bin/python scripts/repair_toss_1m.py audit \
  --data-dir data/toss_1m --symbols TQQQ,AAPL --timestamp-kind end \
  --out "$REPAIR_ROOT/source-audit.json"
.venv/bin/python scripts/repair_toss_1m.py collect \
  --data-dir data/toss_1m --staging-dir "$REPAIR_ROOT/received" \
  --symbols TQQQ,AAPL --timestamp-kind end
.venv/bin/python scripts/repair_toss_1m.py validate \
  --staging-dir "$REPAIR_ROOT/received"
```

If the existing preview-first collector has already fetched this interval, use
the following **instead of `collect`**, supplying its actual staging parent:

```bash
.venv/bin/python scripts/repair_toss_1m.py adopt \
  --input-staging-dir /mnt/research/staging/ACTUAL_PREVIOUS_BACKFILL \
  --data-dir data/toss_1m --staging-dir "$REPAIR_ROOT/received" \
  --symbols TQQQ,AAPL --timestamp-kind end
```

This reads either `SYMBOL.csv.gz` or `SYMBOL/SYMBOL.csv.gz`, checks original
order/conflicts before deduplicating, hashes/binds the source, and leaves its
SQLite/progress/CSV files unchanged. Partial RTH still fails validation. The
earlier collector used `INSERT OR IGNORE`, so its CSV cannot retrospectively
prove that no conflicting API row was previously discarded; provenance stays
unreviewed. Do not claim `adopt` repairs that lost evidence.

Review `source-audit.json`, `staging.json`, `validation.json`: TQQQ/AAPL each six
390/390 sessions, no conflicts, scope/order/timezone/currency violations.
Missing historical API access, pagination gaps, or adjustment uncertainty means
stop here. Do not insert mock data or bypass validation. No default API request
spans the whole historical dataset.

Before production publication, record whether the existing historical collector
was active; arrange the maintenance window without touching the tick collector,
dashboard or live/paper trading. The following stop is an explicit operator
action, never automatic in this tool. Confirm no other process writes these CSVs
or uses these DBs. If a WAL remains, investigate its owner and explicitly perform
a normal SQLite checkpoint/close; do not delete it. **Prepare after quiescence**.

```bash
systemctl is-active collect-expanded-universe.service
sudo systemctl stop collect-expanded-universe.service
pgrep -af '[c]ollect_toss_1m.py|[e]valuate_population_v03.py|[r]un_evolution_v0.py|[b]acktest_intraday_v4.py'
.venv/bin/python scripts/repair_toss_1m.py prepare \
  --data-dir data/toss_1m --staging-dir "$REPAIR_ROOT/received" \
  --symbol TQQQ --operation-dir "$REPAIR_ROOT/merge-TQQQ"
.venv/bin/python scripts/repair_toss_1m.py prepare \
  --data-dir data/toss_1m --staging-dir "$REPAIR_ROOT/received" \
  --symbol AAPL --operation-dir "$REPAIR_ROOT/merge-AAPL"
```

Review both `merge.json` hashes, inserted/unchanged counts and backups. If any
conflict exists, `failure.json` documents it; production has not changed. Only
after review and price-basis compatibility evidence:

```bash
.venv/bin/python scripts/repair_toss_1m.py apply \
  --operation-dir "$REPAIR_ROOT/merge-TQQQ" --confirm-no-writers
.venv/bin/python scripts/repair_toss_1m.py apply \
  --operation-dir "$REPAIR_ROOT/merge-AAPL" --confirm-no-writers
.venv/bin/python scripts/repair_toss_1m.py audit \
  --data-dir data/toss_1m --symbols TQQQ,AAPL --timestamp-kind end \
  --out "$REPAIR_ROOT/post-merge-audit.json"
.venv/bin/python scripts/repair_toss_1m.py snapshot \
  --data-dir data/toss_1m --out-dir "$REPAIR_ROOT/campaign-input" \
  --symbols TQQQ,AAPL,SPY,QQQ,SOXX \
  --operation-dir "$REPAIR_ROOT/merge-TQQQ" \
  --operation-dir "$REPAIR_ROOT/merge-AAPL"
```

Snapshot includes full 2024 coverage diagnostics and both repair plans. It never
accepts an unresolved journal, changed source hash or incomplete reference.
If a normal apply succeeded but a final journal deletion was interrupted,
explicit recovery conservatively rolls back; prepare a NEW operation afterward.
Recovery command (only for the named interrupted operation):

```bash
.venv/bin/python scripts/repair_toss_1m.py recover \
  --operation-dir "$REPAIR_ROOT/merge-TQQQ" --confirm-no-writers
```

Do not restart the collector while any recovery journal exists. If it was active
before maintenance, restart it only after audits/snapshot succeed and after
confirming its configured resume/range behavior is still appropriate:

```bash
sudo systemctl start collect-expanded-universe.service
```

The NEW campaign reads the immutable bounded snapshot, not the still-changing
production files. Its default V07 population/seed/selection/fitness stay exactly
as defined by the current campaign CLI. This is long-running operator work, not
performed by this coding task. Use the normal resumable operational supervisor
or a separate transient unit; do not attach it to a fragile interactive session.
Example (unique service name, no collision with existing collectors):

```bash
sudo systemd-run --unit="evolution-v07-repair-$REPAIR_ID" \
  --uid=ubuntu --property=WorkingDirectory=/home/ubuntu/market-career-dashboard \
  /home/ubuntu/market-career-dashboard/.venv/bin/python \
  research/evolution/run_v07_campaign.py \
  --data-dir "$REPAIR_ROOT/campaign-input" \
  --dataset-snapshot "$REPAIR_ROOT/campaign-input/repair_snapshot.json" \
  --results-dir /home/ubuntu/evolution-results --symbols TQQQ,AAPL
journalctl -fu "evolution-v07-repair-$REPAIR_ID"
```

The snapshot has a fresh identity/provenance and the campaign always creates a
new timestamped campaign directory. `--dataset-snapshot` verifies the repaired
file hashes and copies provenance into that new campaign. Without the flag,
the campaign's existing behavior is unchanged. Do not edit prior failed run/status/summary
files. 2024 completion is necessary but not sufficient: training, 2025 coverage,
valid frozen bots, source lineage and shortlist requirements must still pass.

After the campaign completes:

```bash
.venv/bin/python research/autonomous/audit_v07_baseline.py \
  --results-dir /home/ubuntu/evolution-results
```

Require latest strict `BASELINE_VALID`, selectedRunId not null, no relaxed
criteria, and full 2024 `requestedSessions=completedSessions=expectedSessions=252`,
`resultsValid=true` with no incomplete symbol/reference sessions. If it remains
invalid, stop and use its exact root cause; never rewrite validity flags.

Only then (API enabled, credentials already configured securely in environment):

```bash
RESEARCH_EXPORT_BASE_URL=http://127.0.0.1:8080 \
  .venv/bin/python research/autonomous/run_research_loop.py \
  --dry-run --max-iterations 1
```

Completed dry-run remains immutable. V08 selection remains annual 2024 > 0,
positive quarters >= 3, median quarter > 0; insufficient candidates fail, never
relax. Baseline is not promoted automatically. Final OOS remains CLOSED.

## Local validation

```bash
.venv/bin/python -m py_compile scripts/collect_toss_1m.py scripts/repair_toss_1m.py research/toss_1m_repair.py
.venv/bin/python scripts/repair_toss_1m.py --help
.venv/bin/python -m unittest discover -s research -p 'test_*.py'
for test in research/test_*_smoke.cjs; do node "$test" || exit; done
```

Fixtures are deliberately small and synthetic; they prove tool safety, not
Toss historical availability, source-price provenance, or a real valid baseline.
