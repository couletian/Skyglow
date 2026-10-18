"""Smoke-test the single-file page with a stubbed network.

Open-Meteo is rate-limiting this IP right now, so this verifies the plumbing
(fetch -> assemble -> assess -> render) with structurally real, deterministic data.
Returns the rendered DOM so the checks can assert on it.
"""
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SINGLE = os.path.join(os.path.dirname(HERE), "Skyglow-单文件版.html")
PROBE = os.path.join(os.path.dirname(HERE), "_probe")
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

STUB = r"""
<script>
window.__stubCalls = 0;
window.fetch = async function (url) {
  window.__stubCalls++;
  const u = String(url);
  if (u.indexOf("geocoding") >= 0) {
    return { ok: true, status: 200, json: async () => ({ results: [
      { name: "杭州", admin1: "浙江", country: "中国", latitude: 30.2936,
        longitude: 120.1614, population: 11936010 }
    ] }) };
  }
  const latM = u.match(/latitude=([^&]+)/);
  const n = latM ? latM[1].split(",").length : 1;
  const isAir = u.indexOf("air-quality") >= 0;
  const utc = u.indexOf("timezone=UTC") >= 0;
  const startM = u.match(/start_date=([\d-]+)/);
  const endM = u.match(/end_date=([\d-]+)/);
  const start = startM ? startM[1] : "2026-10-15";
  const end = endM ? endM[1] : "2026-10-17";

  const t0 = Date.parse(start + "T00:00:00Z");
  const t1 = Date.parse(end + "T00:00:00Z");
  const hours = Math.round((t1 - t0) / 3600000) + 24;
  const time = [];
  for (let i = 0; i < hours; i++) {
    const d = new Date(t0 + i * 3600000);
    time.push(d.toISOString().slice(0, 16));
  }

  const models = ["best_match", "ecmwf_ifs025", "gfs_seamless", "icon_seamless"];
  const base = { best_match: 1.0, ecmwf_ifs025: 0.92, gfs_seamless: 1.05, icon_seamless: 0.98 };

  function mk(varName, amp, phase) {
    const o = {};
    models.forEach((m, mi) => {
      o[varName + "_" + m] = time.map((_, i) => {
        const v = amp * base[m] * (0.62 + 0.38 * Math.sin(i / 7 + phase + mi * 0.3));
        return Math.max(0, Math.min(100, Math.round(v)));
      });
    });
    return o;
  }

  const hourly = { time: time };
  Object.assign(hourly, mk("cloud_cover_low", 35, 0.4));
  Object.assign(hourly, mk("cloud_cover_mid", 62, 1.1));
  Object.assign(hourly, mk("cloud_cover_high", 84, 2.0));
  Object.assign(hourly, mk("relative_humidity_2m", 78, 0.2));
  Object.assign(hourly, mk("boundary_layer_height", 900, 0.6));
  models.forEach(m => {
    hourly["visibility_" + m] = time.map((_, i) => 12000 + 6000 * Math.sin(i / 9));
    hourly["dew_point_2m_" + m] = time.map(() => 18.5);
    hourly["precipitation_" + m] = time.map(() => 0);
    hourly["precipitation_probability_" + m] = time.map((_, i) => (i % 11) * 3);
    hourly["wind_speed_10m_" + m] = time.map(() => 12);
    hourly["wind_direction_10m_" + m] = time.map(() => 180);
  });
  if (isAir) {
    hourly.aerosol_optical_depth = time.map((_, i) => 0.12 + 0.06 * Math.sin(i / 13));
  }

  const one = { latitude: 31.23, longitude: 121.47, utc_offset_seconds: utc ? 0 : 28800,
    timezone: utc ? "GMT" : "Asia/Shanghai", elevation: 5, hourly };
  if (isAir) one.hourly = { time: time, aerosol_optical_depth: hourly.aerosol_optical_depth };

  const out = [];
  for (let i = 0; i < n; i++) out.push(JSON.parse(JSON.stringify(one)));
  return { ok: true, status: 200, json: async () => (n === 1 ? out[0] : out) };
};
</script>
"""

CLICK_GO = """
<script>
/* __clickGo: 首屏不再自动取数，测试需代用户点击「开始预测」 */
setTimeout(() => { const b = document.querySelector('#go'); if (b) b.click(); }, 700);
</script>
"""

CHECKS = [
    ("主结果百分比", r'id="heroPct">(\d+)<small>%'),
    ("地点名", r'id="heroPlace">([^<]+)<'),
    ("等级", r'id="heroGrade"[^>]*>([^<]+)<'),
]


def main():
    html = open(SINGLE, encoding="utf-8").read()
    if "window.fetch = async function" in html:
        print("already stubbed?")
        return 1
    test = html.replace("<body>", "<body>\n" + STUB + CLICK_GO, 1)
    os.makedirs(PROBE, exist_ok=True)
    tpath = os.path.join(PROBE, "t.html")
    with open(tpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(test)

    prof = os.path.join(PROBE, "p3")
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", prof], capture_output=True)
    out = subprocess.run(
        [EDGE, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--user-data-dir=" + prof, "--virtual-time-budget=45000",
         "--window-size=1400,2400", "--dump-dom", "file:///" + tpath.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    dom = out.stdout
    dpath = os.path.join(PROBE, "dom2.html")
    with open(dpath, "w", encoding="utf-8", newline="\n") as f:
        f.write(dom)
    # strip inline scripts: their source text would match the same patterns
    body = re.sub(r"<script[\s\S]*?</script>", "", dom)
    print("dom bytes:", len(dom), " html-only:", len(body))

    bad = 0
    for label, pat in CHECKS:
        m = re.search(pat, body)
        val = m.group(1).strip() if m else None
        ok = bool(val) and val not in ("—", "")
        print(("  OK   " if ok else "  FAIL ") + label + " = " + repr(val))
        if not ok:
            bad += 1

    # structural checks
    for label, pat, want in [
        ("四源条目", r'class="src-row"', 4),
        ("七日格子", r'class="wday', 8),
        ("云层分解行", r'class="deck"', 3),
        ("判断依据 chips", r'class="chip', 3),
    ]:
        n = len(re.findall(pat, body))
        ok = n >= want
        print(("  OK   " if ok else "  FAIL ") + label + " = %d (>=%d)" % (n, want))
        if not ok:
            bad += 1

    # error text leaking through?
    if "取数失败" in body or "计算失败" in body:
        m = re.search(r'(取数失败|计算失败)[^<]{0,120}', body)
        print("  FAIL 页面出现错误：" + (m.group(0) if m else ""))
        bad += 1
    else:
        print("  OK   无错误提示")

    print()
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
