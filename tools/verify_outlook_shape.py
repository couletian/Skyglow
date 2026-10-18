"""Capture the request footprint of one Skyglow build, without touching the network.

Usage: python tools/verify_outlook_shape.py <build-dir>

Stubs meteo._get with a synthetic but structurally correct payload, then records every
URL the service builds. Proves whether the 7-day strip uses the 25-point ring or a
single centre point, and over how many days.
"""
import datetime
import sys
import urllib.parse

build = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, build)

from core import meteo, service, store  # noqa: E402

TOKENS = {
    "best_match": "",
    "ecmwf_ifs025": "ecmwf_ifs025",
    "gfs_seamless": "gfs_seamless",
    "icon_seamless": "icon_seamless",
}
VARS = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "visibility",
        "relative_humidity_2m", "precipitation", "boundary_layer_height")


def payload(npts, start, end, air=False):
    d0 = datetime.date.fromisoformat(start)
    d1 = datetime.date.fromisoformat(end)
    hours = (d1 - d0).days * 24 + 24
    t0 = datetime.datetime.combine(d0, datetime.time())
    times = [(t0 + datetime.timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
             for i in range(hours)]
    out = []
    for _ in range(npts):
        hourly = {"time": times}
        if air:
            hourly["aerosol_optical_depth"] = [0.15] * hours
        else:
            for v in VARS:
                for suf in TOKENS.values():
                    key = v + ("_" + suf if suf else "")
                    hourly[key] = [60] * hours if "cloud" in v else [1] * hours
        out.append({"latitude": 31.23, "longitude": 121.47,
                    "utc_offset_seconds": 28800, "timezone": "Asia/Shanghai",
                    "elevation": 5, "hourly": hourly})
    return out


cap = []
orig = meteo._get


def spy(url, ttl=1800, timeout=60, locations=1):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    host = urllib.parse.urlsplit(url).netloc
    n = len(q["latitude"][0].split(",")) if "latitude" in q else 0
    nv = len([x for x in q.get("hourly", [""])[0].split(",") if x])
    sd = q.get("start_date", [""])[0]
    ed = q.get("end_date", [""])[0]
    try:
        days = (datetime.date.fromisoformat(ed) - datetime.date.fromisoformat(sd)).days + 1
    except ValueError:
        days = 0
    cap.append({"host": host, "pts": n, "vars": nv, "days": days,
                "units": n * nv * days, "start": sd, "end": ed})
    air = "air-quality" in host
    return payload(n or 1, sd or "2026-10-18", ed or "2026-10-18", air=air)


meteo._get = spy
# 只做形态测量：绝不写真实缓存，否则桩数据会污染生产结果
_store_put = store.put
store.put = lambda kind, key, payload: None
store.get = lambda kind, key, d: (None, 0)

print("== 七日趋势 outlook() ==")
try:
    r = service.outlook(31.2304, 121.4737, days=7, refresh=True)
    days_out = len(r.get("days") or r.get("strip") or [])
    print("  返回成功，条目数:", days_out if days_out else "（结构见 payload）")
except Exception as e:  # noqa: BLE001
    print("  调用异常:", type(e).__name__, str(e)[:100])

for c in cap:
    print("  %-34s %3d 点 x %d 变量 x %2d 天 = %5d 单位  %s..%s"
          % (c["host"], c["pts"], c["vars"], c["days"], c["units"],
             c["start"], c["end"]))

cap.clear()
meteo._get = spy
print()
print("== 单日预测 predict_day() ==")
try:
    service.predict_day(31.2304, 121.4737, datetime.date(2026, 10, 18), refresh=True)
except Exception as e:  # noqa: BLE001
    print("  调用异常:", type(e).__name__, str(e)[:100])
for c in cap:
    print("  %-34s %3d 点 x %d 变量 x %2d 天 = %5d 单位  %s..%s"
          % (c["host"], c["pts"], c["vars"], c["days"], c["units"],
             c["start"], c["end"]))

fails = 0
mains = [c for c in cap if c["vars"] == 7]
print()
if mains:
    ok = all(c["pts"] == 25 for c in mains)
    print("  单日预测格点数:", [c["pts"] for c in mains], "→", "OK" if ok else "FAIL")
    fails += 0 if ok else 1
else:
    print("  单日预测: 未捕获 7 变量请求（可能命中缓存）")
store.put = _store_put
print("FAILURES:", fails)
sys.exit(1 if fails else 0)
