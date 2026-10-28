# Toss market-data collection: home-server migration (phase 1)

This stage migrates only the **data collector code**, not trading APIs or order submission.
The public dashboard API at 127.0.0.1:18084 and SSH-only admin at 127.0.0.1:18085 remain unchanged.

## Provenance

The code is taken from the pinned commit of the repository's `strategy-engine-v3` branch, extracted with `git archive` into `/srv/apps/_deploy/stock-collection/releases/<commit>`. The `main` checkout and deployed dashboard image are not modified.

- `setup.sh` stages the original Toss history and tick collectors and installs their dependencies in an isolated virtual environment; it uses API credentials **already saved** in the SSH-only admin key vault to create a restricted 0600 broker configuration. It starts **no collection job**.
- `smoke.sh` starts a temporary single-issuer local OAuth broker, fetches up to three pages per symbol for NVDA/SOXL/SOXS/SPY, writes restartable SQLite files to `/srv/ai-data/stock-collection/historical-smoke`, prints summary rows and shuts down the broker. It does not start a scheduled or real-time job.
- The script uses +09:00 ISO date boundaries because the existing historical collector compares API timestamp strings lexicographically. A future production scheduler should compare normalized datetime objects instead.
- Research files remain **unverified** for complete per-minute trading-day coverage even when an individual call succeeds.
- Actual tick trades are a lossy feed and cannot be assumed to provide all executions.

## Installation (run as heh-heh)

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/setup.sh > /tmp/stock-collection-setup.sh
git -C /srv/apps/market-career-dashboard show origin/main:deploy/stock-collection/smoke.sh > /tmp/stock-collection-smoke.sh
bash /tmp/stock-collection-setup.sh
bash /tmp/stock-collection-smoke.sh
```

If `python3 -m venv` fails because the package is missing, install `python3-venv` and retry setup. Do not run the scripts with sudo.

## Security and migration status

- Only the home-server user can read the broker credentials; no tokens or secrets are passed on a command line. A temporary Unix-domain socket has local UID checks.
- Never publish the broker socket, credentials, SQLite directory, or admin endpoints via Tailscale Funnel.
- The OAuth broker owns the token. Do not run competing independent token issuers at the same time.
- No actual orders, account information, trading bot control or automatic order submission.
- Phase 2 must verify checkpoint integrity, session calendar and data retention, deploy a single-service scheduler with explicit pause/resume, and add verified status to the private admin portal before enabling daily historical and tick streams.
