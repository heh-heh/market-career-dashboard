# Server-hosted public dashboard — staged migration (no GitHub Actions deploy)

This deployment is **independent** of the stock collector, Dashboard-only API,
authenticated administrator, Chess Academy, Portainer and GitHub Pages portfolio.

**Before the switch:** Existing `https://heh-heh.tail39e546.ts.net:443`
still exposes only the Dashboard-only API on `127.0.0.1:18084`.
The private administrator remains on Tailscale Serve `:8444`.

## Architecture

- nginx-unprivileged runs as a separate Docker Compose project
  `market-dashboard-public-web`, binding **only 127.0.0.1:18086**.
- Static assets are copied into an immutable release from a **Git revision**.
  No source directories, credentials, .env files or data collector DBs are mounted.
- `dashboard-config.js` is generated to use `window.location.origin` so the
  website and public dashboard API share one origin.
- nginx proxies **GET/HEAD only** for an explicit list of Dashboard-only
  API routes to the existing `127.0.0.1:18084`. Every other `/api/*` returns 404.
- The public administrator login form can point to the private :8444 backend
  for connected Tailnet members. The private administrator service itself
  **must never** be published by Funnel.
- No automatic GitHub Pages workflow or GitHub Actions artifact is required
  to serve the dashboard; the home server must stay powered on and connected.

## Phase 1: Stage, run locally and verify

From SSH on the home server as user `heh-heh`, run one command at a time:

```bash
cd /srv/apps/market-career-dashboard
git fetch origin feature/home-server-web-deploy
git show origin/feature/home-server-web-deploy:deploy/public-web/install.sh > /tmp/market-public-web-install.sh
PUBLIC_WEB_REF=refs/remotes/origin/feature/home-server-web-deploy bash /tmp/market-public-web-install.sh
```

This starts a new *separate* Docker container, not an API/stock/Chess redeploy.
The script checks the API, page, telemetry and denied routes before reporting
`PUBLIC_WEB_READY=http://127.0.0.1:18086/`. No Tailscale changes are made.

Manual checks:

```bash
docker ps --filter name=market-dashboard-web
curl -fsS http://127.0.0.1:18086/api/health
curl -fsS http://127.0.0.1:18086/api/server-telemetry | python3 -m json.tool | head -35
curl -I http://127.0.0.1:18086/server-status.html
tailscale funnel status
tailscale serve status
```

If the new container fails, the installer attempts to restore the old public-web
release (if one exists); it does not touch the old Funnel/API/stock collector.
`docker compose up -d` can report "Started" before Nginx listens; the installer
waits up to 30 seconds and prints recent logs if startup fails. For diagnosis:

```bash
docker ps -a --filter name=market-dashboard-web
docker logs --tail 70 market-dashboard-web
ss -ltn | grep ':18086' || true
curl -sS -i --max-time 5 http://127.0.0.1:18086/api/health
```

If startup fails again, send the container logs; do not modify Funnel or the existing
API until the local listener is healthy.

## Phase 2: OPTIONAL safe public pilot (explicit manual action)

After the localhost checks pass and you inspect the existing Tailscale config,
you can add an **independent pilot** on Funnel port 10000, without changing the
current public API port 443:

```bash
tailscale funnel status
sudo tailscale funnel --bg --https=10000 http://127.0.0.1:18086
tailscale funnel status
```

If the command is accepted, test
`https://heh-heh.tail39e546.ts.net:10000/` from outside your Tailnet.
Funnel access is internet-public, and your home server must remain online.
Do not run `tailscale funnel reset` and do not expose private :8444.

## Phase 3: Switch the existing public origin after pilot succeeds

**Only after** the site/API/telemetry work through the pilot, and after saving
the prior Tailscale Serve/Funnel status, you may re-point public HTTPS 443:

```bash
sudo tailscale funnel --bg --https=443 http://127.0.0.1:18086
tailscale funnel status
```

The normal dashboard then lives at
`https://heh-heh.tail39e546.ts.net/`; the public API stays available at
`https://heh-heh.tail39e546.ts.net/api/health` via the nginx proxy.
Private :8444 still needs separate verification.

To restore the **previous** API-only route at 443 (if it was configured as
documented):

```bash
sudo tailscale funnel --bg --https=443 http://127.0.0.1:18084
```

This rollback command must NOT be used if the inspected original port-443
Funnel target was different.

## Admin login after switching the public site

The public dashboard now links to **https://heh-heh.tail39e546.ts.net:8444/**
instead of trying to POST the credentials from the new public origin.
The private :8444 page hosts its own same-origin ID/password/OTP login.

- Connect the browser/device to the Tailscale tailnet first.
- From the public dashboard, select **관리자** and then **Tailscale 관리자 로그인 화면 열기**.
- The public `/admin.html` route responds 302 to the **Tailnet-only** :8444 URL.
- Never enable Funnel for :8444 or proxy `/api/login` through this public site.
- GitHub Pages legacy login flow remains unchanged.
- Public web redeploy and its `/admin.html` redirect smoke check are required
  after merging this patch. No admin console redeploy is necessary.

## Phase 4: Retire GitHub Pages dashboard AFTER live verification

- Keep the distinct `heh-heh/heh-heh` profile portfolio Pages site:
  `https://heh-heh.github.io/heh-heh/`.
- Change its Dashboard link to the verified new public hostname.
- Disable the `Deploy GitHub Pages` workflow for the *dashboard repository*
  and unpublish that dashboard Pages site in repository Settings > Pages.
  Do not disable the profile portfolio Pages site.
- Review and remove unnecessary artifact archives and caches in GitHub Actions
  storage separately. Changing the web host does not erase prior quota usage.
- The other GitHub Actions checks, if enabled, may still consume GitHub resources.
  Their schedule/permissions need separate review; do not delete them wholesale.

For future web-only deploys after this branch lands on `main`:

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/public-web/install.sh > /tmp/market-public-web-install.sh
bash /tmp/market-public-web-install.sh
```

There is **no automatic background deployment** or extra internet-exposed
admin port in this script. This procedure requires manual SSH execution.
