# 50-symbol collection and V0.7 Codex research handoff

## Deployment reality
These changes are in the GitHub research branch. GitHub Pages `index.html`
updates after Pages deployment, but its API calls require the running EC2
`server.py` to be deliberately updated. Do not overwrite the operational
server with an unreviewed whole-file copy. Inspect its local changes,
backup, and merge only reviewed API portions in a maintenance window.

### Collection status
`GET /api/research/collection50/status` is metadata only: ticker, progress,
saved row count, oldest/latest timestamps, status. It does not claim
390/390 RTH coverage or valid V0.7 baseline.
Data source: `/mnt/research/collection-50-v1/_collection_progress.json`.
Configure `COLLECTION_50_DIR` only if that directory differs.

### Download
On the Research & Data tab, admin login first, select year 2022-2025 and
download a ticker's gzip CSV via
`GET /api/research/collection50/download/AAPL?year=2024`.
The endpoint requires the established admin bearer session, validates ticker
name and source path, filters by US/Eastern calendar year, and deliberately
will not serve 2026 Final OOS. Large exports can consume CPU and time,
so start with one ticker/year and monitor EC2 resources.

### Codex offline work package
For a portable copy independent of the web API, in the research worktree:

```bash
/home/ubuntu/market-career-dashboard/.venv/bin/python \
  scripts/export_collection50_research.py \
  --source /mnt/research/collection-50-v1 \
  --output /mnt/research/codex-v07-data-20261011 \
  --symbols AAPL,TQQQ \
  --years 2022,2023,2024,2025
```

Use a *new* output directory each time. Archive contains 8 gzip CSV files
plus `manifest.json` with SHA256 and row counts. This does not publish the
files online, give Codex EC2 access, or claim missing days are filled.
Download the archive/files locally or attach to the Codex task through an
authorized transfer path. Never commit raw market archives or OAuth secrets
to GitHub.

### Research policy
Codex may audit actual 2022-2025 data, compare old/new datasets, propose
patches to timestamp/order/adjustment/pagination bugs, add regression tests,
and create a *new* immutable candidate V0.7 input snapshot. Run the V0.7
campaign only once prerequisites (coverage, OHLCV, source quality and price
adjustments) are reviewed and pass. Historical V0.7 baselines are immutable.
No broad silent merges into production candles, no synthetic fill, no
unreviewed 2026 Final OOS, and no live order changes.
