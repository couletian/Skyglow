/* Skyglow maps — national probability field + city-scale field.
   Boundary data: 阿里云 DataV.GeoAtlas (includes Taiwan, Hong Kong, Macao and the
   South China Sea islands); rendered as a thematic layer, no third-party tiles. */
(() => {
  const R = Math.PI / 180;
  const NAT = { bbox: [73.5, 18, 135, 54], step: 2.0 };
  const state = { china: null, paths: new Map(), nat: null, loc: null, req: 0,
    meta: null, src: 'openmeteo', natKey: null, locKey: null, natShown: false, locShown: false };
  const $ = s => document.querySelector(s);

  /* ---------- projection ---------- */
  const P1 = 25 * R, P2 = 47 * R, P0 = 35 * R, L0 = 105 * R;
  const N = 0.5 * (Math.sin(P1) + Math.sin(P2));
  const C = Math.cos(P1) ** 2 + 2 * N * Math.sin(P1);
  const RHO0 = Math.sqrt(C - 2 * N * Math.sin(P0)) / N;
  function albers(lon, lat) {
    const rho = Math.sqrt(Math.max(0, C - 2 * N * Math.sin(lat * R))) / N;
    const th = N * (lon * R - L0);
    return [rho * Math.sin(th), RHO0 - rho * Math.cos(th)];
  }
  function makeFit(w, h, bbox, pad) {
    const [x0, y0] = albers(bbox[0], bbox[1]);
    const [x1, y1] = albers(bbox[2], bbox[3]);
    const s = Math.min((w - 2 * pad) / (x1 - x0), (h - 2 * pad) / (y1 - y0));
    const dx = (w - (x1 - x0) * s) / 2, dy = (h - (y1 - y0) * s) / 2;
    return {
      s, proj(lon, lat) {
        const [x, y] = albers(lon, lat);
        return [dx + (x - x0) * s, dy + (y1 - y) * s];
      },
    };
  }

  /* ---------- china geometry ---------- */
  async function loadChina() {
    if (state.china) return state.china;
    const r = await fetch('/china.json');
    state.china = await r.json();
    return state.china;
  }
  function buildPath(fit) {
    const p = new Path2D();
    for (const f of state.china.features) {
      const g = f.geometry;
      if (!g) continue;
      const polys = g.type === 'Polygon' ? [g.coordinates] : g.coordinates;
      for (const poly of polys) {
        for (const ring of poly) {
          ring.forEach(([lon, lat], i) => {
            const [x, y] = fit.proj(lon, lat);
            i ? p.lineTo(x, y) : p.moveTo(x, y);
          });
          p.closePath();
        }
      }
    }
    return p;
  }

  /* ---------- colour ---------- */
  const STOPS = [
    [0.00, 10, 12, 18, 0.00],
    [0.03, 24, 26, 46, 0.45],
    [0.07, 50, 40, 78, 0.70],
    [0.11, 84, 48, 94, 0.84],
    [0.16, 124, 54, 94, 0.92],
    [0.20, 162, 62, 82, 0.96],
    [0.25, 196, 76, 60, 1.00],
    [0.32, 224, 94, 44, 1.00],
    [0.42, 242, 122, 40, 1.00],
    [0.55, 252, 154, 52, 1.00],
    [0.72, 255, 190, 84, 1.00],
    [1.00, 255, 232, 166, 1.00],
  ];
  const CITIES = [['北京', 116.41, 39.90], ['上海', 121.47, 31.23], ['广州', 113.26, 23.13],
    ['成都', 104.07, 30.57], ['西安', 108.94, 34.34], ['乌鲁木齐', 87.62, 43.83],
    ['哈尔滨', 126.53, 45.80], ['拉萨', 91.14, 29.65], ['昆明', 102.72, 25.04],
    ['武汉', 114.31, 30.59]];
  function ramp(p) {
    if (p === null || p === undefined) return 'rgba(0,0,0,0)';
    const v = Math.pow(Math.max(0, Math.min(1, p)), 0.82);
    for (let i = 1; i < STOPS.length; i++) {
      if (v <= STOPS[i][0]) {
        const a = STOPS[i - 1], b = STOPS[i];
        const k = (v - a[0]) / (b[0] - a[0] || 1);
        return `rgba(${Math.round(a[1] + (b[1] - a[1]) * k)},${Math.round(a[2] + (b[2] - a[2]) * k)},${Math.round(a[3] + (b[3] - a[3]) * k)},${(a[4] + (b[4] - a[4]) * k).toFixed(3)})`;
      }
    }
    return 'rgba(255,214,124,1)';
  }

  function fitCanvas(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth, h = cv.clientHeight;
    cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  /* ---------- national ---------- */
  function drawNational(payload, sid) {
    const src = (payload.sources || {})[sid] || { probs: [] };
    const probs = src.probs || [], grades = src.grades || [];
    const failed = (payload.failed || []).includes(sid);
    const cv = $('#mapNational');
    const { ctx, w, h } = fitCanvas(cv);
    const fit = makeFit(w, h, NAT.bbox, 30);
    if (!state.paths.has('n' + w + 'x' + h)) {
      state.paths.set('n' + w + 'x' + h, buildPath(fit));
    }
    const china = state.paths.get('n' + w + 'x' + h);
    ctx.clearRect(0, 0, w, h);

    // graticule
    ctx.strokeStyle = 'rgba(255,255,255,.035)';
    ctx.lineWidth = 1;
    for (let lon = 80; lon <= 130; lon += 10) {
      const a = fit.proj(lon, 17), b = fit.proj(lon, 55);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }
    for (let lat = 20; lat <= 50; lat += 10) {
      const a = fit.proj(73, lat), b = fit.proj(136, lat);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
    }

    // heat layer clipped to national boundary
    const cell = Math.max(5, (payload.step || NAT.step) * fit.s * (Math.PI / 180) * 1.15);
    ctx.save();
    ctx.clip(china);
    ctx.filter = 'blur(7px)';
    const pts = [];
    probs.forEach((p, i) => {
      if (p === null || p === undefined) return;
      const lon = payload.bbox[0] + (i % payload.nx) * payload.step;
      const lat = payload.bbox[1] + Math.floor(i / payload.nx) * payload.step;
      const [x, y] = fit.proj(lon, lat);
      if (x < -30 || y < -30 || x > w + 30 || y > h + 30) return;
      ctx.fillStyle = ramp(p);
      ctx.beginPath();
      ctx.arc(x, y, cell * 0.78, 0, Math.PI * 2);
      ctx.fill();
      pts.push({ x, y, p, g: grades[i], lat, lon });
    });
    ctx.restore();
    ctx.filter = 'none';

    // province / national outline
    ctx.save();
    ctx.strokeStyle = 'rgba(255,255,255,.10)';
    ctx.lineWidth = 0.6;
    ctx.stroke(china);
    ctx.strokeStyle = 'rgba(255,225,190,.42)';
    ctx.lineWidth = 1.1;
    ctx.stroke(china);
    ctx.restore();

    // major city labels
    ctx.font = '10px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
    CITIES.forEach(([nm, lon, lat]) => {
      const [x, y] = fit.proj(lon, lat);
      ctx.fillStyle = 'rgba(255,255,255,.7)';
      ctx.beginPath(); ctx.arc(x, y, 1.5, 0, Math.PI * 2); ctx.fill();
      ctx.fillStyle = 'rgba(255,255,255,.62)';
      ctx.shadowColor = 'rgba(0,0,0,.9)'; ctx.shadowBlur = 4;
      ctx.fillText(nm, x + 4, y - 3);
      ctx.shadowBlur = 0;
    });

    // South China Sea inset
    drawInset(ctx, w, h, payload, sid);

    // selected location marker
    if (state.meta) {
      const [mx, my] = fit.proj(state.meta.lon, state.meta.lat);
      ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.4;
      ctx.beginPath(); ctx.arc(mx, my, 4.5, 0, Math.PI * 2); ctx.stroke();
      ctx.beginPath(); ctx.arc(mx, my, 9, 0, Math.PI * 2);
      ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1; ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.9)';
      ctx.font = '10px ui-monospace, monospace';
      ctx.fillText(state.meta.name || '', mx + 12, my + 3);
    }

    // caption
    ctx.fillStyle = 'rgba(255,255,255,.30)';
    ctx.font = '9.5px ui-monospace, monospace';
    ctx.fillText('边界数据：阿里云 DataV.GeoAtlas · 点击地图选点', 12, h - 10);

    state.natPts = pts;
    state.natFit = fit;
    if (failed) showFail('#stateNational', true); else showFail('#stateNational', false);
  }

  function drawInset(ctx, w, h, payload, sid) {
    const bw = 148, bh = 178;
    const x0 = w - bw - 16, y0 = h - bh - 16;
    const bbox = [106, 2.5, 123, 24];
    const fit = makeFit(bw, bh, bbox, 6);
    ctx.save();
    ctx.beginPath(); ctx.rect(x0, y0, bw, bh); ctx.clip();
    ctx.fillStyle = 'rgba(8,11,18,.92)'; ctx.fillRect(x0, y0, bw, bh);
    const key = 'i' + bw + 'x' + bh;
    if (!state.paths.has(key)) state.paths.set(key, buildPath(fit));
    const path = state.paths.get(key);
    ctx.save(); ctx.clip(path);
    ctx.filter = 'blur(3px)';
    const ip = ((payload.sources || {})[sid] || {}).probs || [];
    if (ip.length) {
      ip.forEach((p, i) => {
        if (p === null) return;
        const lon = NAT.bbox[0] + (i % payload.nx) * (payload.step || NAT.step);
        const lat = NAT.bbox[1] + Math.floor(i / payload.nx) * (payload.step || NAT.step);
        if (lat > 25 || lon < 105 || lon > 124) return;
        const [x, y] = fit.proj(lon, lat);
        ctx.fillStyle = ramp(p);
        ctx.beginPath(); ctx.arc(x0 + x, y0 + y, 7, 0, Math.PI * 2); ctx.fill();
      });
    }
    ctx.restore();
    ctx.filter = 'none';
    ctx.save(); ctx.translate(x0, y0);
    ctx.strokeStyle = 'rgba(255,225,190,.35)'; ctx.lineWidth = 0.8;
    ctx.stroke(path);
    ctx.restore();
    ctx.restore();
    ctx.strokeStyle = 'rgba(255,255,255,.18)';
    ctx.lineWidth = 1; ctx.strokeRect(x0, y0, bw, bh);
    ctx.fillStyle = 'rgba(255,255,255,.55)';
    ctx.font = '9px -apple-system, "PingFang SC", sans-serif';
    ctx.fillText('南海诸岛', x0 + 5, y0 + 12);
  }

  /* ---------- local ---------- */
  function drawLocal(data, sid) {
    const src = (data.sources || {})[sid] || { probs: [] };
    const probs = src.probs || [], grades = src.grades || [];
    const failed = (data.failed || []).includes(sid);
    const cv = $('#mapLocal');
    const { ctx, w, h } = fitCanvas(cv);
    const bb = data.bbox;
    const latMid = (bb[1] + bb[3]) / 2;
    const kx = Math.cos(latMid * R);
    const pad = 34;
    const spanX = (bb[2] - bb[0]) * kx, spanY = bb[3] - bb[1];
    const s = Math.min((w - 2 * pad) / spanX, (h - 2 * pad) / spanY);
    const dx = (w - spanX * s) / 2, dy = (h - spanY * s) / 2;
    const proj = (lon, lat) => [dx + (lon - bb[0]) * kx * s, dy + (bb[3] - lat) * s];

    ctx.clearRect(0, 0, w, h);

    // heat field first, reference lines on top
    const cw = data.step * kx * s;
    const pts = [];
    ctx.filter = 'blur(6px)';
    probs.forEach((p, i) => {
      if (p === null || p === undefined) return;
      const ix = i % data.nx, iy = Math.floor(i / data.nx);
      const lon = bb[0] + ix * data.step;
      const lat = bb[1] + iy * data.step;
      const [x, y] = proj(lon, lat);
      ctx.fillStyle = ramp(p);
      ctx.beginPath(); ctx.arc(x, y, cw * 0.85, 0, Math.PI * 2); ctx.fill();
      pts.push({ x, y, p, g: grades[i], lat, lon });
    });
    ctx.filter = 'none';

    // feather the field edges so the geography underneath remains visible
    const gx = dx + spanX * s / 2, gy = dy + spanY * s / 2;
    const rg = ctx.createRadialGradient(gx, gy, Math.min(spanX, spanY) * s * 0.28,
                                        gx, gy, Math.max(spanX, spanY) * s * 0.62);
    rg.addColorStop(0, 'rgba(0,0,0,0)');
    rg.addColorStop(0.55, 'rgba(0,0,0,.35)');
    rg.addColorStop(1, 'rgba(0,0,0,1)');
    ctx.globalCompositeOperation = 'destination-out';
    ctx.fillStyle = rg;
    ctx.fillRect(0, 0, w, h);
    ctx.globalCompositeOperation = 'source-over';

    // graticule
    ctx.strokeStyle = 'rgba(255,255,255,.10)';
    ctx.lineWidth = 1;
    for (let lon = Math.ceil(bb[0] * 2) / 2; lon <= bb[2]; lon += 0.5) {
      const a = proj(lon, bb[1]), b = proj(lon, bb[3]);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.30)';
      ctx.font = '8.5px ui-monospace, monospace';
      ctx.fillText(lon.toFixed(1) + '°E', a[0] - 14, h - 7);
    }
    for (let lat = Math.ceil(bb[1] * 2) / 2; lat <= bb[3]; lat += 0.5) {
      const a = proj(bb[0], lat), b = proj(bb[2], lat);
      ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.30)';
      ctx.font = '8.5px ui-monospace, monospace';
      ctx.fillText(lat.toFixed(1) + '°N', 4, a[1] + 3);
    }

    // province boundaries for geographic context
    if (state.china) {
      const pp = buildPath({ proj });
      ctx.save();
      ctx.strokeStyle = 'rgba(255,238,215,.40)';
      ctx.lineWidth = 1;
      ctx.stroke(pp);
      ctx.restore();
    }

    // distance rings
    const kmPerPx0 = 111.32 / s;
    [25, 50, 100].forEach(km => {
      const [cx0, cy0] = proj(data.center[1], data.center[0]);
      ctx.strokeStyle = 'rgba(255,255,255,.10)';
      ctx.setLineDash([3, 5]);
      ctx.beginPath(); ctx.arc(cx0, cy0, km / kmPerPx0, 0, Math.PI * 2); ctx.stroke();
      ctx.setLineDash([]);
    });

    // contour-ish border
    ctx.strokeStyle = 'rgba(255,255,255,.08)';
    ctx.strokeRect(dx, dy, spanX * s, spanY * s);

    ctx.fillStyle = 'rgba(255,255,255,.45)';
    ctx.font = '9.5px -apple-system, "PingFang SC", sans-serif';
    ctx.fillText('0.25° 网格 · 点击地图可切换位置', 12, 18);

    // centre marker
    const [cx, cy] = proj(data.center[1], data.center[0]);
    ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = 1.3;
    ctx.beginPath(); ctx.arc(cx, cy, 5, 0, Math.PI * 2); ctx.stroke();
    ctx.beginPath(); ctx.moveTo(cx - 11, cy); ctx.lineTo(cx - 6, cy);
    ctx.moveTo(cx + 6, cy); ctx.lineTo(cx + 11, cy);
    ctx.moveTo(cx, cy - 11); ctx.lineTo(cx, cy - 6);
    ctx.moveTo(cx, cy + 6); ctx.lineTo(cx, cy + 11); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.55)';
    ctx.font = '9px ui-monospace, monospace';
    ctx.fillText(`${data.center[0].toFixed(3)}N ${data.center[1].toFixed(3)}E`, cx + 12, cy - 8);

    // scale bar ~50 km
    const kmPerPx = 111.32 / s;
    let barKm = 50, barPx = barKm / kmPerPx;
    if (barPx > 160) { barKm = 100; barPx = barKm / kmPerPx; }
    if (barPx < 40) { barKm = 25; barPx = barKm / kmPerPx; }
    ctx.strokeStyle = 'rgba(255,255,255,.45)'; ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(w - 24 - barPx, h - 22); ctx.lineTo(w - 24, h - 22);
    ctx.moveTo(w - 24 - barPx, h - 26); ctx.lineTo(w - 24 - barPx, h - 18);
    ctx.moveTo(w - 24, h - 26); ctx.lineTo(w - 24, h - 18); ctx.stroke();
    ctx.fillStyle = 'rgba(255,255,255,.45)';
    ctx.font = '9px ui-monospace, monospace';
    ctx.fillText(barKm + ' km', w - 24 - barPx, h - 30);

    state.locPts = pts;
    state.locProj = proj;
    state.locData = data;
    if (failed) showFail('#stateLocal', true); else showFail('#stateLocal', false);
  }

  /* ---------- interaction ---------- */
  function bindHover(cvId, tipId, getPts, getFit) {
    const cv = $(cvId), tip = $(tipId);
    cv.addEventListener('mousemove', e => {
      const pts = getPts();
      if (!pts || !pts.length) return;
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      let best = null, bd = 22;
      for (const p of pts) {
        const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      if (!best) { tip.hidden = true; return; }
      const grade = ['不烧', '微烧', '小烧', '小到中烧', '中到大烧', '大烧'][best.g] || '';
      tip.innerHTML = `<b>${Math.round(best.p * 100)}%</b> · ${grade} · ${best.lat.toFixed(2)}N ${best.lon.toFixed(2)}E`;
      tip.hidden = false;
      const tw = tip.offsetWidth, th = tip.offsetHeight;
      tip.style.left = Math.min(r.width - tw - 6, mx + 14) + 'px';
      tip.style.top = Math.max(4, my - th - 10) + 'px';
    });
    cv.addEventListener('mouseleave', () => { tip.hidden = true; });
    cv.addEventListener('click', e => {
      const pts = getPts();
      if (!pts || !pts.length) return;
      const r = cv.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      let best = null, bd = 30;
      for (const p of pts) {
        const d = (p.x - mx) ** 2 + (p.y - my) ** 2;
        if (d < bd) { bd = d; best = p; }
      }
      if (best) window.dispatchEvent(new CustomEvent('skyglow:pick', { detail: { lat: +best.lat.toFixed(3), lon: +best.lon.toFixed(3) } }));
    });
  }

  /* ---------- failure overlay ---------- */
  function showFail(sel, on) {
    const el = $(sel);
    if (!on) { el.hidden = true; el.classList.remove('err'); return; }
    el.hidden = false;
    el.classList.add('err');
    el.textContent = '数据获取失败';
  }

  /* ---------- on-demand maps ---------- */
  function setVis(which, on) {
    if (which === 'nat') {
      $('#wrapNational').classList.toggle('collapsed', !on);
      $('#genNational').textContent = on ? '隐藏全国图' : '生成全国图';
      state.natShown = on;
    } else {
      $('#wrapLocal').classList.toggle('collapsed', !on);
      $('#genLocal').textContent = on ? '隐藏城市图' : '生成城市图';
      state.locShown = on;
    }
  }

  async function ensureLocal() {
    const m = state.meta;
    if (!m) return null;
    const key = `${m.date}|${m.mode}|${m.lat.toFixed(3)}|${m.lon.toFixed(3)}`;
    if (state.loc && state.locKey === key) return state.loc;
    setVis('loc', true);
    const el = $('#stateLocal');
    el.hidden = false; el.classList.remove('err');
    el.textContent = '正在计算城市格点（约 15–25 秒）…';
    $('#genLocal').disabled = true;
    try {
      const r = await fetch(`/api/glowmap?date=${m.date}&mode=${m.mode}`
        + `&scope=local&lat=${m.lat}&lon=${m.lon}`);
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      state.loc = j.local; state.locKey = key;
      drawLocal(state.loc, state.src);
      paintLocalMeta();
      el.hidden = true;
      return state.loc;
    } catch (e) {
      el.hidden = false; el.classList.add('err');
      el.textContent = '城市图生成失败：' + e.message;
      return null;
    } finally {
      $('#genLocal').disabled = false;
    }
  }

  async function ensureNational() {
    const m = state.meta;
    if (!m) return null;
    const key = `${m.date}|${m.mode}`;
    if (state.nat && state.natKey === key) return state.nat;
    setVis('nat', true);
    const el = $('#stateNational');
    el.hidden = false; el.classList.remove('err');
    el.textContent = '正在计算全国格点（约 40–60 秒）…';
    $('#genNational').disabled = true;
    try {
      const r = await fetch(`/api/glowmap?date=${m.date}&mode=${m.mode}`
        + `&scope=national&lat=${m.lat}&lon=${m.lon}`);
      const j = await r.json();
      if (j.error) throw new Error(j.error);
      state.nat = j.national; state.natKey = key;
      drawNational(state.nat, state.src);
      paintNatMeta();
      el.hidden = true;
      return state.nat;
    } catch (e) {
      el.hidden = false; el.classList.add('err');
      el.textContent = '全国图生成失败：' + e.message;
      return null;
    } finally {
      $('#genNational').disabled = false;
    }
  }

  function paintNatMeta() {
    if (!state.nat) return;
    const meta = state.nat.sources_meta || [];
    const label = (meta.find(m => m.id === state.src) || {}).label || state.src;
    const arr = ((state.nat.sources || {})[state.src] || {}).probs || [];
    const good = arr.filter(x => x !== null);
    const failed = (state.nat.failed || []).includes(state.src);
    const peak = good.length ? Math.max(...good) : null;
    $('#mapNatMeta').textContent = failed
      ? `${label} · 数据获取失败`
      : `${state.nat.date} · ${label} 峰值 ${Math.round(peak * 100)}% · ${state.nat.nx}×${state.nat.ny} 格点`;
  }

  function paintLocalMeta() {
    if (!state.loc) return;
    const meta = state.loc.sources_meta || [];
    const label = (meta.find(m => m.id === state.src) || {}).label || state.src;
    const arr = ((state.loc.sources || {})[state.src] || {}).probs || [];
    const ok = arr.filter(x => x !== null);
    $('#mapLocalMeta').textContent = (state.loc.failed || []).includes(state.src)
      ? `${label} · 数据获取失败`
      : label + ' · ' + state.loc.step + '° 网格 · 本地 '
        + Math.round(Math.min(...ok) * 100) + '–' + Math.round(Math.max(...ok) * 100) + '%';
  }

  /* ---------- data: no map fetch on a normal query ---------- */
  async function load(meta) {
    state.meta = meta;
    state.src = meta.map || state.src;
    await loadChina();

    const key = `${meta.date}|${meta.mode}|${meta.lat.toFixed(3)}|${meta.lon.toFixed(3)}`;
    const natKey = `${meta.date}|${meta.mode}`;

    if (state.loc && state.locKey === key) {
      drawLocal(state.loc, state.src);
      paintLocalMeta();
    } else if (state.locShown) {
      setVis('loc', false);
      $('#mapLocalMeta').textContent = '位置、日期或朝晚已变更，请重新生成';
    }
    if (state.nat && state.natKey === natKey) {
      drawNational(state.nat, state.src);
      paintNatMeta();
    } else if (state.natShown) {
      setVis('nat', false);
      $('#mapNatMeta').textContent = '日期或朝晚已变更，请重新生成';
    }
  }

  window.addEventListener('skyglow:data', e => load(e.detail));
  window.addEventListener('resize', () => {
    if (state.nat && state.natShown) drawNational(state.nat, state.src);
    if (state.loc && state.locShown) drawLocal(state.loc, state.src);
  });
  document.addEventListener('click', e => {
    const id = e.target && e.target.id;
    if (id === 'genNational') {
      const key = state.meta ? `${state.meta.date}|${state.meta.mode}` : null;
      if (state.natShown && state.nat && state.natKey === key) setVis('nat', false);
      else ensureNational();
    }
    if (id === 'genLocal') {
      const m = state.meta;
      const key = m ? `${m.date}|${m.mode}|${m.lat.toFixed(3)}|${m.lon.toFixed(3)}` : null;
      if (state.locShown && state.loc && state.locKey === key) setVis('loc', false);
      else ensureLocal();
    }
  });
  bindHover('#mapNational', '#tipNational', () => state.natPts, () => state.natFit);
  bindHover('#mapLocal', '#tipLocal', () => state.locPts, () => state.locProj);
})();
