"""High-level service used by the HTTP API.

Every event is evaluated independently for all four data sources
(Open-Meteo, ECMWF IFS, GFS, ICON). A source that has no usable data returns
`{"error": "数据获取失败"}` instead of being silently substituted.
"""
from datetime import date, datetime, timedelta, timezone

from . import config, meteo, model, nn, qweather, solar, store, weatherapi

PRIMARY = "openmeteo"


def _conf(lead, source, w):
    if source == "direct":
        c = 0.74
        if w.get("spread") is not None:
            c -= min(0.15, w["spread"] / 400.0)
        return max(0.2, min(0.9, c))
    if source.startswith("ERA5"):
        c = 0.90
    else:
        c = 0.86 - 0.058 * max(0, lead)
    if w.get("spread") is not None:
        c -= min(0.22, w["spread"] / 280.0)
    if w.get("aod") is None:
        c -= 0.08
    return max(0.15, min(0.95, c))


def _reasons(w, r, event):
    f = r["features"]
    dk = {d["key"]: d for d in r["decks"]}
    out = []
    if f["canvas"] < 0.20:
        out.append({"k": "neg", "t": "缺少可被照亮的云层：高空/中空云量不足以形成“幕布”"})
    elif f["canvas"] < 0.45:
        out.append({"k": "warn", "t": "云量偏少，只有局部云块可能被染色"})
    else:
        out.append({"k": "pos", "t": "中高空云层配置良好，具备大面积受光条件"})

    if f["horizon"] < 0.55:
        out.append({"k": "neg", "t": "太阳方向低云遮挡明显，光路被切断（上游低云 %.0f%%）" % (w.get("cc_low_up") or 0)})
    elif f["horizon"] < 0.8:
        out.append({"k": "warn", "t": "上游低云偏多，地平线附近光路存在不确定性"})

    if f["purity"] < 0.55:
        out.append({"k": "neg", "t": "空气通透度不足：气溶胶消光或湿度偏高，颜色会发灰"})
    elif f["purity"] > 0.85:
        out.append({"k": "pos", "t": "大气洁净干燥，瑞利散射占主导，颜色更纯"})

    if f["light"] < 0.02:
        out.append({"k": "neg", "t": "到达云底的直射光已极弱，接近地球阴影边界"})
    elif f["light"] < 0.06:
        out.append({"k": "warn", "t": "云底较低，受光时间窗口短、亮度有限"})
    else:
        out.append({"k": "pos", "t": "云底受光充分，亮度条件良好"})

    if (w.get("precip") or 0) >= 0.2:
        out.append({"k": "neg", "t": "事件时段有降水，云系厚度大、透光性差"})
    if dk["low"]["cover"] > 70:
        out.append({"k": "neg", "t": "低云覆盖率 %.0f%%，本地天顶被低云封堵" % dk["low"]["cover"]})
    if w.get("aod") and w["aod"] > 0.45:
        out.append({"k": "neg", "t": "气溶胶光学厚度 %.2f 偏大，米散射会降低色彩饱和度" % w["aod"]})
    return out


def _risks(w, r):
    f = r["features"]
    out = []
    if 0.3 < f["canvas"] < 0.65:
        out.append("云量处于临界值，云缝的实际位置决定观感，模式网格可能平滑掉云缝")
    if 0.35 < f["horizon"] < 0.75:
        out.append("太阳方向低云边界若比预报更近，光路会被提前切断")
    if 0.15 < f["aod"] < 0.5:
        out.append("上游气溶胶垂直分布未知，等效消光高度存在 ±1 km 量级的不确定性")
    if 0.03 < f["light"] < 0.12:
        out.append("云底高度接近受光极限，±500 m 的云底误差会明显改变持续时间")
    if (w.get("cc_high") or 0) > 85 and (w.get("cc_mid") or 0) > 60:
        out.append("高云与中云叠置，若高云光学厚度偏大，可能整体发灰而非发红")
    return out


def _nn_features(w, elev):
    f = model.features_from_weather(w, elev)
    return [w.get("cc_low") or 0, w.get("cc_mid") or 0, w.get("cc_high") or 0,
            w.get("cc_low_up") or 0, w.get("cc_mid_up") or 0,
            w.get("rh") or 0, w.get("blh") or 0, elev,
            f["canvas"], f["canvas_high"], f["light"], f["horizon"],
            f["purity"], f["aod"], f["precip"]]


def _block(w, elev, key, t, lat, lon, tz, mdl, lead, bundle_source, with_timeline=True):
    r = model.assess(w, elev, key, mdl)
    tl = model.timeline(w, t, lat, lon, mdl) if with_timeline else []
    win = model.best_window(tl) if tl else None
    p_nn = nn.predict(_nn_features(w, elev))
    r.update({
        "time": t.astimezone(tz).isoformat(),
        "time_label": t.astimezone(tz).strftime("%H:%M"),
        "window": win,
        "confidence": round(_conf(lead, bundle_source, w), 2),
        "reasons": _reasons(w, r, key),
        "risks": _risks(w, r),
        "context": {
            "cloud_low": round(w.get("cc_low") or 0, 0),
            "cloud_mid": round(w.get("cc_mid") or 0, 0),
            "cloud_high": round(w.get("cc_high") or 0, 0),
            "cloud_low_up": round(w.get("cc_low_up") or 0, 0),
            "cloud_mid_up": round(w.get("cc_mid_up") or 0, 0),
            "cloud_high_up": round(w.get("cc_high_up") or 0, 0),
            "aod": round(w["aod"], 3) if w.get("aod") is not None else None,
            "rh": round(w.get("rh") or 0, 0),
            "precip": round(w.get("precip") or 0, 2),
            "wind": round(w.get("wind") or 0, 1),
            "blh": round(w.get("blh") or 0, 0),
            "spread": round(w["spread"], 1) if w.get("spread") is not None else None,
        },
        "nn": None if p_nn is None else {
            "p": round(p_nn, 4), "delta": round(p_nn - r["probability"], 3),
            "version": nn.load().get("version", ""),
        },
        "timeline": [{"m": x["minute"], "v": x["view"], "e": x["elev"],
                      "p": x["probability"], "g": x["grade"]} for x in tl],
    })
    return r


AOD_NEUTRAL = 0.2


def _fill_aux(w, wa, qw):
    """Fill gaps the bundle left behind, WeatherAPI first, QWeather second.

    Only the three quantities the physical model actually reads are filled:
    visibility (purity), relative humidity (haze) and precipitation (washout).
    """
    used = {}
    pairs = (("weatherapi", wa), ("qweather", qw))
    if w.get("vis") is None:
        for name, s in pairs:
            if s and s.get("visibility_km") is not None:
                w["vis"] = float(s["visibility_km"]) * 1000.0
                used["vis"] = name
                break
    if w.get("rh") is None:
        for name, s in pairs:
            if s and s.get("humidity") is not None:
                w["rh"] = float(s["humidity"])
                used["rh"] = name
                break
    if (w.get("precip") or 0) <= 0:
        for name, s in pairs:
            if s and s.get("precip_mm"):
                w["precip"] = float(s["precip_mm"])
                used["precip"] = name
                break
    return used


def _aux_samples(lat, lon, t_local):
    """(weatherapi, qweather) samples for one event; both are disk-cached."""
    wa = qw = None
    if weatherapi.configured() and weatherapi.covers(t_local):
        try:
            wa = weatherapi.hourly(lat, lon, t_local)
        except weatherapi.WeatherApiError:
            wa = None
    if wa is None and qweather.configured():
        try:
            qw = qweather.hourly(lat, lon, t_local)
        except qweather.QWeatherError:
            qw = None
    return wa, qw


def _aux_check(w, wa):
    """Compare our diagnosis against the auxiliary total-cloud/visibility sample.

    WeatherAPI has no layered cloud, so this is a sanity check — not a score input.
    Returns a small dict for the UI plus an optional risk string.
    """
    if not wa:
        return None, None
    ours = max(w.get("cc_low") or 0, w.get("cc_mid") or 0, w.get("cc_high") or 0)
    cloud = wa.get("cloud_total")
    vis_ours = None if w.get("vis") is None else round(w["vis"] / 1000.0, 1)
    out = {
        "cloud_ours": round(ours),
        "cloud_aux": cloud,
        "cloud_gap": None if cloud is None else round(abs(ours - cloud)),
        "vis_ours_km": vis_ours,
        "vis_aux_km": wa.get("visibility_km"),
        "rh_ours": round(w.get("rh") or 0),
        "rh_aux": wa.get("humidity"),
        "precip_aux": wa.get("precip_mm"),
        "rain_chance": wa.get("chance_of_rain"),
        "condition": wa.get("condition"),
        "dewpoint": wa.get("dewpoint_c"),
        "time": wa.get("time"),
    }
    risk = None
    if cloud is not None and abs(ours - cloud) >= 35:
        risk = ("校验源总云量 %d%% 与我们诊断的 %d%% 相差较大，"
                "该时刻云量存在不确定性" % (cloud, round(ours)))
    elif (wa.get("chance_of_rain") or 0) >= 60:
        risk = "校验源给出 %d%% 降水概率（%s），雨幕可能压低能见度与色彩纯度" % (
            wa["chance_of_rain"], wa.get("condition") or "降水")
    return out, risk


def _direct_block(sid, lat, lon, t, elev, key, tz, mdl, lead, aod=None, aux=None):
    """Evaluate one source from its official archive (centre point only)."""
    d = meteo.fetch_direct(sid, lat, lon, t)
    w = {
        "cc_low": d["cc_low"], "cc_mid": d["cc_mid"], "cc_high": d["cc_high"],
        "cc_low_up": d["cc_low"], "cc_mid_up": d["cc_mid"], "cc_high_up": d["cc_high"],
        "rh": None, "precip": 0.0, "blh": None,
        "aod": AOD_NEUTRAL if aod is None else aod, "vis": None, "spread": None,
    }
    aux = aux or (None, None)
    filled = _fill_aux(w, *aux)
    blk = _block(w, elev, key, t, lat, lon, tz, mdl, lead, "direct")
    if filled:
        blk["aux_from"] = filled
    chk, risk = _aux_check(w, (aux or (None, None))[0])
    if chk:
        blk["aux_check"] = chk
    if risk:
        blk.setdefault("risks", []).append(risk)
    blk["id"] = sid
    blk["origin"] = "direct"
    blk["direct"] = {k: d[k] for k in ("run", "step", "grid", "transport") if k in d}
    if d.get("diagnosed"):
        blk["direct"]["diagnosed"] = True
        blk["direct"]["note"] = "由总云量与湿度廓线诊断，非模式自带分层云量"
    blk["degraded"] = ("直连模式：仅取最近格点，无上游环带；湿度/边界层用中性值"
                       if not ({"rh", "vis"} <= set(filled)) else
                       "直连模式：仅取最近格点，无上游环带；湿度与能见度已由辅助源补齐")
    return blk


def predict_day(lat, lon, d, name=None, force_direct=False, refresh=False):
    """Assembled prediction. Local cache first; the network is touched only on miss."""
    mdl_version = model.load_model()["version"]
    ckey = store.key(lat, lon, d, "day" + ("|direct" if force_direct else ""), mdl_version)
    if not refresh:
        hit, age = store.get("predict", ckey, d)
        if hit:
            hit["cache"] = {"hit": True, "age": age, "ttl": store.ttl_for(d)}
            return hit
    payload = _predict_day(lat, lon, d, name, force_direct)
    payload["cache"] = {"hit": False, "age": 0, "ttl": store.ttl_for(d)}
    store.put("predict", ckey, payload)
    return payload


def _predict_day(lat, lon, d, name=None, force_direct=False):
    bundle = None
    bundle_err = None
    if not force_direct:   # forced direct: do not wait for the relay at all
        try:
            bundle = meteo.fetch_bundle(lat, lon, d - timedelta(days=1), d + timedelta(days=1))
        except meteo.FetchError as e:
            bundle_err = str(e)
    off = bundle["offset"] if bundle else 28800
    tz = timezone(timedelta(seconds=off))
    ev = solar.event_times(lat, lon, d, off / 3600.0)
    lead = (d - date.today()).days
    mdl = model.load_model()

    # Auxiliary samples — one cached request serves both events.
    # Order matters: WeatherAPI is primary, QWeather only fills the gaps.
    wa_map = {}
    wa_err = None
    qw_map = {}
    qw_err = None

    res = {}
    for key in ("sunrise", "sunset"):
        t = ev[key]
        if t is None:
            res[key] = None
            continue
        t_local = t.astimezone(tz).replace(tzinfo=None)
        elev, az = solar.solar_position(lat, lon, t)
        wa = None
        if weatherapi.configured() and weatherapi.covers(t_local):
            try:
                wa = weatherapi.hourly(lat, lon, t_local)
                wa_map[key] = wa
            except weatherapi.WeatherApiError as e:
                wa_err = str(e)
        qw = None
        if qweather.configured() and wa is None:
            try:
                qw = qweather.hourly(lat, lon, t_local)
                qw_map[key] = qw
            except qweather.QWeatherError as e:
                qw_err = str(e)
        cfg = config.direct_settings()
        recent = d >= date.today() - timedelta(days=2)
        centre_aod = AOD_NEUTRAL if not bundle else None
        if bundle:
            centre_aod = meteo._interp(bundle["points"][0].get("aod"),
                                       bundle["points"][0]["time"], t_local)
        per = {}
        for sid in meteo.SOURCE_IDS:
            relay_ok = (not force_direct) and bool(bundle) and meteo.available(bundle, sid, t_local)
            if relay_ok:
                w = meteo.assemble(bundle, t_local, az, sid)
                filled = _fill_aux(w, wa, qw)
                blk = _block(w, elev, key, t, lat, lon, tz, mdl, lead, bundle["source"])
                blk["id"] = sid
                blk["origin"] = "relay"
                if filled:
                    blk["aux_from"] = filled
                chk, risk = _aux_check(w, wa)
                if chk:
                    blk["aux_check"] = chk
                if risk:
                    blk.setdefault("risks", []).append(risk)
                per[sid] = blk
                continue
            allow = (cfg["auto"] or force_direct) and cfg["sources"].get(sid) \
                and recent and meteo.direct_ready(sid)
            if not allow:
                reason = "数据获取失败"
                if sid == "openmeteo":
                    reason = "数据获取失败（Open-Meteo 无官方档案可直连）"
                elif not meteo.direct_ready(sid):
                    reason = "数据获取失败（本机缺少 eccodes，无法直连）"
                elif not cfg["sources"].get(sid):
                    reason = "数据获取失败（该源直连已关闭）"
                elif not recent:
                    reason = "数据获取失败（历史日期无官方实时档案）"
                per[sid] = {"id": sid, "error": reason, "origin": "none"}
                continue
            try:
                per[sid] = _direct_block(sid, lat, lon, t, elev, key, tz, mdl, lead,
                                         centre_aod, aux=(wa, qw))
            except Exception as e:  # noqa: BLE001
                per[sid] = {"id": sid, "error": "数据获取失败", "origin": "direct",
                            "detail": str(e)[:160]}
        ok = [v for v in per.values() if "error" not in v]
        res[key] = {
            "time": t.astimezone(tz).isoformat(),
            "time_label": t.astimezone(tz).strftime("%H:%M"),
            "azimuth": round(az, 1),
            "sun_elev": round(elev, 3),
            "sources": per,
            "consensus": None if not ok else {
                "mean": round(sum(v["probability"] for v in ok) / len(ok), 4),
                "min": round(min(v["probability"] for v in ok), 4),
                "max": round(max(v["probability"] for v in ok), 4),
                "n": len(ok),
            },
        }

    primary_aux = wa_map.get("sunset") or wa_map.get("sunrise")
    return {
        "location": {"name": name or "%.3f, %.3f" % (lat, lon), "lat": lat, "lon": lon,
                     "timezone": bundle["tz"] if bundle else "Asia/Shanghai",
                     "elevation": bundle["elevation"] if bundle else None},
        "date": d.isoformat(),
        "source": bundle["source"] if bundle else "direct archives",
        "direct": config.direct_settings(),
        "direct_ready": {sid: meteo.direct_ready(sid) for sid in ("ecmwf", "gfs", "icon")},
        "relay_error": bundle_err,
        "relay": meteo.relay_state(),
        "weatherapi": {
            "configured": weatherapi.configured(),
            "usage": weatherapi.usage(),
            "samples": wa_map,
            "error": wa_err,
            "note": "主校验源：总云量对照 + 能见度/湿度/降水缺测补位；无分层云量，不参与概率打分",
        },
        "qweather": {
            "configured": qweather.configured(),
            "usage": qweather.usage(),
            "samples": qw_map,
            "error": qw_err,
            "note": "可选备用校验源：仅在 WeatherAPI 不可用时补位；同样无分层云量",
        },
        "sources_meta": [{"id": s["id"], "label": s["label"], "short": s["short"],
                          "tag": s["tag"], "color": s["color"]} for s in meteo.SOURCES],
        "primary": PRIMARY,
        "model_version": mdl["version"],
        "results": res,
        "crosscheck": primary_aux,
        "generated_at": datetime.now(tz).isoformat(),
    }


def outlook(lat, lon, days=7, refresh=False):
    """Seven-day strip. Uses the relay when available, otherwise per-day direct values."""
    today = date.today()
    ckey = store.key(lat, lon, today, "outlook%d" % days, model.load_model()["version"])
    if not refresh:
        hit, age = store.get("outlook", ckey, today)
        if hit:
            hit["cache"] = {"hit": True, "age": age, "ttl": store.ttl_for(today)}
            return hit

    bundle = None
    try:
        # 只取今天起 days+1 天。这里不算时间曲线，事件瞬时必然落在当日，
        # 多取前一天纯属浪费（实测该日被读取 0 次）。
        bundle = meteo.fetch_bundle(lat, lon, today, today + timedelta(days=days))
    except meteo.FetchError:
        bundle = None

    if bundle is None:
        # relay unavailable: a stale copy of this strip is far cheaper than
        # 8 days x 2 events of direct GRIB downloads, so prefer it
        stale, age = store.get_any("outlook", ckey)
        if stale and age < 6 * 3600:
            stale["cache"] = {"hit": True, "age": age, "ttl": store.ttl_for(today),
                              "stale": True}
            stale["relay"] = meteo.relay_state()
            return stale

    cfg = config.direct_settings()
    mdl = model.load_model()
    off = bundle["offset"] if bundle else 28800
    tz = timezone(timedelta(seconds=off))
    out = []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        ev = solar.event_times(lat, lon, d, off / 3600.0)
        row = {"date": d.isoformat(), "weekday": "周" + "一二三四五六日"[d.weekday()]}
        ok = False
        for key in ("sunset", "sunrise"):
            t = ev[key]
            if t is None:
                row[key] = None
                continue
            t_local = t.astimezone(tz).replace(tzinfo=None)
            elev, az = solar.solar_position(lat, lon, t)
            aux = _aux_samples(lat, lon, t_local)
            per = {}
            for sid in meteo.SOURCE_IDS:
                if bundle and meteo.available(bundle, sid, t_local):
                    w = meteo.assemble(bundle, t_local, az, sid)
                    _fill_aux(w, *aux)
                    r = model.assess(w, elev, key, mdl)
                    per[sid] = {"p": r["probability"], "grade": r["grade"],
                                "grade_label": r["grade_label"], "hex": r["colour"]["hex"],
                                "origin": "relay"}
                    continue
                if i <= 1 and cfg["auto"] and cfg["sources"].get(sid) \
                        and sid != "openmeteo" and meteo.direct_ready(sid):
                    try:
                        dd = meteo.fetch_direct(sid, lat, lon, t)
                        w = {"cc_low": dd["cc_low"], "cc_mid": dd["cc_mid"],
                             "cc_high": dd["cc_high"], "cc_low_up": dd["cc_low"],
                             "cc_mid_up": dd["cc_mid"], "cc_high_up": dd["cc_high"],
                             "rh": None, "precip": 0.0, "blh": None,
                             "aod": AOD_NEUTRAL, "vis": None, "spread": None}
                        _fill_aux(w, *aux)
                        r = model.assess(w, elev, key, mdl)
                        per[sid] = {"p": r["probability"], "grade": r["grade"],
                                    "grade_label": r["grade_label"], "hex": r["colour"]["hex"],
                                    "origin": "direct"}
                        continue
                    except Exception:  # noqa: BLE001
                        pass
                per[sid] = {"error": "数据获取失败"}
            prim = per.get(PRIMARY) or {}
            if "error" in prim:
                prim = next((v for v in per.values() if "error" not in v), {})
            row[key] = {
                "time": t.astimezone(tz).strftime("%H:%M"),
                "sources": per,
                "probability": prim.get("p"),
                "grade": prim.get("grade"),
                "grade_label": prim.get("grade_label") or prim.get("error"),
                "hex": prim.get("hex"),
            }
            ok = True
        if ok:
            out.append(row)

    payload = {"location": {"lat": lat, "lon": lon, "timezone": bundle["tz"] if bundle
                            else "Asia/Shanghai"},
               "source": bundle["source"] if bundle else "direct archives",
               "primary": PRIMARY, "days": out,
               "relay": meteo.relay_state(),
                      "qweather": {"configured": qweather.configured(),
                            "usage": qweather.usage()},
               "cache": {"hit": False, "age": 0, "ttl": store.ttl_for(today)}}
    store.put("outlook", ckey, payload)
    return payload
