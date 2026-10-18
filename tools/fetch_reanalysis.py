"""Legacy: fetch sunsetbot reanalysis records in 10-day windows and merge."""
import json, time, urllib.parse, urllib.request, csv, os
from datetime import date, timedelta

BASE = "https://sunsetbot.top/reanalysis/"
OUT = os.path.join(os.path.dirname(__file__), "reanalysis_records.csv")

def fetch(start, end):
    params = {
        "start_date": start, "end_date": end, "event": "0", "city_name": "上海",
        "intend": "select_event", "cloud": "0", "color": "0", "quality": "0",
        "sky": "0", "url": "0",
    }
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 sunset-glow-research"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.loads(r.read().decode("utf-8"))
    return data.get("table_content", [])

start = date(2022, 6, 24)
end = date(2023, 12, 30)
rows = {}
cur = start
window = 10
fail = 0
while cur <= end:
    nxt = min(cur + timedelta(days=window - 1), end)
    try:
        table = fetch(cur.isoformat(), nxt.isoformat())
    except Exception as e:
        print("ERR", cur, e); fail += 1; time.sleep(2); continue
    if len(table) >= 20 and nxt < end:
        # window saturated -> shrink
        window = max(3, window - 2)
        print("shrink window to", window); continue
    for r in table:
        rows[(r[0], r[1])] = r
    print(cur, nxt, len(table), "total", len(rows))
    cur = nxt + timedelta(days=1)
    if window < 10:
        window = min(10, window + 1)
    time.sleep(0.4)

with open(OUT, "w", newline="", encoding="utf-8-sig") as f:
    w = csv.writer(f)
    w.writerow(["date", "event", "cloud_type", "color", "actual", "predicted",
                "consistency", "phenomena", "cases", "era5_img", "city_img"])
    for k in sorted(rows):
        w.writerow(rows[k])
print("saved", len(rows), "->", OUT, "fails", fail)
