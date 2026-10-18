"""Result-level local cache: local-first, network only on miss.

Three layers now exist:

  1. data/predict/    finished predictions  (this module)  <- consulted first
  2. data/cache/      raw HTTP responses     (meteo._get)
  3. data/direct/     decoded GRIB points    (direct.point)

Layer 1 is what makes a repeated query instant: it stores the whole assembled
payload, so neither the 25-point ring nor the 109-point azimuth grid is refetched.

Expiry depends on how much the underlying forecast can still change:

  date < today-1    never expires (reanalysis is final)
  date == today     60 min
  date == tomorrow  3 h
  further ahead     6 h

`refresh=1` bypasses layer 1 and overwrites it.
"""
import gzip
import json
import os
import threading
import time
from datetime import date, timedelta

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR = os.path.join(HERE, "data", "predict")
os.makedirs(DIR, exist_ok=True)
_LOCK = threading.Lock()

# Bump when the payload shape changes: old entries then miss instead of returning
# a dict that lacks newly added fields.
SCHEMA = 4


def ttl_for(d):
    """Seconds a cached payload stays valid, or None for 'forever'."""
    today = date.today()
    if d < today - timedelta(days=1):
        return None
    if d == today:
        return 3600
    if d == today + timedelta(days=1):
        return 3 * 3600
    return 6 * 3600


def _path(kind, key):
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in key)
    return os.path.join(DIR, "%s__%s.json.gz" % (kind, safe))


def key(lat, lon, d, mode, version):
    return "%.3f_%.3f_%s_%s_%s_s%d" % (lat, lon, d.isoformat(), mode, version, SCHEMA)


def get_any(kind, key):
    """Return (payload, age) ignoring expiry — last-resort fallback."""
    path = _path(kind, key)
    if not os.path.exists(path):
        return None, 0
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f), int(time.time() - os.path.getmtime(path))
    except (OSError, ValueError):
        return None, 0


def get(kind, key, d):
    """Return (payload, age_seconds) or (None, 0) when missing / stale."""
    path = _path(kind, key)
    if not os.path.exists(path):
        return None, 0
    age = time.time() - os.path.getmtime(path)
    ttl = ttl_for(d)
    if ttl is not None and age > ttl:
        return None, int(age)
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f), int(age)
    except (OSError, ValueError):
        return None, 0


def put(kind, key, payload):
    with _LOCK:
        path = _path(kind, key)
        tmp = path + ".tmp"
        try:
            with gzip.open(tmp, "wt", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            os.replace(tmp, path)
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def stats():
    n = 0
    size = 0
    for fn in os.listdir(DIR):
        if fn.endswith(".json.gz"):
            n += 1
            try:
                size += os.path.getsize(os.path.join(DIR, fn))
            except OSError:
                pass
    return {"entries": n, "bytes": size}


def clear(kind=None):
    removed = 0
    for fn in os.listdir(DIR):
        if not fn.endswith(".json.gz"):
            continue
        if kind and not fn.startswith(kind + "__"):
            continue
        try:
            os.unlink(os.path.join(DIR, fn))
            removed += 1
        except OSError:
            pass
    return removed
