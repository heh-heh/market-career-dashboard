# Toss Auth V2 — single-issuer deployment (research-only rollout)

## Why
Previously `toss_auth.py` used independent /tmp caches per Linux UID. Root and
ubuntu could each rotate OAuth tokens, revoking tokens used by the other.
A valid cache expiry is **not** proof the server accepts the token.

## Architecture
- **One broker** running under root owns the OAuth client credentials and token
  cache, and is the **only issuer** after migration.
- Local clients (root/ubuntu) set `TOSS_TOKEN_SOCKET=/run/market-career-dashboard-toss/token.sock`.
- UNIX filesystem socket mode `0660`, owned root and group 1000; parent
  directory mode `0750`, group ubuntu. Linux SO_PEERCRED restricts to UID 0/1000.
- Broker never logs credentials or token data; requests/responses remain local.
- Clients **fail closed** if broker is unavailable: they must not mint tokens.
- Access tokens do not belong in systemd Environment or the repository.
- The broker's root-owned token cache remains in /tmp as before (0600). For
  stronger at-rest protection or multi-EC2 deployments, migrate to a managed
  secret store and dedicated token service separately. This design prioritizes
  single-issuer correctness and least-privilege local access.

## Preflight (no operational code changes)
Check unit identities, active workers, existing broker socket, source paths
and immutable records before any service migration. Do **not** run background
collectors in parallel with the cutover. Do not delete either old token cache.
Do not copy server_secrets.json to the research worktree.

In `/mnt/research/repair-integration-20261011`, pull the remote research branch
with ordinary `git fetch` and `git merge` (no hard reset). Run:

```bash
/home/ubuntu/market-career-dashboard/.venv/bin/python -m py_compile toss_auth.py toss_token_broker.py scripts/collect_toss_1m.py
/home/ubuntu/market-career-dashboard/.venv/bin/python -m unittest research.test_toss_auth_v2 research.test_toss_1m_repair
```

## Controlled installation (requires separate operator approval)
**Do not execute as part of a backfill command.** The install puts the
broker code in a stable root-controlled directory without merging or modifying
the operational checkout. Review unit paths and the `ubuntu` GID (assumed
1000 in the example) before proceeding.

```bash
sudo install -d -o root -g root -m 0755 /opt/market-career-toss-auth
sudo install -o root -g root -m 0644 toss_auth.py toss_token_broker.py /opt/market-career-toss-auth/
sudo install -o root -g root -m 0644 deploy/toss-token-broker.service /etc/systemd/system/toss-token-broker.service
sudo systemctl daemon-reload
sudo systemctl enable --now toss-token-broker.service
```

Then check `systemctl is-active toss-token-broker.service` and socket
owner/mode. **Do not expose or print token content.** Do not restart running
services before ensuring they are configured for broker mode. Configure
`TOSS_TOKEN_SOCKET` in a reviewed systemd drop-in for each root or ubuntu
service using Toss, and restart them in a planned maintenance window; confirm
that no old independently minting processes remain. Any unmigrated clients
can still revoke the broker token. The production checkout must use the new
`toss_auth.py` or compatible broker client code when migrated.

Research worktree invocation after broker is running:
```bash
TOSS_TOKEN_SOCKET=/run/market-career-dashboard-toss/token.sock \
  /home/ubuntu/market-career-dashboard/.venv/bin/python \
  scripts/repair_toss_1m.py collect \
  --data-dir /home/ubuntu/market-career-dashboard/data/toss_1m \
  --staging-dir /mnt/research/staging/UNIQUE_NEW_ID/received \
  --symbols TQQQ,AAPL --timestamp-kind end
```

Staging must be a *new* path. Run `validate` only on a collected staging.
The previous API response to 2024-07-26 returned 2024-07-23 candles. Fixing
OAuth does NOT solve missing-session historical availability or pagination.
A 390/390 RTH completeness check, timestamp correctness, adjusted-price
compatibility, and provenance review are mandatory before any prepare/apply.

Never touch 2026 Final OOS, real trading, completed V0.7 campaigns, or
production candles during the Auth V2 rollout.
