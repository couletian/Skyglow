"""16-direction glow radar around a point, computed for all four sources.

For each of 16 bearings we sample cloud cover at 60/120/180/260/340/420 km and ask
whether a cloud deck on that bearing would be visible from the observer at sunset:

  canvas  how much mid/high cloud can be lit (distance-weighted, peaking ~200 km)
  block   low cloud in the first 120 km, which cuts the light path / horizon
  sector  angular distance to the solar azimuth (glow only appears on the sun side)

The sun-direction transect is returned as well; it feeds the geometry cross-section.
"""
import math
from datetime import date, timedelta, timezone

from . import meteo, model, solar, store

DISTANCES = (60, 140, 240, 380)   # 4 rings x 16 bearings = 64 + 13 transect
TRANSECT = (40, 80, 120, 160, 200, 240, 280, 320, 360, 400, 450, 500, 550)
DIR_NAMES = ["北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南",
             "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北"]


def _grid(lat, lon, sun_az):
    pts = []
    for i in range(16):
        az = i * 22.5
        for d in DISTANCES:
            la = lat + math.cos(math.radians(az)) * d / 111.0
            lo = lon + math.sin(math.radians(az)) * d / (111.0 * max(0.2, math.cos(math.radians(lat))))
            pts.append((round(la, 3), round(lo, 3), i, d))
    for d in TRANSECT:
        la = lat + math.cos(math.radians(sun_az)) * d / 111.0
        lo = lon + math.sin(math.radians(sun_az)) * d / (111.0 * max(0.2, math.cos(math.radians(lat))))
        pts.append((round(la, 3), round(lo, 3), -1, d))
    return pts


def _fetch(points, start, end, use_forecast):
    base = meteo.FORECAST if use_forecast else meteo.ARCHIVE
    lats = ",".join("%.3f" % p[0] for p in points)
    lons = ",".join("%.3f" % p[1] for p in points)
    url = (base + "?latitude=" + lats + "&longitude=" + lons
           + "&start_date=" + start + "&end_date=" + end
           # 方位评分只用三层云量与边界层高度；湿度在这里从不参与计算
           + "&hourly=cloud_cover_low,cloud_cover_mid,cloud_cover_high,"
             "boundary_layer_height"
           + "&models=" + meteo.MODELS_PARAM + "&timezone=UTC")
    data = meteo._get(url, ttl=1800 if use_forecast else 86400,
                      timeout=150, locations=len(points))
    if isinstance(data, dict):
        data = [data]
    out = []
    for d in data:
        h = d.get("hourly", {})
        rec = {"time": h.get("time", []), "sources": {}}
        for s in meteo.SOURCES:
            rec["sources"][s["id"]] = {
                "cc_low": meteo._series(h, "cloud_cover_low", s["tokens"]),
                "cc_mid": meteo._series(h, "cloud_cover_mid", s["tokens"]),
                "cc_high": meteo._series(h, "cloud_cover_high", s["tokens"]),
                "blh": meteo._series(h, "boundary_layer_height", s["tokens"]),
            }
        out.append(rec)
    return out


def analyze(lat, lon, d, mode, refresh=False):
    """109-point bearing scan. Cached like a prediction: local first, network on miss."""
    ckey = store.key(lat, lon, d, "az" + mode, model.load_model()["version"])
    if not refresh:
        hit, age = store.get("azimuth", ckey, d)
        if hit:
            hit["cache"] = {"hit": True, "age": age, "ttl": store.ttl_for(d)}
            return hit
    payload = _analyze(lat, lon, d, mode)
    payload["cache"] = {"hit": False, "age": 0, "ttl": store.ttl_for(d)}
    store.put("azimuth", ckey, payload)
    return payload


def _analyze(lat, lon, d, mode):
    today = date.today()
    start = (d - timedelta(days=1)).isoformat()
    end = (d + timedelta(days=1)).isoformat()
    use_forecast = d >= today - timedelta(days=1)
    ev = solar.event_times(lat, lon, d, 8, step_min=5, which=mode)[mode]
    elev, sun_az = solar.solar_position(lat, lon, ev)
    pts = _grid(lat, lon, sun_az)
    series = _fetch(pts, start, end, use_forecast)

    per_source = {}
    for sid in meteo.SOURCE_IDS:
        rows = []
        tz_utc = timezone.utc
        for (la, lo, di, dist), rec in zip(pts, series):
            s = rec["sources"].get(sid) or {}
            t_utc = ev.astimezone(tz_utc).replace(tzinfo=None)
            rows.append({
                "dir": di, "dist": dist,
                "low": meteo._interp(s.get("cc_low"), rec["time"], t_utc),
                "mid": meteo._interp(s.get("cc_mid"), rec["time"], t_utc),
                "high": meteo._interp(s.get("cc_high"), rec["time"], t_utc),
                "blh": meteo._interp(s.get("blh"), rec["time"], t_utc),
            })
        if all(r["low"] is None and r["mid"] is None and r["high"] is None for r in rows):
            per_source[sid] = {"id": sid, "error": "数据获取失败"}
            continue

        dirs = []
        for i in range(16):
            sub = [r for r in rows if r["dir"] == i]
            num = den = 0.0
            best = (-1.0, None, 0.0)
            for r in sub:
                u_mid = min(1.0, (r["mid"] or 0) / 100.0)
                u_high = min(1.0, (r["high"] or 0) / 100.0)
                deck = max(1 - math.exp(-3.7 * u_mid), 1 - math.exp(-3.1 * u_high))
                w = math.exp(-((r["dist"] - 200) / 170.0) ** 2)
                num += w * deck
                den += w
                if deck > best[0]:
                    best = (deck, r["dist"], (r["mid"] or 0) + (r["high"] or 0))
            canvas = num / den if den else 0.0
            near = [r for r in sub if r["dist"] <= 140]
            block = max((r["low"] or 0) for r in near) / 100.0 if near else 0.0
            delta = abs((sun_az - i * 22.5 + 180) % 360 - 180)
            sector = math.cos(math.radians(delta) * 0.5) ** 2 if delta < 90 else 0.0
            score = 100 * canvas * (1 - 0.7 * block) * (0.22 + 0.78 * sector)
            dirs.append({
                "az": i * 22.5, "name": DIR_NAMES[i],
                "score": round(max(0.0, min(100.0, score)), 1),
                "dist": best[1], "cover": round(min(100, best[2]), 0),
                "canvas": round(canvas, 3), "block": round(block, 2),
            })

        trans = [{"dist": r["dist"], "mid": r["mid"], "high": r["high"],
                  "low": r["low"], "blh": r["blh"]}
                 for r in rows if r["dir"] == -1]
        bases = model.deck_bases({"blh": (trans[1]["blh"] if len(trans) > 1 else None) or 600})
        h = bases["high"]
        reach = 2 * math.sqrt(2 * 6371.0 * h)
        edge = None
        for t in trans:
            if (t["high"] or 0) + (t["mid"] or 0) >= 50:
                edge = t["dist"]
                break
        per_source[sid] = {
            "id": sid, "dirs": dirs,
            "best": max(dirs, key=lambda x: x["score"]),
            "transect": trans,
            "cloud_base": h, "glow_reach": round(reach), "edge": edge,
        }

    ok = [v for v in per_source.values() if "error" not in v]
    return {
        "sun_az": round(sun_az, 1), "sun_elev": round(elev, 2),
        "sources": per_source,
        "failed": [sid for sid, v in per_source.items() if "error" in v],
        "sources_meta": [{"id": s["id"], "label": s["label"], "short": s["short"],
                          "color": s["color"]} for s in meteo.SOURCES],
        "consensus": None if not ok else {
            "best_dir": max(ok, key=lambda v: v["best"]["score"])["best"]["name"],
            "score_min": min(v["best"]["score"] for v in ok),
            "score_max": max(v["best"]["score"] for v in ok),
        },
        "source": "Open-Meteo forecast" if use_forecast else "ERA5 archive",
    }
