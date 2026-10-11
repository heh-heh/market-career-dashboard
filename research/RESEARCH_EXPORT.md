# Research Data Export

Research storage is read-only. This feature launches only archive jobs, never
training, backtests, collectors, paper trading, broker orders, or Final OOS.
Existing provisional/review flags are exported unchanged. Metadata scanning is
not model evaluation and does not access 2026 prices for research.

## API and authentication

All catalogue, summary, creation, status and direct downloads require the existing
admin Bearer session. No authentication scheme was replaced.

- `GET /api/research/export/catalog`: sources, sizes, update times, opaque run IDs,
  artifact-only performance metadata, symbols and recent jobs. Cached 30 seconds.
- `GET /api/research/export/summary?format=json|csv`: generated
  `evolution_research_summary.json` / `.csv`; also included inside each archive.
- `POST /api/research/export/jobs`: asynchronous creation, returns 202.
- `GET /api/research/export/jobs/<id>`: queued/running/completed/failed, progress,
  files, byte counts, warnings and errors.
- `GET /api/research/export/jobs/<id>/download`: authenticated, streamed ZIP.
- `POST /api/research/export/jobs/<id>/ticket`: authenticated, creates a scoped,
  one-use, 45-second download capability. Enables native browser downloads without
  holding multi-GB Blobs. It cannot authorize another endpoint or job. The admin
  token is never in a URL; ticket query strings are redacted from HTTP access logs.

Example bodies:

```json
{"type":"evolution_summary","runIds":["opaque ID from catalogue"]}
```

```json
{"type":"toss_1m","symbols":["TQQQ","AAPL"],"from":"2024-01-01","to":"2024-12-31"}
```

Export types: `evolution_full`, `evolution_summary`, `evolution_population`,
`evolution_trades`, `V07_FULL_RESEARCH_BUNDLE`, `v4_full`, `research_metadata`,
`toss_1m`, `toss_ticks`. Evolution without runIds selects all catalogued runs.
Raw data always requires explicit symbols and dates. Tick ranges limited to 31
calendar days inclusive; minute data to 3661. Dates partition downloads only, not
research/evaluation. Default max archive 2 GiB; summary decoded payload 64 MiB.
If a full research archive exceeds the cap, select fewer runs and export separately.

## Sources / format

Fixed sources, never caller-supplied filesystem paths:

- `/home/ubuntu/evolution-results`
- `/home/ubuntu/v4-research`
- `<repo>/research/results`
- `<repo>/data/toss_1m`
- `<repo>/data/toss_ticks`
- `<repo>/research/evolution`: README/config metadata only

Evolution runs are discovered by run.json/campaign.json, including nested runs.
Summary retains run/status/config/final_population/validation_summary/
generalization_diagnostics/shortlist/campaign plus README. Full exports retain
supported CSV, CSV.GZ, JSON, JSONL and textual research artifacts. GZ inputs are
streamed/decompressed into ZIP entries. SQLite staging databases are not copied:
use the existing finalized collector CSV/CSV.GZ source, without changing collector
behaviour. Ambiguous simultaneous CSV and CSV.GZ sources fail rather than duplicate.

1-minute CSV selection interprets exclusive END timestamps and New York
bar-start dates (END minus one minute). Aware offsets are honored, including DST.
Naive timestamps fail unless `RESEARCH_EXPORT_1M_TIMEZONE=UTC` or
`America/New_York` is explicitly set based on existing provenance; it is recorded
in the manifest. Existing normalization rejects ambiguous DST minutes. No
forward-fill, adjustment, deduplication or source modification occurs.

Ticks use the actual collector `YYYY-MM-DD/SYMBOL.csv[.gz]` partition date.
Their original source/receive timestamps, connection and delivery sequence are
preserved without inferring naive source timezone. Identical-looking trade rows
remain separate. This LOSSY delivered stream is not a complete exchange tape.

V0.5 Structural / V0.6 Stability / V0.7 Internal Holdout Shortlist labels derive
from version/fitness/selection metadata. Unknown metrics remain null/—; return
units remain as reported, not silently converted. V07 preset follows only
references to already catalogued runs, and includes matched runs in the archive.
The manifest reports unresolved links and partial bundles. Complete evidence
requires populations, both validation summaries, shortlist, diagnostics, declared
2022–23 training / 2024 holdout / 2025 development periods and no unresolved links.
This is packaging evidence, not an endorsement of performance or Final OOS access.
Adapters follow the current V0.7 campaign/shortlist/frozen-validation writers.
Frozen-validation period statistics use stored valid bot rows, never the last-day
status return. Population/genome hashes are not interpreted as filesystem links.
The production artifact contents still require confirmation on EC2; they are absent
from this workspace. Unsupported nested provenance remains visibly unverified.

## Safety / consistency

No source write, rename or delete is used. Directory and file access rejects
traversal, symlink components/escapes and hardlinks, including race-resistant
`openat`/`O_NOFOLLOW` reads. Source roots and export directory must be disjoint.
Only known research file extensions are selected. Hidden files, keys, PEM, OAuth,
token caches, credentials, authorization/cookie/session files and raw logs are
excluded. Secret JSON keys / CSV columns are removed; suspicious strings and
private-key blocks are redacted. Exports are sanitized research copies, not
byte-identical backups of secrets. CSV formula-like text is escaped. Do not put
unlabelled secrets in research artifacts; no content scanner can prove an arbitrary
string is not a secret.

Each file is a bounded snapshot, not a global atomic training checkpoint.
Immutable JSON/GZ/text mutations cause failure. Active append-only CSV/JSONL is
cut at a complete newline, verifies the snapshot prefix, and excludes newly
appended bytes. Rewrites/truncation fail. JSON parsing is streaming/bounded;
no entire population/tick file is loaded. One worker, queue limit 3, catalogue
limit 50k entries per root, metadata documents bounded 1 MiB, per-record bounds.

Manifest: version, exporter version, export UTC time, Git commit, job selection,
run metadata, warnings, source consistency/semantics, exact archiveBytes and files.
Every entry includes exported size/SHA256. Source entries also include source
relative path/size, snapshot bytes/SHA256, modification time, rows, redaction info
and append-during-read. Generated summary entries have their own SHA256. The
manifest's own digest cannot be self-referential; sourceSha256 describes original
snapshot bytes, SHA256 describes the sanitized archived bytes. Archive size is
calculated exactly including the manifest and ZIP64 directory.

## Output / disk / restart

Default `/home/ubuntu/research-exports` (or server `RESEARCH_EXPORT_DIR`). It must
be separate from sources. UUID files prevent collision; private temporary archive
is fsynced through close and renamed only after success. Failure removes only
that job's temporary export. Atomic job metadata survives restart; interrupted
jobs become failed and partial managed outputs are removed. Nothing is resumed
with fabricated progress.

Reserve max(10 GiB, 10% total disk) plus a capped estimate before archiving; periodic
checks and 2 GiB hard archive cap stop work if necessary. Reserve is intentionally
not weakened for small EC2 disks. UI shows precise failure. Completed/failed owned
export files expire after 7 days when catalogue/create requests trigger cleanup
or the service initializes. Unknown files and symlinks are never deleted. Sources
are never cleanup targets. This is lazy expiry, not a system-wide cron cleanup.

## EC2 deployment (manual, no export automatically started)

From the existing checkout:

```bash
cd /home/ubuntu/market-career-dashboard
git branch --show-current
git status --short
git fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3
git merge --ff-only origin/strategy-engine-v3
install -d -m 700 /home/ubuntu/research-exports
.venv/bin/python -m py_compile server.py research_export.py research_export_metadata.py
sudo systemctl restart market-career-dashboard.service
sudo systemctl status market-career-dashboard.service --no-pager
```

Proceed only on strategy-engine-v3; if tracked local changes block fast-forward,
keep them and resolve separately. No reset/clean/data removal or recursive chmod.
Ensure the API service user can read source roots and write only the export
output. Do not restart collectors. Set naive timezone only with established
source provenance. Allow sufficient disk reserve. No new Python dependency.

In browser: Settings → administrator login → data/research monitor →
연구 데이터 다운로드 → 목록 갱신 → select runs or raw symbols/dates → Export
생성 → monitor completion → 다운로드. Summary JSON/CSV are direct small downloads.
UI files alone are synchronized to main/Pages; the API runs strategy-engine-v3.

## Offline verification

```bash
python -m unittest discover -s research -p 'test_*.py'
node research/test_research_export_smoke.cjs
```

Fixtures cover auth/native-download capabilities, traversal, root restrictions,
symlink/hardlink escape, filename/content secrets, summary/run/V07 packaging,
selective END/DST and gzip minute data, tick duplicate preservation, manifest
SHA/exact ZIP64 size, disk guard, interrupted cleanup, append snapshots/rewrite
failure and UI escaping/explicit raw selection. No production data is exported.
