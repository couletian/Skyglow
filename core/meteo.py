"""Live data access: four sources fetched together, plus CAMS aerosol.

Four sources are requested in the same HTTP call (Open-Meteo multiplexes models and
suffixes the variable names), so comparing them costs no extra round trips:

  openmeteo  Open-Meteo best_match (its own multi-model blend)
  ecmwf      ECMWF IFS 0.25°
  gfs        GFS seamless (NOAA GFS 0.11°/0.25°)
  icon       ICON seamless (DWD ICON global)

AOD comes from CAMS and is shared by all four; no free per-model aerosol product
exists, so every source uses the same aerosol field (stated in the UI).

Free-tier quota is counted per location, so a token bucket keeps the client under
~450 locations per rolling minute and multi-point requests are chunked.
"""
import collections
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from . import config

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "cache")
os.makedirs(CACHE, exist_ok=True)

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
FORECAST = "https://api.open-meteo.com/v1/forecast"
AIR = "https://air-quality-api.open-meteo.com/v1/air-quality"
GEOCODE = "https://geocoding-api.open-meteo.com/v1/search"

SOURCES = [
    {"id": "openmeteo", "label": "Open-Meteo", "short": "OM", "tag": "best match",
     "color": "#ff8a4c", "tokens": ["best_match", "archive_best_match", ""]},
    {"id": "ecmwf", "label": "ECMWF IFS", "short": "IFS", "tag": "0.25°",
     "color": "#6f8fb8", "tokens": ["ecmwf_ifs025"]},
    {"id": "gfs", "label": "GFS", "short": "GFS", "tag": "seamless",
     "color": "#5ad19a", "tokens": ["gfs_seamless"]},
    {"id": "icon", "label": "ICON", "short": "ICON", "tag": "seamless",
     "color": "#c58ad6", "tokens": ["icon_seamless"]},
]
SOURCE_IDS = [s["id"] for s in SOURCES]
SOURCE_BY_ID = {s["id"]: s for s in SOURCES}
MODELS_PARAM = "best_match,ecmwf_ifs025,gfs_seamless,icon_seamless"

# 只取模型真正读取的 7 个量。Open-Meteo 的免费额度按
# 「格点数 x 变量数 x 天数」加权，不是按请求次数；露点、风速风向、
# 降水概率既不参与 model.py / nn.py 的计算，前端也不显示，取回来纯属浪费额度。
FORECAST_VARS = ("cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,"
                 "relative_humidity_2m,precipitation,boundary_layer_height")
ARCHIVE_VARS = FORECAST_VARS

RINGS = [120, 250, 380]
AZIMUTHS = [0, 45, 90, 135, 180, 225, 270, 315]
CHUNK = 300

_BUDGET = collections.deque()
_BUDGET_MAX = 430

# Circuit breaker for the relay: once the hourly quota is exhausted every further
# call fails anyway, so remember it and fail fast instead of sleeping through
# backoff on each request (that used to cost ~90 s per query).
_RELAY_DOWN_UNTIL = 0.0
_RELAY_COOLDOWN = 600
RELAY_HOSTS = ("api.open-meteo.com", "archive-api.open-meteo.com")


_BREAKER_FILE = os.path.join(CACHE, "_relay_breaker.json")


def relay_state():
    left = _RELAY_DOWN_UNTIL - time.time()
    if left <= 0:
        try:
            with open(_BREAKER_FILE, encoding="utf-8") as f:
                left = float(json.load(f).get("until", 0)) - time.time()
        except (OSError, ValueError):
            left = 0
    return {"down": left > 0, "retry_in": int(max(0, left))}


def _load_breaker():
    global _RELAY_DOWN_UNTIL
    try:
        with open(_BREAKER_FILE, encoding="utf-8") as f:
            _RELAY_DOWN_UNTIL = float(json.load(f).get("until", 0))
    except (OSError, ValueError):
        pass


_load_breaker()


def _is_relay(url):
    return any(h in url for h in RELAY_HOSTS)


def _mark_relay_down():
    global _RELAY_DOWN_UNTIL
    _RELAY_DOWN_UNTIL = time.time() + _RELAY_COOLDOWN
    try:
        with open(_BREAKER_FILE, "w", encoding="utf-8") as f:
            json.dump({"until": _RELAY_DOWN_UNTIL}, f)
    except OSError:
        pass


class FetchError(RuntimeError):
    pass


def _throttle(n):
    while True:
        now = time.time()
        while _BUDGET and now - _BUDGET[0][0] > 60:
            _BUDGET.popleft()
        if not _BUDGET or sum(x[1] for x in _BUDGET) + n <= _BUDGET_MAX:
            _BUDGET.append((now, n))
            return
        time.sleep(max(0.4, 61 - (now - _BUDGET[0][0])))


def sample_points(lat, lon):
    pts = [{"id": "c", "lat": lat, "lon": lon, "dist": 0, "az": 0}]
    for d in RINGS:
        for az in AZIMUTHS:
            dlat = (d / 111.32) * math.cos(math.radians(az))
            dlon = (d / (111.32 * math.cos(math.radians(lat)))) * math.sin(math.radians(az))
            pts.append({"id": f"{d}-{az}", "lat": lat + dlat, "lon": lon + dlon,
                        "dist": d, "az": az})
    return pts


def _cache_path(url):
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20]
    return os.path.join(CACHE, h + ".json")


def _get(url, ttl=1800, timeout=60, locations=1):
    relay = _is_relay(url)
    if relay and time.time() < _RELAY_DOWN_UNTIL:
        raise FetchError("Open-Meteo 小时配额已耗尽，约 %d 分钟后自动恢复"
                         % max(1, int((_RELAY_DOWN_UNTIL - time.time()) / 60)))
    path = _cache_path(url)
    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < ttl:
            try:
                with open(path, encoding="utf-8") as f:
                    return json.load(f)
            except ValueError:
                pass
    _throttle(locations)
    req = urllib.request.Request(url, headers={"User-Agent": "skyglow/2.0 (+research)"})
    last = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read().decode("utf-8"))
            if isinstance(data, dict) and data.get("error"):
                raise FetchError(data.get("reason", "upstream error"))
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f)
            return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                if relay:
                    _mark_relay_down()
                    break
                time.sleep(5)
                continue
            time.sleep(1.0 + attempt)
        except FetchError as e:
            last = e
            if "limit" in str(e).lower() or "配额" in str(e):
                if relay:
                    _mark_relay_down()
                break
            time.sleep(1.0 + attempt)
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.0 + attempt)
    raise FetchError(str(last))


def _series(h, var, tokens):
    for t in tokens:
        key = var + ("_" + t if t else "")
        if key in h and h[key]:
            return h[key]
    return None


def _batch_url(base, lat, lon, start, end, hourly, models=True):
    start = start.isoformat() if hasattr(start, "isoformat") else start
    end = end.isoformat() if hasattr(end, "isoformat") else end
    pts = sample_points(lat, lon)
    lats = ",".join("%.4f" % p["lat"] for p in pts)
    lons = ",".join("%.4f" % p["lon"] for p in pts)
    return (base + "?latitude=" + lats + "&longitude=" + lons
            + "&start_date=" + start + "&end_date=" + end
            + "&hourly=" + hourly
            + ("&models=" + MODELS_PARAM if models else "")
            + "&timezone=auto"), pts


def fetch_bundle(lat, lon, start, end, today=None):
    """Hourly series for [start, end] at the centre + 24 upstream points, four sources."""
    today = today or date.today()
    use_forecast = end >= today - timedelta(days=1)
    if use_forecast:
        url, pts = _batch_url(FORECAST, lat, lon, start, end, FORECAST_VARS)
        data = _get(url, ttl=3600, timeout=90, locations=len(pts))
        source = "Open-Meteo forecast"
    else:
        url, pts = _batch_url(ARCHIVE, lat, lon, start, end, ARCHIVE_VARS)
        data = _get(url, ttl=86400, timeout=120, locations=len(pts))
        source = "ERA5 archive"

    if isinstance(data, dict):
        data = [data]
    if len(data) != len(pts):
        raise FetchError("point/response mismatch: %d vs %d" % (len(pts), len(data)))

    aq = None
    try:
        aq_url, _ = _batch_url(AIR, lat, lon, start, end, "aerosol_optical_depth", models=False)
        aq = _get(aq_url, ttl=3600, timeout=90, locations=len(pts))
        if isinstance(aq, dict):
            aq = [aq]
    except FetchError:
        aq = None

    offset = data[0].get("utc_offset_seconds", 0)
    out = []
    for i, p in enumerate(pts):
        h = data[i].get("hourly", {})
        rec = {"id": p["id"], "dist": p["dist"], "az": p["az"],
               "lat": data[i].get("latitude"), "lon": data[i].get("longitude"),
               "time": h.get("time", []), "sources": {}}
        for s in SOURCES:
            toks = s["tokens"]
            rec["sources"][s["id"]] = {
                "cc_low": _series(h, "cloud_cover_low", toks),
                "cc_mid": _series(h, "cloud_cover_mid", toks),
                "cc_high": _series(h, "cloud_cover_high", toks),
                # 与上面 FORECAST_VARS 严格对应；其余量不再请求，也就无从此取
                "rh": _series(h, "relative_humidity_2m", toks),
                "precip": _series(h, "precipitation", toks),
                "blh": _series(h, "boundary_layer_height", toks),
                "vis": _series(h, "visibility", toks),
            }
        rec["aod"] = aq[i].get("hourly", {}).get("aerosol_optical_depth", []) if aq and i < len(aq) else []
        out.append(rec)
    return {"points": out, "offset": offset, "source": source,
            "tz": data[0].get("timezone"), "elevation": data[0].get("elevation"),
            "use_forecast": use_forecast}


def _interp(series, times, t_local):
    if not series or not times:
        return None
    delta = (t_local - datetime.fromisoformat(times[0])).total_seconds() / 3600.0
    i = int(math.floor(delta))
    f = delta - i
    if i < 0 or i + 1 >= len(series):
        return series[max(0, min(i, len(series) - 1))]
    a, b = series[i], series[i + 1]
    if a is None:
        return b
    if b is None:
        return a
    return a * (1 - f) + b * f


def available(bundle, source_id, t_local):
    """True when this source yields usable layered cloud at the event time."""
    centre = bundle["points"][0]
    s = centre["sources"].get(source_id) or {}
    got = 0
    for k in ("cc_low", "cc_mid", "cc_high"):
        if _interp(s.get(k), centre["time"], t_local) is not None:
            got += 1
    return got == 3


def assemble(bundle, t_local, sun_az, source_id):
    """Interpolate one source at the event time and build upstream aggregates."""
    def ang_w(pt_az):
        d = abs((sun_az - pt_az + 180) % 360 - 180)
        return math.cos(math.radians(d) * 0.5) ** 2 if d < 80 else 0.0

    ws, vals = [], {k: [] for k in ("cc_low", "cc_mid", "cc_high")}
    center = None
    for p in bundle["points"]:
        s = p["sources"].get(source_id) or {}
        w = ang_w(p["az"]) * math.exp(-p["dist"] / 260.0)
        ws.append(w)
        for k in ("cc_low", "cc_mid", "cc_high"):
            vals[k].append(_interp(s.get(k), p["time"], t_local))
        if p["dist"] == 0:
            center = p

    def wmean(key):
        num = den = 0.0
        for w, v in zip(ws, vals[key]):
            if v is None:
                continue
            num += w * v
            den += w
        return num / den if den > 0.2 else None

    s = center["sources"].get(source_id) or {}
    times = center["time"]
    w = {}
    for src in ("cc_low", "cc_mid", "cc_high", "rh", "precip", "blh", "vis"):
        w[src] = _interp(s.get(src), times, t_local)
    for key in ("cc_low", "cc_mid", "cc_high"):
        up = wmean(key)
        w[key + "_up"] = up if up is not None else w[key]

    aod = _interp(center.get("aod"), times, t_local)
    num = den = 0.0
    for wt, p in zip(ws, bundle["points"]):
        v = _interp(p.get("aod"), p["time"], t_local)
        if v is None:
            continue
        num += wt * v
        den += wt
    aod_up = num / den if den > 0.2 else None
    w["aod"] = aod if aod is not None else aod_up
    w["aod_up"] = aod_up if aod_up is not None else w["aod"]

    sample = []
    for wt, mid, high in zip(ws, vals["cc_mid"], vals["cc_high"]):
        if wt > 0.04 and mid is not None and high is not None:
            sample.append(0.5 * (mid + high))
    if len(sample) >= 4:
        mu = sum(sample) / len(sample)
        w["spread"] = math.sqrt(sum((x - mu) ** 2 for x in sample) / len(sample))
    else:
        w["spread"] = None
    return w


def _geo_once(term, count):
    url = GEOCODE + "?" + urllib.parse.urlencode(
        {"name": term, "count": count, "language": "zh", "format": "json"})
    data = _get(url, ttl=86400 * 7)
    return data.get("results") or []


_ADMIN = ("特别行政区", "自治区", "自治州", "新区", "开发区", "地区", "林区",
          "省", "市", "区", "县", "旗", "盟", "镇", "乡", "街道")


def _split_admin(text):
    """Split a Chinese address into administrative segments.

    "上海市浦东新区" -> ["上海市", "浦东新区"]   (kept in order)
    Only suffix boundaries are used; no attempt is made to guess missing levels.
    """
    segs = []
    buf = ""
    i = 0
    while i < len(text):
        buf += text[i]
        hit = next((suf for suf in _ADMIN if text.startswith(suf, i + 1)), None)
        if hit and len(buf) >= 2:
            segs.append(buf + hit)
            buf = ""
            i += 1 + len(hit)
            continue
        i += 1
    if buf:
        if segs:
            segs[-1] += buf
        else:
            segs.append(buf)
    return segs


def _strip_admin(text):
    for suf in _ADMIN:
        if text.endswith(suf) and len(text) - len(suf) >= 2:
            return text[:-len(suf)]
    return text


def geocode(q, count=8):
    """Place name -> coordinates, tolerant of free-form input.

    The upstream geocoder matches whole names only, and "上海市" resolves to a
    village in Illinois, so a literal query is not enough. Candidate terms are
    tried from the most specific to the most general:
      1. the raw string, then space/comma parts (longest first)
      2. administrative segments right-to-left ("上海市浦东新区" -> "浦东新区" -> "上海市")
      3. suffix-stripped forms of each ("海淀区" -> "海淀")
    For Chinese input, results in China are preferred; if nothing is found there,
    the general result is kept rather than returning nothing.
    """
    if not q or not q.strip():
        return []
    raw = q.strip()
    parts = [p for p in re.split(r"[\s,，、/]+", raw) if p]

    terms = []
    def add(t):
        if t and len(t) >= 2 and t not in terms:
            terms.append(t)

    for p in sorted(parts, key=len, reverse=True):
        segs = _split_admin(p)
        for seg in reversed(segs):          # most specific first
            add(seg)
        for seg in reversed(segs):
            add(_strip_admin(seg))
        add(p)
    if raw not in terms:
        terms.append(raw)

    chinese = bool(re.search(r"[\u4e00-\u9fa5]", raw))
    pool = []
    for t in terms:
        try:
            pool = _geo_once(t, count)
        except FetchError:
            pool = []
        if pool:
            break

    out = []
    for r in pool:
        out.append({
            "name": r.get("name"), "admin": r.get("admin1") or "",
            "country": r.get("country") or "", "lat": r.get("latitude"),
            "lon": r.get("longitude"), "timezone": r.get("timezone") or "auto",
            "population": r.get("population") or 0,
        })

    if chinese:
        cn = [x for x in out if x["country"] == "中国"]
        if cn:
            out = cn
    out.sort(key=lambda x: x["population"] or 0, reverse=True)
    return out


# ---------------------------------------------------------------- direct archives
def direct_ready(source_id=None):
    """True when the official-archive path can be used on this machine."""
    try:
        from . import direct
    except ImportError:
        return False
    if not direct.HAVE_ECCODES:
        return False
    return direct.available(source_id) if source_id else True


def fetch_direct(source_id, lat, lon, t_utc):
    """Nearest grid point from ECMWF / GFS / ICON official archives."""
    from . import direct
    return direct.point(source_id, lat, lon, t_utc)
