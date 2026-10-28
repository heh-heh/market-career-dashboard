"""Dashboard-only persistence. No broker, credentials or research dependencies."""
import json
import math
import os
import tempfile
from pathlib import Path


def validate_dashboard(data, require_fresh=False):
    if not isinstance(data, dict) or not data.get('updatedAt'):
        raise ValueError('DASHBOARD_TIMESTAMP_REQUIRED')
    for key in ('stocks', 'indices', 'news', 'employmentNews', 'jobPostings'):
        if not isinstance(data.get(key), list):
            raise ValueError('DASHBOARD_LIST_REQUIRED:' + key)
    quotes = data['stocks'] + data['indices']
    usable = [x for x in quotes if isinstance(x, dict)
              and isinstance(x.get('price'), (int, float))
              and not isinstance(x['price'], bool) and math.isfinite(x['price']) and x['price'] > 0]
    if len(usable) < 10 or not any(x in usable for x in data['stocks']) or not any(x in usable for x in data['indices']):
        raise ValueError('DASHBOARD_INSUFFICIENT_QUOTES')
    if require_fresh and sum(x.get('source') == 'Yahoo Finance' for x in usable) < 5:
        raise ValueError('DASHBOARD_INSUFFICIENT_FRESH_QUOTES')
    json.dumps(data, allow_nan=False)
    return data


def read_dashboard(path):
    path = Path(path)
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError('DASHBOARD_FILE_TOO_LARGE')
    return validate_dashboard(json.loads(path.read_text(encoding='utf-8')))


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.' + path.name + '-', delete=False) as f:
            name = f.name
            json.dump(data, f, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(name, 0o640)
        os.replace(name, path)
        name = None
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)
