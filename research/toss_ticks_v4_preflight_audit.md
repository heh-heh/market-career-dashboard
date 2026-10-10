# Toss microstructure collector and V4 preflight audit

Audit date: 2026-10-10. No production deployment, broker calls, historical runs,
parameter optimization, provenance fabrication or historical-data changes.

## Evidence and root cause

- Initial clean local `strategy-engine-v3`: `cbda179f44edcc991c19b71c495cc6f10c6c154e`.
- Fetched explicit branch refspec; fast-forwarded to `2958088` before edits.
- Baseline: 163 Python tests passed. Existing backtest JS smoke failed because
  its mock omitted the `/api/backtest/engines` response introduced upstream.
- Reproduced with one tiny CSV filename fixture and no manifest:
  `phase=waiting`, `running=false`, `runnable=false`, `manifestAvailable=false`.
  Start raised before `Popen`: **0 subprocess calls, no new state file**.
- Corrected status: **BLOCKED**, explicit `DATA_MANIFEST_REQUIRED`; authenticated
  Start returns **412 Precondition Failed** with `error`, `blockedReason`,
  `runnable=false`, `manifestAvailable`. Client displays the reason and disables
  Start. Unknown/unverified backend engine lists cannot silently launch anything.
- Idle WAITING now means available prerequisites and no other historical job.
  STARTING is distinct from a live RUNNING PID. A dead launched child remains
  INTERRUPTED; child validation errors remain ERROR. Parent and V4 child share
  runId; mismatched launch/result IDs cannot attach old artifacts to a new run.
  Existing completed results remain readable across API reconstruction.
- No provisional mode added; reviewed FINAL mode remains unchanged.

**EC2 was not inspected.** Available AWS config/credentials files contain no
profiles, AWS CLI/SDK is absent, and no authenticated SSH/SSM connection is
available. Neither deployed HEAD nor deployed `/api/backtest/engines`, child PID,
collector progress, file stability or provenance is claimed as verified.
Local `data/toss_1m/`, its progress JSON and final reviewed manifest are absent.
This does **not** establish their EC2 status. Local FINAL V4 is BLOCKED.

## V4 correctness audit

| Severity | Finding / location | Correction or limitation | Bias |
|---|---|---|---|
| HIGH | `backtest_manager.py:status/start`, `server.py` Start | Missing manifest looked like WAITING; now BLOCKED/412, no launch | Operational, no trades generated |
| HIGH | `backtest_intraday_v4.py:read_stream/main` | START-only interpretation conflicted with current official Toss END candles. Add explicit START/END normalization and manifest-bound manager argument | Unknown; shifted session structure/fills, not a proven profitable edge |
| HIGH | Parent start and child Reporter | Common runId; startup grace without falsely claiming RUNNING; reject stale launch/result IDs | Old results could be displayed as current |
| MEDIUM | `intraday_v4_metrics.py:grouped` / combined result breakdowns | Year/month/session/regime/time groups now use actual combined initial capital (30k for 3 books), not fixed 10k | Account percentage returns and drawdowns previously overstated for combined books |
| MEDIUM | `intraday_v4_metrics.py:chronological_folds` | Exclude an outage-delayed exit occurring outside the attributed fold's date range | Unknown; descriptive train metrics could incorporate future OOS exit PnL |
| PASS | `Session.observe/finish`, candidate queue, synchronized references | Features use completed prefix; previous daily and same-clock distributions update only after session finish; 5m requires five consecutive minutes; no forward-fill | No future feature access found |
| PASS | `evaluate`, `Book.enter/manage` | Frozen boundaries, one attempt per symbol/strategy/day, independent cash books, no same-signal fill; strict subsequent minute close proxy; missing entry cancels; stale exits use first available close; observed hard stop precedes trail/target | Synthetic regressions |
| PASS | Calendar/DST/cutoff, validation | XNYS calendar; reviewed timezone; early close cutoffs; hash checks before/after; no altered attestations | Synthetic/calendar checks |
| LIMITATION | Execution / MFE / MAE / equity | Completed-close observation proxy, not executable tick quotes. Intrabar stop touches are diagnostic, not ideal fills. Excursions use observed closes; drawdown is realized exits, not intratrade mark-to-market | Can miss adverse intrabar moves; live executability and real spread/fees unverified |
| LIMITATION | Point-in-time universe | Fixed surviving seed; review flags require external evidence, not just a builder invocation | Survivorship bias remains |

IR1/IR2/IR3 thresholds and frozen S1/S2/S3 rules are unchanged.
Fees remain explicitly unverified. No historical performance conclusion.

## Verified official protocol (retrieved 2026-10-10)

- [Developer source-of-truth index](https://developers.tossinvest.com/llms.txt)
- [Overview](https://openapi.tossinvest.com/openapi-docs/overview.md)
- [Canonical AsyncAPI 1.2.2](https://openapi.tossinvest.com/openapi-docs/latest/asyncapi.json)
- [Canonical OpenAPI](https://openapi.tossinvest.com/openapi-docs/latest/openapi.json)
- Fixture source digest and exact examples: `fixtures/toss_asyncapi_1_2_2.json`.

Verified: `wss://openapi-ws.tossinvest.com/ws/v1`, Bearer handshake, full-replace
JSON array declarations, echo ID and `subscriptions.subscribed/rejected`, 100
channel×symbol topics per connection, **2 connections per account**, 5 declarations
per second, 180s client-idle timeout, plain text `PING` every recommended 60s,
JSON `pong`, exponential jittered reconnect/resubscribe after close/shutdown.
Rejected topics are excluded from subsequent declarations within that run.
401 handshake causes shared OAuth forced refresh on next attempt.

Trade payload: decimal strings `price`, `volume`, ISO `timestamp`, `currency`.
Book payload: `asks/bids` arrays of `{price,volume}`, `currency`, optional nullable
`timestamp`; asks ascending, bids descending. No delta operation/update ID exists;
Toss documents the same shape as the REST returned ladder. Treat each frame as
an independent observation of **returned** depth, never apply delta reconstruction
or claim full exchange depth/atomic matching-engine snapshots. Preserve all raw
levels and flag unexpected fields. No initial snapshot is sent on subscription.

Both channels are **LOSSY**, latest-priority, all supported DAY/PRE/REGULAR/AFTER
sessions, with **no exchange sequence**, cumulative volume or aggressor side.
Never infer complete tape volume, delta continuity, missing tick counts or exchange
order flow. Identical-looking ticks are preserved individually. REST `/trades`
is same-day recent data with max 50, **not historical backfill**; not used here.

OpenAPI Candle.timestamp expressly defines 1m **exclusive END** labels. Existing
collector stores returned timestamp directly, but EC2 exports and source version
still require actual inspection before confirming their manifest interpretation.

No actual live WebSocket handshake, symbol entitlement, live session calendar,
frame rate/depth or market-open frames were verified. All frame tests are offline.

## Collector/storage architecture

`scripts/collect_toss_ticks.py` imports shared OAuth and REST rate limiter only;
no server, trader, account or order client. REST access is market-calendar GET
plus shared OAuth issuance. Default universe is `research/universe_v3.json`:
30 symbols × trade/book =60 topics. `--symbols` permits exact universe subsets.

Single local process lock per storage directory. Async receiver → bounded queue
256 records → one writer thread, max128 records /250ms batch. WebSocket queue16,
max frame256KiB. Backpressure explicitly records loss risk, never silently drops
local accepted frames. Memory remains bounded; remote LOSSY delivery can still
omit frames under backpressure. Unknown/malformed frames remain raw records.
60s keepalive watches for missing pong; SIGTERM/INT flushes accepted records.
Fatal storage/disk failures terminate visibly; no synthetic prices or deletes.

```
/var/lib/market-career-dashboard/toss_ticks/
  collector.lock
  status.json                         atomic, fsynced
  YYYY-MM-DD/                         receive-calendar date, America/New_York
    <collectorRunId>-<segmentId>.sqlite3            append-only frames; WAL + synchronous FULL
    <collectorRunId>-<segmentId>.manifest.json      atomic segment daily manifest, finalized hash
```

A new process uses a unique segment, so it cannot overwrite an interrupted run.
SQLite recovers committed WAL after a crash; committed records remain replayable.
Hard crash can lose the bounded uncommitted queue and leaves `finalized=false`;
never claim a crash segment's open manifest is finalized. No compaction/deletion.
Multiple segment manifests together describe a day's collection; no lossy merge.

`frames.id` is a SQLite storage row ID, **not exchange sequence**. Common columns:
`collector_run_id`, `connection_id`, `local_receive_sequence` (monotonic in that
collector run, across reconnects and local events), UTC receive/source times,
verbatim source timestamp, topic/symbol/channel, actual calendar session/trading
label, kind, raw BLOB, normalized JSON, warnings. Strings retain price/volume;
books add returned best bid/ask/sizes, Decimal spread, level counts. Every ladder
level remains in normalized JSON and raw payload. Events preserve ACK/rejections,
ping/pong, connection starts/ends, failed attempts, reconnect delay and calendar.
Indexes support symbol/source time and receive-time queries. Replay should use
receive sequence within a run, not source-time sorting that moves late frames
backwards. Cross-process receive-time order needs explicit clock-quality policy.

Daily manifests store version/git SHA/universe/channels/ACKs/times/connection and
message counts/quality issues/sizes; clean close checkpoints WAL and streams SHA256.
Atomic health records connectivity, heartbeat, messages by symbol/channel, reconnects,
rejections, raw bytes committed (not equivalent to actual DB size), free space,
partition, queue pressure, last error. Local calendar poll hourly with shared
`MARKET_INFO` limiter; actual aware Toss intervals determine sessions including DST.
Unknown/null source times or calendar outage remain UNKNOWN; receive time never
masquerades as source time. Receive-date partition and Toss trading-date are distinct,
particularly around DAY sessions. Calendar outage does not discard market frames.

Default free-space warning5GiB, fatal stop1GiB; configurable CLI. No auto-delete.
Rate/depth are **unmeasured**. Budget example: 100 received frames/s ×1KiB ≈8.2GiB
per24h before duplicate normalized payload/index/WAL overhead; 1000/s ≈82GiB.
Full-book frames can be larger. Monitor actual file sizes and inode/disk usage;
small EC2 storage can fill quickly. Batch collection waits ≤250ms plus queue/disk delay; a hard crash can lose
the entire bounded uncommitted queue. This trades durability latency for less fsync overhead. This is not a throughput certification.

Future replay may combine 1m context with received ticks/books, but must retain
signal/decision/source/receive/order/fill timestamps separately. Source-time cannot
make late-received information available earlier. No tick strategy conversion here.

## EC2 procedure (NOT executed by this audit)

### Verify and safely sync backend

```bash
cd /home/ubuntu/market-career-dashboard
git branch --show-current
git rev-parse HEAD
git status --short
# Review tracked edits; stop if unrelated changes/conflicts exist. Never reset/clean.
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git switch strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
.venv/bin/python -m pip install -r requirements.txt
sudo systemctl restart market-career-dashboard.service
sudo systemctl status market-career-dashboard.service --no-pager
# ADMIN_TOKEN is an existing authenticated admin session, not Toss credentials.
curl --fail-with-body -H "Authorization: Bearer $ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/backtest/engines
curl --fail-with-body -H "Authorization: Bearer $ADMIN_TOKEN" \
  'http://127.0.0.1:8080/api/backtest/status?engine=v4-ir1'
```

Use the service's configured port if different (check existing unit). Registry must
include v4-ir1/ir2/ir3/all; verify backend HEAD and actual status. If fast-forward
fails, preserve files and resolve explicitly; no hard reset/stash/data deletion.
Restart only the dashboard when deploying the backend; historical collector units
are not touched. Avoid restarting while a historical subprocess is actively running.

### Mechanical data audit, then external review

Inspect `data/toss_1m_progress.json`, finalized symbol file counts versus SQLite
staging, currency/schema, timestamp labels, duplicates/order/coverage and stable
hashes on EC2. If exports are still being rewritten, wait; no FINAL review.

After actually establishing timestamp semantics, a diagnostics-only invocation:

```bash
.venv/bin/python research/build_v4_data_manifest.py \
  --data-dir data/toss_1m --timestamp-kind "$VERIFIED_TIMESTAMP_KIND" \
  --price-adjustment unknown \
  --out /var/lib/market-career-dashboard/v4_mechanical_audit.json
```

It deliberately FAILS external price/identity/eligibility review. It does not create
an accepted final manifest. If raw timestamps are naive, supply **only the actually
verified** `--naive-timezone`; never assume a zone. Review actual provenance for
price AND volume split adjustment/as-traded eligibility, symbol identity and timestamp
interpretation. Existing `--verified-by`, `--verification-note` and confirmation
flags must attest supported evidence, not an instruction to blindly enable them.
Only after that review may the builder publish the FINAL manifest (default
`research/v4_data_manifest.json` or service `V4_DATA_MANIFEST` override).

After genuine PASS, UI Start runs with manifest-declared timestamp kind. Manual
example must match that declaration and avoid competing historical jobs:

```bash
.venv/bin/python research/backtest_intraday_v4.py --strategy ir1 \
  --data-dir data/toss_1m --manifest research/v4_data_manifest.json \
  --timestamp-kind "$VERIFIED_TIMESTAMP_KIND" \
  --out /var/lib/market-career-dashboard/backtest_v4_ir1_result.json \
  --state /var/lib/market-career-dashboard/backtest_v4_ir1_state.json \
  --log /var/lib/market-career-dashboard/backtest_v4_ir1.log \
  --trades-csv /var/lib/market-career-dashboard/backtest_v4_ir1_trades.csv \
  --data-audit /var/lib/market-career-dashboard/backtest_v4_ir1_data_audit.json \
  --decisions /var/lib/market-career-dashboard/backtest_v4_ir1_decisions.jsonl
```

### Install/start collector separately

Check existing account WebSocket clients first: adding a third would evict the
oldest connection. Do not silently disrupt the existing realtime dashboard/client.
Ensure existing `server_secrets.json` supplies shared OAuth and EC2 IP is authorized.

```bash
sudo bash scripts/install_toss_ticks_service.sh
# Installation above neither starts nor enables anything. Explicit operator start:
sudo systemctl start collect-toss-ticks.service
sudo systemctl status collect-toss-ticks.service --no-pager
sudo journalctl -u collect-toss-ticks.service -f
sudo cat /var/lib/market-career-dashboard/toss_ticks/status.json
sudo du -sh /var/lib/market-career-dashboard/toss_ticks
df -h /var/lib/market-career-dashboard/toss_ticks
# Optional boot activation after inspecting ACK/live data and capacity:
sudo systemctl enable collect-toss-ticks.service
# Graceful stop, independent of all other services:
sudo systemctl stop collect-toss-ticks.service
```

New unit uses Restart=on-failure, SIGTERM, persistent external storage, journal,
0077 permissions. No change to existing service units. No shell/admin collector
control endpoint added. Backend admin.html changes belong on this branch; **main /
Pages was not synced**. Publish static admin separately using established policy.

## Validation evidence

- `/tmp/v4-manifest-venv/bin/python -m unittest discover -s research -p 'test*.py' -q`:
  **193 tests passed** (baseline163; +30 regressions, including21 collector tests).
- Both `node research/test_admin_backtest_smoke.cjs` and
  `node research/test_admin_paper_smoke.cjs`: PASS.
- Changed Python sources: py_compile PASS. Collector and IR CLI `--help`: PASS.
- Installer `bash -n`: PASS. `systemd-analyze verify` of a temporary copy with
  only checkout/venv paths substituted to existing local paths: PASS. Actual EC2
  installation/service activation is untested and was not performed.
- Relevant synthetic checks: ACK/rejection/unknown/malformed/source timezone,
  trade/book decimals and all depth, nullable timestamp, pong and PING timeout,
  shutdown/reconnect, flush/disk-stop, daily rotation and clock rollback,
  duplicate-looking trades, market-only imports; missing-manifest BLOCKED/412,
  startup grace/runId/dead child/conflict/current downloads/backend allowlist,
  child audit ERROR; START/END identical availability, combined capital and
  future-exit fold exclusion; existing causal/trader tests remain passing.
- Tests use tiny fixtures/mocks only; no live market frames or historical run.

Cloud onboarding reused the existing venv, installed already-declared websockets
15.0.1 and verified exchange_calendars4.13.2. A tested venv + requirements install
script was saved in the environment configuration draft; it needs review/save and
Publish in environment settings to apply to future environments. No service-start
instructions, credentials, repository membership or network policy were changed.

This commit must be published through the normal branch/review workflow before EC2
fetch can deploy it. This audit itself does not push, deploy, sync main or start
any collector/backtest/trader service.
