# Dirty production checkout: worktree deployment and rollback

## Why abb3f7e's original script is incompatible

The original launcher required a clean, branch-based production checkout,
merged Git there, rewrote `/opt/market-career-toss-auth` code, and restarted the
Broker. It also preserved a previous `paused=false` control on redeployment.
These assumptions do not match this deployment. Do not use that command on the
dirty production checkout. The replacement launcher calls the transaction tool.

Production remains on `strategy-engine-v3`, with its original HEAD and index.
The release is an application overlay, **not** a Git merge. Its actual source
SHA is recorded in `plan.json` and `journal.json`; `git log -1` in production
will still show the old checkout commit. Use the deployment ledger and file
hashes to identify the installed version. Do not later run the old Git sync flow
to hide the deployment changes.

## Preservation / conflict analysis

| Input | Policy |
|---|---|
| `data/dashboard.json` | Analyze base/local/source overlap; preserve operating bytes |
| `toss_auth.py`, `toss_token_broker.py` | Analyze overlap, keep local code; require composite compatibility tests |
| `/opt/market-career-toss-auth`, Broker unit, credentials/cache/socket | Never replace/restart/mint directly |
| `data/toss_1m`, `data/toss_ticks`, V0.7/V4/Evolution results | Never copy over, move, clean, train on, or delete |
| Untracked DB/log/backup files | Not enumerated/read for deployment; never overwritten |
| Other tracked application code | Compare production HEAD / working bytes / pinned source; clean 3-way merge only |
| Untracked application-path collision / overlapping code conflict | Block; no arbitrary choice |
| Installed scheduler unit / worker dropins | Analyze 3-way against Git templates; unknown custom configuration blocks |
| `.env`, existing collection env | Preserve; hash-bind and check shared socket/lock settings |
| `.git`, `.venv` | No reset, stash, checkout, clean, merge, index update, or package installation |

Source may be a clean **detached worktree**. Its SHA must belong to
`origin/strategy-engine-v3` history. The default command is analysis only.
It creates a private, unique directory under `/mnt/research/collection-deployments`,
stages source+preserved local code, and runs compatibility tests in temporary
state directories with live flags disabled. Production sockets/outbound traffic
are blocked in tests. Raw test output is discarded; only exit codes, counts,
test method names and traceback file/line metadata are persisted.

Actual local changes cannot be inferred from GitHub. No EC2 connection was
available during implementation; run this analysis on EC2 to inspect the exact
`localChanged`, `sourceChanged`, `overlapConflict`, action and file SHA values.
Auth conflicts are disclosed but not merged: retained local auth must pass the
new application's tests. A failing ABI/test blocks application.

## EC2 commands

Keep `/mnt/research/collection-deploy-20261012` pinned at validated `abb3f7e`.
Acquire the **new tool** in a second worktree; these Git operations update
shared remote refs and create a new worktree, without modifying production
HEAD/index/working files or the existing validated source:

```bash
git -C /mnt/research/collection-deploy-20261012 fetch origin refs/heads/strategy-engine-v3:refs/remotes/origin/strategy-engine-v3 && git -C /mnt/research/collection-deploy-20261012 worktree add --detach /mnt/research/collection-deploy-tools-20261012 origin/strategy-engine-v3
```

If that tools worktree already exists, do not overwrite it. Use a fresh unique
path or verify its commit before using it. All CLI stages below run with sudo
so the analysis/backups have consistent root ownership and mode 0700/0600.

Analysis first — no services are stopped and no production files are changed:

```bash
sudo /home/ubuntu/market-career-dashboard/.venv/bin/python /mnt/research/collection-deploy-tools-20261012/scripts/deploy_collection_worktree.py --source /mnt/research/collection-deploy-20261012 --target /home/ubuntu/market-career-dashboard
```

Review the printed report and `plan.json`. It returns a unique `transaction`
directory; use that exact path instead of `TRANSACTION_DIR` below. Do not apply
if `blocked` is nonempty or compatibility tests did not pass. Reanalysis is
required if any source, target code, installed config or env changes afterwards.

```bash
sudo /home/ubuntu/market-career-dashboard/.venv/bin/python /mnt/research/collection-deploy-tools-20261012/scripts/deploy_collection_worktree.py --source /mnt/research/collection-deploy-20261012 --target /home/ubuntu/market-career-dashboard --apply TRANSACTION_DIR
```

For interrupted/failed maintenance, or to roll back a completed release:

```bash
sudo /home/ubuntu/market-career-dashboard/.venv/bin/python /mnt/research/collection-deploy-tools-20261012/scripts/deploy_collection_worktree.py --source /mnt/research/collection-deploy-20261012 --target /home/ubuntu/market-career-dashboard --rollback TRANSACTION_DIR
```

## Transaction / recovery behavior

- Canonical production Git-directory flock serializes deployments, including
  the old launcher lock. No reliance on PID existence alone.
- Reject active research campaigns/backtests before service maintenance. A
  temporary API process-only stop setting avoids killing a research child
  launched during the check/stop race; recheck after API stops, before code copy.
- Save each changed application/config preimage plus SHA, uid/gid/mode/mtime
  before replacement. Original credentials/artifacts are never bundled.
- Temporary worker stop settings forbid automatic SIGKILL. Confirm all worker
  processes and the API main process exited before acquiring the shared worker
  flock and applying files. A hung/manual collector blocks rather than overlaps.
- File writes use exclusive temporaries, fsync, atomic replacement and a durable
  **intent-before-write** journal. Same-second Python replacements invalidate
  old timestamp-based bytecode without deleting untracked caches.
- Preserve Broker installation/config; check unit, configured worker peer UID,
  socket access and EOF rejection without asking for a token or forcing OAuth.
- Keep shared rate-lock contents; back up and adjust only that control file's
  group/mode after workers/API stop. No recursive ownership/permission changes.
- Always set `control.json` to `paused=true`, revoke REST permit, disable legacy
  collectors, start scheduler/API and verify **PAUSED**, no worker, CLOSED Final
  OOS, current source-page SHA and deployed file hashes. Temporary stop overrides
  are removed after successful verification; original API stop semantics return.
- Automatically attempt rollback on failure/SIGINT/SIGTERM. After power loss,
  rerun the explicit rollback command using the durable transaction directory.
- Rollback restores application/config preimages and the previous API when it
  was active. **Collectors remain stopped and boot-disabled**, with the control
  still paused. This deliberate difference prevents unintended collection on
  recovery; original enablement/active states remain in `journal.services`.
- If a file was externally edited, a collector refuses shutdown, or research is
  running, recovery stops at `RECOVERY_BLOCKED`. Never discard the edit or start
  another collector to work around this. Resolve the blocker, then rerun rollback.
- Only application/control/unit files created by this transaction may be removed
  during rollback. Original raw data, logs, backups and research outputs are not
  cleanup targets. Backup bundles have no automatic deletion.

Inspect final `journal.phase`, `verification` or `recoveryVerification` and:

```bash
systemctl is-active market-career-dashboard.service research-collection-scheduler.service toss-token-broker.service
curl --fail http://127.0.0.1:8080/api/research/collection50/status
```

Open your API origin + `/api/research/collection50/page`. Deploying does **not**
resume collection. Only a later authenticated **수집 시작** action can resume the
central scheduler. Never start a standalone collector alongside it. After
rollback, inspect the restored unit configuration and original service states
before explicitly choosing one coordinator; do not blindly re-enable all units.

Final OOS stays CLOSED. Collecting 2026 candles does not authorize 2026 learning
or evaluation. This deployment performs no historical run, tuning, live ARM,
broker orders or change to frozen V4/Simple/V3 policy logic.

## Local validation record (2026-10-12 KST)

529 Python tests passed in 50.53 seconds; 11 JS smoke checks passed. New
deployment coverage contains 22 cases, including an actual committed-source
composite test run under the production preflight sandbox. Python compile,
CLI help, Bash syntax and whitespace checks passed. Real Git detached worktrees
were used inside temporary fixtures; systemd was simulated. No EC2 connection,
service change, historical data download/run or live order was performed.
