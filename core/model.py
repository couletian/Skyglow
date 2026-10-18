"""Skyglow model: physics features + calibrated probability.

Pipeline
--------
1. Cloud decks (low / mid / high) are described by their ERA5 layer cover.
2. Each deck gets a "canvas" value (how much usable glowing cloud there is) and a
   spectral `light` value (how bright/red the sunlight reaching its base is).
3. The upstream (sun-side) low cloud field gives a horizon-blocking factor.
4. Visibility / humidity / precipitation give an air-purity factor.
5. log-odds features are combined by a ridge regression fitted on the sunsetbot
   reanalysis labels (see tools/calibrate.py), then mapped to a probability.

The regression is a *calibration* layer on top of physically interpretable
features, not a replacement for them: every term keeps its physical meaning.
"""
import json
import math
import os
from datetime import timedelta

from . import solar
from .spectrum import deck_light, colour_name

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(HERE, "data", "model.json")

GRADES = ["基本不烧", "微烧", "小烧", "小到中烧", "中到大烧", "大烧"]
GRADE_COLORS = ["#5b6477", "#8a6f5a", "#b5764a", "#d4823c", "#e8603c", "#f2402f"]

DECKS = [
    {"key": "high", "label": "高云", "base": 8.6, "thickness": 1.6, "w": 1.00},
    {"key": "mid", "label": "中云", "base": 5.2, "thickness": 1.2, "w": 0.95},
    {"key": "low", "label": "低云", "base": None, "thickness": 0.8, "w": 0.45},
]

DEFAULT_MODEL = {
    "version": "physics-v1",
    "features": ["canvas", "canvas_high", "light", "horizon", "purity", "aod", "precip"],
    "mean": [0.0] * 7,
    "std": [1.0] * 7,
    "coef": [0.15, 0.30, 0.10, 0.25, 0.10, -0.06, -0.15],
    "intercept": -0.55,
    "prob_a": 1.55,
    "prob_b": 0.75,
    "thresholds": [0.9, 1.6, 2.3, 3.0, 3.8],
    "prob_cuts": [0.08, 0.16, 0.26, 0.38, 0.52],
    "metrics": {},
}


def _sigmoid(x):
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _logit(p, eps=0.012):
    p = _clamp(p, eps, 1 - eps)
    return math.log(p / (1 - p))


def load_model():
    try:
        with open(MODEL_PATH, encoding="utf-8") as f:
            m = json.load(f)
        for k, v in DEFAULT_MODEL.items():
            m.setdefault(k, v)
        return m
    except (OSError, ValueError):
        return dict(DEFAULT_MODEL)


def deck_bases(features):
    blh = features.get("blh") or 0
    low_base = _clamp(blh / 1000.0, 0.45, 2.2) if blh else 1.2
    return {"low": low_base, "mid": 5.2, "high": 8.6}


def _canvas_curve(key, cover):
    u = _clamp(cover / 100.0, 0.0, 1.0)
    if key == "high":
        return 1.0 - math.exp(-3.1 * u)
    if key == "mid":
        c = 1.0 - math.exp(-3.7 * u)
        if u > 0.94:
            c *= 0.86
        return c
    return 0.52 * (1.0 - math.exp(-1.5 * u))


def features_from_weather(w, sun_elev):
    """Physical sub-scores for one moment."""
    bases = deck_bases(w)
    aod = _clamp(w.get("aod") if w.get("aod") is not None else 0.15, 0.02, 1.2)
    aod_up = _clamp(w.get("aod_up") if w.get("aod_up") is not None else aod, 0.02, 1.5)

    decks = []
    for d in DECKS:
        base_km = bases[d["key"]]
        cover = w.get("cc_" + d["key"]) or 0.0
        eff_h = base_km + 0.45 * d["thickness"]
        limit = solar.illumination_limit(eff_h)
        # soft Earth-shadow gate: the base stops receiving direct sunlight below
        # the geometric limit, so it cannot contribute a glow any more
        gate = 1.0 / (1.0 + math.exp(-(sun_elev - limit) / 0.7))
        canvas = _canvas_curve(d["key"], cover) * d["w"] * gate
        light = deck_light(eff_h, sun_elev, aod if d["key"] == "low" else aod_up)
        lit = sun_elev >= limit
        decks.append({
            "key": d["key"], "label": d["label"], "cover": cover, "base_km": base_km,
            "canvas": canvas, "lit": lit, "light": light, "weight": d["w"],
            "limit": limit,
        })

    total_w = sum(x["canvas"] for x in decks)
    if total_w > 1e-6:
        canvas = 1.0 - math.prod(1.0 - x["canvas"] for x in decks)
        light_val = sum(x["canvas"] * x["light"]["lum"] for x in decks) / total_w
        rgb = [sum(x["canvas"] * x["light"]["rgb"][i] for x in decks) / total_w for i in range(3)]
    else:
        canvas = 0.0
        light_val = 0.0
        rgb = [108, 96, 92]

    low_up = _clamp((w.get("cc_low_up", w.get("cc_low", 0)) or 0) / 100.0, 0, 1)
    mid_up = _clamp((w.get("cc_mid_up", w.get("cc_mid", 0)) or 0) / 100.0, 0, 1)
    horizon = 1.0 - 0.85 * _sigmoid((low_up - 0.62) / 0.10)
    horizon *= 1.0 - 0.25 * _sigmoid((mid_up - 0.85) / 0.08)
    horizon *= 1.0 - 0.30 * _sigmoid((_clamp((w.get("cc_low") or 0) / 100.0, 0, 1) - 0.80) / 0.08)
    horizon = _clamp(horizon, 0.05, 1.0)

    # ERA5 archive does not expose visibility; estimate it from AOD + humidity
    # (Koschmieder 3.912/b_ext, with hygroscopic growth). Live forecast uses the
    # real visibility value when the API provides one.
    rh = w.get("rh") if w.get("rh") is not None else 60.0
    vis_m = w.get("vis")
    if vis_m is None:
        growth = (1.0 - _clamp(rh, 5, 99) / 100.0) ** -0.55
        b_ext = aod / 1.4 * growth + 0.012  # km^-1
        vis_km = _clamp(3.912 / b_ext, 1.0, 80.0)
    else:
        vis_km = vis_m / 1000.0

    precip = w.get("precip") or 0.0
    purity = (0.62 + 0.38 * _sigmoid((vis_km - 12) / 4.0)) * (1.0 - 0.28 * _sigmoid((rh - 92) / 4.0))
    if precip >= 0.05:
        purity *= 0.55 if precip < 0.6 else 0.28
    purity = _clamp(purity, 0.05, 1.0)

    return {
        "canvas": _clamp(canvas, 0, 0.999),
        "canvas_high": _clamp(decks[0]["canvas"], 0, 0.999),
        "canvas_mid": _clamp(decks[1]["canvas"], 0, 0.999),
        "canvas_low": _clamp(decks[2]["canvas"], 0, 0.999),
        "light": _clamp(light_val, 0, 1),
        "horizon": horizon,
        "purity": purity,
        "aod": aod,
        "precip": precip,
        "rgb": [int(round(c)) for c in rgb],
        "decks": decks,
        "bases": bases,
        "low_up": low_up,
        "mid_up": mid_up,
    }


def _raw_features(f):
    """Nine physically interpretable terms; the ridge layer only reweights them."""
    return [
        _logit(f["canvas"]),
        _logit(f.get("canvas_high", 0.0) * 1.6),
        math.log(max(f["light"], 0.004)),
        _logit(f["horizon"]),
        _logit(f["purity"]),
        f["aod"],
        1.0 if f["precip"] >= 0.2 else 0.0,
    ]


def intensity(f, model):
    x = _raw_features(f)
    z = model["intercept"]
    for xi, mu, sd, b in zip(x, model["mean"], model["std"], model["coef"]):
        z += b * (xi - mu) / (sd or 1.0)
    return z


def probability(idx, model):
    return _sigmoid(model["prob_a"] * (idx - model["prob_b"]))


def grade_from_index(idx, model):
    """Conditional intensity grade from the latent index (not from probability),
    so "if it happens, how strong" is answered independently of "will it happen"."""
    t = model["thresholds"]
    g = 0
    for i, th in enumerate(t):
        if idx >= th:
            g = i + 1
    return g


def assess(w, sun_elev, event, model=None):
    model = model or load_model()
    f = features_from_weather(w, sun_elev)
    idx = _clamp(intensity(f, model), -1.0, 6.5)
    p = probability(idx, model)
    g = grade_from_index(idx, model)
    lum = f["light"]
    rgb = f["rgb"]
    return {
        "probability": round(p, 4),
        "index": round(idx, 3),
        "grade": g,
        "grade_label": GRADES[g],
        "grade_color": GRADE_COLORS[g],
        "colour": {"rgb": rgb, "hex": "#%02x%02x%02x" % tuple(rgb),
                   "name": colour_name(rgb, lum), "luminance": round(lum, 4)},
        "features": {
            "canvas": round(f["canvas"], 4),
            "light": round(lum, 4),
            "horizon": round(f["horizon"], 4),
            "purity": round(f["purity"], 4),
            "aod": round(f["aod"], 3),
            "precip": round(f["precip"], 2),
        },
        "decks": [{
            "key": d["key"], "label": d["label"], "cover": round(d["cover"], 1),
            "base_km": round(d["base_km"], 2), "canvas": round(d["canvas"], 3),
            "lit": d["lit"], "luminance": round(d["light"]["lum"], 4),
            "rgb": d["light"]["rgb"], "tangent_km": round(d["light"]["tangent_km"], 2),
        } for d in f["decks"]],
        "event": event,
        "sun_elev": round(sun_elev, 3),
        "model_version": model["version"],
    }


def _contrast(elev):
    """Perceptual contrast of a lit cloud against the twilight sky.

    Before sunset the sky is still bright and the colour is washed out; the glow
    only reads as "火烧云" once the sun is near or below the horizon.
    """
    return 1.0 / (1.0 + math.exp((elev - 0.6) / 1.5))


def timeline(w, event_time, lat, lon, model=None, before=35, after=55, step=1):
    model = model or load_model()
    _, _ = solar.solar_position(lat, lon, event_time)
    ref = _contrast(solar.solar_position(lat, lon, event_time)[0])
    out = []
    for m in range(-before, after + 1, step):
        t = event_time + timedelta(minutes=m)
        elev, az = solar.solar_position(lat, lon, t)
        r = assess(w, elev, "sunset", model)
        shape = min(1.0, _contrast(elev) / max(ref, 0.25))
        out.append({
            "minute": m,
            "time": t.isoformat(),
            "elev": round(elev, 3),
            "az": round(az, 1),
            "probability": r["probability"],
            "view": round(min(1.0, r["probability"] * shape), 4),
            "grade": r["grade"],
            "luminance": r["features"]["light"],
            "rgb": r["colour"]["rgb"],
        })
    return out


def best_window(tl, model=None):
    """Contiguous minutes with viewability >= 45% of the peak (min 6 minutes)."""
    if not tl:
        return None
    pk = max(x["view"] for x in tl)
    if pk < 0.08:
        return None
    thr = max(0.10, pk * 0.45)
    best = []
    cur = []
    for x in tl:
        if x["view"] >= thr and x["luminance"] > 0.012 and x["elev"] < 4.5:
            cur.append(x)
        else:
            if len(cur) > len(best):
                best = cur
            cur = []
    if len(cur) > len(best):
        best = cur
    if not best:
        return None
    return {
        "start_min": best[0]["minute"], "end_min": best[-1]["minute"],
        "peak_min": max(best, key=lambda x: x["view"])["minute"],
        "peak_probability": round(max(x["view"] for x in best), 3),
        "start": best[0]["time"], "end": best[-1]["time"],
        "duration": len(best),
    }
