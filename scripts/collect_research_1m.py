#!/usr/bin/env python3
"""Fair, restartable off-hours backfill into isolated research SQLite files.
Append only; complete RTH coverage is counted, never inferred from min/max dates.
"""
import argparse
import hashlib
from contextlib import closing
import json
import os
import re
import shutil
import signal
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_collection_scheduler import aware, save, exclusive_lock, backfill_permitted
from research.collection_activity import emit as emit_collection_event
from scripts.collect_toss_1m import fetch_page, token, FIELDS, DEFAULT_SYMBOLS
from research.toss_1m_repair import fingerprint
from research.backtest_v4_strategies import exchange_sessions

UTC = timezone.utc
COLLECTION_SINCE = '2000-01-01T00:00:00-05:00'
COLLECTION_UNTIL = '2026-10-31T23:59:59-04:00'
PRIORITY_DAYS = ['2024-07-26','2024-07-29','2024-07-30','2024-07-31','2024-08-01','2024-08-02']


def validate_collection_range(since, until):
    # Collection is not training: storing October 2026 does NOT open Final OOS.
    start, end = aware(since), aware(until)
    if not aware(COLLECTION_SINCE) <= start < end <= aware(COLLECTION_UNTIL):
        raise ValueError('COLLECTION_RANGE_OUTSIDE_2000_2026_OCTOBER')


def collection_cursor(until, now=None):
    now = now or datetime.now(UTC)
    # END labels: the current uncompleted minute cannot be requested as complete.
    return min(aware(until), now.replace(second=0, microsecond=0)).isoformat()


def preserve_checkpoint(path):
    """Immutable, content-addressed copy before a range migration (never delete)."""
    data = path.read_bytes()
    target = path.with_name(path.stem+'.range-backup-'+hashlib.sha256(data).hexdigest()+path.suffix)
    try:
        with target.open('xb') as out:
            out.write(data); out.flush(); os.fsync(out.fileno())
    except FileExistsError:
        if target.read_bytes() != data: raise ValueError('CHECKPOINT_BACKUP_CONFLICT')


def selected_symbols(path):
    obj = json.loads(Path(path).read_text()) if path else list(DEFAULT_SYMBOLS)
    values = obj.get('symbols') if isinstance(obj, dict) else obj
    if (not isinstance(values, list) or not 1 <= len(values) <= 50 or
        len(set(values)) != len(values) or
        not all(isinstance(s, str) and re.fullmatch(r'[A-Z0-9]{1,8}', s) for s in values)):
        raise ValueError('EXISTING_1_TO_50_SYMBOL_CONFIGURATION_REQUIRED')
    return values


def safe_research_root(path):
    path = Path(path)
    if any(p.is_symlink() for p in [path, *path.parents]): raise ValueError('SYMLINK_ROOT_BLOCKED')
    path = path.resolve()
    for protected in (ROOT/'data/toss_1m', ROOT/'data/toss_ticks'):
        protected = protected.resolve()
        if path == protected or protected in path.parents or path in protected.parents:
            raise ValueError('PRODUCTION_DATA_ROOT_BLOCKED')
    return path


def check_disk(path, required=0):
    disk = shutil.disk_usage(path)
    if disk.free-required < max(10*1024**3, disk.total*0.1):
        raise RuntimeError('DISK_RESERVE_REQUIRED')


def open_research_db(root, symbol):
    path = root/(symbol+'.sqlite3')
    if path.is_symlink(): raise ValueError('DATABASE_SYMLINK_BLOCKED')
    if not path.exists() and (root/(symbol+'.csv.gz')).exists():
        raise ValueError('CSV_WITHOUT_DATABASE_REQUIRES_REVIEWED_IMPORT')
    # Existing research databases get a consistent SQLite online backup before
    # the first schema addition/write. Source WAL is included by backup().
    marker = root/(symbol+'.scheduler-backup.json')
    if marker.is_symlink(): raise ValueError('BACKUP_MARKER_SYMLINK_BLOCKED')
    if marker.exists():
        info = json.loads(marker.read_text())
        name = info.get('backup')
        if not isinstance(name, str) or Path(name).name != name or (root/name).is_symlink() or not (root/name).is_file():
            raise ValueError('EXISTING_BACKUP_REQUIRED')
    if path.exists() and not marker.exists():
        check_disk(root, path.stat().st_size*2)
        backup = root/(symbol+f'.before-scheduler-{time.time_ns()}.sqlite3')
        with closing(sqlite3.connect(f'file:{path}?mode=ro', uri=True)) as source, closing(sqlite3.connect(backup)) as target:
            source.backup(target)
        save(marker, {'backup': backup.name, 'createdAt': datetime.now(UTC).isoformat()})
    db = sqlite3.connect(path, timeout=20)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA synchronous=FULL')
    db.execute('CREATE TABLE IF NOT EXISTS candles (timestamp TEXT PRIMARY KEY, open TEXT, high TEXT, low TEXT, close TEXT, volume TEXT, currency TEXT)')
    # Prevent two offset representations from silently creating duplicate instants.
    db.execute('CREATE INDEX IF NOT EXISTS scheduler_candle_instant ON candles(julianday(timestamp))')
    db.commit()
    return db


def insert_page(db, candles, since, until, symbol="TEST"):
    validate_collection_range(since, until)
    since, until = aware(since), aware(until)
    rows = []
    instants = []
    for c in candles:
        raw = str(c.get('timestamp') or '')
        stamp = aware(raw)
        if stamp.second or stamp.microsecond: raise ValueError('NON_MINUTE_TIMESTAMP')
        if stamp > datetime.now(UTC): raise ValueError('API_FUTURE_TIMESTAMP')
        instants.append(stamp)
        if not since <= stamp <= until: continue
        row = dict(zip(FIELDS, [raw, c.get('openPrice'), c.get('highPrice'), c.get('lowPrice'),
                                 c.get('closePrice'), c.get('volume'), c.get('currency')]))
        fingerprint(row, symbol)
        rows.append(row)
    added = 0
    with db:
        for row in rows:
            matches = db.execute('SELECT timestamp,open,high,low,close,volume,currency FROM candles WHERE julianday(timestamp)=julianday(?)', (row['timestamp'],)).fetchall()
            if any(fingerprint(dict(zip(FIELDS, match)), symbol) != fingerprint(row, symbol) for match in matches):
                raise ValueError('CONFLICTING_DUPLICATE')
            if not matches:
                db.execute('INSERT INTO candles VALUES (?,?,?,?,?,?,?)', tuple(str(row[k]) for k in FIELDS))
                added += 1
    return added, min(instants) if instants else None, max(instants) if instants else None


def coverage(db, since, until):
    # Future sessions in the declared October window are not missing data yet.
    observed_until = min(aware(until), datetime.now(UTC))
    sessions = exchange_sessions(aware(since).date().isoformat(), observed_until.date().isoformat())
    by_day = {str(day): {'expected': int((close-opened).total_seconds()/60), 'observed': 0}
              for day, (opened, close) in sessions.items()}
    windows = {str(day): (opened, close) for day, (opened, close) in sessions.items()}
    from zoneinfo import ZoneInfo
    et = ZoneInfo('America/New_York')
    invalid = 0
    seen = set()
    current_day = None
    # Read only timestamp metadata, streaming. Sets contain at most 390 entries/day.
    for (raw,) in db.execute('SELECT timestamp FROM candles WHERE julianday(timestamp) BETWEEN julianday(?) AND julianday(?) ORDER BY julianday(timestamp)', (since, until)):
        try:
            stamp = aware(raw)
            start = stamp-timedelta(minutes=1)  # END-labelled [T-1m,T)
            key = start.astimezone(et).date().isoformat()
            if key in windows and windows[key][0] <= start < windows[key][1]:
                if stamp.second or stamp.microsecond: invalid += 1; continue
                if current_day != key:
                    current_day = key
                    seen.clear()
                if start not in seen:
                    seen.add(start)
                    by_day[key]['observed'] += 1
        except ValueError: invalid += 1
    counts = {day: {'expected': x['expected'], 'observed': x['observed']} for day, x in by_day.items()}
    complete = sum(x['expected'] == x['observed'] for x in counts.values())
    missing = [day for day, x in counts.items() if x['observed'] < x['expected']]
    return {'verifiedTradingDays': complete, 'missingTradingDays': len(missing), 'expectedTradingDays': len(counts),
            'invalidTimestamps': invalid, 'sessionCounts': counts, 'missingDates': missing,
            'timestampKind': 'end', 'coverageScope': [since, until], 'priceAdjustmentConfirmed': False}


class Backfill:
    def __init__(self, root, symbols, permit, since, until, fetch=fetch_page):
        self.root, self.symbols, self.permit = Path(root), symbols, Path(permit)
        self.since, self.until, self.fetch = since, until, fetch
        validate_collection_range(since, until)
        self.stop = False
        path = self.root/'_scheduler_checkpoint.json'
        self.state = json.loads(path.read_text()) if path.exists() else {'symbols': {}, 'nextIndex': 0}
        old = self.state.get('range', [since, until])
        if old != [since, until]:
            if aware(since) > aware(old[0]) or aware(until) < aware(old[1]):
                raise ValueError('CHECKPOINT_RANGE_NARROWING_BLOCKED')
            preserve_checkpoint(path)
            self.state['previousRange'] = old
        self.state['range'] = [since, until]
        previous = self.state.get('requestedSymbols', symbols)
        if previous != symbols: raise ValueError('CHECKPOINT_SYMBOL_CHANGE_REQUIRES_REVIEW')
        self.state['requestedSymbols'] = symbols
        self.state['symbols'] = {}
        self.progress = {}
        self.cooldowns = {}
        progress = self.root/'_collection_progress.json'
        if progress.exists(): self.progress = json.loads(progress.read_text())
        for symbol in symbols: self.progress.setdefault(symbol, {'status': 'queued', 'stored': 0})

    def allowed(self): return not self.stop and backfill_permitted(self.permit)

    def persist(self):
        for symbol, state in self.state['symbols'].items():
            save(self.root/f'_scheduler_{symbol}.json', state)
        save(self.root/'_scheduler_checkpoint.json', {k:v for k,v in self.state.items() if k != 'symbols'})
        save(self.root/'_collection_progress.json', self.progress)

    def step_symbol(self, symbol):
        if time.time() < self.state.get("notBeforeEpoch", 0): return
        check_disk(self.root)
        db = open_research_db(self.root, symbol)
        try:
            if symbol not in self.state['symbols']:
                path = self.root/f'_scheduler_{symbol}.json'
                self.state['symbols'] = {symbol: json.loads(path.read_text()) if path.exists() else {}}
            state = self.state['symbols'][symbol]
            if state and state.get('range', self.state.get('previousRange', [self.since, self.until])) != [self.since, self.until]:
                preserve_checkpoint(self.root/f'_scheduler_{symbol}.json')
                state = self.state['symbols'][symbol] = {}
            if not state.get('initialized'):
                audit = coverage(db, self.since, self.until)
                # Prioritize known gaps, then other incomplete dates. No date is
                # asserted recovered merely because the API crossed its boundary.
                days = audit['missingDates']
                priority = [d for d in PRIORITY_DAYS if d in days] if symbol in ('AAPL','TQQQ') else []
                state.update(initialized=True, gapQueue=priority+[d for d in days if d not in priority],
                             coverage=audit, historyCursor=collection_cursor(self.until),
                             terminal=None, range=[self.since, self.until], lastHeadAt=time.time())
            newest_first = aware(self.until).year >= 2026
            # Periodically catch up the recent edge while the multi-year cursor
            # continues backward. A separate cursor never destroys old progress.
            if newest_first and time.time()-state.get('lastHeadAt', time.time()) >= 12*3600 and datetime.now(UTC) < aware(self.until):
                if not state.get('headCursor'):
                    state['headCursor'] = collection_cursor(self.until)
                    state['headSince'] = max(aware(self.since), datetime.fromtimestamp(state['lastHeadAt'], UTC)-timedelta(days=1)).isoformat()
                    state['headZeroPages'] = 0
            head = state.get('headCursor')
            gap = state['gapQueue'][0] if state['gapQueue'] and (not newest_first or state.get('terminal')) else None
            if head:
                gap = None
                before = head
            elif gap:
                opened, closed = exchange_sessions(gap, gap)[gap]
                before = state.get('gapCursor') or (closed+timedelta(minutes=1)).isoformat()
            else:
                before = state['historyCursor']
                if state.get('terminal'): return
            if not self.allowed(): return
            tok = token()
            def error(*args):
                # Only codes and counts; never payloads, token or exception text.
                record = self.progress.setdefault(symbol, {})
                code = args[4] if len(args)>4 else None
                record['lastHttpCode'] = code
                record['http429Count'] = record.get('http429Count', 0)+int(code == 429)
                record['http401Count'] = record.get('http401Count', 0)+int(code == 401)
            def requested():
                record = self.progress.setdefault(symbol, {})
                record['restRequests'] = record.get('restRequests', 0)+1
            obj = self.fetch(tok, symbol, before, refresh_token=lambda: token(force=True),
                             error_recorder=error, request_guard=self.allowed, request_observer=requested,
                             refresh_rejected_token=lambda rejected: token(force=True, rejected_token=rejected))
            candles = obj.get('candles') or []
            next_before = obj.get('nextBefore')
            if any(aware(c.get('timestamp')) > aware(before) for c in candles): raise ValueError('API_RESPONSE_AFTER_CURSOR')
            if next_before and aware(next_before) >= aware(before):
                raise ValueError('PAGINATION_CURSOR_NOT_DECREASING')
            added, first, last = insert_page(db, candles, self.since, self.until, symbol)
            if last and last > aware(before): raise ValueError('API_RESPONSE_AFTER_CURSOR')
            record = self.progress.setdefault(symbol, {})
            record.update(symbol=symbol, status='collecting', currentGap=gap, added=added,
                page=record.get('page', 0)+1, firstResponseTimestamp=first.isoformat() if first else None,
                lastResponseTimestamp=last.isoformat() if last else None, requestedBefore=before,
                nextBefore=next_before, updatedAt=time.time(), coverageVerified=False)
            previous_zero = state.get('zeroAddedPages', 0)
            state['zeroAddedPages'] = previous_zero+1 if not added else 0
            if head:
                state['headZeroPages'] = state.get('headZeroPages', 0)+1 if not added else 0
                if not candles or not next_before or first <= aware(state['headSince']) or state['headZeroPages'] >= 10:
                    state.pop('headCursor', None)
                    state['lastHeadAt'] = time.time()
                else: state['headCursor'] = next_before
                # zero-added counters for the old edge must not be contaminated.
                state['zeroAddedPages'] = previous_zero
            elif gap:
                state['gapPages'] = state.get('gapPages', 0)+1
                done = not candles or not next_before or (first is not None and first <= opened) or state['zeroAddedPages'] >= 10 or state['gapPages'] >= 20
                if done:
                    # A failed gap stays recorded but is not retried indefinitely.
                    audit = coverage(db, gap+'T00:00:00+00:00', (closed+timedelta(hours=1)).isoformat())
                    count = audit['sessionCounts'].get(gap, {'expected': 0, 'observed': 0})
                    state['coverage']['sessionCounts'][gap] = count
                    state.setdefault('gapOutcomes', {})[gap] = count
                    state['gapQueue'].pop(0)
                    state.pop('gapCursor', None)
                    state['zeroAddedPages'] = 0
                    state['gapPages'] = 0
                else: state['gapCursor'] = next_before
            else:
                if not candles or not next_before or first <= aware(self.since) or state['zeroAddedPages'] >= 10:
                    state['terminal'] = 'API_BOUNDARY_UNVERIFIED' if state['zeroAddedPages'] < 10 else 'NO_PROGRESS_LIMIT'
                    state['coverage'] = coverage(db, self.since, self.until)
                else: state['historyCursor'] = next_before
                if state.get('terminal') and newest_first:
                    # The history pass also filled dates in the initial gap list.
                    # Rebuild from actual coverage, latest incomplete date first.
                    state['gapQueue'] = sorted(state['coverage']['missingDates'], reverse=True)
            counts = state['coverage']['sessionCounts']
            record.update(verifiedTradingDays=sum(x['expected'] == x['observed'] for x in counts.values()),
                          missingTradingDays=sum(x['observed'] < x['expected'] for x in counts.values()),
                          terminationReason=state.get('terminal'), timestampKind='end',
                          knownGapOutcomes=state.get('gapOutcomes', {}))
            count, oldest, latest = db.execute('SELECT COUNT(*),MIN(julianday(timestamp)),MAX(julianday(timestamp)) FROM candles').fetchone()
            bounds = db.execute('SELECT timestamp FROM candles ORDER BY julianday(timestamp) LIMIT 1').fetchone()
            end = db.execute('SELECT timestamp FROM candles ORDER BY julianday(timestamp) DESC LIMIT 1').fetchone()
            record.update(stored=count, oldestTimestamp=bounds[0] if bounds else None, latestTimestamp=end[0] if end else None)
            record.update(collectionFrom=self.since, collectionTo=self.until,
                          direction='newest_to_oldest' if newest_first else 'gap_priority',
                          historyCursor=state.get('historyCursor'), finalOosGate='CLOSED')
            if state.get('terminal') and not state['gapQueue']: record['status'] = 'collection_pass_finished_unverified'
        finally: db.close()

    def run(self):
        while self.allowed():
            index = self.state['nextIndex'] % len(self.symbols)
            symbol = self.symbols[index]
            before = self.progress.get(symbol, {})
            prior_page = int(before.get('page') or 0)
            save(self.root/'_worker_status.json', {'currentSymbol': symbol, 'updatedAt': datetime.now(UTC).isoformat(), 'state': 'collecting'})
            try:
                if time.monotonic() >= self.cooldowns.get(symbol, 0): self.step_symbol(symbol)
            except Exception as exc:
                self.progress.setdefault(symbol, {}).update(status='error', errorCode=str(exc) if re.fullmatch(r'[A-Z_0-9]+', str(exc)) else type(exc).__name__, updatedAt=time.time())
                if getattr(exc, 'retry_after', None): self.state['notBeforeEpoch'] = time.time()+exc.retry_after
                self.cooldowns[symbol] = time.monotonic()+60
            self.state['nextIndex'] = (index+1) % len(self.symbols)
            self.persist()
            record = self.progress.get(symbol, {})
            new_page = int(record.get('page') or 0)
            if new_page > prior_page:
                emit_collection_event(self.root, 'CANDLE_PAGE', symbol=symbol,
                                      page=new_page, added=int(record.get('added') or 0),
                                      stored=int(record.get('stored') or 0))
            elif record.get('status') == 'error' and time.time() - float(record.get('updatedAt') or 0) < 3:
                emit_collection_event(self.root, 'CANDLE_ERROR', symbol=symbol,
                                      code=record.get('errorCode'))
            # Conservative per-worker REST pacing; shared group limiter is also used.
            for _ in range(8):
                if not self.allowed(): break
                time.sleep(0.25)
        self.persist()
        save(self.root/'_worker_status.json', {'currentSymbol': None, 'updatedAt': datetime.now(UTC).isoformat(), 'state': 'paused'})


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument('--out-dir', type=Path, required=True)
    p.add_argument('--symbols-file', type=Path, default=os.getenv('COLLECTION_SYMBOLS_FILE'))
    p.add_argument('--permit-file', type=Path, required=True)
    p.add_argument('--since', default=COLLECTION_SINCE, help='Earliest collection date, explicit timezone required')
    p.add_argument('--until', default=COLLECTION_UNTIL, help='Latest collection date; cursor is capped at the last completed minute')
    a = p.parse_args()
    try: validate_collection_range(a.since, a.until)
    except ValueError as exc: p.error(str(exc))
    if not os.getenv('TOSS_TOKEN_SOCKET'): p.error('TOKEN_BROKER_REQUIRED')
    root = safe_research_root(a.out_dir)
    root.mkdir(parents=True, exist_ok=True)
    with exclusive_lock():
        worker = Backfill(root, selected_symbols(a.symbols_file), a.permit_file, a.since, a.until)
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: setattr(worker, 'stop', True))
        worker.run()


if __name__ == '__main__': main()
