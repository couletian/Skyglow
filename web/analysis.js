/* Azimuth radar (four sources at once) + geometry cross-section (switchable source). */
(() => {
  const $ = s => document.querySelector(s);
  const DIRS = ["北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南",
    "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北"];
  const FAIL = '数据获取失败';
  const state = { az: null, key: null, detail: null, req: 0 };

  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function fit(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const ctx = cv.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  window.addEventListener("skyglow:data", e => load(e.detail));
  window.addEventListener("skyglow:pending", e => {
    state.key = null;                 // force a refetch on the next data event
    paintPending((e.detail && e.detail.reason) || "正在重算方位…");
  });

  function paintPending(reason) {
    const cv = $("#radar");
    const { ctx, w, h } = fit(cv);
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(255,255,255,.45)";
    ctx.font = '13px -apple-system, "PingFang SC", sans-serif';
    ctx.textAlign = "center";
    ctx.fillText("正在计算…", w / 2, h / 2);
    $("#radarBest").textContent = reason;
    $("#radarTop").innerHTML = "";
    $("#geoSvg").innerHTML =
      `<text x="330" y="152" fill="rgba(255,255,255,.5)" font-size="15" text-anchor="middle">正在计算…</text>`
      + `<text x="330" y="178" fill="rgba(255,255,255,.32)" font-size="11" text-anchor="middle">${reason}</text>`;
    $("#geoCap").textContent = "—";
    const pc = $("#profile");
    const g = fit(pc);
    g.ctx.clearRect(0, 0, g.w, g.h);
    g.ctx.fillStyle = "rgba(255,255,255,.4)";
    g.ctx.font = '12px -apple-system, "PingFang SC", sans-serif';
    g.ctx.textAlign = "center";
    g.ctx.fillText("正在计算…", g.w / 2, g.h / 2);
    $("#geoNote").textContent = reason;
  }

  async function load(detail) {
    state.detail = detail;
    const key = `${detail.date}|${detail.mode}|${detail.lat.toFixed(3)}|${detail.lon.toFixed(3)}`;
    if (!state.az || state.key !== key) {
      const my = ++state.req;
      paintPending(state.az ? "正在按新的地点/时刻重算方位…" : "正在计算方位与几何…");
      try {
        const r = await fetch(`/api/azimuth?lat=${detail.lat}&lon=${detail.lon}`
          + `&date=${detail.date}&mode=${detail.mode}`);
        const az = await r.json();
        if (my !== state.req) return;
        if (az.error) throw new Error(az.error);
        state.az = az; state.key = key;
      } catch (err) {
        state.az = null;
        $("#geoNote").textContent = "方位分析暂不可用：" + err.message;
        $("#radarBest").textContent = FAIL;
        $("#radarTop").innerHTML = "";
        $("#geoSvg").innerHTML = `<text x="330" y="150" fill="rgba(255,255,255,.55)" font-size="15" text-anchor="middle">${FAIL}</text>
          <text x="330" y="176" fill="rgba(255,255,255,.35)" font-size="11" text-anchor="middle">${err.message}</text>`;
        $("#geoCap").textContent = FAIL;
        return;
      }
    }
    drawRadar(state.az, detail.meta);
    drawGeo(state.az, detail.geo || "openmeteo");
    drawProfile(state.az, detail.geo || "openmeteo");
  }

  const dirName = a => DIRS[Math.round(((a % 360) + 360) % 360 / 22.5) % 16];

  /* ---------------- radar: one polygon per source ---------------- */
  function drawRadar(az, meta) {
    const cv = $("#radar");
    const { ctx, w, h } = fit(cv);
    const cx = w / 2, cy = h / 2 + 4;
    const R = Math.min(w, h) / 2 - 34;
    ctx.clearRect(0, 0, w, h);

    for (let k = 1; k <= 4; k++) {
      ctx.beginPath(); ctx.arc(cx, cy, R * k / 4, 0, Math.PI * 2);
      ctx.strokeStyle = k === 4 ? "rgba(255,255,255,.18)" : "rgba(255,255,255,.07)";
      ctx.stroke();
    }
    for (let i = 0; i < 16; i++) {
      const a = (i * 22.5 - 90) * Math.PI / 180;
      ctx.beginPath(); ctx.moveTo(cx, cy);
      ctx.lineTo(cx + Math.cos(a) * R, cy + Math.sin(a) * R);
      ctx.strokeStyle = "rgba(255,255,255,.055)"; ctx.stroke();
    }
    ctx.font = '10px -apple-system, "PingFang SC", sans-serif';
    ctx.textAlign = "center"; ctx.textBaseline = "middle";
    for (let i = 0; i < 8; i++) {
      const a = (i * 45 - 90) * Math.PI / 180;
      ctx.fillStyle = "rgba(255,255,255,.42)";
      ctx.fillText(DIRS[i * 2], cx + Math.cos(a) * (R + 17), cy + Math.sin(a) * (R + 17));
    }

    const sa = (az.sun_az - 90) * Math.PI / 180;
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = "rgba(255,209,102,.55)";
    ctx.beginPath(); ctx.moveTo(cx, cy);
    ctx.lineTo(cx + Math.cos(sa) * R, cy + Math.sin(sa) * R); ctx.stroke();
    ctx.setLineDash([]);
    ctx.fillStyle = "rgba(255,209,102,.85)";
    ctx.beginPath(); ctx.arc(cx + Math.cos(sa) * R, cy + Math.sin(sa) * R, 3, 0, Math.PI * 2); ctx.fill();
    ctx.font = "9px ui-monospace, monospace";
    ctx.fillText(`${az.sun_az}°`, cx + Math.cos(sa) * (R + 30), cy + Math.sin(sa) * (R + 30));

    const colors = {};
    (meta || []).forEach(m => { colors[m.id] = m.color; });
    const okList = [];
    Object.entries(az.sources).forEach(([sid, s], idx) => {
      if (!s || s.error) return;
      okList.push(sid);
      ctx.beginPath();
      s.dirs.forEach((d, i) => {
        const a = (d.az - 90) * Math.PI / 180;
        const rad = Math.max(3, d.score / 100 * R);
        const x = cx + Math.cos(a) * rad, y = cy + Math.sin(a) * rad;
        i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
      });
      ctx.closePath();
      const col = colors[sid] || '#ff8a4c';
      ctx.fillStyle = hexA(col, idx === 0 ? 0.14 : 0.06);
      ctx.fill();
      ctx.strokeStyle = col; ctx.lineWidth = 1.6; ctx.stroke();

      const b = s.best;
      const ba = (b.az - 90) * Math.PI / 180;
      const br = Math.max(3, b.score / 100 * R);
      ctx.beginPath();
      ctx.arc(cx + Math.cos(ba) * br, cy + Math.sin(ba) * br, 3, 0, Math.PI * 2);
      ctx.fillStyle = col; ctx.fill();
    });

    ctx.beginPath(); ctx.arc(cx, cy, 2.5, 0, Math.PI * 2);
    ctx.fillStyle = "rgba(255,255,255,.8)"; ctx.fill();

    // legend (top-left)
    ctx.textAlign = "left"; ctx.textBaseline = "alphabetic";
    let ly = 16;
    (meta || []).forEach(m => {
      const failed = (az.failed || []).includes(m.id);
      ctx.fillStyle = failed ? "rgba(255,255,255,.25)" : m.color;
      ctx.fillRect(12, ly - 6, 10, 3);
      ctx.fillStyle = failed ? "rgba(255,255,255,.4)" : "rgba(255,255,255,.72)";
      ctx.font = "9.5px -apple-system, 'PingFang SC', sans-serif";
      ctx.fillText(failed ? `${m.label}：${FAIL}` : m.label, 27, ly);
      ly += 15;
    });

    const bestRows = (meta || []).map(m => {
      const s = az.sources[m.id];
      if (!s || s.error) {
        return `<div class="rt err"><div class="n"><span>${m.label}</span><em>${FAIL}</em></div></div>`;
      }
      return `<div class="rt"><div class="n"><span>${m.short}</span><em>${Math.round(s.best.score)}</em></div>
        <div class="d">${s.best.name} · 云幕约 ${s.best.dist || "—"} 公里</div></div>`;
    }).join("");
    $("#radarBest").innerHTML = okList.length
      ? `日落方位 <b>${az.sun_az}°</b>（${dirName(az.sun_az)}）· 四源最佳方位已叠加
         <div style="margin-top:4px;font-size:11.5px;color:var(--muted2)">霞光主要出现在太阳一侧 ±90° 的扇区内，线条越靠外表示该方向越容易看到霞光。</div>`
      : FAIL;
    $("#radarTop").innerHTML = bestRows;
    $("#geoNote").textContent =
      `以观测点为中心，16 个方位扫描 400 km 内的云况 · 日落方位 ${az.sun_az}°（${dirName(az.sun_az)}）`
      + (az.failed && az.failed.length ? ` · ${az.failed.length} 个数据源获取失败` : "");
  }

  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  /* ---------------- geometry ---------------- */
  function drawGeo(az, sid) {
    const s = az.sources[sid];
    if (!s || s.error) {
      $("#geoSvg").innerHTML = `<text x="330" y="160" fill="rgba(255,255,255,.55)" font-size="15"
        text-anchor="middle">${FAIL}</text>`;
      $("#geoCap").textContent = FAIL;
      return;
    }
    const W = 660, H = 320;
    const obsX = 372, surfY = 246;
    const D = s.edge || 260;
    const reach = s.glow_reach || 700;
    const edgeX = clamp(obsX - 40 - D * 0.42, 70, obsX - 60);
    const sunX = 96, sunY = 292;
    const cloudY = 132;
    const glowEnd = clamp(edgeX + 190, edgeX + 80, W - 20);

    $("#geoSvg").innerHTML = `
    <defs>
      <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0" stop-color="#1a1430"/><stop offset="0.55" stop-color="#4a2638"/>
        <stop offset="1" stop-color="#8a3a2a"/>
      </linearGradient>
      <linearGradient id="glow" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#ff6a3d" stop-opacity="0.62"/>
        <stop offset="1" stop-color="#ffd166" stop-opacity="0.10"/>
      </linearGradient>
      <radialGradient id="sunglow"><stop offset="0" stop-color="#ffe9b0" stop-opacity=".95"/>
        <stop offset="1" stop-color="#ff9a3c" stop-opacity="0"/></radialGradient>
    </defs>
    <rect x="0" y="0" width="${W}" height="${H}" fill="url(#sky)" rx="10"/>
    <path d="M0 ${surfY} Q ${W / 2} ${surfY - 34} ${W} ${surfY}" fill="#0b0d16" stroke="rgba(255,220,180,.35)" stroke-width="1.2"/>
    <circle cx="${sunX}" cy="${sunY}" r="46" fill="url(#sunglow)"/>
    <circle cx="${sunX}" cy="${sunY}" r="12" fill="#ffd98a"/>
    <circle cx="${sunX}" cy="${sunY}" r="19" fill="none" stroke="#ffcf7a" stroke-opacity=".55"/>

    <path d="M20 ${cloudY + 10} L${edgeX} ${cloudY + 3} L${edgeX} ${cloudY + 26} L20 ${cloudY + 30} Z"
          fill="rgba(220,220,235,.13)" stroke="rgba(230,230,245,.45)" stroke-width="1"/>
    <path d="M20 ${cloudY + 10} q18 -9 36 0 t36 0 t36 0 t36 0 t36 0 t36 0 t36 0"
          fill="none" stroke="rgba(235,235,250,.55)" stroke-width="1"/>

    <path d="M${edgeX} ${cloudY + 26} L${glowEnd} ${surfY - 26} L${glowEnd} ${surfY} L${edgeX} ${surfY} Z"
          fill="url(#glow)"/>
    <line x1="${sunX + 6}" y1="${sunY - 6}" x2="${edgeX}" y2="${cloudY + 26}"
          stroke="rgba(255,190,120,.35)" stroke-width="1" stroke-dasharray="3 3"/>
    <line x1="${obsX}" y1="${surfY}" x2="${edgeX}" y2="${cloudY + 26}"
          stroke="rgba(255,255,255,.55)" stroke-width="1.1" stroke-dasharray="5 4"/>
    <text x="${(obsX + edgeX) / 2}" y="${(surfY + cloudY + 26) / 2 - 6}" fill="rgba(255,255,255,.6)" font-size="10.5" text-anchor="middle">视线</text>

    <line x1="28" y1="${cloudY + 3}" x2="28" y2="${surfY}" stroke="rgba(255,255,255,.28)"/>
    <line x1="24" y1="${cloudY + 3}" x2="32" y2="${cloudY + 3}" stroke="rgba(255,255,255,.28)"/>
    <line x1="24" y1="${surfY}" x2="32" y2="${surfY}" stroke="rgba(255,255,255,.28)"/>
    <text x="36" y="${(cloudY + surfY) / 2}" fill="rgba(255,255,255,.7)" font-size="10.5" font-family="ui-monospace, monospace">H ${s.cloud_base.toFixed(1)} km</text>

    <line x1="${edgeX}" y1="${surfY + 16}" x2="${obsX}" y2="${surfY + 16}" stroke="rgba(255,255,255,.35)"/>
    <line x1="${edgeX}" y1="${surfY + 12}" x2="${edgeX}" y2="${surfY + 20}" stroke="rgba(255,255,255,.35)"/>
    <line x1="${obsX}" y1="${surfY + 12}" x2="${obsX}" y2="${surfY + 20}" stroke="rgba(255,255,255,.35)"/>
    <text x="${(edgeX + obsX) / 2}" y="${surfY + 30}" fill="rgba(255,255,255,.62)" font-size="10" text-anchor="middle">云边界 D = ${D} km</text>

    <text x="24" y="${cloudY - 10}" fill="rgba(255,255,255,.55)" font-size="10">云层（可被低角度阳光照亮的幕布）</text>
    <text x="${glowEnd - 6}" y="${surfY - 34}" fill="rgba(255,190,140,.85)" font-size="10.5" text-anchor="end">霞光区</text>
    <text x="${glowEnd - 6}" y="${surfY - 22}" fill="rgba(255,255,255,.45)" font-size="9.8" text-anchor="end">D_glow ≈ ${reach} km</text>

    <circle cx="${obsX}" cy="${surfY - 4}" r="4" fill="#fff"/>
    <text x="${obsX}" y="${surfY - 12}" fill="rgba(255,255,255,.8)" font-size="10" text-anchor="middle">观测点</text>
    <text x="${sunX}" y="${sunY + 34}" fill="rgba(255,220,160,.8)" font-size="10" text-anchor="middle">太阳 ${az.sun_elev}°</text>
    <text x="16" y="${H - 12}" fill="rgba(255,255,255,.3)" font-size="9.8">← 太阳方位 ${dirName(az.sun_az)} ${az.sun_az}°　|　水平距离（垂直比例已放大）　→</text>`;

    const ev = state.detail && state.detail.event;
    const r = ev && ev.sources ? ev.sources[sid] : null;
    const w2 = r && !r.error ? r.window : null;
    $("#geoCap").innerHTML = w2
      ? `时间窗：${w2.start_min < 0 ? "日落前 " + (-w2.start_min) : "日落后 " + w2.start_min} 分开始，
         持续 <b>${w2.duration} 分钟</b>（峰值 ${w2.peak_min >= 0 ? "+" + w2.peak_min : w2.peak_min} 分）`
      : (r && r.error ? FAIL : "时间窗：—");
  }

  /* ---------------- cloud profile ---------------- */
  function drawProfile(az, sid) {
    const cv = $("#profile");
    const { ctx, w, h } = fit(cv);
    const s = az.sources[sid];
    ctx.clearRect(0, 0, w, h);
    if (!s || s.error) {
      ctx.fillStyle = 'rgba(255,255,255,.45)';
      ctx.font = '12px -apple-system, "PingFang SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(FAIL, w / 2, h / 2);
      return;
    }
    const ml = 32, mr = 12, mt = 16, mb = 22;
    const iw = w - ml - mr, ih = h - mt - mb;
    const maxD = 550;
    const X = d => ml + d / maxD * iw;
    const Y = v => mt + (1 - clamp(v, 0, 100) / 100) * ih;

    ctx.strokeStyle = "rgba(255,255,255,.06)";
    for (let v = 0; v <= 100; v += 50) {
      ctx.beginPath(); ctx.moveTo(ml, Y(v)); ctx.lineTo(w - mr, Y(v)); ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,.28)";
      ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "right";
      ctx.fillText(v + "%", ml - 4, Y(v) + 3);
    }

    const mid = s.transect.map(t => clamp((t.mid || 0) + (t.high || 0), 0, 100));
    ctx.beginPath();
    s.transect.forEach((t, i) => {
      const x = X(t.dist), y = Y(mid[i]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.lineTo(X(s.transect[s.transect.length - 1].dist), Y(0));
    ctx.lineTo(X(s.transect[0].dist), Y(0));
    ctx.closePath();
    const g = ctx.createLinearGradient(0, mt, 0, mt + ih);
    g.addColorStop(0, "rgba(255,180,120,.45)");
    g.addColorStop(1, "rgba(255,180,120,.03)");
    ctx.fillStyle = g; ctx.fill();
    ctx.beginPath();
    s.transect.forEach((t, i) => {
      const x = X(t.dist), y = Y(mid[i]);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = "#ffb457"; ctx.lineWidth = 1.4; ctx.stroke();

    ctx.beginPath();
    s.transect.forEach((t, i) => {
      const x = X(t.dist), y = Y(t.low || 0);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.strokeStyle = "rgba(120,160,210,.85)"; ctx.lineWidth = 1.1;
    ctx.setLineDash([3, 3]); ctx.stroke(); ctx.setLineDash([]);

    if (s.edge) {
      ctx.strokeStyle = "rgba(255,255,255,.3)";
      ctx.beginPath(); ctx.moveTo(X(s.edge), mt); ctx.lineTo(X(s.edge), mt + ih); ctx.stroke();
      ctx.fillStyle = "rgba(255,255,255,.5)";
      ctx.font = "9px -apple-system, sans-serif"; ctx.textAlign = "left";
      ctx.fillText("云边界", X(s.edge) + 3, mt + 9);
    }
    ctx.fillStyle = "rgba(255,255,255,.3)";
    ctx.font = "9px ui-monospace, monospace"; ctx.textAlign = "center";
    for (let d = 0; d <= 450; d += 150) ctx.fillText(d + " km", X(d), h - 6);
    ctx.textAlign = "left";
    ctx.fillStyle = "rgba(255,255,255,.4)";
    ctx.font = "9px -apple-system, sans-serif";
    ctx.fillText("中高云", ml + 4, mt + 2);
    ctx.fillStyle = "rgba(255,190,120,.9)";
    ctx.fillRect(ml + 40, mt - 4, 12, 3);
    ctx.fillStyle = "rgba(120,160,210,.9)";
    ctx.fillRect(ml + 92, mt - 4, 12, 2);
    ctx.fillStyle = "rgba(255,255,255,.4)";
    ctx.fillText("低云", ml + 108, mt + 2);
  }
})();
