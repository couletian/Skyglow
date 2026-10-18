"""Direct access to the three official NWP archives (no relay, no API key).

  gfs    NOAA NOMADS          grib2 via byte ranges from the .idx sidecar
  ecmwf  ECMWF open data      grib2 per step, .index sidecar gives byte ranges
  icon   DWD open data        bzip2 grib2 per variable, icosahedral grid

Only layered cloud is needed. Each fetch downloads a few MB, decodes with eccodes
and samples the nearest grid point to a coordinate.

Optional dependency: eccodes (pip install eccodes). Without it every call raises
DirectUnavailable and the caller keeps the Open-Meteo path.
"""
import bz2
import http.client
import json
import math
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "direct")
os.makedirs(CACHE, exist_ok=True)

UA = {"User-Agent": "skyglow/2.1 (+research; contact: local)"}
GFS_BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
ECMWF_HOSTS = [
    "https://data.ecmwf.int/forecasts",
    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
]
ECMWF_PAUSE = 1.2   # be polite between the per-message range requests
ICON_BASE = "https://opendata.dwd.de/weather/nwp/icon/grib"

try:
    import eccodes  # noqa: F401
    HAVE_ECCODES = True
except ImportError:  # pragma: no cover
    HAVE_ECCODES = False

try:
    import numpy as np
    HAVE_NUMPY = True
except ImportError:  # pragma: no cover
    HAVE_NUMPY = False


class DirectError(RuntimeError):
    pass


class DirectUnavailable(DirectError):
    pass


# ---------------------------------------------------------------- transport
def _http(url, rng=None, timeout=60, tries=4, expect_bytes=True, strict=False):
    last = None
    for i in range(tries):
        req = urllib.request.Request(url, headers=UA)
        if rng:
            req.add_header("Range", "bytes=%d-%d" % rng)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                try:
                    body = r.read()
                except http.client.IncompleteRead as e:
                    if strict:
                        raise DirectError("连接中断，重试") from e
                    body = e.partial   # NOMADS often closes the range early
            if expect_bytes and not body:
                raise DirectError("empty body")
            if body[:4] in (b"<?xm", b"<Err") and b"<Error" in body[:200]:
                raise DirectError("upstream error xml")
            return body
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (429, 500, 503):
                # S3 answers 503 "Slow Down" under bursty range requests
                time.sleep(6 * (i + 1) * (i + 1))
                continue
            if e.code == 403:
                raise DirectError("403 forbidden (check the run hour)") from e
            raise DirectError("HTTP %d" % e.code) from e
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.5 * (i + 1))
    raise DirectError(str(last))


def _decode(data, want_values=True):
    """Yield (shortName, values) for each GRIB message in a byte string."""
    if not HAVE_ECCODES:
        raise DirectUnavailable("eccodes 未安装（pip install eccodes）")
    out = []
    offset = 0
    n = len(data)
    while offset < n - 8:
        if data[offset:offset + 4] != b"GRIB":
            nxt = data.find(b"GRIB", offset + 1)
            if nxt < 0:
                break
            offset = nxt
            continue
        import tempfile
        with tempfile.NamedTemporaryFile(delete=False, suffix=".grib2") as f:
            f.write(data[offset:])
            path = f.name
        try:
            with open(path, "rb") as fh:
                while True:
                    try:
                        gid = eccodes.codes_grib_new_from_file(fh)
                    except Exception:  # noqa: BLE001  trailing message may be truncated
                        break
                    if gid is None:
                        break
                    try:
                        name = eccodes.codes_get(gid, "shortName")
                        vals = eccodes.codes_get_values(gid) if want_values else None
                        meta = {"Ni": None, "Nj": None}
                        try:
                            meta["Ni"] = eccodes.codes_get(gid, "Ni")
                            meta["Nj"] = eccodes.codes_get(gid, "Nj")
                        except Exception:  # noqa: BLE001
                            pass
                        out.append((name, vals, meta))
                    finally:
                        eccodes.codes_release(gid)
        finally:
            try:
                os.unlink(path)
            except OSError:
                pass
        break
    return out


# ---------------------------------------------------------------- GFS
def _gfs_run(now=None):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(hours=5)
    hour = (t.hour // 6) * 6
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def _gfs_steps():
    return list(range(0, 121))


def gfs(lat, lon, t_utc):
    run = _gfs_run()
    hours = (t_utc - run).total_seconds() / 3600.0
    step = max(0, min(120, int(round(hours))))
    ymd, hh = run.strftime("%Y%m%d"), run.strftime("%H")
    stem = "gfs.t%sz.pgrb2.0p25.f%03d" % (hh, step)
    idx_url = "%s/gfs.%s/%s/atmos/%s.idx" % (GFS_BASE, ymd, hh, stem)
    idx = _http(idx_url, timeout=30, expect_bytes=True).decode("utf-8", "replace")

    want = {"LCDC": None, "MCDC": None, "HCDC": None}
    offs = []
    rows = []
    for line in idx.splitlines():
        parts = line.split(":")
        if len(parts) > 3:
            try:
                off = int(parts[1])
            except ValueError:
                continue
            offs.append(off)
            if parts[3] in want:
                want[parts[3]] = off
                rows.append(off)
    if None in want.values():
        raise DirectError("GFS 文件缺少分层云量")
    offs.sort()
    start = min(rows)
    after = [o for o in offs if o > max(rows)]
    exact_stop = (after[0] - 1) if after else (max(rows) + 900_000)

    def grab(stop):
        body = _http("%s/gfs.%s/%s/atmos/%s" % (GFS_BASE, ymd, hh, stem),
                     rng=(start, stop), timeout=120)
        out = {}
        for name, vals, meta in _decode(body):
            if name in ("lcc", "mcc", "hcc") and vals is not None:
                out[name] = (vals, meta)
        return out

    # NOMADS sometimes closes the connection early, truncating the last message
    got = grab(exact_stop)
    if len(got) < 3:
        got = grab(max(rows) + 1_400_000)
    if len(got) < 3:
        raise DirectError("GFS 解码不足三个云量层：%s" % sorted(got))
    ni = got["lcc"][1].get("Ni") or 1440
    nj = got["lcc"][1].get("Nj") or 721
    ix = int(round((lon % 360.0) / (360.0 / ni))) % ni
    iy = int(round((90.0 - lat) / (180.0 / (nj - 1))))
    iy = max(0, min(nj - 1, iy))
    k = iy * ni + ix

    def val(key):
        v = got[key][0][k]
        return None if v is None else float(v)

    return {"cc_low": val("lcc"), "cc_mid": val("mcc"), "cc_high": val("hcc"),
            "run": run.isoformat(timespec="hours"), "step": step,
            "grid": "%dx%d" % (ni, nj), "transport": "GRIB2 byte-range"}


# ---------------------------------------------------------------- ECMWF
def _ecmwf_run(now=None):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(hours=9)
    hour = (t.hour // 6) * 6
    return t.replace(hour=hour, minute=0, second=0, microsecond=0)


def _ecmwf_step(hours):
    if hours <= 144:
        return int(round(hours / 3.0) * 3)
    return int(round(hours / 6.0) * 6)


def _rh_to_fraction(rh, lo=55.0, span=45.0, power=1.6):
    """Sundqvist-type cloud fraction from relative humidity."""
    if rh is None:
        return 0.0
    u = max(0.0, min(1.0, (rh - lo) / span))
    return u ** power


def ecmwf(lat, lon, t_utc):
    """ECMWF open data publishes total cloud (tcc) only, not lcc/mcc/hcc.

    Layered cloud is therefore diagnosed from 850/500/300 hPa relative humidity
    (Sundqvist-type) and constrained by the model's own total cloud. The UI marks
    these values as diagnosed rather than model-native.
    """
    run = _ecmwf_run()
    hours = (t_utc - run).total_seconds() / 3600.0
    step = max(0, min(240, _ecmwf_step(hours)))
    ymd, hh = run.strftime("%Y%m%d"), run.strftime("%H")
    fname = "%s%s0000-%dh-oper-fc.grib2" % (ymd, hh, step)
    rel = "/%s/%sz/ifs/0p25/oper/%s" % (ymd, hh, fname)

    last = None
    for host in ECMWF_HOSTS:
        base = host + rel
        try:
            raw_index = _http(base[:-len(".grib2")] + ".index", timeout=45)
            text = raw_index.decode("utf-8").strip()
            index = json.loads("[" + text.replace("}\n{", "},\n{") + "]")
        except Exception as e:  # noqa: BLE001
            last = e
            continue
        picks = {}
        for rec in index:
            pname = rec.get("param")
            lev = str(rec.get("levelist") or "")
            if pname == "tcc" and rec.get("levtype") == "sfc":
                picks.setdefault("tcc", (int(rec["_offset"]), int(rec["_length"])))
            elif pname == "r" and rec.get("levtype") == "pl" and lev in ("850", "500", "300"):
                picks.setdefault(lev, (int(rec["_offset"]), int(rec["_length"])))
        if len(picks) < 4:
            last = DirectError("ECMWF 索引缺少 tcc/r 层")
            continue

        ni, nj = 1440, 721
        ix = int(round((lon % 360.0) / 0.25)) % ni
        iy = max(0, min(nj - 1, int(round((90.0 - lat) / 0.25))))
        k = iy * ni + ix
        vals = {}
        try:
            for key, (off, ln) in picks.items():
                time.sleep(ECMWF_PAUSE)
                msg = _http(base, rng=(off, off + ln - 1), timeout=90)
                msgs = _decode(msg)
                if not msgs or msgs[0][1] is None:
                    raise DirectError("ECMWF %s 解码失败" % key)
                vals[key] = float(msgs[0][1][k])
        except DirectError as e:
            last = e
            continue

        low = _rh_to_fraction(vals.get("850"))
        mid = _rh_to_fraction(vals.get("500"))
        high = _rh_to_fraction(vals.get("300"))
        tcc = vals.get("tcc")
        total = 1 - (1 - low) * (1 - mid) * (1 - high)
        if tcc is not None and total > 1e-6:
            f = min(1.0, (tcc / 100.0) / total)
            low, mid, high = low * f, mid * f, high * f
        return {"cc_low": round(low * 100, 1), "cc_mid": round(mid * 100, 1),
                "cc_high": round(high * 100, 1),
                "run": run.isoformat(timespec="hours"), "step": step,
                "grid": "%dx%d" % (ni, nj),
                "transport": "GRIB2 range via " + host.split("/")[2],
                "diagnosed": True,
                "rh": {kk: round(vv, 1) for kk, vv in vals.items() if kk != "tcc"},
                "tcc": None if tcc is None else round(tcc, 1)}
    raise DirectError(str(last) if last else "ECMWF 不可达")


# ---------------------------------------------------------------- ICON
def _icon_run(now=None):
    now = now or datetime.now(timezone.utc)
    t = now - timedelta(hours=6)
    return t.replace(hour=(t.hour // 6) * 6, minute=0, second=0, microsecond=0)


def _icon_step(hours):
    h = max(0.0, hours)
    if h <= 78:
        return int(round(h))
    return int(round(h / 3.0) * 3)


def _icon_grid():
    """Time-invariant icosahedral coordinates, cached on disk."""
    path = os.path.join(CACHE, "icon_grid.npz")
    if os.path.exists(path):
        try:
            d = np.load(path)
            return d["lat"], d["lon"]
        except Exception:  # noqa: BLE001
            pass
    if not (HAVE_ECCODES and HAVE_NUMPY):
        raise DirectUnavailable("ICON 需要 eccodes + numpy")
    run = _icon_run()
    stamp = run.strftime("%Y%m%d%H")
    lat = lon = None
    for var, name in (("clat", "CLAT"), ("clon", "CLON")):
        url = ("%s/%s/%s/icon_global_icosahedral_time-invariant_%s_%s.grib2.bz2"
               % (ICON_BASE, run.strftime("%H"), var, stamp, name))
        raw = bz2.decompress(_http(url, timeout=120))
        msgs = _decode(raw)
        if not msgs:
            raise DirectError("ICON %s 解码失败" % name)
        arr = np.array(msgs[0][1], dtype="float32")
        if name == "CLAT":
            lat = arr
        else:
            lon = arr
    lon = np.where(lon > 180, lon - 360, lon)
    np.savez_compressed(path, lat=lat, lon=lon)
    return lat, lon


def icon(lat, lon, t_utc):
    if not (HAVE_ECCODES and HAVE_NUMPY):
        raise DirectUnavailable("ICON 需要 eccodes + numpy")
    run = _icon_run()
    hours = (t_utc - run).total_seconds() / 3600.0
    step = max(0, min(180, _icon_step(hours)))
    stamp = run.strftime("%Y%m%d%H")
    glat, glon = _icon_grid()
    # nearest neighbour on the sphere, restricted to a lon/lat box first
    dlon = np.abs(((glon - lon + 180) % 360) - 180)
    dlat = np.abs(glat - lat)
    cand = np.where((dlon < 1.2) & (dlat < 0.8))[0]
    if cand.size == 0:
        cand = np.argsort(dlat)[:4000]
    d = (dlat[cand] ** 2) + (dlon[cand] * math.cos(math.radians(lat))) ** 2
    idx = int(cand[int(np.argmin(d))])

    out = {}
    for var, key in (("clcl", "cc_low"), ("clcm", "cc_mid"), ("clch", "cc_high")):
        url = ("%s/%s/%s/icon_global_icosahedral_single-level_%s_%03d_%s.grib2.bz2"
               % (ICON_BASE, run.strftime("%H"), var, stamp, step, var.upper()))
        raw = bz2.decompress(_http(url, timeout=120))
        msgs = _decode(raw)
        if not msgs:
            raise DirectError("ICON %s 解码失败" % var.upper())
        vals = msgs[0][1]
        out[key] = float(vals[idx])
    out.update({"run": run.isoformat(timespec="hours"), "step": step,
                "grid": "icosahedral %d" % len(glat), "transport": "GRIB2 bz2"})
    return out


# ---------------------------------------------------------------- dispatch
FETCHERS = {"gfs": gfs, "ecmwf": ecmwf, "icon": icon}
LABELS = {"gfs": "GFS", "ecmwf": "ECMWF IFS", "icon": "ICON"}


def available(source_id):
    if not HAVE_ECCODES:
        return False
    if source_id == "icon":
        return HAVE_NUMPY
    return source_id in FETCHERS


def point(source_id, lat, lon, t_utc):
    """Nearest grid-point layered cloud from an official archive."""
    fn = FETCHERS.get(source_id)
    if fn is None:
        raise DirectUnavailable("未知数据源 %s" % source_id)
    key = os.path.join(CACHE, "%s_%s_%.2f_%.2f_%s.json"
                       % (source_id, t_utc.strftime("%Y%m%d%H"), lat, lon, ""))
    try:
        if os.path.exists(key) and time.time() - os.path.getmtime(key) < 1800:
            with open(key, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    res = fn(lat, lon, t_utc)
    try:
        with open(key, "w", encoding="utf-8") as f:
            json.dump(res, f)
    except OSError:
        pass
    return res
