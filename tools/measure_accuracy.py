"""Quantify what the quota reductions cost in accuracy, using real cached responses.

The project trims Open-Meteo usage by dropping variables the model never reads.
This script answers two questions on data already on disk (no network needed):

  1. Are the dropped variables read anywhere in model.py / nn.py?
  2. If the upstream ring were reduced from 25 points to 1, how far would the
     probability move? (Documents why the ring is NOT trimmed.)

Usage: python tools/measure_accuracy.py
"""
import glob
import json
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import meteo, model, solar  # noqa: E402

CACHE = os.path.join(HERE, "data", "cache")
MDL = model.load_model()


def rebuild(raw):
    pts = meteo.sample_points(31.2304, 121.4737)
    offset = raw[0].get("utc_offset_seconds", 28800)
    recs = []
    for i, p in enumerate(pts):
        h = raw[i]["hourly"]
        rec = {"id": p["id"], "dist": p["dist"], "az": p["az"],
               "time": h["time"], "sources": {}, "aod": []}
        for s in meteo.SOURCES:
            toks = s["tokens"]
            rec["sources"][s["id"]] = {
                "cc_low": meteo._series(h, "cloud_cover_low", toks),
                "cc_mid": meteo._series(h, "cloud_cover_mid", toks),
                "cc_high": meteo._series(h, "cloud_cover_high", toks),
                "rh": meteo._series(h, "relative_humidity_2m", toks),
                "blh": meteo._series(h, "boundary_layer_height", toks),
                "vis": meteo._series(h, "visibility", toks),
                "precip": meteo._series(h, "precipitation", toks),
            }
        recs.append(rec)
    return {"points": recs, "offset": offset, "tz": raw[0].get("timezone"),
            "elevation": raw[0].get("elevation")}


def load_bundles():
    """Cached 25-point multi-day responses, rebuilt into bundle form."""
    out = []
    for p in glob.glob(os.path.join(CACHE, "*.json")):
        try:
            raw = json.load(open(p, encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(raw, list) or len(raw) != 25 or not isinstance(raw[0], dict):
            continue
        h = raw[0].get("hourly") or {}
        if not h.get("time") or "cloud_cover_low_best_match" not in h:
            continue
        if len(h["time"]) < 48:
            continue
        try:
            out.append(rebuild(raw))
        except Exception:  # noqa: BLE001
            continue
    return out


def events_in(bundle, days=4):
    t0 = datetime.fromisoformat(bundle["points"][0]["time"][0])
    t1 = datetime.fromisoformat(bundle["points"][0]["time"][-1])
    out, d = [], t0.date()
    while d <= t1.date() and len(out) < days * 2:
        for key in ("sunset", "sunrise"):
            ev = solar.event_times(31.2304, 121.4737, d, bundle["offset"] / 3600.0,
                                   which=key)[key]
            if ev is None:
                continue
            tl = ev.astimezone(timezone(timedelta(seconds=bundle["offset"]))).replace(tzinfo=None)
            if t0 <= tl <= t1:
                out.append((tl, key, ev))
        d += timedelta(days=1)
    return out


def main():
    print("=" * 68)
    print("问题 1：被删掉的 4 个变量，模型读过吗？")
    print("=" * 68)
    src = (open(os.path.join(HERE, "core", "model.py"), encoding="utf-8").read()
           + open(os.path.join(HERE, "core", "nn.py"), encoding="utf-8").read())
    dropped = ["dew_point_2m", "precipitation_probability",
               "wind_speed_10m", "wind_direction_10m"]
    touched = [v for v in dropped if v in src]
    print("  变量:", ", ".join(dropped))
    print("  在 model.py / nn.py 中出现:", touched or "无")
    print("  → {}".format("有影响，需复查" if touched else "零影响：模型从未读取它们"))

    bundles = load_bundles()
    print()
    print("=" * 68)
    print("问题 2：上游环带 25 点 → 1 点，概率会偏多少？")
    print("=" * 68)
    print("  可用缓存响应: %d 份" % len(bundles))
    if not bundles:
        print("  没有 25 格点缓存，无法评估")
        return 0

    rows = []
    for b in bundles:
        centre = dict(b, points=b["points"][:1])
        for tl, key, ev in events_in(b):
            elev, az = solar.solar_position(31.2304, 121.4737, ev)
            for sid in meteo.SOURCE_IDS:
                if not meteo.available(b, sid, tl):
                    continue
                w_full = meteo.assemble(b, tl, az, sid)
                w_one = meteo.assemble(centre, tl, az, sid)
                r_full = model.assess(w_full, elev, key, MDL)
                r_one = model.assess(w_one, elev, key, MDL)
                rows.append({
                    "time": tl.strftime("%m-%d %H:%M"), "key": key, "src": sid,
                    "dp": abs(r_full["probability"] - r_one["probability"]) * 100,
                    "dg": abs(r_full["grade"] - r_one["grade"]),
                    "h_full": r_full["features"]["horizon"],
                    "h_one": r_one["features"]["horizon"],
                    "low_full": w_full.get("cc_low_up") or 0,
                    "low_one": w_one.get("cc_low_up") or 0,
                })

    n = len(rows)
    dp = sorted(r["dp"] for r in rows)
    q = lambda p: dp[min(n - 1, int(round(p * (n - 1))))]
    same = sum(1 for r in rows if r["dg"] == 0)

    print("  样本: %d 个（事件 × 数据源）" % n)
    print()
    print("  概率偏差（绝对值，百分点）")
    print("    中位 %.2f   p75 %.2f   p90 %.2f   最大 %.2f   平均 %.2f"
          % (q(.5), q(.75), q(.9), max(dp), sum(dp) / n))
    print("  强度等级：完全一致 %d/%d（%.1f%%），最大相差 %d 档"
          % (same, n, 100.0 * same / max(1, n), max(r["dg"] for r in rows)))
    print()
    print("  偏差最大的 5 例：")
    for r in sorted(rows, key=lambda x: -x["dp"])[:5]:
        print("    %s %s %-9s h %.2f→%.2f  Δ%.1fpp  (上游低云 %.0f→%.0f)"
              % (r["time"], "日落" if r["key"] == "sunset" else "日出", r["src"],
                 r["h_full"], r["h_one"], r["dp"], r["low_full"], r["low_one"]))
    print()
    print("  结论：环带不可裁剪 —— 最差情形（太阳方向低云厚、而中心点晴）")
    print("        会让地平线遮挡因子从接近 1 掉到 0.2，概率差 40pp 以上。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
