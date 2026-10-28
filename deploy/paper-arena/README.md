# V0.9-lite — 20-agent paper market pilot

> **Not the full V0.9 Virtual Market Arena.** This is a smaller, independently isolated *received tick price paper-trading experiment* for watching twenty virtual agents while the historical 1-minute archive is still incomplete. It does **not** have a shared limit-order book, endogenous price impact, genetic evolution, training, or statistically validated profitability. It does not call any brokerage endpoint.

## Design

- Data input (read-only): `/srv/ai-data/stock-collection/ticks/<US-session-date>/{NVDA,AMD,SOXL,SOXS,SPY,QQQ}.csv` and collector `ticks/status.json`.
- Simulation: **20 cash-only virtual accounts**, each initially **$1,000**; *virtual* aggregate capital **$20,000**.
- Strategies: intentionally simple fixed variants (ten momentum, ten mean-reverting). These are **rules**, not trained AI. Each agent uses a restricted, deterministic subset of six markets, up to **three** long-only positions, max **35% of that account's starting capital** per purchase, minimum 90-second decision cooldown.
- Accounting: durable SQLite cash/positions/trades and tick-file byte cursors under `/srv/ai-data/paper-arena`. No leverage, shorting, real positions, transfers or actual orders.
- Execution assumption: simulated BUY at observed latest tick +10bps adverse slippage; SELL at tick -10bps; additionally 5bps *hypothetical fee*. No actual bid/ask queue, spread or market impact. The observed trade price is not a guaranteed fill; these numbers are for a *sandbox* only.
- Guard: tick collector status must be `collecting`, session must be `REGULAR`, and the current time must be inside the stated open-close interval. Received ticks older than 90s or newer than 5s are not used. Missing/stale data results in no fresh paper decisions.
- New session/mid-session boot: initial file offset is current EOF so the agent **never backdates an action** against older rows. Restart with an existing cursor resumes without reprocessing committed rows. Pausing leaves cash, inventory and ledger intact.
- Input limitations: provider is not an exchange-complete tape; duplicates, reconnects, invalid tick records, files changing while read, missing symbols and the lack of real bid/ask can bias the simulation. P&L is mark-to-observed-tick, not executable liquidation value.
- Market/portfolio status is stored at `/srv/ai-data/paper-arena/status.json`. The SSH-only admin aggregates cash, invested value, equity, ROI, holdings by symbol and recent virtual trade events. No agent roster.

## Install (Ubuntu SSH, run as heh-heh)

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/paper-arena/install.sh > /tmp/paper-arena-install.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/paper-arena/control.sh > /tmp/paper-arena-control.sh
bash /tmp/paper-arena-install.sh
bash /tmp/paper-arena-control.sh status
```

The installer fetches the research code into an isolated release, **runs an offline fixture test** (account provisioning, fake ticks, a virtual buy, restart idempotence and pause), and installs a dedicated systemd service. It does not modify the existing market-data collection, Chess Academy, Dashboard API or API key vault. **First install is PAUSED** and waits for explicit activation.

### Update the SSH-only admin UI

```bash
git -C /srv/apps/market-career-dashboard show origin/main:deploy/admin-console/install.sh > /tmp/market-admin-install.sh
bash /tmp/market-admin-install.sh
```

Open `http://127.0.0.1:18085/` over your **existing SSH tunnel**; log in and navigate to **AI → V0.9 가상시장 관제**. Press **가상 시뮬레이션 시작 / 재개** only when ready. Or via SSH:

```bash
bash /tmp/paper-arena-control.sh resume
bash /tmp/paper-arena-control.sh status
bash /tmp/paper-arena-control.sh pause
```

The service starts after reboot and respects the persisted `paused` setting. It never asks for Toss keys and must not be publicly exposed via Funnel.

## Operator views

The private administrator has two independent pages under **AI**:

- **V0.9 가상시장 관제** — combined 20-account virtual equity, cash, realized/unrealized P&L, instrument exposures, latest hypothetical fills and global pause/resume.
- **봇별 상태** — one **status card for each A01–A20** with its rule variant (momentum or mean-reversion), current state, individual virtual cash/equity/P&L/return, held ticker and quantity, entry/observed mark, last virtual trade, and watched symbols. Can filter holdings/cash/profit/loss and sort by return/equity.

Both pages refresh roughly every seven seconds while visible. **State is not streaming fills**: an older/stale mark is labeled, and a card still reflects the last saved read model. The per-agent and aggregate values come from the same persistent SQLite ledger; offline regression tests check that they reconcile.

All agent names are virtual ledger identities. There is no one-to-one mapping to autonomous AI processes or separate API sessions.

## Operational diagnostics

```bash
systemctl is-active market-paper-arena.service
sudo journalctl -u market-paper-arena.service -n 35 --no-pager
cat /srv/ai-data/paper-arena/status.json
```

When the US market is closed, the engine reports WAITING_FOR_MARKET. If installed and enabled mid-session, the engine begins at the end of the current CSV and waits for **new** lines to arrive. It may take a while for its initial EMA signals to warm up; zero early trades is not necessarily an error.

## Scope of this milestone

**Success:** independent, read-only, persisted twenty-account simulator working from *received* US regular-session ticks, with an authenticated status cockpit.

**Not yet implemented:** V0.9 A synthetic shared LOB and A→R transfer experiments, market impact, historical 1m backtests, 5-valid-session evolutionary selection, corporate actions and complete session coverage, recoverability under arbitrary power loss, realistic transaction costs, actual market orders. Do not interpret live paper ROI as proven or predictive results.
