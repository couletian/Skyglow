"""Skyglow HTTP server — standard library only.

    python server.py [port]

Serves the single-page app from ./web and a small JSON API under /api.
"""
import json
import mimetypes
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from core import (azimuth, config, direct, glowmap, meteo, model, qweather,  # noqa: E402
                  service, store, weatherapi)

WEB = os.path.join(HERE, "web")
DATA = os.path.join(HERE, "data")


def _json(handler, obj, code=200):
    body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    handler.send_response(code)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    if not getattr(handler, "_head_only", False):
        handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    server_version = "Skyglow/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def do_HEAD(self):
        """Same headers as GET, no body. Health checkers and preview panels use it."""
        self._head_only = True
        try:
            self.do_GET()
        finally:
            self._head_only = False

    def _body(self, data):
        """Write the body unless this was a HEAD request."""
        if not getattr(self, "_head_only", False):
            self.wfile.write(data)

    def do_GET(self):
        try:
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/health":
                return _json(self, {"ok": True, "model": model.load_model()["version"],
                                    "cache": store.stats(),
                                    "relay": meteo.relay_state()})
            if u.path == "/api/cache":
                if (q.get("clear") or [""])[0] == "1":
                    n = store.clear((q.get("kind") or [None])[0])
                    return _json(self, {"ok": True, "removed": n, "stats": store.stats()})
                return _json(self, {"stats": store.stats()})
            if u.path == "/api/geocode":
                return _json(self, {"results": meteo.geocode((q.get("q") or [""])[0])})
            if u.path == "/api/predict":
                return self._predict(q)
            if u.path == "/api/outlook":
                return self._outlook(q)
            if u.path == "/api/glowmap":
                return self._glowmap(q)
            if u.path == "/api/validation":
                v = self._read_json("validation.json", {})
                nm = self._read_json("nn_metrics.json", None)
                if nm:
                    v["nn"] = nm
                return _json(self, v)
            if u.path == "/api/azimuth":
                return self._azimuth(q)
            if u.path == "/api/direct":
                return self._direct(q)
            if u.path == "/api/qweather":
                return self._qweather(q)
            if u.path == "/api/weatherapi":
                return self._weatherapi(q)
            if u.path == "/api/model":
                return self._static_json("model.json", model.DEFAULT_MODEL)
            return self._file(u.path)
        except BrokenPipeError:
            return
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return _json(self, {"error": str(e)}, 500)

    def _read_json(self, name, default):
        path = os.path.join(DATA, name)
        if not os.path.exists(path):
            return default
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _static_json(self, name, default):
        return _json(self, self._read_json(name, default))

    def _azimuth(self, q):
        lat = self._num(q, "lat")
        lon = self._num(q, "lon")
        if lat is None or lon is None:
            return _json(self, {"error": "缺少 lat/lon 参数"}, 400)
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return _json(self, {"error": "经纬度超出范围：纬度 −90～90，经度 −180～180"}, 400)
        ds = (q.get("date") or [date.today().isoformat()])[0]
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            return _json(self, {"error": "日期格式应为 YYYY-MM-DD"}, 400)
        mode = (q.get("mode") or ["sunset"])[0]
        refresh = (q.get("refresh") or ["0"])[0] not in ("0", "", "false")
        try:
            return _json(self, azimuth.analyze(lat, lon, d, mode, refresh=refresh))
        except meteo.FetchError as e:
            return _json(self, {"error": "气象数据源暂时不可用：%s" % e}, 502)

    def _num(self, q, key, default=None):
        v = (q.get(key) or [None])[0]
        if v is None:
            return default
        return float(v)

    def _predict(self, q):
        lat = self._num(q, "lat")
        lon = self._num(q, "lon")
        if lat is None or lon is None:
            return _json(self, {"error": "缺少 lat/lon 参数"}, 400)
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return _json(self, {"error": "经纬度超出范围：纬度 −90～90，经度 −180～180"}, 400)
        ds = (q.get("date") or [date.today().isoformat()])[0]
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            return _json(self, {"error": "日期格式应为 YYYY-MM-DD"}, 400)
        today = date.today()
        if d > today + timedelta(days=15):
            return _json(self, {"error": "预报有效期为未来 15 天，请选择更近的日期"}, 400)
        if d < date(1980, 1, 1):
            return _json(self, {"error": "历史数据最早支持 1980 年"}, 400)
        name = (q.get("name") or [None])[0]
        force = (q.get("force_direct") or ["0"])[0] not in ("0", "", "false")
        refresh = (q.get("refresh") or ["0"])[0] not in ("0", "", "false")
        try:
            payload = service.predict_day(lat, lon, d, name,
                                          force_direct=force, refresh=refresh)
        except meteo.FetchError as e:
            return _json(self, {"error": "气象数据源暂时不可用：%s" % e}, 502)
        return _json(self, payload)

    def _outlook(self, q):
        lat = self._num(q, "lat")
        lon = self._num(q, "lon")
        if lat is None or lon is None:
            return _json(self, {"error": "缺少 lat/lon 参数"}, 400)
        if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
            return _json(self, {"error": "经纬度超出范围：纬度 −90～90，经度 −180～180"}, 400)
        days = int((q.get("days") or ["7"])[0])
        days = max(1, min(14, days))
        refresh = (q.get("refresh") or ["0"])[0] not in ("0", "", "false")
        try:
            return _json(self, service.outlook(lat, lon, days, refresh=refresh))
        except meteo.FetchError as e:
            return _json(self, {"error": "气象数据源暂时不可用：%s" % e}, 502)

    def _glowmap(self, q):
        ds = (q.get("date") or [date.today().isoformat()])[0]
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            return _json(self, {"error": "日期格式应为 YYYY-MM-DD"}, 400)
        mode = (q.get("mode") or ["sunset"])[0]
        if mode not in ("sunset", "sunrise"):
            return _json(self, {"error": "mode 应为 sunset 或 sunrise"}, 400)
        scope = (q.get("scope") or ["local"])[0]
        if scope not in ("local", "national", "both"):
            return _json(self, {"error": "scope 应为 local / national / both"}, 400)
        try:
            lat = self._num(q, "lat")
            lon = self._num(q, "lon")

            # National field is expensive (hundreds of locations). It is built only
            # when explicitly requested, and every call may reuse the cache.
            nat = None
            if scope in ("national", "both"):
                if lat is not None and lon is not None:
                    nat = glowmap.national(d, mode, near=(lat, lon))
                else:
                    nat = glowmap.national(d, mode)

            loc = None
            if scope in ("local", "both"):
                if lat is None or lon is None:
                    return _json(self, {"error": "缺少 lat/lon 参数"}, 400)
                loc = glowmap.local(lat, lon, d, mode, nat)
            if scope == "local":
                return _json(self, {"local": loc})
            pub = dict(nat)
            pub.pop("fields", None)      # internal only, keeps the payload small
            if scope == "national":
                return _json(self, {"national": pub})
            return _json(self, {"national": pub, "local": loc})
        except meteo.FetchError as e:
            return _json(self, {"error": "气象数据源暂时不可用：%s" % e}, 502)

    def _direct(self, q):
        """GET returns the switches; POST (query args) updates them."""
        if (q.get("_method") or [""])[0] == "post":
            auto = (q.get("auto") or ["1"])[0] not in ("0", "false", "")
            src = {k: (q.get(k) or ["0"])[0] not in ("0", "false", "")
                   for k in ("ecmwf", "gfs", "icon")}
            cfg = config.save_direct(auto, src)
            return _json(self, {"ok": True, "settings": cfg,
                                "ready": {k: meteo.direct_ready(k) for k in src}})
        cfg = config.direct_settings()
        return _json(self, {
            "settings": cfg,
            "ready": {k: meteo.direct_ready(k) for k in ("ecmwf", "gfs", "icon")},
            "eccodes": direct.HAVE_ECCODES, "numpy": direct.HAVE_NUMPY,
        })

    def _qweather(self, q):
        if (q.get("_method") or [""])[0] == "test":
            return _json(self, qweather.selftest())
        if (q.get("_method") or [""])[0] == "post":
            patch = {}
            for k in ("host", "key", "kid", "iss", "sub", "private_key", "auth"):
                v = (q.get(k) or [None])[0]
                if v is not None:
                    patch[k] = v
            cap = (q.get("daily_cap") or [None])[0]
            if cap:
                try:
                    patch["daily_cap"] = int(cap)
                except ValueError:
                    pass
            if "host" in patch and patch["host"]:
                h = patch["host"].strip().rstrip("/")
                patch["host"] = h if h.startswith("http") else "https://" + h
            config.save_qweather(patch)
        return _json(self, self._qw_state())

    def _weatherapi(self, q):
        if (q.get("_method") or [""])[0] == "test":
            try:
                import datetime as _dt
                now = _dt.datetime.now()
                sample = weatherapi.hourly(31.2304, 121.4737, now)
                return _json(self, {"ok": True, "sample": sample,
                                    "usage": weatherapi.usage()})
            except Exception as e:  # noqa: BLE001
                return _json(self, {"ok": False, "error": str(e)[:200]})
        if (q.get("_method") or [""])[0] == "post":
            patch = {}
            key = (q.get("key") or [None])[0]
            if key is not None:
                patch["weatherapi_key"] = key.strip()
            cap = (q.get("daily_cap") or [None])[0]
            if cap:
                try:
                    patch["weatherapi_cap"] = int(cap)
                except ValueError:
                    pass
            config.save_top(patch)
            return _json(self, self._wa_state())
        return _json(self, self._wa_state())

    def _wa_state(self):
        d = weatherapi.describe()
        return {"configured": d["configured"], "key_hint": d["key_hint"],
                "source": d["source"], "usage": d["usage"], "note": d["note"],
                "cap": config.weatherapi_cap()}

    def _qw_state(self):
        c = config.qweather_settings()
        d = qweather.describe()
        return {
            "host": c.get("host"),
            "daily_cap": c.get("daily_cap"),
            "mode": d["mode"],
            "kid": d["kid"], "iss": d["iss"], "sub": d["sub"],
            "has_key": d["has_key"], "has_private_key": d["has_private_key"],
            "key_file": d["key_file"],
            "missing": d["missing"],
            "configured": d["configured"],
            "cooldown": qweather.cooldown(),
            "usage": qweather.usage(),
            "note": "仅天气和基础服务在免费额度内（每月前 5 万次）；太阳辐射接口 0.3 元/次，未接入",
        }

    def _file(self, path):
        if path in ("/", ""):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/")).replace("\\", "/")
        if rel.startswith(".."):
            return _json(self, {"error": "forbidden"}, 403)
        full = os.path.join(WEB, rel)
        if not os.path.isfile(full):
            return _json(self, {"error": "not found", "path": rel}, 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self._body(body)


def _dep_report():
    """Which optional packages are present, and what is lost without each."""
    rows = []
    for mod, label, impact, need in (
        ("eccodes", "ECMWF / GFS / ICON 直连", "只能走 Open-Meteo，直连开关显示「缺解码器」", "eccodes"),
        ("numpy", "ICON 直连解码", "ICON 直连不可用，ECMWF/GFS 不受影响", "numpy"),
        ("cryptography", "和风天气 JWT 认证", "只能用 API Key 方式", "cryptography[cryptography]"),
    ):
        try:
            __import__(mod)
            rows.append((label, True, "", need))
        except ImportError:
            rows.append((label, False, impact, need))
    return rows


def _print_deps():
    rows = _dep_report()
    missing = [r for r in rows if not r[1]]
    if not missing:
        print("可选依赖：eccodes / numpy / cryptography 均已就绪")
        return
    print("")
    print("!" * 66)
    print("缺少可选依赖，部分功能不可用：")
    for label, ok, impact, need in missing:
        print("  - %-24s %s" % (label, impact))
    print("")
    print("安装（当前解释器）：")
    print("  %s -m pip install %s" % (sys.executable,
                                      " ".join(r[3].split("[")[0] for r in missing)))
    print("或用本目录的 setup 脚本创建独立环境：")
    print("  Windows: setup.bat        其它: ./setup.sh")
    print("!" * 66)
    print("")


def main():
    # Port: argv > $PORT (Render/Railway/Fly set this) > 8770
    want = int(sys.argv[1]) if len(sys.argv) > 1 else int(os.environ.get("PORT") or 8770)
    # Bind: localhost by default; containers must listen on 0.0.0.0
    host = os.environ.get("SKYGLOW_HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")

    srv = None
    port = want
    for cand in [want] + [want + i for i in range(1, 11)]:
        try:
            srv = ThreadingHTTPServer((host, cand), Handler)
            port = cand
            break
        except OSError:
            continue
    if srv is None:
        print("")
        print("[X] 端口 %d–%d 都被占用，无法启动。" % (want, want + 10))
        print("    关掉占用该端口的程序，或换一个端口：python server.py 8888")
        print("")
        return 1

    _print_deps()
    if port != want:
        print("[i] 端口 %d 已被占用，自动改用 %d。" % (want, port))

    # 0.0.0.0 is a bind address, never a URL — browsers reject it.
    print("Skyglow running at http://127.0.0.1:%d" % port)
    print("  （等价写法：http://localhost:%d）" % port)
    if host not in ("127.0.0.1", "localhost"):
        print("  监听地址 %s：面向容器/局域网部署。浏览器请勿使用 0.0.0.0，它不是可访问地址。" % host)
    print("")

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
