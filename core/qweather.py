"""QWeather (和风天气) — supplementary verification source.

What it gives (hourly forecast, /weather/v1/hourly/{lat}/{lon}):
  cloudCover (TOTAL, 0-1), humidity, visibility, precipitation, dewPoint,
  pressure, uvIndex.  There is **no layered cloud (low/mid/high)** and no aerosol
  optical depth in any free-tier endpoint.

Therefore it is deliberately NOT turned into a fifth probability. Splitting a total
cloud into layers would fabricate the very quantity the physical model depends on.
It is used for two things it does well:

  1. verification — total cloud, visibility, humidity, precipitation next to ours
  2. visibility rescue — when ERA5 has no visibility, our purity factor falls back to
     an AOD+RH estimate; a real modelled visibility is better

Cost guard: the free tier is 50 000 requests/month for weather services. A hard
daily cap (default 800) plus the result cache keeps us at roughly 24 calls/day per
location, i.e. three orders of magnitude below the limit.

Auth: API KEY via `X-QW-Api-Key`, or JWT (EdDSA) when a private key is configured.
"""
import base64
import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(HERE, "data", "cache")
USAGE = os.path.join(CACHE, "_qweather_usage.json")
DAILY_CAP = 800


class QWeatherError(RuntimeError):
    pass


_COOLDOWN_UNTIL = 0.0
_COOLDOWN_SEC = 300


def cooldown():
    left = _COOLDOWN_UNTIL - time.time()
    return {"active": left > 0, "retry_in": int(max(0, left))}


def reset_cooldown():
    global _COOLDOWN_UNTIL
    _COOLDOWN_UNTIL = 0.0


KEY_FILES = ("ed25519-private.pem", "qweather-private.pem", "private.pem")


def _find_key_file():
    """Private key may live in config.local.json or in a local .pem next to it."""
    for name in KEY_FILES:
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            return p
    return ""


def _load_pem(path_or_pem):
    """Accept either a PEM string or a path to a PEM file."""
    v = (path_or_pem or "").strip()
    if not v:
        return ""
    if v.startswith("-----BEGIN") or "\n" in v:
        return v.replace("\\n", "\n")
    if os.path.exists(v):
        with open(v, encoding="utf-8") as f:
            return f.read()
    return ""


def _cfg():
    from . import config
    sec = config.qweather_settings()          # single source of truth
    pem = _load_pem(sec.get("private_key") or "")
    if not pem:
        pem = _load_pem(_find_key_file())
    host = (sec.get("host") or "").strip().rstrip("/")
    if host and not host.startswith("http"):
        host = "https://" + host          # tolerate a bare domain from any source
    mode = (sec.get("auth") or "").strip().lower()
    if mode not in ("jwt", "key"):
        mode = "key" if (sec.get("key") and not sec.get("private_key")) else "jwt"
    key = (sec.get("key") or "").strip()
    # guard: a credential id pasted into the key box must not silently replace JWT
    if key and key == (sec.get("kid") or "").strip():
        key = ""
    return {
        "auth": mode,
        "host": host,
        "key": key,
        "kid": sec.get("kid") or "",
        "iss": sec.get("iss") or "",
        "sub": sec.get("sub") or "",
        "private_key": pem,
        "cap": int(sec.get("daily_cap") or DAILY_CAP),
    }


def describe():
    c = _cfg()
    missing = []
    if not c["host"]:
        missing.append("API Host")
    if not (c["key"] or (c["private_key"] and c["kid"] and c["iss"] and c["sub"])):
        if c["private_key"]:
            for f, label in (("kid", "凭据 ID"), ("iss", "开发者 ID"), ("sub", "项目 ID")):
                if not c[f]:
                    missing.append(label)
        else:
            missing.append("API Key 或 Ed25519 私钥")
    return {
        "mode": c["auth"] if configured() else "none",
        "auth": c["auth"],
        "kid": c["kid"], "iss": c["iss"], "sub": c["sub"],
        "has_key": bool(c["key"]), "has_private_key": bool(c["private_key"]),
        "key_file": _find_key_file(),
        "missing": missing,
        "configured": configured(),
    }


def selftest():
    """Sign a JWT and call one cheap endpoint; returns a readable verdict."""
    try:
        token = _jwt(_cfg())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "stage": "sign", "error": str(e)[:200]}
    try:
        data = _request("/weather/v1/current/31.23/121.47", {})
    except QWeatherError as e:
        msg = str(e)
        hint = ""
        if "401" in msg:
            hint = ("JWT 签名与密钥对本身校验通过，401 表示服务端不认这份 (kid, 公钥) 组合："
                    "请把 skyglow/qweather-public.pem 的内容贴到控制台凭据 %s 的公钥框，"
                    "或改用与已登记公钥配对的私钥；同时确认 kid 与项目 ID 属于同一项目。"
                    % (_cfg()["kid"] or "?"))
        return {"ok": False, "stage": "request", "error": msg[:200], "hint": hint,
                "token_head": token[:24] + "…"}
    now = data.get("now") or data.get("current") or {}
    return {"ok": True, "stage": "done", "token_head": token[:24] + "…",
            "sample": {k: now.get(k) for k in ("temp", "text", "humidity", "vis")
                       if isinstance(now, dict) and k in now} or None}


def configured():
    c = _cfg()
    if not c["host"]:
        return False
    if c["auth"] == "key":
        return bool(c["key"])
    # JWT needs all four: private key, kid, iss, sub
    return all(bool(c[k]) for k in ("private_key", "kid", "iss", "sub"))


def _b64u(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _jwt(cfg):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    now = int(time.time())
    header = {"alg": "EdDSA", "kid": cfg["kid"]}
    payload = {"iss": cfg["iss"], "sub": cfg["sub"], "iat": now - 30, "exp": now + 900}
    signing = (_b64u(json.dumps(header, separators=(",", ":")).encode())
               + "." + _b64u(json.dumps(payload, separators=(",", ":")).encode()))
    key = serialization.load_pem_private_key(cfg["private_key"].encode(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise QWeatherError("私钥不是 Ed25519")
    sig = key.sign(signing.encode())
    return signing + "." + _b64u(sig)


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


def _record(ok, auth, error=None):
    """Count real usage: a 401 still costs a request, so attempts and successes
    are tracked separately and the last outcome is kept for the UI."""
    d = _usage()
    d["attempts"] = int(d.get("attempts", 0)) + 1
    if ok:
        d["ok"] = int(d.get("ok", 0)) + 1
        d["count"] = int(d.get("count", 0)) + 1
        d["last_ok"] = datetime.now().isoformat(timespec="seconds")
        d["last_error"] = None
    else:
        d["fail"] = int(d.get("fail", 0)) + 1
        if error and error.startswith("HTTP 401"):
            pass          # rejected before reaching the data service: not billed
        else:
            d["count"] = int(d.get("count", 0)) + 1
        d["last_error"] = error
        d["last_fail"] = datetime.now().isoformat(timespec="seconds")
    d["auth"] = auth
    d["date"] = date.today().isoformat()
    try:
        with open(USAGE, "w", encoding="utf-8") as f:
            json.dump(d, f)
    except OSError:
        pass
    return d


def usage():
    d = _usage()
    counted = int(d.get("count", 0))
    return {
        "date": d.get("date"), "count": counted, "cap": _cfg()["cap"],
        "remaining": max(0, _cfg()["cap"] - counted),
        "attempts": int(d.get("attempts", 0)),
        "ok": int(d.get("ok", 0)), "fail": int(d.get("fail", 0)),
        "last_ok": d.get("last_ok"), "last_fail": d.get("last_fail"),
        "last_error": d.get("last_error"), "auth": d.get("auth"),
    }


def _request(path, params):
    global _COOLDOWN_UNTIL
    cfg = _cfg()
    if not cfg["host"]:
        raise QWeatherError("未配置 API Host")
    if not configured():
        raise QWeatherError("未配置密钥")
    if time.time() < _COOLDOWN_UNTIL:
        raise QWeatherError("鉴权持续失败，冷却中（约 %d 秒后重试）"
                            % max(1, int(_COOLDOWN_UNTIL - time.time())))
    u = usage()
    if u["remaining"] <= 0:
        raise QWeatherError("已达每日护栏上限（%d 次），明日自动恢复" % u["cap"])
    url = cfg["host"] + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"User-Agent": "skyglow/2.2", "Accept-Encoding": "gzip"}
    used = "key"
    if cfg["auth"] == "key" and cfg["key"]:
        headers["X-QW-Api-Key"] = cfg["key"]
    else:
        headers["Authorization"] = "Bearer " + _jwt(cfg)
        used = "jwt"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            raw = r.read()
            enc = (r.headers.get("Content-Encoding") or "").lower()
        # the API ignores Accept-Encoding and may answer gzip regardless
        if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
            raw = gzip.decompress(raw)
        data = json.loads(raw.decode("utf-8"))
        _record(True, used, None)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            pass
        _record(False, used, "HTTP %d %s" % (e.code, body[:120]))
        if e.code in (401, 403):
            _COOLDOWN_UNTIL = time.time() + _COOLDOWN_SEC
        if e.code == 401:
            raise QWeatherError("鉴权失败（401）：请检查 API Key 与 Host") from e
        if e.code == 403:
            raise QWeatherError("无权限或额度受限（403）") from e
        if e.code == 429:
            raise QWeatherError("请求过于频繁（429）") from e
        raise QWeatherError("HTTP %d %s" % (e.code, body)) from e
    except Exception as e:  # noqa: BLE001
        _record(False, used, str(e)[:120])
        raise QWeatherError(str(e)) from e
    if isinstance(data, dict) and data.get("error"):
        raise QWeatherError(str(data["error"].get("detail") or data["error"])[:160])
    return data


def _payload(lat, lon, ttl=3600):
    """Raw hourly payload, cached per (lat, lon, local date): one call serves both events."""
    key = "qw_%.2f_%.2f_%s.json" % (lat, lon, date.today().isoformat())
    path = os.path.join(CACHE, key)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < ttl:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    payload = _request("/weather/v1/hourly/%.2f/%.2f" % (lat, lon),
                       {"hours": "48", "localTime": "true"})
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
    except OSError:
        pass
    return payload


def hourly(lat, lon, t_local, ttl=3600):
    """Nearest hourly step to a local naive datetime (reuses the day-level cache)."""
    payload = _payload(lat, lon, ttl)
    hours = payload.get("hours") or []
    if not hours:
        raise QWeatherError("返回为空")
    best = None
    for h in hours:
        ts = h.get("forecastTime") or ""
        try:
            t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if t.tzinfo is not None:
                t = t.astimezone().replace(tzinfo=None)
        except ValueError:
            continue
        dt = abs((t - t_local).total_seconds())
        if best is None or dt < best[0]:
            best = (dt, h)
    if best is None:
        raise QWeatherError("时间解析失败")
    h = best[1]
    cloud = h.get("cloudCover")
    return {
        "time": h.get("forecastTime"),
        "cloud_total": None if cloud is None else round(float(cloud) * 100, 1),
        "humidity": None if h.get("humidity") is None else round(float(h["humidity"]) * 100, 1),
        "visibility_km": None if not h.get("visibility") else round(float(h["visibility"]["value"]) / 1000, 1),
        "precip_mm": None if not h.get("precipitation") else h["precipitation"].get("amount", {}).get("value"),
        "dew_point": None if not h.get("dewPoint") else h["dewPoint"].get("value"),
        "condition": (h.get("condition") or {}).get("text"),
        "offset_minutes": int(best[0] / 60),
    }
