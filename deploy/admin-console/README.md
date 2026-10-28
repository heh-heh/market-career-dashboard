# Home-server administrator portal (SSH only)

This is the **private** companion to the public Market & Career Dashboard. It is not the old AWS administration API and must not be exposed through a public Tailscale Funnel.

## What is restored

- Main GitHub Pages dashboard: **관리자 로그인** link opens the private login.
- SSH-only administrator login at `http://127.0.0.1:18085/`, password verification using PBKDF2-SHA256, 8-hour HttpOnly SameSite=Strict session cookie, CSRF check for changes, login throttling.
- AI: research and bot status (honest `NOT_DEPLOYED` until the V0.8.3 engine is migrated).
- Collection: dashboard collector status and diagnostics; historical stock data and tick feed show migration status, **not simulated running services**.
- Settings: read-only public API status, private server/Git diagnostics, and a local API credential vault (values are never returned by API).
- The credential vault is saved only to `/srv/apps/_deploy/dashboard/admin-console/private/api_keys.json` with mode 0600. The password verifier is `credentials.json`. API keys are **not yet wired into trading, research, or collector processes**. They are stored as restricted plaintext, not encrypted at rest.

## Install or update on the Ubuntu home server

Run **without sudo** as `heh-heh`. The first installation asks interactively for a new administrator password (at least 12 characters).

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/admin-console/install.sh > /tmp/market-admin-install.sh
bash /tmp/market-admin-install.sh
curl -fsS http://127.0.0.1:18085/api/health
```

From Windows PowerShell, while Tailscale is connected:

```powershell
ssh -N -L 18085:127.0.0.1:18085 heh-heh@100.86.121.51
```

Then open `http://127.0.0.1:18085/`, or click the administrator button in the public dashboard. The tunnel must remain open for this link to work. The public GitHub Pages website cannot authenticate directly against the localhost service; login occurs after the browser navigates to the private portal.

## Safety and status

- Do not use `tailscale funnel` or router port forwarding for port 18085.
- Do not commit the private directory, credentials, SSH keys, or API keys.
- No arbitrary shell, Git push/merge, or live trading is available from the portal.
- Existing Docker production API (18084), hourly collector, Chess Academy, and Portainer are unchanged.
- Failed portal installs restore the previous systemd service and release when possible. The private credential files remain untouched.
- To diagnose: `sudo systemctl status market-dashboard-admin-console --no-pager` and `sudo journalctl -u market-dashboard-admin-console -n 40 --no-pager`.
- The AI research engine and historical/tick collectors must be deployed separately before their sections can report active work.
