"""Build the backtest feature table from the reanalysis labels + 25-point ERA5 grid."""
import csv
import gzip
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import solar  # noqa: E402
from core.model import features_from_weather, GRADES  # noqa: E402

DATA = os.path.join(HERE, "data")
TZ = timezone(timedelta(hours=8))

VARS = ["cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
        "relative_humidity_2m", "dew_point_2m", "precipitation",
        "wind_speed_10m", "boundary_layer_height"]
KEYS = {"cloud_cover_low": "cc_low", "cloud_cover_mid": "cc_mid",
        "cloud_cover_high": "cc_high", "relative_humidity_2m": "rh",
        "dew_point_2m": "dew", "precipitation": "precip",
        "wind_speed_10m": "wind", "boundary_layer_height": "blh"}


def load_grid():
    pts = []
    for fn in sorted(os.listdir(os.path.join(DATA, "era5"))):
        if not fn.endswith(".json.gz"):
            continue
        with gzip.open(os.path.join(DATA, "era5", fn), "rt", encoding="utf-8") as f:
            d = json.load(f)
        d["times"] = {t: i for i, t in enumerate(d["archive"]["time"])}
        pts.append(d)
    return pts


def interp(arr, t0, event_local):
    """Linear interpolation of an hourly series at a local naive datetime."""
    delta = (event_local - t0).total_seconds() / 3600.0
    i = int(math.floor(delta))
    frac = delta - i
    if i < 0 or i + 1 >= len(arr):
        return None
    a, b = arr[i], arr[i + 1]
    if a is None or b is None:
        return a if a is not None else b
    return a * (1 - frac) + b * frac


def angular_weight(sun_az, pt_az):
    d = abs((sun_az - pt_az + 180) % 360 - 180)
    if d >= 80:
        return 0.0
    return math.cos(math.radians(d) * 0.5) ** 2


def main():
    pts = load_grid()
    rows = list(csv.DictReader(open(os.path.join(DATA, "reanalysis_records.csv"), encoding="utf-8-sig")))
    out = []
    for rec in rows:
        d = datetime.strptime(rec["date"], "%Y-%m-%d").date()
        ev = solar.event_times(31.2304, 121.4737, d, 8)
        t = ev["sunrise"] if rec["event"] == "日出" else ev["sunset"]
        if t is None:
            continue
        t_local = t.astimezone(TZ).replace(tzinfo=None)
        elev, az = solar.solar_position(31.2304, 121.4737, t)

        weights = []
        vals = {k: [] for k in ["cc_low", "cc_mid", "cc_high", "aod"]}
        center = None
        for p in pts:
            w = angular_weight(az, p["point"]["az"]) * math.exp(-p["point"]["dist"] / 260.0)
            weights.append(w)
            t0 = datetime.fromisoformat(p["archive"]["time"][0])
            for src, dst in [("cloud_cover_low", "cc_low"), ("cloud_cover_mid", "cc_mid"),
                             ("cloud_cover_high", "cc_high")]:
                v = interp(p["archive"][src], t0, t_local)
                vals[dst].append(v)
            aod = interp(p["air"]["aerosol_optical_depth"], t0, t_local)
            vals["aod"].append(aod)
            if p["point"]["dist"] == 0:
                center = p
        sw = sum(weights) or 1.0

        def wmean(key, fallback):
            num = den = 0.0
            for w, v in zip(weights, vals[key]):
                if v is None:
                    continue
                num += w * v
                den += w
            if den < 0.25:
                return fallback
            return num / den

        t0c = datetime.fromisoformat(center["archive"]["time"][0])
        w = {}
        for src, dst in KEYS.items():
            w[dst] = interp(center["archive"][src], t0c, t_local)
        w["cc_low_up"] = wmean("cc_low", w["cc_low"])
        w["cc_mid_up"] = wmean("cc_mid", w["cc_mid"])
        w["cc_high_up"] = wmean("cc_high", w["cc_high"])
        w["aod"] = interp(center["air"]["aerosol_optical_depth"], t0c, t_local)
        w["aod_up"] = wmean("aod", w["aod"] if w["aod"] is not None else 0.15)
        if w["aod"] is None:
            w["aod"] = w["aod_up"]
        w["vis"] = None

        f = features_from_weather(w, elev)
        label = rec["actual"]
        pred_sb = rec["predicted"]
        out.append({
            "date": rec["date"], "event": rec["event"], "actual": label,
            "y": GRADES.index(label) if label in GRADES else -1,
            "sunsetbot": GRADES.index(pred_sb) if pred_sb in GRADES else -1,
            "consistency": rec["consistency"],
            "cases": rec["cases"], "phenomena": rec["phenomena"],
            "sun_elev": round(elev, 2), "sun_az": round(az, 1),
            "cc_low": round(w["cc_low"] or 0, 1), "cc_mid": round(w["cc_mid"] or 0, 1),
            "cc_high": round(w["cc_high"] or 0, 1),
            "cc_low_up": round(w["cc_low_up"], 1), "cc_mid_up": round(w["cc_mid_up"], 1),
            "cc_high_up": round(w["cc_high_up"], 1),
            "aod": round(w["aod"], 3), "aod_up": round(w["aod_up"], 3),
            "rh": round(w["rh"] or 0, 1), "dew": round(w["dew"] or 0, 1),
            "precip": round(w["precip"] or 0, 3), "wind": round(w["wind"] or 0, 1),
            "blh": round(w["blh"] or 0, 0),
            "canvas": round(f["canvas"], 4),
            "canvas_high": round(f["canvas_high"], 4),
            "canvas_mid": round(f["canvas_mid"], 4),
            "canvas_low": round(f["canvas_low"], 4),
            "light": round(f["light"], 4),
            "horizon": round(f["horizon"], 4), "purity": round(f["purity"], 4),
            "rgb": "%d;%d;%d" % tuple(f["rgb"]),
        })
    path = os.path.join(DATA, "backtest_features.csv")
    with open(path, "w", newline="", encoding="utf-8-sig") as fp:
        wr = csv.DictWriter(fp, fieldnames=list(out[0].keys()))
        wr.writeheader()
        wr.writerows(out)
    print("rows", len(out), "->", path)


if __name__ == "__main__":
    main()
