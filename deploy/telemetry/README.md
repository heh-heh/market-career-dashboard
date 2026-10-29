# Home Server Resource Monitor (GitHub Pages read-only + local Admin)

The operator requested a graphical view of CPU, RAM, internet/network transfer,
power and storage from the existing GitHub Pages dashboard.

## Access separation

| Feature | Access | Credentials & safety |
| --- | --- | --- |
| [Server status graphs](https://heh-heh.github.io/market-career-dashboard/server-status.html) | **PUBLIC, read-only** | CPU/RAM/traffic/disk percentages and optional CPU package power only; no names, passwords, IP, processes, ports or API keys |
| [Pages login](https://heh-heh.github.io/market-career-dashboard/admin.html) | Login form in Pages; private backend in Tailnet | ID + password + Google OTP validated on home server, via Tailscale Serve |
| Private manager → Server status | Same Tailnet, authenticated | Graphs plus authenticated existing read-only resource diagnostics |
| API keys, broker credentials, Git control, service switches | **Private home-server manager only** | Never mounted into the public dashboard image or exposed via public Funnel |

Important: **being accessible via GitHub Pages does not make a remote admin console secure**. Only the telemetry *read-only* screen is internet-public. Administrator control remains on a non-public Tailscale-only host or loopback SSH tunnel. No client-side password or OTP-only guard is treated as authorization.

## Measurements

Host-side Python service `market-server-telemetry.service` samples once per minute using Linux `/proc/stat`, `/proc/meminfo`, `/sys/class/net` and disk usage. It keeps up to **720 data points (~12 hours)** in:

`/srv/ai-data/market-dashboard/server-telemetry.json`

The existing Docker **read-only dashboard API** sees only that data volume; a new explicit GET `/api/server-telemetry` returns a strict allowlisted JSON schema with measurements and series. The private admin fetches the *same* sanitized API endpoint locally and renders the same SVG chart library.

- CPU: host CPU non-idle time ratio since the last sample (first sample has no delta)
- RAM: physical memory used, MemTotal minus MemAvailable
- Network: receive/transmit Mbps calculated from running physical NIC counters; excludes virtual interfaces to avoid double-counting
- Disks: root and `/srv/ai-data` usage as percentages, GiB used / total; may be the same filesystem
- Power: **optional Intel RAPL CPU-package power** (difference in `energy_uj` / elapsed seconds). Often inaccessible without sysfs permissions, so marked “측정 불가” when not measurable. **Full PC wall-socket power is NOT available without a smart plug or meter**, and must never be confused with CPU power. Hardware GPU/fans/PSU are not included.
- The public endpoint does **not** reveal per-process usage, usernames, network interface names, actual IPs, internal paths or secrets.
- If the latest sample is older than 3 minutes, UI reports stale.
- The graphs use public data. Do not publish them if even aggregate server utilization must be kept private; restrict the page/API to Tailnet instead.

## Deploy from Ubuntu SSH (user `heh-heh`)

First stage the independent, low-resource host sampler:

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/telemetry/install.sh > /tmp/market-telemetry-install.sh
bash /tmp/market-telemetry-install.sh
```

Offline test should print `HOST_TELEMETRY_OFFLINE_TEST=PASS`, and the installer should print `HOST_TELEMETRY_SERVICE=active`. This **does not** affect the live Toss collector or its OAuth token broker.

Then deploy the public dashboard API code (the Docker API needs the new allowlisted route). **This installer rebuilds and restarts only the dashboard's public API + hourly dashboard collector**, not the independent stock-tick research service. Inspect any local repo branch changes before invoking:

```bash
git -C /srv/apps/market-career-dashboard fetch origin main
git -C /srv/apps/market-career-dashboard show origin/main:deploy/dashboard/install.sh > /tmp/market-dashboard-install.sh
bash /tmp/market-dashboard-install.sh
```

The installer may require that your operating checkout is on `main` and it refuses to overwrite an unrecognized running service. Don't bypass its guards.

Finally update the separate authenticated admin UI, to add its charts:

```bash
git -C /srv/apps/market-career-dashboard show origin/main:deploy/admin-console/install.sh > /tmp/market-admin-install.sh
bash /tmp/market-admin-install.sh
```

Verify:

```bash
systemctl is-active market-server-telemetry.service
curl -fsS http://127.0.0.1:18084/api/server-telemetry | python3 -m json.tool | head -30
systemctl is-active market-dashboard-admin-console.service
```

For an off-network browser, the read-only HTTPS Funnel at `https://heh-heh.tail39e546.ts.net` must be functioning and serving the new dashboard-only API (443). Do **not** use the private administrator HTTPS Serve :8444 for public metrics.

**The GitHub Pages view is static UI**: it cannot measure your laptop's resources or talk directly to Linux sysfs. The home server telemetry sampler + Docker API must be installed for actual data to appear.

## Diagnostics

```bash
sudo journalctl -u market-server-telemetry.service -n 30 --no-pager
ls -lh /srv/ai-data/market-dashboard/server-telemetry.json
curl -i http://127.0.0.1:18084/api/server-telemetry
```

No real watt value? This is expected if the machine lacks RAPL power sensor access. CPU package draw is not the same thing as wall power. A smart-plug API/meter integration can be planned in a separately reviewed step. **Never substitute an invented total wattage**.

## Scope limitation

Historical charts begin at sampler installation, not in the past. 12 hours is an in-place sliding window, not full historical monitoring. None of the commands here grants remote control to the GitHub Pages site or changes the private MFA/login policy.
