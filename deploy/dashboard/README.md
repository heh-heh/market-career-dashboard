# Home dashboard (main only)

The isolated image includes only the read-only dashboard API and public-data collector.
No broker, token, trading, research or Spot module is copied into it. API POSTs and
non-allowlisted routes return 403 even with valid admin credentials. API data mount
is read-only. CORS permits https://heh-heh.github.io; public data needs no credential.

## Install on the home server (not in a Codex workspace)

Run as `heh-heh`, with Docker group access and write permission to the existing deploy
and data directories. Working-tree changes are never pulled/reset/stashed. The
installer archives `origin/main` into a hash-checked separate release, backs up
changed dashboard JSON/config files, builds, verifies the new API, then stops only
`market-collector-test` and `market-dashboard-api-stage-1` if running. Other
containers (including preview, Chess, PostgreSQL and Portainer) stay untouched.
On failure it restores the preceding Compose release and previously running named
legacy containers. Do not run with sudo. Original snapshot files are never deleted.

```bash
git -C /srv/apps/market-career-dashboard fetch origin main && git -C /srv/apps/market-career-dashboard show origin/main:deploy/dashboard/install.sh | bash
```

Production API: `127.0.0.1:18084` (new port; stage/preview ports are not reused).
Containers: `market-dashboard-api`, `market-dashboard-collector`. Every 3600 seconds,
up to 600 seconds/run, at most four concurrent requests, daily detail cache and a
shared inherited flock prevent overlapping runs, including service restarts.
API/collector limits: 0.5/1 CPU, 384/768 MiB; rotated logs. Restart policy is
`unless-stopped`. Check Docker daemon boot enablement with `systemctl is-enabled docker`.

Files are under `/srv/ai-data/market-dashboard`: dashboard.json (atomic validated
snapshot), dashboard.previous.json (previous good snapshot), job_detail_cache.json,
collector_status.json and collector.lock. No historical Toss files are accessed.
A failed scrape/timeout leaves the last successful snapshot; absent fresh quotes
cannot be advertised as a new successful update. Failed/empty section refreshes
retain their original section timestamp. Job postings are deduplicated before
bounded detail fetching. Missing details do not remove valid listing metadata.

```bash
docker ps --filter name=market-dashboard
curl -fsS http://127.0.0.1:18084/api/health
docker logs --tail 30 market-dashboard-collector
cat /srv/ai-data/market-dashboard/collector_status.json
```

Manual rollback (does not revert/delete collected data):

```bash
bash /srv/apps/_deploy/dashboard/releases/REVISION/deploy/dashboard/rollback.sh /srv/apps/_deploy/dashboard/backups/BACKUP_ID
```

Automatic rollback prints its backup path; manual rollback defaults to `last-backup`.
Backups of Docker inspect output may contain existing environment secrets: directory
mode is private, never upload these files or place them in Git. No `.env` or keys
are mounted into the new containers.

## Free HTTPS / Pages

Use the already-installed Tailscale client on the HOME SERVER, not a new VPN in Codex.
After local validation, install with `bash -s -- --funnel` to request Funnel. Existing
Serve/Funnel configuration is never overwritten. Funnel may require account-admin
approval, MagicDNS/HTTPS and a permitted public DNS name; no paid resources are created.
Only loopback API 18084 is exposed; no admin/SSH/Portainer/DB routes are forwarded.
Do not Funnel the existing preview or stage API. If Serve already belongs to another
project, use a separately reviewed hostname/tunnel rather than resetting Serve.

Once Funnel shows `https://YOUR-HOST.YOUR-TAILNET.ts.net`, on a GitHub-authenticated
machine verify public health, data, CORS and blocked private endpoints BEFORE setting
the public (non-secret) repository variable and deploying:

```bash
RELEASE=$(sed -n 's/^DASHBOARD_SOURCE_DIR=//p' /srv/apps/_deploy/dashboard/home.env)
python3 "$RELEASE/scripts/configure_dashboard_api.py" --url https://YOUR-HOST.YOUR-TAILNET.ts.net --out /tmp/dashboard-config-verified.js
gh variable set DASHBOARD_API_BASE_URL --repo heh-heh/market-career-dashboard --body https://YOUR-HOST.YOUR-TAILNET.ts.net
gh workflow run pages.yml --repo heh-heh/market-career-dashboard --ref main
```

Alternatively set the variable in GitHub Settings > Secrets and variables > Actions >
Variables, then run Deploy GitHub Pages. The workflow independently verifies HTTPS
before replacing the old AWS endpoint in the generated Pages config. Until then the
old endpoint is unchanged and the page can explicitly show its bundled stale snapshot.
Public Pages publish only index/config/dashboard JSON, not admin/research pages or keys.
The main workflow must be permitted by the repository's github-pages environment;
if GitHub blocks main, approve main in that environment (do not alter the research branch).
