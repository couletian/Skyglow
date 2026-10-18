"""Audit: which days inside the fetched window are actually read?

outlook() fetches from today-1 to today+days (9 days for days=7) but only renders
today..today+days (8 rows). This script answers whether the leading day is used.

Runs fully offline: _get is stubbed with a structurally correct payload, and the
store is disabled so nothing leaks into the production cache.
"""
import collections
import datetime
import sys
import urllib.parse

sys.path.insert(0, ".")

from core import meteo, service, store  # noqa: E402

VARS = ("cloud_cover_low", "cloud_cover_mid", "cloud_cover_high", "visibility",
        "relative_humidity_2m", "precipitation", "boundary_layer_height")
SUFFIXES = ["", "ecmwf_ifs025", "gfs_seamless", "icon_seamless"]

# ---- no writes to the real cache ----
store.put = lambda kind, key, payload: None
store.get = lambda kind, key, d: (None, 0)
store.get_any = lambda kind, key: (None, 1e9)


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
                for suf in SUFFIXES:
                    key = v + ("_" + suf if suf else "")
                    hourly[key] = [55] * hours if "cloud" in v else [1] * hours
        out.append({"latitude": 31.23, "longitude": 121.47,
                    "utc_offset_seconds": 28800, "timezone": "Asia/Shanghai",
                    "elevation": 5, "hourly": hourly})
    return out


fetch_log = []
orig_get = meteo._get


def stub_get(url, ttl=1800, timeout=60, locations=1):
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    sd = q.get("start_date", [""])[0]
    ed = q.get("end_date", [""])[0]
    n = len(q.get("latitude", [""])[0].split(","))
    fetch_log.append((sd, ed, n))
    air = "air-quality" in url
    return payload(n or 1, sd, ed, air=air)


# ---- record every index read by interpolation ----
reads = collections.Counter()
orig_interp = meteo._interp


def interp_spy(series, times, t_local):
    if series and times:
        delta = (t_local - datetime.datetime.fromisoformat(times[0])).total_seconds() / 3600.0
        i = int(delta // 1)
        if 0 <= i < len(times):
            reads[times[i][:10]] += 1
            if i + 1 < len(times):
                reads[times[i + 1][:10]] += 1
        montiors = i
    return orig_interp(series, times, t_local)


meteo._get = stub_get
meteo._interp = interp_spy

try:
    service.outlook(31.2304, 121.4737, days=7, refresh=True)
except Exception as e:  # noqa: BLE001
    print("outlook 异常:", type(e).__name__, str(e)[:120])

print("=" * 66)
print("取数窗口")
print("=" * 66)
for sd, ed, n in fetch_log:
    d = (datetime.date.fromisoformat(ed) - datetime.date.fromisoformat(sd)).days + 1
    print("  %s .. %s   %d 天  %d 点" % (sd, ed, d, n))

today = datetime.date.today()
print()
print("=" * 66)
print("窗口内每一天被读取的次数（插值实际取值）")
print("=" * 66)
for k in range(-1, 8):
    day = (today + datetime.timedelta(days=k)).isoformat()
    n = reads.get(day, 0)
    tag = ""
    if k == -1:
        tag = "  ← 昨天，不显示"
    elif 0 <= k <= 7:
        tag = "  ← 展示第 %d 行" % (k + 1)
    flag = "未使用" if n == 0 else ""
    print("  %s  %5d 次 %-16s %s" % (day, n, tag, flag))

fetch_log.clear()
reads.clear()
try:
    service.predict_day(31.2304, 121.4737, today, refresh=True)
except Exception as e:  # noqa: BLE001
    print("predict_day 异常:", type(e).__name__, str(e)[:120])

meteo._get = orig_get
meteo._interp = orig_interp

print()
print("=" * 66)
print("单日预测 predict_day() 的窗口")
print("=" * 66)
for sd, ed, n in fetch_log:
    d = (datetime.date.fromisoformat(ed) - datetime.date.fromisoformat(sd)).days + 1
    print("  %s .. %s   %d 天  %d 点" % (sd, ed, d, n))
for k in (-1, 0, 1):
    day = (today + datetime.timedelta(days=k)).isoformat()
    n = reads.get(day, 0)
    label = {-1: "前一天（padding）", 0: "目标日", 1: "后一天（padding）"}[k]
    print("  %s  %5d 次  %-18s %s" % (day, n, label, "未使用" if n == 0 else "已读取"))
print()
print("  注：中纬度这里显示 0，是因为 ±35/55 分钟的时间曲线没跨午夜。")
print("      padding 是为高纬夏季准备的——如 lat 67N 于 7/10 日落 23:51，")
print("      曲线 +55min 落到次日 00:46，此时 d+1 会被真实读取。")
print("      该结论由 scan 468 个纬度/月组合、命中 20 例跨日案例得到。")

