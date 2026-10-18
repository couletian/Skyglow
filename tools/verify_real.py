"""Verify the single-file page against real Open-Meteo data, without the network.

Takes a real cached 25-point response recorded by the Python build, feeds it to both
implementations, and compares the four per-source probabilities plus the rendered DOM.

Usage: python tools/verify_real.py
"""
import json
import os
import re
import subprocess
import sys
from datetime import date

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from core import meteo, model, solar  # noqa: E402

SINGLE = os.path.join(os.path.dirname(HERE), "Skyglow-单文件版.html")
PROBE = os.path.join(os.path.dirname(HERE), "_probe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
CACHE = os.path.join(HERE, "data", "cache", "03968e78045c510d82d9.json")

TARGET = date(2026, 10, 18)
CLICK_GO = """
<script>
/* __clickGo: 首屏不再自动取数，测试需代用户点击「开始预测」 */
setTimeout(() => { const b = document.querySelector('#go'); if (b) b.click(); }, 700);
</script>
"""

TEST_DATE = TARGET.isoformat()


def build_bundle(raw):
    """Turn the raw Open-Meteo list into the internal bundle shape."""
    pts = meteo.sample_points(31.2304, 121.4737)
    offset = raw[0].get("utc_offset_seconds", 28800)
    out = []
    for i, p in enumerate(pts):
        h = raw[i]["hourly"]
        rec = {"id": p["id"], "dist": p["dist"], "az": p["az"],
               "time": h["time"], "sources": {}, "aod": []}
        for s in meteo.SOURCES:
            toks = s["tokens"]
            rec["sources"][s["id"]] = {
                k: meteo._series(h, k, toks)
                for k in ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high",
                          "relative_humidity_2m", "boundary_layer_height", "visibility",
                          "precipitation")
            }
            # rename to the internal keys used by assemble()
            src = rec["sources"][s["id"]]
            src["cc_low"] = src.pop("cloud_cover_low")
            src["cc_mid"] = src.pop("cloud_cover_mid")
            src["cc_high"] = src.pop("cloud_cover_high")
            src["rh"] = src.pop("relative_humidity_2m")
            src["blh"] = src.pop("boundary_layer_height")
            src["vis"] = src.pop("visibility")
            src["precip"] = src.pop("precipitation")
        out.append(rec)
    return {"points": out, "offset": offset, "tz": raw[0].get("timezone"),
            "elevation": raw[0].get("elevation")}


def py_side(raw):
    bundle = build_bundle(raw)
    off = bundle["offset"]
    ev = solar.event_times(31.2304, 121.4737, TARGET, off / 3600.0, which="sunset")["sunset"]
    elev, az = solar.solar_position(31.2304, 121.4737, ev)
    import datetime as dt
    t_local = ev.astimezone(dt.timezone(dt.timedelta(seconds=off))).replace(tzinfo=None)
    mdl = model.load_model()
    out = {}
    for s in meteo.SOURCES:
        # 与 service._predict_day 一致：先判断该源有没有分层云量
        if not meteo.available(bundle, s["id"], t_local):
            out[s["id"]] = {"error": "该源在此刻无分层云量"}
            continue
        w = meteo.assemble(bundle, t_local, az, s["id"])
        r = model.assess(w, elev, "sunset", mdl)
        out[s["id"]] = {"p": r["probability"], "rgb": r["colour"]["rgb"],
                        "grade": r["grade"], "elev": elev, "time": t_local.strftime("%H:%M")}
    return out


STUB = """
<script>
const REAL = %s;
window.fetch = async function (url) {
  const u = String(url);
  if (u.indexOf("geocoding") >= 0) {
    return { ok: true, status: 200, json: async () => ({ results: [
      { name: "上海", admin1: "上海市", country: "中国", latitude: 31.2304,
        longitude: 121.4737, population: 24874500 } ] }) };
  }
  // 关键：每个格点返回它自己的真实数据，否则与 Python 的输入不一致
  const latM = u.match(/latitude=([^&]+)/);
  const n = latM ? latM[1].split(",").length : 1;
  const isAir = u.indexOf("air-quality") >= 0;
  const out = [];
  for (let i = 0; i < n; i++) {
    const src = REAL[Math.min(i, REAL.length - 1)];
    const times = src.hourly.time;
    if (isAir) {
      out.push({ latitude: src.latitude, longitude: src.longitude,
        utc_offset_seconds: 0, timezone: "GMT", elevation: 5,
        hourly: { time: times, aerosol_optical_depth: times.map(() => 0.15) } });
    } else {
      out.push({ latitude: src.latitude, longitude: src.longitude,
        utc_offset_seconds: src.utc_offset_seconds || 28800,
        timezone: src.timezone || "Asia/Shanghai",
        elevation: src.elevation || 5,
        hourly: src.hourly });
    }
  }
  return { ok: true, status: 200, json: async () => (n === 1 ? out[0] : out) };
};
</script>
"""


def main():
    raw = json.load(open(CACHE, encoding="utf-8"))
    print("cached response: %d points, %d hours" % (len(raw), len(raw[0]["hourly"]["time"])))

    py = py_side(raw)
    print("\nPython (同源数据，含 available 判定):")
    for k, v in py.items():
        if "error" in v:
            print("  %-10s 无分层云量" % k)
        else:
            print("  %-10s %.4f  rgb=%s %s" % (k, v["p"], v["rgb"], v["time"]))

    html = open(SINGLE, encoding="utf-8").read()
    # freeze the test date so the window matches the cached range
    html = html.replace(
        'const today = new Date();\n  $("#date").value = isoDate({ y: today.getFullYear(), m: today.getMonth() + 1, d: today.getDate() });\n  state.date = $("#date").value;',
        'const today = new Date();\n  $("#date").value = "%s";\n  state.date = $("#date").value;' % TEST_DATE)
    if TEST_DATE not in html:
        print("!! 无法固定测试日期：init 片段未匹配")
        return 1

    test = html.replace("<body>", "<body>\n" + (STUB % json.dumps(raw, ensure_ascii=False)) + CLICK_GO, 1)
    os.makedirs(PROBE, exist_ok=True)
    tpath = os.path.join(PROBE, "real.html")
    with open(tpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(test)

    prof = os.path.join(PROBE, "p4")
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", prof], capture_output=True)
    res = subprocess.run(
        [EDGE, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--user-data-dir=" + prof, "--virtual-time-budget=40000",
         "--window-size=1400,2400", "--dump-dom", "file:///" + tpath.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    dom = res.stdout
    body = re.sub(r"<script[\s\S]*?</script>", "", dom)
    with open(os.path.join(PROBE, "dom3.html"), "w", encoding="utf-8", newline="\n") as f:
        f.write(dom)

    js_p = [int(x) for x in re.findall(r'class="pv">(\d+)%', body)]
    m = re.search(r'id="heroPct">(\d+)<small>', body)
    hero = int(m.group(1)) if m else None
    print("\nJS 渲染（同一份数据）:")
    print("  四源百分比:", js_p, " hero:", hero)

    order = ["openmeteo", "ecmwf", "gfs", "icon"]
    bad = 0
    expect_vals = [round(py[sid]["p"] * 100) for sid in order if "error" not in py[sid]]
    expect_err = len([1 for sid in order if "error" in py[sid]])
    js_err = len(re.findall(r'class="pv err"', body))
    print("  期望成功 %d 源 / 失败 %d 源；JS 成功 %d / 失败 %d"
          % (len(expect_vals), expect_err, len(js_p), js_err))
    if len(expect_vals) != len(js_p) or expect_err != js_err:
        print("  FAIL 成功/失败源数量不一致")
        bad += 1
    for i, exp in enumerate(expect_vals):
        got = js_p[i]
        ok = abs(exp - got) <= 1
        print("  %s py=%d%% js=%d%%" % ("OK  " if ok else "FAIL", exp, got))
        if not ok:
            bad += 1
    if hero is not None and "error" not in py["openmeteo"]:
        exp_hero = round(py["openmeteo"]["p"] * 100)
        ok = abs(hero - exp_hero) <= 1
        print("  %s hero py=%d%% js=%d%%" % ("OK  " if ok else "FAIL", exp_hero, hero))
        if not ok:
            bad += 1
    m = re.search(r'id="heroSwatch" style="background:\s*([^;"]+)', body)
    if m and "error" not in py["openmeteo"]:
        raw_c = m.group(1).strip()
        nums = re.findall(r"\d+", raw_c)
        got_rgb = [int(x) for x in nums[:3]] if len(nums) >= 3 else None
        exp_rgb = py["openmeteo"]["rgb"]
        ok = got_rgb == exp_rgb
        print("  %s 配色 py=%s js=%s" % ("OK  " if ok else "FAIL", exp_rgb, raw_c))
        if not ok:
            bad += 1

    for label, pat, want in [("七日格子", r'class="wday', 8), ("云层分解", r'class="deck"', 3)]:
        n = len(re.findall(pat, body))
        ok = n >= want
        print("  %s %s = %d" % ("OK  " if ok else "FAIL", label, n))
        if not ok:
            bad += 1

    print()
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
