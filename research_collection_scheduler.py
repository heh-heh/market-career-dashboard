"""Exclusive regular-session Tick / off-hours research backfill orchestration.
No broker orders. Only local status files are served by the web application.
"""
import argparse
import contextlib
import fcntl
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

ROOT = Path(__file__).resolve().parent
UTC = timezone.utc
ET = ZoneInfo('America/New_York')
STATE_DIR = Path(os.getenv('RESEARCH_COLLECTION_STATE_DIR', '/var/lib/market-career-dashboard/collection'))
WORKER_LOCK = Path(os.getenv('RESEARCH_COLLECTION_WORKER_LOCK', str(STATE_DIR/'worker.lock')))


def aware(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('TIMESTAMP_TIMEZONE_REQUIRED')
    return dt.astimezone(UTC)


def save(path, value, share_group=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_EXCL random temporary: a root API must not follow a predictable symlink
    # in the ubuntu-owned state directory. Only this new temp is cleaned up.
    fd, name = tempfile.mkstemp(prefix=path.name+'.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as f:
            if share_group:
                os.fchown(f.fileno(), -1, path.parent.stat().st_gid)
                os.fchmod(f.fileno(), 0o640)
            json.dump(value, f, indent=2, allow_nan=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


@contextlib.contextmanager
def exclusive_lock(path=WORKER_LOCK):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('COLLECTOR_ALREADY_RUNNING') from None
        yield stream


def regular_clock(now, calendar=None):
    now = aware(now)
    calendar = calendar or xcals.get_calendar('XNYS', start='2000-01-01', end='2030-12-31')
    day = pd.Timestamp(now.astimezone(ET).date())
    session = calendar.date_to_session(day, direction='next')
    opened = calendar.session_open(session).to_pydatetime()
    closed = calendar.session_close(session).to_pydatetime()
    if now >= closed:
        session = calendar.next_session(session)
        opened = calendar.session_open(session).to_pydatetime()
        closed = calendar.session_close(session).to_pydatetime()
    return {'sessionDate': session.date().isoformat(), 'isOpen': opened <= now < closed,
            'openAt': opened.isoformat(), 'closeAt': closed.isoformat()}


def choose_mode(now, toss_regular=None, calendar=None, lead_seconds=90, paused=False):
    clock = regular_clock(now, calendar)
    now = aware(now)
    reason = None
    # Missing or conflicting official regular window never authorizes REST backfill.
    confirmed = False
    if toss_regular:
        try:
            confirmed = (aware(toss_regular['openAt']) == aware(clock['openAt']) and
                         aware(toss_regular['closeAt']) == aware(clock['closeAt']))
        except (KeyError, ValueError, TypeError):
            pass
    if paused:
        mode = 'PAUSED'
    elif clock['isOpen']:
        mode = 'MARKET_OPEN'  # Tick only; outages never fall back to REST.
        if not confirmed: reason = 'CALENDAR_UNCONFIRMED_TICK_XNYS_ONLY'
    elif not confirmed:
        mode, reason = 'ERROR', 'TOSS_XNYS_CALENDAR_UNCONFIRMED'
    elif (aware(clock['openAt'])-now).total_seconds() <= lead_seconds:
        mode, reason = 'SWITCHING', 'PREOPEN_REQUEST_GUARD'
    else:
        mode = 'MARKET_CLOSED'
    return {**clock, 'mode': mode, 'reason': reason, 'now': now.isoformat(),
            'etTime': now.astimezone(ET).isoformat(),
            'kstTime': now.astimezone(ZoneInfo('Asia/Seoul')).isoformat(),
            'calendarConfirmed': confirmed, 'automatic': True}


def backfill_permitted(path, now=None):
    now = now or datetime.now(UTC)
    try:
        state = json.loads(Path(path).read_text())
        return (state['mode'] == 'MARKET_CLOSED' and
                now < aware(state['leaseExpiresAt']) and
                now < aware(state['requestDeadline']) and
                not regular_clock(now)['isOpen'])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def official_regular(session_date):
    # Reuse the existing official Toss shape/parser, not a guessed REST schema.
    from scripts.collect_toss_ticks import _fetch_us_market_calendar, _calendar_windows
    result = _fetch_us_market_calendar(session_date)
    day = result.get('today', result)
    regular = day.get('regularMarket') if isinstance(day, dict) else None
    if not isinstance(regular, dict): return None
    # The legacy parser accepts naive values; scheduler authorization must not.
    aware(regular.get('startTime'))
    aware(regular.get('endTime'))
    return next((w for w in _calendar_windows(day, session_date)
                 if w['marketSession'] == 'REGULAR'), None)


class Scheduler:
    def __init__(self, args, launch=subprocess.Popen):
        self.args, self.launch = args, launch
        self.child = None
        self.role = None
        self.stopping = False
        self.stop_sent = False
        self.last_exit = None
        self.retry_at = 0
        self.failures = 0

    def stop_child(self):
        if self.child and self.child.poll() is None:
            if not self.stop_sent:
                self.child.terminate()
                self.stop_sent = True
            return False  # Never launch the other worker before confirmed exit.
        if self.child:
            self.last_exit = self.child.poll()
            self.child = None
            self.role = None
            self.stop_sent = False
        return True

    def step(self, decision, now=None):
        now = now or datetime.now(UTC)
        desired = {'MARKET_OPEN': 'tick', 'MARKET_CLOSED': 'backfill'}.get(decision['mode'])
        if self.stopping: desired = None
        if self.child and self.child.poll() is not None:
            code = self.child.poll()
            self.stop_child()
            self.failures = self.failures+1 if code else 0
            self.retry_at = time.monotonic()+min(300, 5*2**min(self.failures, 6))
        if desired != self.role:
            save(self.args.state_dir/'permit.json', {'mode': 'SWITCHING'})
        if desired != self.role and not self.stop_child():
            decision = {**decision, 'mode': 'SWITCHING', 'reason': 'WAITING_FOR_WORKER_FLUSH'}
        permit = {**decision, 'leaseExpiresAt': (now+timedelta(seconds=4)).isoformat(),
                  'requestDeadline': (aware(decision['openAt'])-timedelta(seconds=self.args.preopen_seconds)).isoformat()}
        # During every transition/shutdown invalidate REST permission before SIGTERM.
        if desired != 'backfill' or self.stopping: permit['mode'] = 'SWITCHING'
        save(self.args.state_dir/'permit.json', permit)
        if desired and self.child is None and time.monotonic() >= self.retry_at:
            if desired == 'tick':
                cmd = [sys.executable, str(ROOT/'scripts/collect_toss_ticks.py'), '--regular-only',
                       '--data-dir', str(self.args.tick_dir)]
            else:
                cmd = [sys.executable, str(ROOT/'scripts/collect_research_1m.py'),
                       '--out-dir', str(self.args.data_dir), '--permit-file', str(self.args.state_dir/'permit.json')]
            if desired == 'backfill' and self.args.symbols_file:
                cmd += ['--symbols-file', str(self.args.symbols_file)]
            env = {**os.environ, 'RESEARCH_COLLECTION_WORKER_LOCK': str(WORKER_LOCK)}
            try:
                self.child = self.launch(cmd, cwd=ROOT, env=env)
                self.role = desired
                self.stop_sent = False
            except OSError:
                self.failures += 1
                self.retry_at = time.monotonic()+min(300, 5*2**min(self.failures, 6))
                decision = {**decision, 'mode': 'ERROR', 'reason': 'WORKER_LAUNCH_FAILED'}
                save(self.args.state_dir/'permit.json', {'mode': 'ERROR'})
        state = {**decision, 'collector': self.role, 'pid': self.child.pid if self.child else None,
                 'lastExitCode': self.last_exit, 'workerFailures': self.failures,
                 'updatedAt': now.isoformat(), 'researchOnly': True, 'finalOosGate': 'CLOSED'}
        save(self.args.state_dir/'status.json', state)
        return state

    def run(self):
        cache, cached_at = {}, 0.0
        while not self.stopping:
            now = datetime.now(UTC)
            clock = regular_clock(now)
            if time.monotonic()-cached_at >= 60 or cache.get('sessionDate') != clock['sessionDate']:
                try:
                    value = official_regular(clock['sessionDate'])
                    cache = {**(value or {}), 'sessionDate': clock['sessionDate']}
                except Exception:
                    cache = {'sessionDate': clock['sessionDate']}
                cached_at = time.monotonic()
            try:
                control = json.loads((self.args.state_dir/'control.json').read_text())
                paused = not isinstance(control, dict) or control.get('paused') is not False
            except FileNotFoundError:
                paused = True  # Deployment alone must not start collection.
            except (OSError, ValueError):
                paused = True
            self.step(choose_mode(now, cache, lead_seconds=self.args.preopen_seconds, paused=paused), now)
            time.sleep(1)
        save(self.args.state_dir/'permit.json', {'mode': 'PAUSED'})
        while not self.stop_child(): time.sleep(0.2)
        save(self.args.state_dir/'status.json', {'mode': 'PAUSED', 'collector': None,
             'automatic': False, 'updatedAt': datetime.now(UTC).isoformat()})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=STATE_DIR)
    p.add_argument('--data-dir', type=Path, default=Path(os.getenv('COLLECTION_50_DIR', '/mnt/research/collection-50-v1')))
    p.add_argument('--tick-dir', type=Path, default=ROOT/'data/toss_ticks')
    p.add_argument('--symbols-file', type=Path, default=os.getenv('COLLECTION_SYMBOLS_FILE'),
                   help='Optional operator JSON override; default is existing collector 50 symbols')
    p.add_argument('--preopen-seconds', type=int, default=90)
    a = p.parse_args()
    if a.preopen_seconds < 90: p.error('preopen guard must be >=90 seconds')
    if not os.environ.get('TOSS_TOKEN_SOCKET'): p.error('TOKEN_BROKER_REQUIRED')
    from scripts.collect_research_1m import selected_symbols, safe_research_root
    selected_symbols(a.symbols_file)
    safe_research_root(a.data_dir)
    with exclusive_lock(a.state_dir/'scheduler.lock'):
        save(a.state_dir/'ownership.json', {'managed': True, 'activatedAt': datetime.now(UTC).isoformat()})
        scheduler = Scheduler(a)
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: setattr(scheduler, 'stopping', True))
        scheduler.run()


if __name__ == '__main__': main()
