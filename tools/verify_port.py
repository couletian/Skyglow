"""Compare the JS port against the Python implementation on identical inputs.

Usage: python tools/verify_port.py
Builds a fixture from the Python side, runs the JS with node, and diffs the numbers.
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import model, nn, solar, spectrum  # noqa: E402

NODE = r"C:\Users\Miracle R\.workbuddy\binaries\node\versions\22.22.2-6\node.exe"
CORE_JS = os.path.join(os.path.dirname(HERE), "_build", "core.js")

CASES = [
    # lat, lon, local date, tz offset, mode, weather dict
    (31.2304, 121.4737, (2026, 10, 18), 8, "sunset",
     {"cc_low": 20, "cc_mid": 45, "cc_high": 80, "cc_low_up": 30, "cc_mid_up": 40,
      "rh": 76, "blh": 900, "aod": 0.18, "vis": None, "precip": 0.0}),
    (28.14, 86.85, (2026, 6, 21), 8, "sunrise",
     {"cc_low": 5, "cc_mid": 10, "cc_high": 95, "cc_low_up": 8, "cc_mid_up": 12,
      "rh": 40, "blh": 3000, "aod": 0.06, "vis": 30000, "precip": 0.0}),
    (39.9042, 116.4074, (2026, 1, 15), 8, "sunset",
     {"cc_low": 90, "cc_mid": 70, "cc_high": 30, "cc_low_up": 88, "cc_mid_up": 65,
      "rh": 95, "blh": 400, "aod": 0.6, "vis": None, "precip": 0.8}),
    (0.0, 0.0, (2026, 3, 20), 0, "sunset",
     {"cc_low": 0, "cc_mid": 0, "cc_high": 0, "cc_low_up": 0, "cc_mid_up": 0,
      "rh": 60, "blh": 600, "aod": 0.15, "vis": None, "precip": 0.0}),
    (-33.87, 151.21, (2026, 12, 21), 11, "sunset",
     {"cc_low": 35, "cc_mid": 60, "cc_high": 70, "cc_low_up": 40, "cc_mid_up": 55,
      "rh": 65, "blh": 1200, "aod": 0.25, "vis": 20000, "precip": 0.0}),
]


def py_side():
    m = model.load_model()
    nnm = nn.load()
    out = []
    for lat, lon, (y, mo, d), tz, mode, w in CASES:
        import datetime as dt
        ev = solar.event_times(lat, lon, dt.date(y, mo, d), tz, which=mode)[mode]
        elev, az = solar.solar_position(lat, lon, ev)
        r = model.assess(w, elev, mode, m)
        f = model.features_from_weather(w, elev)
        feats = [w.get("cc_low") or 0, w.get("cc_mid") or 0, w.get("cc_high") or 0,
                 w.get("cc_low_up") or 0, w.get("cc_mid_up") or 0,
                 w.get("rh") or 0, w.get("blh") or 0, elev,
                 f["canvas"], f["canvas_high"], f["light"], f["horizon"],
                 f["purity"], f["aod"], f["precip"]]
        p_nn = nn.predict(feats)
        dl = spectrum.deck_light(8.6 + 0.45 * 1.6, elev, w["aod"])
        out.append({
            "event_ms": int(ev.timestamp() * 1000),
            "elev": elev, "az": az,
            "prob": r["probability"], "index": r["index"], "grade": r["grade"],
            "rgb": r["colour"]["rgb"], "lum": r["colour"]["luminance"],
            "feat": r["features"],
            "deck_light": {"lum": dl["lum"], "rgb": dl["rgb"], "tangent": dl["tangent_km"],
                           "m_ray": dl["m_ray"], "m_aer": dl["m_aer"]},
            "nn": p_nn,
        })
    return out


def js_side(fixture):
    script = os.path.join(os.path.dirname(HERE), "_build", "run_verify.js")
    with open(script, "w", encoding="utf-8") as f:
        f.write(
            "const core = require(%s);\n"
            "const fx = require(%s);\n"
            "const MODEL = require(%s);\n"
            "const NN = require(%s);\n"
            "const out = [];\n"
            "for (const c of fx.cases) {\n"
            "  const [elev, az] = core.solarPosition(c.lat, c.lon, c.event_ms);\n"
            "  const r = core.assess(c.w, elev, c.mode, MODEL);\n"
            "  const f = core.featuresFromWeather(c.w, elev);\n"
            "  const feats = [c.w.cc_low||0, c.w.cc_mid||0, c.w.cc_high||0,\n"
            "    c.w.cc_low_up||0, c.w.cc_mid_up||0, c.w.rh||0, c.w.blh||0, elev,\n"
            "    f.canvas, f.canvas_high, f.light, f.horizon, f.purity, f.aod, f.precip];\n"
            "  out.push({\n"
            "    event_ms: c.event_ms, elev, az, prob: r.probability, index: r.index,\n"
            "    grade: r.grade, rgb: r.colour.rgb, lum: r.colour.luminance,\n"
            "    feat: r.features, nn: core.nnPredict(feats, NN),\n"
            "    deck_light: (() => { const d = core.deckLight(8.6 + 0.45*1.6, elev, c.w.aod);\n"
            "      return { lum: d.lum, rgb: d.rgb, tangent: d.tangent_km, m_ray: d.m_ray, m_aer: d.m_aer }; })(),\n"
            "  });\n"
            "}\n"
            "console.log(JSON.stringify(out));\n"
            % (json.dumps(CORE_JS), json.dumps(os.path.join(os.path.dirname(HERE), "_build", "fixture.json")),
               json.dumps(os.path.join(HERE, "data", "model.json")),
               json.dumps(os.path.join(HERE, "data", "nn_model.json")))
        )

    fx_path = os.path.join(os.path.dirname(HERE), "_build", "fixture.json")
    with open(fx_path, "w", encoding="utf-8") as f:
        json.dump(fixture, f)

    res = subprocess.run([NODE, script], capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        print("node failed:\n", res.stderr[:2000])
        sys.exit(1)
    return json.loads(res.stdout)


def main():
    py = py_side()
    fixture = {"cases": [
        {"lat": lat, "lon": lon, "event_ms": p["event_ms"], "mode": mode, "w": w}
        for (lat, lon, _d, _tz, mode, w), p in zip(CASES, py)
    ]}
    js = js_side(fixture)

    bad = 0
    for i, (p, j) in enumerate(zip(py, js)):
        print("--- case %d ---" % i)
        checks = [
            ("sun_elev", p["elev"], j["elev"], 1e-6),
            ("sun_az", p["az"], j["az"], 1e-6),
            ("prob", p["prob"], j["prob"], 1e-4),
            ("index", p["index"], j["index"], 1e-3),
            ("grade", p["grade"], j["grade"], 0),
            ("lum", p["lum"], j["lum"], 1e-4),
            ("nn", p["nn"], j["nn"], 1e-5),
            ("deck_lum", p["deck_light"]["lum"], j["deck_light"]["lum"], 1e-6),
            ("deck_tangent", p["deck_light"]["tangent"], j["deck_light"]["tangent"], 1e-6),
            ("deck_m_ray", p["deck_light"]["m_ray"], j["deck_light"]["m_ray"], 1e-6),
            ("deck_m_aer", p["deck_light"]["m_aer"], j["deck_light"]["m_aer"], 1e-6),
        ]
        for name, a, b, tol in checks:
            ok = (a is None and b is None) or (
                a is not None and b is not None and abs(float(a) - float(b)) <= tol)
            if not ok:
                bad += 1
                print("   FAIL %-10s py=%r js=%r" % (name, a, b))
        if p["deck_light"]["rgb"] != j["deck_light"]["rgb"]:
            bad += 1
            print("   FAIL deck_rgb  py=%r js=%r" % (p["deck_light"]["rgb"], j["deck_light"]["rgb"]))
        if p["rgb"] != j["rgb"]:
            bad += 1
            print("   FAIL rgb      py=%r js=%r" % (p["rgb"], j["rgb"]))
        for k in p["feat"]:
            a, b = p["feat"][k], j["feat"][k]
            if abs(float(a) - float(b)) > 1e-3:
                bad += 1
                print("   FAIL feat.%-8s py=%r js=%r" % (k, a, b))
        print("   rgb=%s prob=%.4f idx=%.3f grade=%d nn=%.4f"
              % (j["rgb"], j["prob"], j["index"], j["grade"], j["nn"] or -1))

    print()
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
