"""National and city-scale glow maps for all four data sources.

A coarse regular grid (1.5 deg) is fetched once per (date, mode). Each request
carries all four models, so a four-source map costs the same number of locations
as a single-source map, and every source gets its own probability grid.

Upstream weighting approximates the 24-point ring sampler: three rays (+/-30 deg
around the solar azimuth) at 140/280/420 km, bilinearly interpolated.
"""
import gzip
import json
import math
import os
from datetime import date, datetime, timedelta, timezone

from . import meteo, model, solar

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "cache")

BBOX = (73.5, 18.0, 135.0, 54.0)
STEP = 2.0
CHUNK = 150
# Maps only need layered cloud (+AOD). Humidity / precipitation / BLH make the
# archive endpoint ~9x slower, so maps use neutral assumptions for those and say so.
VARS = "cloud_cover_low,cloud_cover_mid,cloud_cover_high"
RAY_ANGLES = ((-30, 0.22), (0, 0.56), (30, 0.22))
RAY_DISTS = ((140, 0.45), (280, 0.33), (420, 0.22))


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _grid_points(lon0, lat0, lon1, lat1, step):
    nx = int(round((lon1 - lon0) / step)) + 1
    ny = int(round((lat1 - lat0) / step)) + 1
    pts = []
    for j in range(ny):
        for i in range(nx):
            pts.append((lat0 + j * step, lon0 + i * step))
    return pts, nx, ny


def _fetch(points, start, end, use_forecast):
    base = meteo.FORECAST if use_forecast else meteo.ARCHIVE
    out = []
    for chunk in _chunks(points, CHUNK):
        lats = ",".join("%.3f" % p[0] for p in chunk)
        lons = ",".join("%.3f" % p[1] for p in chunk)
        url = (base + "?latitude=" + lats + "&longitude=" + lons
               + "&start_date=" + start + "&end_date=" + end
               + "&hourly=" + VARS + "&models=" + meteo.MODELS_PARAM
               + "&timezone=UTC")
        data = meteo._get(url, ttl=1800 if use_forecast else 86400,
                          timeout=150, locations=len(chunk))
        if isinstance(data, dict):
            data = [data]
        for d in data:
            h = d.get("hourly", {})
            rec = {"time": h.get("time", []), "sources": {}}
            for s in meteo.SOURCES:
                rec["sources"][s["id"]] = {
                    "cc_low": meteo._series(h, "cloud_cover_low", s["tokens"]),
                    "cc_mid": meteo._series(h, "cloud_cover_mid", s["tokens"]),
                    "cc_high": meteo._series(h, "cloud_cover_high", s["tokens"]),
                }
            rec["aod"] = []
            out.append(rec)
    try:
        for k, chunk in enumerate(_chunks(points, CHUNK)):
            lats = ",".join("%.3f" % p[0] for p in chunk)
            lons = ",".join("%.3f" % p[1] for p in chunk)
            url = (meteo.AIR + "?latitude=" + lats + "&longitude=" + lons
                   + "&start_date=" + start + "&end_date=" + end
                   + "&hourly=aerosol_optical_depth&timezone=UTC")
            aq = meteo._get(url, ttl=3600, timeout=120, locations=len(chunk))
            if isinstance(aq, dict):
                aq = [aq]
            for i, a in enumerate(aq):
                idx = k * CHUNK + i
                if idx < len(out):
                    out[idx]["aod"] = (a.get("hourly", {}).get("aerosol_optical_depth") or [])
    except meteo.FetchError:
        pass
    return out


def _bilinear(flat, nx, ny, lat0, lon0, step, lat, lon):
    if not flat:
        return None
    fx = (lon - lon0) / step
    fy = (lat - lat0) / step
    x0 = max(0, min(nx - 1, int(math.floor(fx))))
    y0 = max(0, min(ny - 1, int(math.floor(fy))))
    x1 = min(nx - 1, x0 + 1)
    y1 = min(ny - 1, y0 + 1)
    tx = max(0.0, min(1.0, fx - x0))
    ty = max(0.0, min(1.0, fy - y0))
    vs = [flat[y0 * nx + x0], flat[y0 * nx + x1], flat[y1 * nx + x0], flat[y1 * nx + x1]]
    if any(v is None for v in vs):
        return None
    return ((vs[0] * (1 - tx) + vs[1] * tx) * (1 - ty)
            + (vs[2] * (1 - tx) + vs[3] * tx) * ty)


def _upstream(fields, nx, ny, lat0, lon0, step, lat, lon, az):
    out = {}
    for key in ("cc_low", "cc_mid", "cc_high"):
        num = den = 0.0
        for dang, wa in RAY_ANGLES:
            a = math.radians(az + dang)
            for dist, wd in RAY_DISTS:
                la = lat + math.cos(a) * dist / 111.0
                lo = lon + math.sin(a) * dist / (111.0 * max(0.2, math.cos(math.radians(lat))))
                v = _bilinear(fields[key], nx, ny, lat0, lon0, step, la, lo)
                if v is None:
                    continue
                w = wa * wd
                num += w * v
                den += w
        out[key] = num / den if den > 0.1 else None
    return out


def _interp_utc(series, t0, t):
    if not series:
        return None
    delta = (t - t0).total_seconds() / 3600.0
    i = int(math.floor(delta))
    if i < 0 or i + 1 >= len(series):
        i = max(0, min(len(series) - 1, i))
        return series[i]
    a, b = series[i], series[i + 1]
    if a is None:
        return b
    if b is None:
        return a
    f = delta - i
    return a * (1 - f) + b * f


def _fields_at_events(series, events):
    out = {sid: {"cc_low": [], "cc_mid": [], "cc_high": [], "aod": []}
           for sid in meteo.SOURCE_IDS}
    for rec, t in zip(series, events):
        t0 = (datetime.fromisoformat(rec["time"][0]).replace(tzinfo=timezone.utc)
              if rec["time"] else None)
        aod = _interp_utc(rec.get("aod"), t0, t) if (t0 and t) else None
        for s in meteo.SOURCES:
            src = rec["sources"].get(s["id"]) or {}
            for k in ("cc_low", "cc_mid", "cc_high"):
                out[s["id"]][k].append(_interp_utc(src.get(k), t0, t) if (t0 and t) else None)
            out[s["id"]]["aod"].append(aod)
    return out


def _assess(points, nx, ny, lon0, lat0, step, series, events, mode, mdl, fields):
    res = {sid: {"probs": [], "grades": []} for sid in meteo.SOURCE_IDS}
    for k, (lat, lon) in enumerate(points):
        t = events[k]
        for sid in meteo.SOURCE_IDS:
            fld = fields.get(sid) or {}
            lo = fld.get("cc_low") or []
            mid = fld.get("cc_mid") or []
            hi = fld.get("cc_high") or []
            if t is None or k >= len(lo) or lo[k] is None or mid[k] is None or hi[k] is None:
                res[sid]["probs"].append(None)
                res[sid]["grades"].append(None)
                continue
            elev, az = solar.solar_position(lat, lon, t)
            rec = series[k]
            t0 = datetime.fromisoformat(rec["time"][0]).replace(tzinfo=timezone.utc)
            src = rec["sources"].get(sid) or {}
            w = {
                "cc_low": lo[k], "cc_mid": mid[k], "cc_high": hi[k],
                "rh": None, "precip": 0.0, "blh": None,   # neutral, see VARS note
                "aod": (fld.get("aod") or [None] * len(lo))[k], "vis": None,
            }
            up = _upstream(fld, nx, ny, lat0, lon0, step, lat, lon, az)
            w["cc_low_up"] = up["cc_low"] if up["cc_low"] is not None else w["cc_low"]
            w["cc_mid_up"] = up["cc_mid"] if up["cc_mid"] is not None else w["cc_mid"]
            w["cc_high_up"] = up["cc_high"] if up["cc_high"] is not None else w["cc_high"]
            w["aod_up"] = w["aod"]
            r = model.assess(w, elev, mode, mdl)
            res[sid]["probs"].append(round(r["probability"], 3))
            res[sid]["grades"].append(r["grade"])
    return res


def _cache_path(name):
    return os.path.join(CACHE, name + ".json.gz")


def _load(name, max_age):
    path = _cache_path(name)
    if not os.path.exists(path):
        return None
    if max_age is not None and (datetime.now().timestamp() - os.path.getmtime(path)) > max_age:
        return None
    try:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _save(name, obj):
    with gzip.open(_cache_path(name), "wt", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _meta():
    return [{"id": s["id"], "label": s["label"], "short": s["short"],
             "tag": s["tag"], "color": s["color"]} for s in meteo.SOURCES]


def _missing(res):
    return [sid for sid in meteo.SOURCE_IDS
            if all(v is None for v in res[sid]["probs"])]


def national(d, mode, near=None):
    """Coarse national field. Only built on explicit request (expensive)."""
    today = date.today()
    max_age = None if d < today - timedelta(days=2) else 3600 * 6
    key = "map2_%s_%s" % (d.isoformat(), mode)
    cached = _load(key, max_age)
    if cached:
        cached["cached"] = True
        if near:
            cached["focus"] = [near[0], near[1]]
        return cached

    pts, nx, ny = _grid_points(BBOX[0], BBOX[1], BBOX[2], BBOX[3], STEP)
    start = end = d.isoformat()
    use_forecast = d >= today - timedelta(days=1)
    series = _fetch(pts, start, end, use_forecast)
    events = [solar.event_times(lat, lon, d, 8, step_min=10, which=mode)[mode]
              for lat, lon in pts]
    fields = _fields_at_events(series, events)
    mdl = model.load_model()
    res = _assess(pts, nx, ny, BBOX[0], BBOX[1], STEP, series, events, mode, mdl, fields)
    payload = {
        "scope": "national", "date": d.isoformat(), "mode": mode,
        "bbox": list(BBOX), "step": STEP, "nx": nx, "ny": ny,
        "sources": res, "failed": _missing(res), "sources_meta": _meta(),
        "fields": fields, "focus": list(near) if near else None,
        "source": "Open-Meteo forecast" if use_forecast else "ERA5 archive",
        "generated_at": datetime.now().isoformat(timespec="seconds"), "cached": False,
    }
    _save(key, payload)
    return payload


def local(lat, lon, d, mode, nat=None):
    """0.25 deg field around the selected coordinate.

    nat=None means the national field was not generated (the default): the city
    grid then also serves as its own upstream source, which is adequate because the
    169-point box already spans +/-1.5 deg (about 330 km).
    """
    step = 0.25
    half = 1.5
    lon0 = round((lon - half) / step) * step
    lat0 = round((lat - half) / step) * step
    pts, nx, ny = _grid_points(lon0, lat0, lon0 + 2 * half, lat0 + 2 * half, step)
    today = date.today()
    key = "maploc2_%s_%s_%.2f_%.2f" % (d.isoformat(), mode, lat, lon)
    cached = _load(key, None if d < today - timedelta(days=2) else 3600 * 3)
    if cached:
        cached["cached"] = True
        return cached

    start = end = d.isoformat()
    use_forecast = d >= today - timedelta(days=1)
    series = _fetch(pts, start, end, use_forecast)
    events = [solar.event_times(la, lo, d, 8, step_min=10, which=mode)[mode]
              for la, lo in pts]
    mdl = model.load_model()

    if nat and nat.get("fields"):
        fields = nat["fields"]
        nb = nat["bbox"]
        nb_nx, nb_ny, nb_step = nat["nx"], nat["ny"], nat["step"]
        nb_lat0, nb_lon0 = nb[1], nb[0]
    else:
        fields = _fields_at_events(series, events)
        nb_nx, nb_ny, nb_step, nb_lat0, nb_lon0 = nx, ny, step, lat0, lon0
    res = _assess(pts, nb_nx, nb_ny, nb_lon0, nb_lat0, nb_step,
                  series, events, mode, mdl, fields)
    payload = {
        "scope": "local", "date": d.isoformat(), "mode": mode,
        "bbox": [lon0, lat0, lon0 + 2 * half, lat0 + 2 * half],
        "step": step, "nx": nx, "ny": ny, "center": [lat, lon],
        "sources": res, "failed": _missing(res), "sources_meta": _meta(),
        "source": "Open-Meteo forecast" if use_forecast else "ERA5 archive",
        "generated_at": datetime.now().isoformat(timespec="seconds"), "cached": False,
    }
    _save(key, payload)
    return payload
