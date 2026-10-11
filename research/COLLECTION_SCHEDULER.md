# Regular-session collection switching — deployment and evidence

## One-command update / collection start (October 2026 revision)

**Superseded for dirty production / detached worktrees:** use
[COLLECTION_WORKTREE_DEPLOY.md](COLLECTION_WORKTREE_DEPLOY.md) and
`scripts/deploy_collection_worktree.py`. The launcher now delegates to analysis
by default; the old clean-checkout command below is historical documentation,
not a supported deployment recipe. The new path never merges production Git,
never replaces/restarts Broker code, and always leaves collection paused.

Run on EC2, from the production checkout (mounted `/mnt/research`, existing
`.venv`, configured/active token broker and sudo access are prerequisites):

```bash
cd /home/ubuntu/market-career-dashboard && test "$(git branch --show-current)" = strategy-engine-v3 && git diff --quiet && git diff --cached --quiet && git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3 && git merge --ff-only origin/strategy-engine-v3 && bash scripts/deploy_collection.sh
```

This installs dependencies, runs focused tests BEFORE stopping workers, updates
the broker/scheduler, preserves untracked data and existing env/control settings,
disables the two independent collector units, checks for manual collectors, and
restarts the API. It refuses a divergent/dirty tracked checkout or missing EBS
mount. It does not reset/clean, migrate/delete production data, train AI, or change
live/paper configuration. Restarting the API should be scheduled outside active
research/trading jobs. A newly installed scheduler starts **paused**. Existing
operator pause/resume choice is preserved on updates.
Deployment tests use temporary paper/state/output paths, disabled live flags
and a nonexistent broker socket, so importing the API for smoke tests cannot
touch production paper history or acquire real Toss credentials.

Open your API origin + `/api/research/collection50/page`. This fixed, mobile-first
page is served with `Cache-Control: no-store`, so API deployment immediately
updates it without waiting for Pages. Log in with the existing admin password,
then click **수집 시작**. Authentication/expired-session errors are explicit.
An absent/stale/stopped scheduler refuses Start (HTTP 409). No live ARM is used.
If regular hours are open, Start runs Tick; outside regular hours it runs 1m.
Control writes explicitly inherit the state-directory group with mode 0640 so
root API → ubuntu scheduler works even with API umask 0077. Atomic writes use
exclusive random temporary files rather than following predictable symlinks.
Legacy worker stop timeout is 180 seconds without automatic SIGKILL; a stuck
old worker stops deployment rather than allowing a second collector to start.

Declared range is **2000-01-01 ET through 2026-10-31 ET inclusive**. Newest data
first, one page per symbol turn, walking **2026 → 2000**. Future minutes are not
created; the initial cursor is capped at the last completed UTC minute. A
separate recent-edge cursor catches up every 12 hours without resetting the
multi-year cursor. After the history pass, unresolved dates are retried latest
first and still reported missing if the API cannot supply them. Data before
listing or outside Toss retention remains unavailable, never fabricated.

Widening a saved collection range creates immutable SHA-addressed copies of
global/per-symbol checkpoints and re-audits coverage before resetting only
collection cursors. Existing DB rows remain. Shrinking the range is refused.
Research/evaluation/download Final OOS guards remain CLOSED for 2026.

GitHub Pages workflow now accepts UI changes on `strategy-engine-v3` too and
uploads only explicitly listed public HTML/CSS/JS plus `data/dashboard.json`.
Backend code, tokens, collector databases and research artifacts are excluded.
No merge into `main` is required. Pages deployment takes its normal CI time;
the API-served page provides the immediate fallback. Existing trading/research
views remain available; legacy service controls are folded under advanced
inspection instead of deleting features.

GitHub runtime check: environment `github-pages` currently permits only `main`.
Adding the exact `strategy-engine-v3` branch policy returned HTTP 403 with the
available GitHub integration. An owner must add that branch in Settings →
Environments → github-pages → Deployment branches before its Pages job can
publish. No protection is bypassed and no backend is merged into main. The
EC2 API-served collection page does not depend on this GitHub permission.

Local evidence for this revision: all 507 Python tests passed (44.94 s), all
11 JavaScript smoke checks passed, Python compilation/CLI help and `bash -n`
passed. The actual isolated deployment-test block passed 69 tests (3.30 s).
New tests cover ET October boundary, allowed 2026 collection with CLOSED OOS,
backward pagination/restart, immutable checkpoint copies, recent-edge catchup,
pause-by-default, both switch directions, shared control permissions, temporary
symlink protection, authenticated ready-only Start and public-only Pages output.
No EC2 service deployment, live market request or historical backtest was run.

## Scope and findings

Research branch only. No live/paper policy, order endpoints, Evolution fitness,
V4 thresholds, original production candles or past campaigns are changed.

Inspected: historical/Tick collectors, auth/broker/limiter, server collection
handlers, collection UI, V07 campaign interface and COLLECTION_50_CODEX_HANDOFF.
The existing Tick collector already implements Toss trade WebSocket ACK,
reconnect/outage metadata, bounded queues and lossy-source disclosure. Reuse it.
No new Toss payload fields, topics or REST recovery were invented.

Confirmed code defects/limitations:

- Shared limiter replaced all API group timestamps with the last group. Fixed
  group preservation; one canonical lock path can be configured across worktrees.
- Historical collector compares timestamp strings, resumes only the oldest edge,
  processes whole symbols sequentially, and exports CSV only at the end. Its
  original data algorithm remains available; the scheduler instead uses an
  isolated fair research worker with aware timestamps, persistent cursors and
  priority gap tasks. Existing independent workers must be stopped at cutover.
- Latest remote `collect_toss_1m.DEFAULT_SYMBOLS` already contains **50 symbols**.
  This exact list is reused without copying/replacing it. Existing Tick universe_v3
  remains 30. Optional external JSON override accepts 1–50 existing operator symbols.
- Auth V2 already fails closed when the single-issuer broker is unavailable.
  Added safe stale-socket recovery and broker lifetime lock; the new worker
  passes the exact rejected token to broker refresh to avoid reusing a newly
  revoked cache entry; credential clients
  do not need local secrets in broker mode.
- Tick default includes DAY/PRE/REGULAR/AFTER. It stays compatible outside this
  deployment; scheduler launches it with `--regular-only` (XNYS).
- Tick stream previously could wait until session close after SIGTERM; now
  cancels the receiver and drains the existing writer before release.
- Missing finalized CSV prevented downloads during collection. The year export
  now uses a read-only SQLite transaction including WAL, with CSV fallback.

## Architecture and fail-closed behavior

`research_collection_scheduler.py` owns one subprocess at a time. Duplicate
scheduler prevention uses flock; both current worker CLIs acquire the SAME
worker flock. A persisted `ownership.json` also makes the legacy 1m CLI refuse
standalone execution, and omitted request guards inherit the central permit.
Switching invalidates the REST permit before signalling shutdown,
then waits for the child to exit. Tick failure never authorizes backfill during
regular hours. Retries use bounded exponential backoff. systemd owns the complete
process group; reboot does not rely on stale PIDs.

XNYS provides holidays, DST and early closes. Every minute the scheduler checks
the existing Toss US calendar REST implementation (MARKET_INFO limiter). Only
matching, timezone-aware regular windows authorize backfill. Missing/conflicting
Toss calendar => ERROR outside regular hours, no new candle requests. During
XNYS regular hours Tick can continue with an explicit unconfirmed-calendar
warning. Market holidays/weekends authorize backfill only when the next session
window is confirmed. Closed sessions are not assumed from a failed API call.

Before every candle request/retry, including AFTER waiting for the shared rate
limiter, worker checks a four-second scheduler lease, local XNYS clock and a
90-second pre-open deadline. Long Retry-After is honored and interruptible. Cooldowns are per API group and
shared across processes without holding the lock during sleeps. A guarded delay
over 30 seconds is deferred to a persisted worker cooldown rather than pinning
a symbol turn indefinitely. A
request started before the guard may finish/commit afterwards; no NEW request
starts during regular hours. Tick waits for that durable completion. A hung
worker blocks switching rather than allowing overlap; inspect journal and do
not start another worker to work around it.

REST request count (attempts), HTTP 401/429 count and WebSocket receive counters
are separate. WebSocket frames never reserve REST slots. Nominal current Toss
TPS limits were not independently reverified; existing conservative spacing
is retained and backfill additionally sleeps two seconds per symbol/page.

### Backfill persistence

Output defaults `/mnt/research/collection-50-v1`, independent of production
`data/toss_1m` and `data/toss_ticks`. Source data isn't moved, rewritten or deleted.
Existing research SQLite gets an online consistent backup BEFORE the first
additive timestamp index/write. Backups and manifests are retained. Inserts
are append-only; conflicting duplicate UTC instants roll back the page, exact
same-price duplicates are skipped. Currency/OHLCV/timezone/minute alignment are
validated. Original timestamp strings are retained; comparison uses parsed UTC.
SQLite WAL + synchronous FULL commits, then atomic resumable checkpoint files.
One day's minute set at a time during coverage auditing; one symbol's full
checkpoint in memory. Page-sized API buffers, not entire histories.

Initial coverage audit uses END labels [T-1m,T), XNYS RTH expectations. For the
new 2026 range, backward history comes first, then latest incomplete dates.
Legacy explicitly pre-2026 ranges retain the known AAPL/TQQQ gap priority.
One API page per symbol turn; failures
have a 60-second per-symbol cooldown. Each gap task ends at its date boundary,
empty API response, 10 zero-add pages or 20 pages. Failed/partial gap outcomes
remain in metadata; no fabricated fill or false completion. History termination
is separately `API_BOUNDARY_UNVERIFIED` / `NO_PROGRESS_LIMIT`.

An API response with an earlier date is captured in first/last response and gap
coverage; it is not evidence the requested day exists. An increasing cursor or
response after the requested cursor is rejected BEFORE insert. Invalid OHLCV or
conflicting duplicate leaves existing candles intact. Missing sessions from
before listing remain missing; no point-in-time listing evidence is fabricated.

Collection now explicitly accepts 2026 candles up to October 31, following the
operator's updated collection scope. Metadata coverage includes those dates;
download OHLCV SELECT and all existing research Final OOS guards remain bounded
before 2026. No feature/training/evaluation changes are made. Collection is not
Final OOS training.

### Monitoring / download

Public GET `/api/research/collection50/status`: whitelisted local status only,
scheduler mode/ET/KST/next regular window/health/staleness, rows/counters/coverage
and six-day outcomes. No Toss call triggered by web polling (3s main / 5s admin).
Admin POST `/api/research/collection50/control` accepts ONLY `{paused: bool}` via
existing admin session. No shell/path parameters, no trading controls. Automatic
scheduler's enabled/active unit prevents legacy admin worker start/restart/enable.

Admin GET `/api/research/collection50/download/SYMBOL?year=2022..2025`: current
SQLite read snapshot preferred, CSV fallback. Gzip in a unique temporary folder
OUTSIDE source, then hash/length before HTTP headers, then bounded streaming.
`X-Content-SHA256`, `X-Export-Rows`, `X-Data-Validation` exposed through Pages CORS.
Browser verifies SHA when WebCrypto is available. No rows forward-filled. Naive
CSV timestamps fail rather than silently being skipped. Download is provisional,
NOT reviewed price-adjustment/completeness evidence. >=10GiB or10% free reserve
required; temp export cleaned after response. Limit downloads to one symbol/year
on a small instance. UI collapses details on mobile. Offline export tool also
supports SQLite without requiring final CSV and produces file SHA manifests.

## EC2 rollout — OPERATOR commands, NOT executed by Codex

There is no EC2 connection in this session. Current server, operator symbol overrides,
mounts/free space, OAuth broker and live market delivery are NOT verified here.
Do not claim deployment from a GitHub commit. Plan a collection-only maintenance
window; do not stop trading/dashboard to apply unreviewed source changes.

1. Record current unit definitions/active/enabled states and manual workers:

```bash
cd /home/ubuntu/market-career-dashboard
git status --short
git branch --show-current
git log -1 --oneline
systemctl is-active toss-token-broker.service collect-toss-ticks.service collect-expanded-universe.service
systemctl is-enabled collect-toss-ticks.service collect-expanded-universe.service
systemctl cat toss-token-broker.service collect-toss-ticks.service collect-expanded-universe.service
pgrep -af 'collect_toss_1m|collect_expanded_universe|collect_research_1m|collect_toss_ticks|research_collection_scheduler'
findmnt /mnt/research
df -h /mnt/research
```

Do not print credentials or tokens. Save service definitions and progress files
in a NEW timestamped external backup folder. Record local source diff without
secret files. With local modifications/divergence, normal reviewed merge is
required; never reset or overwrite the running backend whole-file blindly.

```bash
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest research.test_research_collection_scheduler research.test_collect_toss_ticks research.test_toss_auth_v2
```

2. Verify the actual existing 50-symbol default and any operator overrides:

```bash
.venv/bin/python -c 'from scripts.collect_research_1m import selected_symbols; s=selected_symbols(None); print(len(s), ",".join(s))'
```

If EC2 uses a deliberately different list, save that reviewed list as a JSON
`{"symbols": [...]}` in `/etc/market-career-dashboard/collection-symbols.json`
and set the NONSECRET `COLLECTION_SYMBOLS_FILE` environment override. Otherwise
no symbol config file is required; the current collector's 50 defaults are used.

3. Verify all existing Toss clients already use Auth V2 broker mode; otherwise
root/ubuntu legacy minting can still revoke its token. Follow TOSS_AUTH_V2.md.
Update `/opt/market-career-toss-auth` broker/auth files with reviewed installation
and controlled broker restart only; no new credential copies. Existing secrets
stay at their current protected location. The broker socket permissions/UIDs
must match Ubuntu actual UID/GID. Ensure no independently minting process remains.

4. Stop **ONLY collection workers**, let current request/SQLite commit finish.
Confirm every old/manual collection process is gone; do not kill -9 or delete
WAL. Record previously active/enabled states for rollback.

```bash
sudo systemctl stop collect-toss-ticks.service collect-expanded-universe.service
sudo systemctl disable collect-toss-ticks.service collect-expanded-universe.service
sudo install -d -o ubuntu -g ubuntu -m 0750 /var/lib/market-career-dashboard/collection
sudo install -d -o root -g root -m 0755 /etc/market-career-dashboard
sudo install -d -m 0755 /etc/systemd/system/collect-toss-ticks.service.d /etc/systemd/system/collect-expanded-universe.service.d
sudo install -m 0644 ops/collection-worker-dropin/10-scheduler.conf /etc/systemd/system/collect-toss-ticks.service.d/10-scheduler.conf
sudo install -m 0644 ops/collection-worker-dropin/10-scheduler.conf /etc/systemd/system/collect-expanded-universe.service.d/10-scheduler.conf
sudo install -m 0644 ops/research-collection-scheduler.service /etc/systemd/system/
```

Ensure research DB/directory ownership permits ubuntu read/append and online
backup. Inspect specific files with `stat`; do not use recursive chown/chmod.
For root/ubuntu common limiter, inspect the ONE existing lock inode and give
root+ubuntu write access to that FILE only (e.g. root:ubuntu 0660). Do not replace
an active lock inode. Every updated Toss process in any worktree should use
`TOSS_RATE_LIMIT_FILE=/home/ubuntu/market-career-dashboard/data/.toss_api_rate.lock`.
Old production processes use that default already. Never distribute OAuth token
values in Environment files.

Create `/etc/market-career-dashboard/collection.env` with NONSECRET overrides
only (or an empty root-owned file). Defaults use production checkout code,
`/mnt/research/collection-50-v1` research data, original Tick root. Set same
`RESEARCH_COLLECTION_STATE_DIR` in dashboard service if overriding its location.
API service must have group access to control.json; default server runs root,
worker directory belongs ubuntu. If changing server service UID, review 0750
parent and 0640 control file ownership; don't weaken authentication.

```bash
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/research-collection-scheduler.service
sudo systemctl enable --now research-collection-scheduler.service
systemctl status research-collection-scheduler.service --no-pager
journalctl -u research-collection-scheduler.service -n 100 --no-pager
curl -fsS http://127.0.0.1:8080/api/research/collection50/status
```

Updating running server.py requires the established reviewed dashboard deployment
procedure; source on disk alone does not update Python handlers. Restart dashboard
ONLY after confirming that doing so does not interrupt active trading/paper
workers; use an approved service window. No dashboard restart was done here.
No service installs or starts happen automatically from the repository.

5. Verify real market transition in both directions: at open only Tick child,
at close only backfill child; compare permit/status and journal, UTC/ET/KST,
Toss/XNYS window and early close. Test admin pause/resume through existing login.
Inspect Tick ACK/subscribed/reconnect/outage and SQLite row increments. An ERROR
calendar mode is a valid safety block; do not bypass lease to force collection.

Pages: explicit public UI assets deploy from research branch changes via the
Pages workflow above; `main` is not merged/pushed. In Research & Data expand symbol details,
login as admin, choose 2022–2025 and download a single CSV.gz. Header SHA and
provisional label are available; it is not automatically a valid V07 baseline.

### Offline Codex handoff / known gaps

```bash
.venv/bin/python scripts/export_collection50_research.py \
 --source /mnt/research/collection-50-v1 \
 --output /mnt/research/codex-v07-UNIQUE_NEW_ID \
 --symbols AAPL,TQQQ --years 2022,2023,2024,2025
.venv/bin/python scripts/repair_toss_1m.py audit \
 --data-dir /mnt/research/collection-50-v1 --symbols AAPL,TQQQ \
 --timestamp-kind end --out /mnt/research/UNIQUE_NEW_GAP_AUDIT.json
```

Do not run V07 merely because collection progressed: inspect six-day 390/390
RTH, SPY/QQQ/SOXX reference coverage, adjustment provenance and manifest first.
Existing campaign/audit flags are never changed. Codex access still requires an
authenticated browser download/attachment or authorized transfer; no EC2 access
is implied by producing an archive. Final OOS remains CLOSED.

### Rollback

Stop/disable ONLY the new scheduler and wait for both children to exit. Preserve
all new records, backups, checkpoints, progress files and originals. Remove only
the installed scheduler conflict drop-ins (after comparing against installation
backup), reload units, restore original independent collectors' recorded enabled
and active states. Explicitly rename the scheduler `ownership.json` to a dated
rollback record after disabling the scheduler (do not delete research data).
Revert code by a normal reviewed revert commit, not reset/clean.
If restoring a DB is actually necessary, stop all writers, compare backup and
current SHA/rows and use a separate staged/verified restoration; never overwrite
new valid candles merely to roll back the scheduler. No automatic DB rollback or
cleanup destroys collected data.

## Local validation record (2026-10-11)

- Pre-change baseline: 462 Python tests PASS (38.980s).
- Final implementation: 492 Python tests PASS (41.062s); 30 new scheduler,
  backfill, broker/cooldown, shutdown, cross-process lock and HTTP/export cases.
- All 10 JavaScript smoke suites PASS, including local DOM scheduler/coverage
  rendering and syntax of index/admin scripts.
- Changed Python py_compile and scheduler/worker/Tick/export/broker CLI help PASS.
- Validation interpreter Python 3.12.14; EC2 Python 3.14 still needs runtime check.
- Existing Tick fixture unclosed-file ResourceWarnings remain non-failing;
  no assertions were suppressed or tests disabled.
- Public Pages fetch succeeded; new scheduler UI is NOT deployed there.
- HTTPS API request in this cloud environment failed TLS hostname validation
  (observed certificate subject did not match IP). Verification was NOT disabled.
  This does not establish the real EC2 backend version or certificate health.
- No real OAuth/Toss requests, real Tick session, historical downloads, EC2
  service changes, DB merges, research campaigns or full backtests were performed.
- All production data and research artifacts remain untouched. No main changes.
