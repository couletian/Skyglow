"""Local configuration (never committed). Environment variables take priority."""
import json
import os
import threading

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "config.local.json")
_LOCK = threading.Lock()

DEFAULT_DIRECT = {
    "auto": True,                      # fall back to official archives on failure
    "sources": {"ecmwf": True, "gfs": True, "icon": False},
}


def _read():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# No key ships with the code. Each deployment supplies its own:
#   env WEATHERAPI_KEY / SKYGLOW_WEATHERAPI_KEY  >  config.local.json -> weatherapi_key
# Empty means "disabled" — the app still runs, it just skips verification.
DEFAULT_WEATHERAPI_KEY = ""
DEFAULT_WEATHERAPI_CAP = 2000


def weatherapi_key():
    """env > config.local.json > 空（未启用）."""
    env = os.environ.get("SKYGLOW_WEATHERAPI_KEY") or os.environ.get("WEATHERAPI_KEY")
    if env:
        return env.strip()
    return (_read().get("weatherapi_key") or DEFAULT_WEATHERAPI_KEY).strip()


def weatherapi_cap():
    v = _read().get("weatherapi_cap")
    try:
        return int(v) if v else DEFAULT_WEATHERAPI_CAP
    except (TypeError, ValueError):
        return DEFAULT_WEATHERAPI_CAP


DEFAULT_QWEATHER = {
    "host": "",          # 留空：请在页面「和风天气」面板填写自己的 API Host
    "key": "",
    "daily_cap": 800,
}


QW_ENV_KEYS = ("host", "key", "kid", "iss", "sub", "private_key", "daily_cap", "auth")


def qweather_settings():
    """File section first, then SKYGLOW_QWEATHER_* environment variables win."""
    d = _read().get("qweather") or {}
    out = dict(DEFAULT_QWEATHER)
    for k in QW_ENV_KEYS:
        if d.get(k):
            out[k] = d[k]
    for k in QW_ENV_KEYS:
        env = os.environ.get("SKYGLOW_QWEATHER_" + k.upper())
        if env:
            out[k] = env
    return out


def save_qweather(patch):
    with _LOCK:
        data = _read()
        cur = dict(data.get("qweather") or {})
        for k, v in patch.items():
            if v is None:
                continue
            cur[k] = v
        data["qweather"] = cur
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return qweather_settings()


def section(name):
    return dict(_read().get(name) or {})


def save_top(patch):
    """Write top-level keys (weatherapi_key / weatherapi_cap ...)."""
    with _LOCK:
        data = _read()
        for k, v in patch.items():
            if v is None:
                continue
            data[k] = v
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return data


def save_section(name, patch):
    with _LOCK:
        data = _read()
        cur = dict(data.get(name) or {})
        for k, v in patch.items():
            if v is None:
                continue
            cur[k] = v
        data[name] = cur
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return cur


def get(key, default=None):
    env = os.environ.get("SKYGLOW_" + key.upper()) or os.environ.get(key.upper())
    if env:
        return env
    return _read().get(key, default)


def direct_settings():
    d = _read().get("direct") or {}
    out = {"auto": bool(d.get("auto", DEFAULT_DIRECT["auto"])), "sources": {}}
    src = d.get("sources") or {}
    for k, v in DEFAULT_DIRECT["sources"].items():
        out["sources"][k] = bool(src.get(k, v))
    return out


def save_direct(auto, sources):
    with _LOCK:
        data = _read()
        data["direct"] = {
            "auto": bool(auto),
            "sources": {k: bool(sources.get(k, False))
                        for k in ("ecmwf", "gfs", "icon")},
        }
        with open(PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    return direct_settings()
