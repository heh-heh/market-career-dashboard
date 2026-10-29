# Home stock data collection — phases 1 and 2

## New: 50 US symbols / 2015-01-01 to 2026-10-01 (inclusive)

The **historical 1-minute collector** now has a separate, opt-in research
target: **50 curated US symbols**, from **2015-01-01** through
**2026-10-01**, with timestamps evaluated in US/Eastern exchange time.
The real-time 1-tick worker and its existing data remain unchanged.

**Important:** The external Toss historical API may not provide 1-minute data
back to 2015 for every stock. Later-listed symbols such as ARM/COIN/HOOD/PLTR
did not trade publicly throughout the full period. A requested historical
range is *not* a guarantee of retrievable records. In the administrator UI,
**scan progress** means how far the API pagination cursor moved through the
requested dates, *not* the percent of actual OHLCV rows obtained. A separate
verified-session ratio is only shown when a collector pass has ended; missing
sessions and errors are never counted as 100% complete.

Historical output lives in a dedicated folder:

`/srv/ai-data/stock-collection/historical-50-2015-2026/`

The previous `historical/` and `historical-smoke/` datasets are preserved.
Historical symbol definitions are private and separate from any legacy
real-time tick subscriptions:

`/srv/apps/_deploy/stock-collection/private/historical-symbols.json`

The new independent start gate is:

`/srv/ai-data/stock-collection/runtime/collection/historical-control.json`

The gate is created with `"enabled": false` and **is not changed by a
reinstallation**. Existing scheduler pause state is also preserved. To
collect, explicitly use the private administrator page **자료수집 → 주식 과거
데이터 → 과거 50종목 수집 허용** and ensure the overall scheduler is resumed
(the live 1-tick worker still has exclusive ownership during the US session).
Old real-time tick symbols and credentials are not changed.

### Start the historical collector without reinstalling (SSH)

The 50-symbol history worker requires **both** `historical-control.json.enabled=true`
and `control.json.paused=false`. A running systemd unit is not proof that
the historical worker is active. To use the CLI without restarting services:

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/control.sh > /tmp/market-stock-control.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/history-control.sh > /tmp/market-history-control.sh
bash /tmp/market-history-control.sh status
bash /tmp/market-history-control.sh resume
bash /tmp/market-stock-control.sh resume
bash /tmp/market-history-control.sh status
```

An older installer omitted `control.sh` from the deployed `bin/` folder.
The installer now stages both scripts, but **no reinstall is needed** to run
the temporary scripts above. For a paused job, `ADMIN_HISTORY_PAUSE` means
the private admin pause route was invoked. The scheduler itself never sets
that control-file reason.

Even with the two permits enabled, downloading requires an **officially
confirmed closed US market session**. During US regular hours the real-time
tick collector has priority. Check `runtime/collection/status.json` for
`collector: backfill` and monitor
`historical-50-2015-2026/_collection_progress.json`. Never disable
calendar guards merely to force the download.

### Install/upgrade via SSH (user heh-heh)

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/setup.sh > /tmp/stock-collection-setup.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/install-services.sh > /tmp/stock-collection-install-services.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/admin-console/install.sh > /tmp/market-admin-install.sh

bash /tmp/stock-collection-setup.sh
bash /tmp/stock-collection-install-services.sh
bash /tmp/market-admin-install.sh
```

The setup stages an isolated release from `strategy-engine-v3` with a
reviewed `main` branch overlay that applies the two fixed dates and checks
for the independent historical start gate. If the frozen upstream source
changed unexpectedly or the guard cannot be installed, staging **fails
closed**. The stock service install restarts only stock-specific systemd
units; it does not start the 50-symbol backfill without an explicit opt-in.

After login, the private administrator UI shows:
- overall cursor scan percent (arithmetic average of 50 per-symbol cursors),
- progress bars per ticker, number of stored bars and API pages,
- completed/missing US trading-day audit percent when confirmed,
- paused/running/error status, with filters and a manual refresh.

The new 50-symbol list is based on the existing collector's 50-symbol
universe. If the provider stops at an earlier historical boundary, the
worker preserves the collected rows and marks the pass **unverified**;
no fabricated backfill, no forced 100%, and no trading API.

---



This is **market-data-only**. It never accesses account balances, makes orders, enables automated trading, or opens a public admin API. All artifacts are stored on the home server under `/srv/ai-data/stock-collection`.

## Verified smoke test (2026-10-28)

The operator confirmed Toss OAuth OK, 1-minute candles HTTP 200, and four research symbols (NVDA, SOXL, SOXS, SPY) saved **300 rows each** under `historical-smoke/` (1,200 total). This does not demonstrate complete daily coverage; records included US extended hours. Tick and periodic services were not yet running.

## Architecture

- A *single* OAuth broker (`market-stock-token-broker.service`) runs as `heh-heh`, reads a restricted 0600 credential file, and serves tokens only to authorized local Unix socket clients.
- A scheduler (`market-stock-collection.service`) runs as `heh-heh` with limited memory, CPU and filesystem access. It is **paused on first install**, and reads `runtime/collection/control.json`. It launches only one worker at a time: the US regular-market WebSocket tick collector during trading hours, or a checkpointed historical one-minute REST backfill outside trading hours. If the official calendar does not confirm the session, REST backfill fails closed.
- First deployment uses four explicitly configured symbols. The symbol list lives under `/srv/apps/_deploy/stock-collection/private/symbols.json` so updates don't reset the operator's selection. Only reviewed symbols can be added; valid list length is 1–50.
- Historical SQLite: `/srv/ai-data/stock-collection/historical/`; tick sessions/status: `/srv/ai-data/stock-collection/ticks/`; scheduler state/leases: `/srv/ai-data/stock-collection/runtime/collection/`.
- Old smoke data is preserved at `historical-smoke/`. No existing dashboard, Chess Academy, or Portainer containers are changed.
- Tick status in the legacy worker used the old `ROOT/data/toss_ticks` path. The stage script maps that path to the persistent `ticks` directory without editing the frozen research branch.

## Install from home server SSH (as heh-heh, not sudo)

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/setup.sh > /tmp/stock-collection-setup.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/install-services.sh > /tmp/stock-collection-install-services.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/control.sh > /tmp/stock-collection-control.sh

bash /tmp/stock-collection-setup.sh
bash /tmp/stock-collection-install-services.sh
bash /tmp/stock-collection-control.sh status
```

The service installer calls `sudo` only for writing the two new systemd unit files and restarting the two **market-data-specific** services. Its initial control file is explicitly paused. Rerunning does not overwrite existing pause/symbol config.

## Enable and pause

Open the SSH-only portal at `http://127.0.0.1:18085/` (SSH tunnel required), then visit **자료수집 → 주식 과거 데이터** or **1틱 단위 수집기** and select *자동 수집 재개*. Update the administrator portal with the latest `deploy/admin-console/install.sh` before using the buttons.

Alternative SSH commands (explicit operator action):

```bash
bash /tmp/stock-collection-control.sh resume
bash /tmp/stock-collection-control.sh status
bash /tmp/stock-collection-control.sh pause
```

The scheduler is automatically restarted by systemd after reboot. A previous `resume` persists; a previous `pause` also persists. The CLI changes **only** the collection pause state; it does not start trading or touch other apps.

## Status and maintenance

```bash
systemctl is-active market-stock-token-broker.service market-stock-collection.service
sudo journalctl -u market-stock-collection.service -n 40 --no-pager
ls -lh /srv/ai-data/stock-collection/{historical,ticks}/
```

If the old `smoke.sh` was already run successfully, do not run it in parallel with the live scheduler because the new scheduler owns the backfill lock.

## Important limitations

- The inherited research worker currently has a hardcoded **October 31, 2026** historical upper bound, and its research dataset's Final OOS remains CLOSED. This is **not** yet a production-ready continuous historical collector beyond this date. It must be reviewed/updated separately, including data provenance and validation, before an indefinite backfill claim is warranted.
- Backfilled candles are not assumed to be complete merely because 300 rows or an HTTP 200 response was received. The scheduler records verified vs missing US regular-session minutes.
- Tick WebSocket messages may be lossy. Store reconnects, drops, and subscription failures; never imply an exchange-complete tape.
- Data usage, OAuth issuance and rate limits are subject to Toss platform policy; the scheduler paces REST requests and refuses uncontrolled parallel workers.
- API keys stored in the private admin vault are restricted plaintext files, **not encryption at rest**; do not sync or commit them.
