"""Download ERA5 (Open-Meteo archive) + CAMS AOD (Open-Meteo air quality) on a
25-point sampling grid around Shanghai, for the backtest period.

Grid: centre + 3 rings (120/250/380 km) x 8 azimuths.  The upstream weighting is
applied later, per event, using the actual solar azimuth.
"""
import gzip
import json
import math
import os
import time
import urllib.parse
import urllib.request
from datetime import date

CENTRE = (31.2304, 121.4737)
RINGS = [120, 250, 380]
AZIMUTHS = [0, 45, 90, 135, 180, 225, 270, 315]
START = "2022-06-24"
END = "2023-12-30"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "era5")

ARCHIVE_VARS = ("cloud_cover_low,cloud_cover_mid,cloud_cover_high,visibility,"
                "relative_humidity_2m,dew_point_2m,precipitation,wind_speed_10m,"
                "wind_direction_10m,boundary_layer_height")


def grid():
    pts = [{"id": "c000", "lat": CENTRE[0], "lon": CENTRE[1], "dist": 0, "az": 0}]
    for d in RINGS:
        for az in AZIMUTHS:
            dlat = (d / 111.32) * math.cos(math.radians(az))
            dlon = (d / (111.32 * math.cos(math.radians(CENTRE[0])))) * math.sin(math.radians(az))
            pts.append({"id": f"r{d:03d}a{az:03d}", "lat": CENTRE[0] + dlat,
                        "lon": CENTRE[1] + dlon, "dist": d, "az": az})
    return pts


def get(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "skyglow-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            print("  retry", i, e)
            time.sleep(2 + 2 * i)
    raise RuntimeError("failed " + url)


def main():
    os.makedirs(OUT, exist_ok=True)
    pts = grid()
    for i, p in enumerate(pts):
        path = os.path.join(OUT, p["id"] + ".json.gz")
        if os.path.exists(path):
            print("skip", p["id"])
            continue
        base = ("?latitude=%.4f&longitude=%.4f&start_date=%s&end_date=%s"
                "&timezone=Asia%%2FShanghai" % (p["lat"], p["lon"], START, END))
        t0 = time.time()
        arch = get("https://archive-api.open-meteo.com/v1/archive" + base + "&hourly=" + ARCHIVE_VARS)
        aq = get("https://air-quality-api.open-meteo.com/v1/air-quality" + base
                 + "&hourly=aerosol_optical_depth,pm2_5")
        payload = {"point": p, "archive": arch["hourly"], "air": aq["hourly"],
                   "elevation": arch.get("elevation")}
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump(payload, f)
        print(f"[{i+1}/{len(pts)}] {p['id']} {time.time()-t0:.1f}s")
    print("done")


if __name__ == "__main__":
    main()
