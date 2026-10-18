"""WeatherAPI.com — the primary verification / gap-filling source.

Why it ranks above QWeather here:

* one call returns 3 days of hourly values, so sunrise and sunset share a request;
* it carries the three quantities the physical model actually consumes —
  visibility, relative humidity and precipitation — plus total cloud for checks;
* the free tier allows 1,000,000 calls/month, far above this project's usage.

It is still **not** a probability source: it has total cloud only, with no
low/mid/high split, so feeding it into the scoring would invent the very
quantity the model depends on.

Usage priority inside a query:  Open-Meteo bundle  ->  WeatherAPI  ->  QWeather
"""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "cache")
USAGE = os.path.join(CACHE, "_weatherapi_usage.json")
DAILY_CAP = 2000


class WeatherApiError(RuntimeError):
    pass


def _cfg():
    from . import config
    return {"key": config.weatherapi_key(), "cap": config.weatherapi_cap()}


def configured():
    return bool(_cfg()["key"])


def describe():
    c = _cfg()
    u = usage()
    return {
        "configured": bool(c["key"]),
        "key_hint": ("…" + c["key"][-4:]) if c["key"] else "",
        "source": "内置默认" if c["key"] and c["key"] == _default_key() else "自定义",
        "usage": u,
        "note": "提供总云量、能见度、湿度、降水；无分层云量，故只做校验与缺测补位，不参与概率打分",
    }


def _default_key():
    from . import config
    return getattr(config, "DEFAULT_WEATHERAPI_KEY", "")


def _usage():
    try:
        with open(USAGE, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        d = {}
    today = date.today().isoformat()
    if d.get("date") != today:
        d = {"date": today, "count": 0}
    return d


def usage():
    d = _usage()
    cap = _cfg()["cap"]
    return {"date": d.get("date"), "count": int(d.get("count", 0)), "cap": cap,
            "remaining": max(0, cap - int(d.get("count", 0))),
            "ok": int(d.get("ok", 0)), "fail": int(d.get("fail", 0)),
            "last_ok": d.get("last_ok"), "last_error": d.get("last_error")}


def _record(ok, error=None):
    d = _usage()
    if ok:
        d["count"] = int(d.get("count", 0)) + 1
        d["ok"] = int(d.get("ok", 0)) + 1
        d["last_ok"] = datetime.now().isoformat(timespec="seconds")
        d["last_error"] = None
    else:
        d["fail"] = int(d.get("fail", 0)) + 1
        d["last_error"] = error
    d["date"] = date.today().isoformat()
    try:
        with open(USAGE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass


def _request(lat, lon, days=3):
    cfg = _cfg()
    if not cfg["key"]:
        raise WeatherApiError("未配置 WeatherAPI 密钥")
    if usage()["remaining"] <= 0:
        raise WeatherApiError("已达每日护栏上限（%d 次），明日自动恢复" % cfg["cap"])
    url = ("https://api.weatherapi.com/v1/forecast.json?" + urllib.parse.urlencode({
        "key": cfg["key"], "q": "%.4f,%.4f" % (lat, lon),
        "days": days, "aqi": "no", "alerts": "no", "lang": "zh"}))
    req = urllib.request.Request(url, headers={"User-Agent": "skyglow/2.2"})
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            data = json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:160]
        except Exception:  # noqa: BLE001
            pass
        if e.code == 401:
            msg = "鉴权失败（401）：WeatherAPI 密钥无效"
        elif e.code == 403:
            msg = "无权限或额度受限（403）"
        else:
            msg = "HTTP %d %s" % (e.code, body)
        _record(False, msg[:120])
        raise WeatherApiError(msg) from e
    except Exception as e:  # noqa: BLE001
        _record(False, str(e)[:120])
        raise WeatherApiError(str(e)) from e
    if isinstance(data, dict) and data.get("error"):
        msg = str(data["error"].get("message") or data["error"])[:160]
        _record(False, msg)
        raise WeatherApiError(msg)
    _record(True)
    return data


def payload(lat, lon, ttl=1800, days=3):
    """Raw forecast payload, cached per (lat, lon, local date)."""
    key = "wa_%.2f_%.2f_%s.json" % (lat, lon, date.today().isoformat())
    path = os.path.join(CACHE, key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    data = _request(lat, lon, days)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError:
        pass
    return data


def _hours(data):
    out = []
    for day in (data.get("forecast") or {}).get("forecastday") or []:
        for h in day.get("hour") or []:
            out.append(h)
    return out


def hourly(lat, lon, t_local, ttl=1800):
    """Nearest hourly step to a local naive datetime."""
    data = payload(lat, lon, ttl)
    hours = _hours(data)
    if not hours:
        raise WeatherApiError("返回为空")
    best = None
    for h in hours:
        try:
            t = datetime.fromisoformat(h["time"])
        except (KeyError, ValueError):
            continue
        dt = abs((t - t_local).total_seconds())
        if best is None or dt < best[0]:
            best = (dt, h)
    if best is None:
        raise WeatherApiError("时间解析失败")
    h = best[1]
    cond = h.get("condition") or {}
    return {
        "time": h.get("time"),
        "cloud_total": h.get("cloud"),
        "humidity": h.get("humidity"),
        "visibility_km": h.get("vis_km"),
        "precip_mm": h.get("precip_mm"),
        "chance_of_rain": h.get("chance_of_rain"),
        "dewpoint_c": h.get("dewpoint_c"),
        "temp_c": h.get("temp_c"),
        "pressure_mb": h.get("pressure_mb"),
        "wind_kph": h.get("wind_kph"),
        "wind_dir": h.get("wind_dir"),
        "uv": h.get("uv"),
        "solar_wm2": h.get("short_rad"),
        "condition": cond.get("text"),
        "offset_minutes": int(best[0] / 60),
        "station": (data.get("location") or {}).get("name"),
    }


def covers(t_local):
    """WeatherAPI forecasts 3 days ahead; nothing useful beyond that."""
    return (t_local.date() - date.today()).days <= 2
