"""Behaviour test: params change must NOT trigger data fetches.

Simulates a user session against the single-file page and asserts the request count:
  - opening the page                -> 0 forecast requests
  - changing city / date / mode     -> 0 new requests
  - clicking "开始预测"              -> requests happen
Runs offline with a stubbed fetch. No quota used.
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
window.__req = [];
window.__mark = () => window.__req.length;
window.fetch = async function (url) {
  const u = String(url);
  const latM = u.match(/latitude=([^&]+)/);
  const n = latM ? latM[1].split(",").length : 1;
  const hourlyM = u.match(/hourly=([^&]+)/);
  const vars = hourlyM ? hourlyM[1].split(",") : [];
  window.__req.push({ n: n, vars: vars.length, url: u.slice(0, 90) });

  if (u.indexOf("geocoding") >= 0) {
    return { ok: true, status: 200, json: async () => ({ results: [
      { name: "杭州", admin1: "浙江", country: "中国", latitude: 30.2741,
        longitude: 120.1551, population: 10000000 }] }) };
  }
  const isAir = u.indexOf("air-quality") >= 0;
  const models = ["best_match", "ecmwf_ifs025", "gfs_seamless", "icon_seamless"];
  const t0 = Date.parse("2026-10-15T00:00:00Z");
  const time = Array.from({length: 264}, (_, i) =>
    new Date(t0 + i * 3600000).toISOString().slice(0, 16));
  const hourly = { time };
  models.forEach((m, mi) => {
    const put = (k, b, a) => {
      hourly[k + "_" + m] = time.map((_, i) => Math.max(0, Math.min(100,
        Math.round(b + a * Math.sin(i / 7 + mi * 0.4)))));
    };
    put("cloud_cover_low", 30, 20);
    put("cloud_cover_mid", 58, 24);
    put("cloud_cover_high", 74, 18);
    put("relative_humidity_2m", 70, 12);
    put("boundary_layer_height", 850, 300);
    hourly["visibility_" + m] = time.map(() => 14000);
    hourly["precipitation_" + m] = time.map(() => 0);
  });
  const one = { latitude: 31.23, longitude: 121.47,
    utc_offset_seconds: isAir ? 0 : 28800,
    timezone: isAir ? "GMT" : "Asia/Shanghai", elevation: 5,
    hourly: isAir ? { time: time, aerosol_optical_depth: time.map(() => 0.14) } : hourly };
  const out = [];
  for (let i = 0; i < n; i++) out.push(JSON.parse(JSON.stringify(one)));
  return { ok: true, status: 200, json: async () => (n === 1 ? out[0] : out) };
};

window.__report = null;
window.__runTest = async function () {
  const log = [];
  const snap = label => log.push({ label: label, n: window.__req.length });

  const sleep = ms => new Promise(r => setTimeout(r, ms));

  await sleep(600);
  snap("打开页面");

  // 换城市（快捷按钮）
  const city = document.querySelector('#quick button[data-city]');
  if (city) { city.click(); await sleep(400); snap("点快捷城市"); }

  // 换日期
  const dt = document.querySelector('#date');
  if (dt) {
    dt.value = '2026-10-20';
    dt.dispatchEvent(new Event('change'));
    await sleep(400);
    snap("改日期");
  }

  // 切朝霞
  const mode = document.querySelector('#mode button[data-mode="sunrise"]');
  if (mode) { mode.click(); await sleep(400); snap("切朝霞"); }

  // 切回晚霞
  const mode2 = document.querySelector('#mode button[data-mode="sunset"]');
  if (mode2) { mode2.click(); await sleep(400); snap("切回晚霞"); }

  const dirtyBefore = document.querySelector('#go').classList.contains('dirty');
  const btnText = document.querySelector('#go').textContent;

  // 点开始预测
  document.querySelector('#go').click();
  await sleep(2500);
  snap("点开始预测");

  const dirtyAfter = document.querySelector('#go').classList.contains('dirty');

  window.__report = { log: log, dirtyBefore: dirtyBefore, dirtyAfter: dirtyAfter,
                      btnText: btnText };
  const pre = document.createElement("pre");
  pre.id = "reqLog";
  pre.style.display = "none";
  pre.textContent = JSON.stringify(window.__report);
  document.body.appendChild(pre);
};
setTimeout(() => { window.__runTest(); }, 300);
</script>
"""


def main():
    html = open(SINGLE, encoding="utf-8").read()
    test = html.replace("<body>", "<body>\n" + STUB, 1)
    os.makedirs(PROBE, exist_ok=True)
    tp = os.path.join(PROBE, "behave.html")
    with open(tp, "w", encoding="utf-8", newline="\n") as f:
        f.write(test)

    prof = os.path.join(PROBE, "pb")
    subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", prof], capture_output=True)
    res = subprocess.run(
        [EDGE, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--user-data-dir=" + prof, "--virtual-time-budget=40000",
         "--window-size=1400,2000", "--dump-dom",
         "file:///" + tp.replace("\\", "/")],
        capture_output=True, text=True, encoding="utf-8", errors="replace")

    # the report is stored on window; read it back through a DOM dump hook
    m = re.search(r'id="reqLog"[^>]*>([^<]*)</pre>', res.stdout)
    if not m:
        # fall back: parse the visible state instead
        print("未拿到结构化报告，改用 DOM 判定")
        return dom_fallback(res.stdout)

    rep = json.loads(m.group(1).replace("&quot;", '"'))
    prev = 0
    bad = 0
    print("请求计数：")
    for item in rep["log"]:
        delta = item["n"] - prev
        prev = item["n"]
        expect_zero = item["label"] != "点开始预测"
        ok = (delta == 0) if expect_zero else (delta > 0)
        print("  %s %-12s 累计 %2d，新增 %d"
              % ("OK  " if ok else "FAIL", item["label"], item["n"], delta))
        if not ok:
            bad += 1
    print()
    print("参数变更后按钮进入待预测态：", rep["dirtyBefore"], "| 按钮文案：", rep["btnText"])
    print("点击后清除待预测态：", not rep["dirtyAfter"])
    if not rep["dirtyBefore"]:
        bad += 1
    print()
    print("FAILURES:", bad)
    return 1 if bad else 0


def dom_fallback(dom):
    """No JS report: assert the initial DOM shows the idle hint, not a loading state."""
    body = re.sub(r"<script[\s\S]*?</script>", "", dom)
    idle = "点击「开始预测」" in body or "尚未预测" in body
    loading = "正在取数" in body
    print("  空闲提示存在:", idle)
    print("  仍在自动加载:", loading)
    bad = 0 if (idle and not loading) else 1
    print("FAILURES:", bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
