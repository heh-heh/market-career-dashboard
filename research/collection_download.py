"""Read-only pre-2026 year exports from current SQLite or finalized CSV.
A SQLite read transaction provides a consistent WAL-aware snapshot. No Toss calls.
"""
import contextlib
import csv
import gzip
import hashlib
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo
from research_collection_scheduler import aware

FIELDS = ('timestamp','open','high','low','close','volume','currency')


def source_rows(root, symbol, year):
    if not re.fullmatch(r'[A-Z0-9]{1,8}', symbol) or year not in range(2022,2026):
        raise ValueError('FINAL_OOS_CLOSED_OR_INVALID_SYMBOL')
    root = Path(root)
    if any(p.is_symlink() for p in [root, *root.parents]): raise ValueError('SYMLINK_ROOT_BLOCKED')
    db = root/(symbol+'.sqlite3')
    src = root/(symbol+'.csv.gz')
    if db.is_symlink() or src.is_symlink(): raise ValueError('SOURCE_SYMLINK_BLOCKED')
    et = ZoneInfo('America/New_York')
    if db.is_file():
        with contextlib.closing(sqlite3.connect(db.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            conn.execute('PRAGMA query_only=ON')
            conn.execute('BEGIN')
            # Narrow the SQL scan to the ET calendar year; rows are not forward-filled.
            lo, hi = f'{year}-01-01T05:00:00+00:00', f'{year+1}-01-01T05:00:00+00:00'
            for row in conn.execute('SELECT timestamp,open,high,low,close,volume,currency FROM candles WHERE julianday(timestamp)>=julianday(?) AND julianday(timestamp)<julianday(?) ORDER BY julianday(timestamp)', (lo,hi)):
                if aware(row[0]).astimezone(et).year == year: yield dict(zip(FIELDS, row))
    elif src.is_file():
        with gzip.open(src, 'rt', newline='', encoding='utf8') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames != list(FIELDS): raise ValueError('INVALID_CSV_SCHEMA')
            for row in reader:
                if aware(row['timestamp']).astimezone(et).year == year: yield row
    else: raise FileNotFoundError('SYMBOL_NOT_COLLECTED')


@contextlib.contextmanager
def year_download(root, symbol, year, export_dir):
    export_dir = Path(export_dir)
    root = Path(root).resolve()
    if any(p.is_symlink() for p in [export_dir, *export_dir.parents]): raise ValueError('EXPORT_SYMLINK_BLOCKED')
    destination = export_dir.resolve()
    if destination == root or root in destination.parents or destination in root.parents:
        raise ValueError('EXPORT_MUST_BE_OUTSIDE_SOURCE')
    export_dir.mkdir(parents=True, exist_ok=True)
    disk = shutil.disk_usage(export_dir)
    if disk.free < max(10*1024**3, disk.total*0.1): raise RuntimeError('DISK_RESERVE_REQUIRED')
    with tempfile.TemporaryDirectory(prefix='collection-', dir=export_dir) as folder:
        file = Path(folder)/f'{symbol}-{year}.csv.gz'
        rows = 0
        with gzip.open(file, 'wt', newline='', encoding='utf8') as stream:
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for row in source_rows(root, symbol, year):
                writer.writerow(row); rows += 1
                if rows % 10000 == 0:
                    disk = shutil.disk_usage(export_dir)
                    if disk.free < max(10*1024**3, disk.total*0.1): raise RuntimeError('DISK_RESERVE_REQUIRED')
        with file.open('rb') as stream: digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        yield file, {'sha256': digest, 'rows': rows, 'bytes': file.stat().st_size,
                     'timestampKind': 'end', 'verifiedComplete': False, 'finalOosGate': 'CLOSED'}
