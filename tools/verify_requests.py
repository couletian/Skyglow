"""Runtime check: how many points does the single-file page actually request?

Stubs fetch, records every coordinate list the page sends (main query, 7-day strip,
azimuth scan), and writes the tally into the DOM for --dump-dom to read.
No network, no quota.
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
TEST_DATE = "2026-10-18"

STUB = r"""
<script>
window.__log = [];
function fakeSeries(n, base, amp) {
  return Array.from({length: n}, (_, i) => Math.max(0, Math.min(100,
    base + amp * Math.sin(i / 5 + 1))));
}
window.fetch = async function (url) {
  const u = String(url);
  const latM = u.match(/latitude=([^&]+)/);
  const n = latM ? latM[1].split(",").length : 1;
  const hourlyM = u.match(/hourly=([^&]+)/);
  const vars = hourlyM ? hourlyM[1].split(",") : [];
  const sdM = u.match(/start_date=([\d-]+)/), edM = u.match(/end_date=([\d-]+)/);
  const days = (sdM && edM)
    ? (Date.parse(edM[1]) - Date.parse(sdM[1])) / 86400000 + 1 : 1;
  window.__log.push({ points: n, vars: vars.length, days: days,
                      units: n * vars.length * days,
                      start: sdM ? sdM[1] : "?", end: edM ? edM[1] : "?",
                      host: (u.match(/^https?:\/\/([^\/]+)/) || [])[1] || "?" });

  if (u.indexOf("geocoding") >= 0) {
    return { ok: true, status: 200, json: async () => ({ results: [
      { name: "上海", admin1: "上海市", country: "中国", latitude: 31.2304,
        longitude: 121.4737, population: 24874500 }] }) };
  }
  const isAir = u.indexOf("air-quality") >= 0;
  const models = ["best_match", "ecmwf_ifs025", "gfs_seamless", "icon_seamless"];
  const t0 = Date.parse("2026-10-15T00:00:00Z");
  const time = Array.from({length: 240}, (_, i) =>
    new Date(t0 + i * 3600000).toISOString().slice(0, 16));
  const base = { best_match: 1.0, ecmwf_ifs025: 0.94, gfs_seamless: 1.06,
                 icon_seamless: 0.97 };
  const hourly = { time };
  models.forEach((m, mi) => {
    const k = base[m];
    const put = (name, b, a) => {
      hourly[name + "_" + m] = time.map((_, i) =>
        Math.max(0, Math.min(100, Math.round(
          (b + a * Math.sin(i / 7 + mi * 0.4)) * k))));
    };
    put("cloud_cover_low", 28, 22);
    put("cloud_cover_mid", 58, 26);
    put("cloud_cover_high", 78, 20);
    put("relative_humidity_2m", 70, 14);
    put("boundary_layer_height", 850, 350);
    hourly["visibility_" + m] = time.map((_, i) => 11000 + 5000 * Math.sin(i / 9));
    hourly["precipitation_" + m] = time.map(() => 0);
  });
  const one = { latitude: 31.2304, longitude: 121.4737,
    utc_offset_seconds: isAir ? 0 : 28800,
    timezone: isAir ? "GMT" : "Asia/Shanghai", elevation: 5,
    hourly: isAir ? { time: time, aerosol_optical_depth: time.map(() => 0.14) }
                  : hourly };
  const out = [];
  for (let i = 0; i < n; i++) out.push(JSON.parse(JSON.stringify(one)));
  return { ok: true, status: 200, json: async () => (n === 1 ? out[0] : out) };
};
/* 首屏不再自动取数：测试需代用户点击「开始预测」 */
setTimeout(() => {
  const b = document.querySelector('#go');
  if (b) b.click();
}, 1200);

setTimeout(() => {
  const pre = document.createElement("pre");
  pre.id = "reqLog";
  pre.style.display = "none";
  pre.textContent = JSON.stringify(window.__log);
  document.body.appendChild(pre);
}, 20000);
</script>
"""


def main():
    html = open(SINGLE, encoding="utf-8").read()
    html = html.replace(
        'const today = new Date();\n  $("#date").value = isoDate({ y: today.getFullYear(), m: today.getMonth() + 1, d: today.getDate() });\n  state.date = $("#date").value;',
        'const today = new Date();\n  $("#date").value = "%s";\n  state.date = $("#date").value;' % TEST_DATE)
    if TEST_DATE not in html:
        print("!! init 片段未匹配")
        return 1

    test = html.replace("<body>", "<body>\n" + STUB, 1)
    os.makedirs(PROBE, exist_ok=True)
    tp = os.path.join(PROBE, "req.html")
    with open(tp, "w", encoding="utf-8", newline="\n") as f:
        f.write(test)

    prof = os.path.join(PROBE, "p9")
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", prof], capture_output=True)
    res = subprocess.run(
        [EDGE, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--user-data-dir=" + prof, "--virtual-time-budget=60000",
         "--window-size=1400,2200", "--dump-dom",
         "file:///" + tp.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")

    m = re.search(r'id="reqLog"[^>]*>([^<]*)</pre>', res.stdout)
    if not m:
        print("未捕获到请求日志")
        return 1
    raw = m.group(1).replace("&quot;", '"').replace("&amp;", "&")
    log = json.loads(raw)

    print("单文件版实际发出的请求:")
    for i, c in enumerate(log, 1):
        print("  %d) %3d 点 x %d 变量 x %2d 天 = %5d 单位   %s..%s  %s"
              % (i, c["points"], c["vars"], c["days"], c["units"],
                 c.get("start", "?"), c.get("end", "?"),
                 (c.get("host") or "")[:26]))

    bad = 0
    main_reqs = [c for c in log if c["vars"] == 7]
    print()
    if not main_reqs:
        print("  FAIL 未找到主查询（7 变量）请求")
        bad += 1
    else:
        for c in main_reqs:
            ok = c["points"] == 25
            print("  %s 主查询/七日 %d 点（应 25）" % ("OK  " if ok else "FAIL", c["points"]))
            if not ok:
                bad += 1
    az = [c for c in log if c["vars"] == 4]
    if az:
        print("  OK   方位雷达 %d 点 x 4 变量" % az[0]["points"])
    else:
        print("  --   方位雷达请求未出现（可能尚未触发）")

    total = sum(c["units"] for c in log)
    print()
    print("  合计额度成本: %d 单位" % total)
    print()
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
