/* Skyglow front-end — vanilla JS, no build step.
   All indicators are computed for four data sources; each analytical panel has its
   own source switcher, so the same instant can be compared model by model. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const state = {
    lat: 31.2304, lon: 121.4737, name: '上海', date: '', mode: 'sunset',
    src: 'openmeteo', geo: 'openmeteo', map: 'openmeteo',
    data: null, week: null, val: null, meta: null, dirCfg: null, dirReady: {},
  };

  const DIR16 = ["北", "北东北", "东北", "东东北", "东", "东东南", "东南", "南东南",
    "南", "南西南", "西南", "西西南", "西", "西西北", "西北", "北西北"];
  const FAIL = '数据获取失败';
  const fmtPct = v => Math.round(v * 100);
  const hhmm = iso => (iso || '').slice(11, 16);
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));

  function addMin(t, d) {
    const [h, m] = t.split(':').map(Number);
    const x = (h * 60 + m + d + 1440) % 1440;
    return String(Math.floor(x / 60)).padStart(2, '0') + ':' + String(x % 60).padStart(2, '0');
  }
  function todayStr(off = 0) {
    const d = new Date();
    d.setDate(d.getDate() + off);
    const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  }
  const weekday = ds => '周' + '日一二三四五六'[new Date(ds + 'T12:00:00').getDay()];

  async function api(path) {
    const r = await fetch(path);
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || ('HTTP ' + r.status));
    return j;
  }
  function weekLoading(msg) {
    const el = $('#weekGrid');
    if (el) el.innerHTML = `<div class="wk-loading"><i class="spin2"></i>${msg}</div>`;
  }

  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg; el.hidden = false;
    clearTimeout(el._t); el._t = setTimeout(() => { el.hidden = true; }, 4200);
  }
  const busy = on => { $('#loading').hidden = !on; };

  /* 参数改动只做标记，不触发取数：取数只发生在点「开始预测」/ 回车提交时 */
  function markDirty() {
    const b = $('#go');
    if (b) b.classList.add('dirty');
  }
  function clearDirty() {
    const b = $('#go');
    if (b) b.classList.remove('dirty');
  }

  /* ---------------- location ---------------- */
  let sugTimer = null;
  let sugItems = [];

  function applyPlace(p, silent) {
    state.lat = +p.lat;
    state.lon = +p.lon;
    state.name = p.name;
    if ($('#inLat')) $('#inLat').value = state.lat.toFixed(4);
    if ($('#inLon')) $('#inLon').value = state.lon.toFixed(4);
    if ($('#inName')) $('#inName').value = '';
    $('#loc').value = p.name;
    $('#suggest').hidden = true;
    if (!silent) markDirty();
  }

  function renderSuggest(results) {
    const box = $('#suggest');
    sugItems = results;
    if (!results.length) {
      box.hidden = true;
      return;
    }
    box.innerHTML = results.map((r, i) => {
      const admin = [r.admin, r.country].filter(Boolean).join(' · ');
      const c = r.lat.toFixed(2) + ', ' + r.lon.toFixed(2);
      return `<button data-i="${i}">
        <span>${r.name}</span>
        <span class="s-admin">${admin}</span>
        <span class="s-coord">${c}</span></button>`;
    }).join('');
    box.hidden = false;
    $$('#suggest button').forEach(btn => {
      btn.onmousedown = ev => ev.preventDefault();   // keep focus, avoid blur race
      btn.onclick = () => applyPlace(sugItems[+btn.dataset.i]);
    });
  }

  async function fetchPlaces(q) {
    const { results } = await api('/api/geocode?q=' + encodeURIComponent(q));
    return results || [];
  }

  async function searchLoc(q) {
    if (!q || !q.trim()) { $('#suggest').hidden = true; return []; }
    try {
      const results = await fetchPlaces(q.trim());
      renderSuggest(results);
      return results;
    } catch (e) {
      $('#suggest').hidden = true;
      return [];
    }
  }

  /* ---- custom coordinate panel ---- */
  function openCoordPanel(on) {
    const panel = $('#coordPanel');
    if (!panel) return;
    panel.hidden = !on;
    $('#coordToggle').textContent = on ? '自定义经纬度 ▴' : '自定义经纬度 ▾';
    if (on) {
      if (!$('#inLat').value && state.lat != null) $('#inLat').value = state.lat.toFixed(4);
      if (!$('#inLon').value && state.lon != null) $('#inLon').value = state.lon.toFixed(4);
      $('#inLat').focus();
    }
  }

  function coordHint(msg, isErr) {
    const el = $('#coordHint');
    el.textContent = msg || '范围：纬度 −90～90，经度 −180～180。填入后会用该点重算全部指标与地图。';
    el.classList.toggle('err', !!isErr);
  }

  /** Validate the two numeric fields; returns {lat, lon} or null (and explains why). */
  function readCoordInput() {
    const rawLat = ($('#inLat').value || '').trim();
    const rawLon = ($('#inLon').value || '').trim();
    if (!rawLat || !rawLon) { coordHint('请同时填写纬度与经度', true); return null; }
    const lat = Number(rawLat);
    const lon = Number(rawLon);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      coordHint('经纬度必须是数字', true); return null;
    }
    if (lat < -90 || lat > 90) { coordHint('纬度需在 −90 到 90 之间', true); return null; }
    if (lon < -180 || lon > 180) { coordHint('经度需在 −180 到 180 之间', true); return null; }
    return { lat, lon };
  }

  function applyCoord() {
    const v = readCoordInput();
    if (!v) return false;
    const note = ($('#inName').value || '').trim();
    state.lat = +v.lat.toFixed(4);
    state.lon = +v.lon.toFixed(4);
    state.name = note || `${state.lat.toFixed(3)},${state.lon.toFixed(3)}`;
    $('#loc').value = state.name;
    $('#suggest').hidden = true;
    coordHint(`${state.lat.toFixed(4)}°N  ${state.lon.toFixed(4)}°E 已应用`, false);
    return true;
  }

  /** Turn whatever is in the box into state.lat/lon. Returns true on success. */
  async function resolveInput() {
    const txt = ($('#loc').value || '').trim();
    if (!txt) { toast('请输入地点'); return false; }
    if (txt === state.name && state.lat != null) return true;

    const m = txt.match(/^\s*(-?\d+(?:\.\d+)?)\s*[,，]\s*(-?\d+(?:\.\d+)?)\s*$/);
    if (m) {
      state.lat = +m[1]; state.lon = +m[2];
      state.name = `${(+m[1]).toFixed(2)},${(+m[2]).toFixed(2)}`;
      $('#suggest').hidden = true;
      return true;
    }
    busy(true);
    try {
      const results = await fetchPlaces(txt);
      if (!results.length) {
        toast(`未找到「${txt}」，试试更常见的名称或直接输入经纬度`);
        return false;
      }
      applyPlace(results[0], true);
      return true;
    } catch (e) {
      toast('地点解析失败：' + e.message);
      return false;
    } finally { busy(false); }
  }

  /* ---------------- data ---------------- */
  async function run(force) {
    clearDirty();
    if (!force) busy(true);
    weekLoading('正在计算七日趋势…');
    try {
      const q = `lat=${state.lat}&lon=${state.lon}&date=${state.date}`
        + `&name=${encodeURIComponent(state.name)}` + (force ? '&refresh=1' : '');
      const data = await api('/api/predict?' + q);
      state.data = data;
      state.meta = data.sources_meta;
      render(data);
      history.replaceState(null, '', `?lat=${state.lat.toFixed(4)}&lon=${state.lon.toFixed(4)}&name=${encodeURIComponent(state.name)}&date=${state.date}&mode=${state.mode}`);
      if (!force) loadWeek(); else loadWeek(true);
    } catch (e) {
      toast(e.message || '计算失败，请稍后重试');
    } finally { busy(false); }
  }

  async function loadWeek(force) {
    weekLoading('正在计算七日趋势…');
    try {
      const w = await api(`/api/outlook?lat=${state.lat}&lon=${state.lon}&days=7`
        + (force ? '&refresh=1' : ''));
      state.week = w; renderWeek(w);
    } catch (e) { $('#weekGrid').innerHTML = '<div class="empty">七日趋势暂不可用</div>'; }
  }

  /* ---------------- source switchers ---------------- */
  function buildSwitch(el, meta, current, onPick) {
    el.innerHTML = meta.map(m => {
      const on = m.id === current ? ' on' : '';
      return `<button class="${on}" data-src="${m.id}" title="${m.label}">
        <i style="background:${m.color}"></i>${m.short}</button>`;
    }).join('');
    $$('button', el).forEach(b => b.onclick = () => {
      $$('button', el).forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      onPick(b.dataset.src);
    });
  }

  function pickSource(ev, want) {
    const S = ev.sources || {};
    if (S[want] && !S[want].error) return want;
    const first = Object.keys(S).find(k => !S[k].error);
    return first || want;
  }

  /* ---------------- render ---------------- */
  function render(data) {
    const ev = data.results[state.mode];
    if (!ev) { toast('该日期无有效的日出/日落时刻'); return; }
    const meta = data.sources_meta;
    if (data.direct) { state.dirCfg = data.direct; state.dirReady = data.direct_ready || {}; }
    paintDirect();
    state.meta = meta;
    const sid = pickSource(ev, state.src);
    state.src = sid;
    const r = ev.sources[sid];
    const failedAll = !r || r.error;

    $('#heroEyebrow').textContent = state.mode === 'sunset' ? '晚霞 · SUNSET' : '朝霞 · SUNRISE';
    $('#heroPlace').textContent = data.location.name;
    $('#heroDate').textContent = data.date.replace(/-/g, '.') + ' ' + weekday(data.date);
    $('#heroTime').textContent = (state.mode === 'sunset' ? '日落 ' : '日出 ') + ev.time_label;

    /* four-source chips */
    $('#srcChips').innerHTML = meta.map(m => {
      const s = ev.sources[m.id];
      const err = !s || s.error;
      const on = m.id === sid ? ' on' : '';
      const val = err ? FAIL : `${fmtPct(s.probability)}%`;
      const badge = (!err && s.origin === 'direct')
        ? '<span class="badge-dir">直连</span>' : '';
      const sub = err ? ' —' : ` ${s.grade_label}${badge}`;
      const tip = err ? (s.detail || '') : (s.degraded || '');
      return `<button class="src-chip${on}${err ? ' err' : ''}" data-src="${m.id}" title="${tip}">
        <i style="background:${m.color}"></i>
        <span class="sc-name">${m.label}</span>
        <span class="sc-val">${val}</span>
        <span class="sc-sub">${sub}</span></button>`;
    }).join('');
    $$('#srcChips .src-chip').forEach(b => b.onclick = () => {
      state.src = b.dataset.src;
      render(data);
    });

    if (failedAll) {
      $('#conf').innerHTML = `置信度 <b>—</b>`;
      $('#probNum').textContent = '—';
      $('#gradeChip').textContent = FAIL;
      $('#probNote').textContent = '该数据源在此日期无分层云量数据';
      ['#stWindow', '#stPeak', '#stDeck', '#stColour'].forEach(k => $(k).textContent = FAIL);
      $('#factors').innerHTML = `<div class="empty" style="padding:8px 0 16px">${FAIL}</div>`;
      $('#decks').innerHTML = '';
      $('#reasons').innerHTML = '';
      $('#risks').innerHTML = '';
      $('#chartFoot').textContent = FAIL;
      drawTimeline(null, null);
      dispatchAll(data, ev);
      return;
    }

    $('#conf').innerHTML = `置信度 <b>${confLabel(r.confidence)}</b>`;
    const cons = ev.consensus;
    const range = cons && cons.n > 1
      ? ` · 四源 ${fmtPct(cons.min)}–${fmtPct(cons.max)}%` : '';
    $('#probNote').textContent = (r.grade > 0 ? `若成霞，预计${r.grade_label}` : '预计不成规模') + range;
    $('#gradeChip').textContent = chanceLabel(r.probability);
    $('#gradeChip').style.color = r.grade_color;
    $('#gradeChip').style.borderColor = r.grade_color + '66';
    $('#gradeChip').style.background = r.grade_color + '18';
    countTo($('#probNum'), fmtPct(r.probability));

    $('#stWindow').textContent = r.window ? `${hhmm(r.window.start)}–${hhmm(r.window.end)}` : '无有效窗口';
    $('#stPeak').textContent = r.window ? addMin(hhmm(r.window.start), r.window.peak_min - r.window.start_min) : '—';
    const dk = r.decks.filter(d => d.canvas > 0.05).sort((a, b) => b.canvas - a.canvas)[0];
    $('#stDeck').textContent = dk ? `${dk.label} ${dk.base_km.toFixed(1)} km` : '无云';
    $('#stSwatch').style.background = r.colour.hex;
    $('#stColour').textContent = r.colour.name;

    $('#skyLabel').textContent = state.mode === 'sunset' ? '日落后' : '日出前';
    $('#skyAz').textContent = `方位 ${ev.azimuth.toFixed(0)}°`;
    $('#skyElev').textContent = `太阳高度 ${ev.sun_elev.toFixed(1)}°`;
    $('#skyLegend').innerHTML = r.decks.map(d =>
      `<span><i style="background:${rgbCss(d.rgb)}"></i>${d.label} ${Math.round(d.cover)}%</span>`).join('');
    drawSky(r);

    /* factors: four sources side by side */
    const defs = [
      ['云幕覆盖', s => s.features.canvas * 100,
        s => `高 ${Math.round(s.context.cloud_high)} · 中 ${Math.round(s.context.cloud_mid)}`],
      ['受光强度', s => clamp(s.features.light / 0.4, 0, 1) * 100,
        s => s.features.light.toFixed(3)],
      ['光路通畅', s => s.features.horizon * 100,
        s => `上游低云 ${Math.round(s.context.cloud_low_up)}%`],
      ['大气通透', s => s.features.purity * 100,
        s => `RH ${Math.round(s.context.rh)}%`],
      ['气溶胶', s => clamp((0.6 - s.features.aod) / 0.6, 0, 1) * 100,
        s => 'AOD ' + s.features.aod.toFixed(2)],
      ['降水抑制', s => (s.features.precip > 0.05 ? 20 : 100),
        s => s.features.precip.toFixed(2) + ' mm'],
    ];
    $('#factors').innerHTML = defs.map(([name, get, note]) => {
      const cells = meta.map(m => {
        const s = ev.sources[m.id];
        if (!s || s.error) {
          return `<div class="fx"><div class="fx-top"><em>${m.short}</em><b class="err">${FAIL}</b></div>
            <div class="bar low"><i style="width:0"></i></div></div>`;
        }
        const v = clamp(get(s), 0, 100);
        return `<div class="fx"><div class="fx-top"><em>${m.short}</em><b>${Math.round(v)}</b></div>
          <div class="bar ${v < 35 ? 'low' : ''}"><i style="width:${Math.max(3, v)}%;background:${m.color}"></i></div>
          <div class="fx-note">${note(s)}</div></div>`;
      }).join('');
      return `<div class="factor-row"><div class="f-name">${name}</div><div class="fx-grid">${cells}</div></div>`;
    }).join('');

    $('#deckSrcNote').textContent = `${labelOf(meta, sid)} · 受光高度与亮度`;
    $('#decks').innerHTML = r.decks.map(d => `
      <div class="deck-block">
        <div class="deck">
          <span class="d-name">${d.label}</span>
          <div class="d-bar"><i style="width:${d.cover < 0.5 ? 0 : clamp(d.cover, 1, 100)}%;background:${rgbCss(d.rgb)};
            box-shadow:0 0 18px ${rgbCss(d.rgb)}55"></i></div>
          <span class="d-meta">${d.cover.toFixed(0)}%</span>
        </div>
        <div class="deck-note">云底 ${d.base_km.toFixed(1)} km · ${d.lit ? '受光' : '已入影'} · 相对亮度 ${d.luminance.toFixed(3)}</div>
      </div>`).join('');
    $('#risks').innerHTML = r.risks.length
      ? `<h4>可能的翻车方式（${labelOf(meta, sid)}）</h4><ul>${r.risks.map(x => `<li>${x}</li>`).join('')}</ul>` : '';

    const chips = r.reasons.map(x => `<span class="${x.k}">${x.t}</span>`);
    const aux = ev.sources && ev.sources[state.src] && ev.sources[state.src].aux_check;
    if (aux) {
      const gap = aux.cloud_gap == null ? '—' : aux.cloud_gap + 'pp';
      chips.push(`<span>校验 · WeatherAPI 总云量 ${aux.cloud_aux ?? '—'}%`
        + `（我们 ${aux.cloud_ours}%，差 ${gap}）· 能见度 ${aux.vis_aux_km ?? '—'} km`
        + ` · 降水概率 ${aux.rain_chance ?? '—'}%</span>`);
    } else if (data.crosscheck) {
      chips.push(`<span>校验 · WeatherAPI 总云量 ${data.crosscheck.cloud_total ?? '—'}%`
        + `（${data.crosscheck.condition || '—'}）</span>`);
    }
    $('#reasons').innerHTML = chips.join('');

    buildSwitch($('#srcTimeline'), meta, state.src, id => { state.src = id; render(state.data); });
    $('#chartFoot').textContent =
      `横轴为相对${state.mode === 'sunset' ? '日落' : '日出'}时刻的分钟数；曲线为综合可观赏度，阴影为最佳窗口。`
      + `当前：${labelOf(meta, sid)}（${meta.find(m => m.id === sid).tag}）。`;

    drawTimeline(r.timeline.concat ? r.timeline : [], r.window);
    renderCross(ev, sid);
    dispatchAll(data, ev);

    $('#footMeta').textContent = `数据：${data.source} · 四源并列 · 标签：sunsetbot 再分析记录 · 模型 ${data.model_version}`;
    $$('.wday').forEach(el => el.classList.toggle('on', el.dataset.date === state.date));
    paintCache(data.cache);
    $('#heroCoord').textContent =
      `${data.location.lat.toFixed(4)}°N  ${data.location.lon.toFixed(4)}°E`
      + (data.location.elevation != null ? `  ·  海拔 ${Math.round(data.location.elevation)} m` : '');
  }

  const labelOf = (meta, id) => {
    const m = (meta || []).find(x => x.id === id);
    return m ? `${m.label} ${m.tag}` : id;
  };

  function dispatchAll(data, ev) {
    const detail = {
      date: data.date, mode: state.mode, lat: data.location.lat, lon: data.location.lon,
      name: data.location.name, event: ev, meta: data.sources_meta,
      src: state.src, geo: state.geo, map: state.map,
    };
    window.dispatchEvent(new CustomEvent('skyglow:data', { detail }));

    const geoEl = $('#srcGeo'), mapEl = $('#srcMap');
    if (!$('#srcGeo').dataset.built) {
      buildSwitch(geoEl, data.sources_meta, state.geo, id => {
        state.geo = id; render(state.data);
      });
      buildSwitch(mapEl, data.sources_meta, state.map, id => {
        state.map = id; render(state.data);
      });
      geoEl.dataset.built = '1'; mapEl.dataset.built = '1';
    }
    $$('button', geoEl).forEach(b => b.classList.toggle('on', b.dataset.src === state.geo));
    $$('button', mapEl).forEach(b => b.classList.toggle('on', b.dataset.src === state.map));
  }

  const DIR_INFO = {
    ecmwf: { name: 'ECMWF IFS', color: '#6f8fb8',
      desc: '0p25 业务场，仅发布总云量 tcc；分层云量由 850/500/300 hPa 相对湿度诊断' },
    gfs: { name: 'GFS', color: '#5ad19a',
      desc: 'NOAA NOMADS，自带 LCDC/MCDC/HCDC，0.25° 规则网格，按字节区间取三条消息' },
    icon: { name: 'ICON', color: '#c58ad6',
      desc: 'DWD opendata，CLCL/CLCM/CLCH，全球二十面体网格，需 13 MB 下载' },
  };

  function paintDirect() {
    const cfg = state.dirCfg || { auto: true, sources: { ecmwf: true, gfs: true, icon: false } };
    const ready = state.dirReady || {};
    const auto = $('#dirAuto');
    if (auto) auto.checked = !!cfg.auto;
    const grid = $('#dirGrid');
    if (!grid) return;
    grid.innerHTML = Object.keys(DIR_INFO).map(id => {
      const info = DIR_INFO[id];
      const on = !!cfg.sources[id];
      const rd = ready[id] !== false;
      const why = rd ? '可解码' : (state.dirEnv && state.dirEnv.eccodes === false
        ? '缺 eccodes' : '缺 numpy');
      return `<div class="dir-card">
        <div class="dh"><i style="background:${info.color}"></i>${info.name}
          <b class="${rd ? 'ok' : 'no'}" title="${rd ? 'GRIB2 解码器已就绪' : '运行 setup.bat / ./setup.sh 安装解码器'}">${why}</b>
          <label class="sw" style="margin-left:6px"><input type="checkbox" data-dir="${id}"
            ${on ? 'checked' : ''}><span></span></label>
        </div>
        <div class="dd">${info.desc}</div>
      </div>`;
    }).join('');
    $$('#dirGrid input[data-dir]').forEach(el => el.onchange = () => saveDirect());
    if (auto) auto.onchange = () => saveDirect();
    const note = $('#dirNote');
    if (note) {
      const env = state.dirEnv || {};
      const missing = [];
      if (env.eccodes === false) missing.push('eccodes');
      if (env.numpy === false) missing.push('numpy');
      const base = cfg.auto
        ? '已开启：Open-Meteo 某源无数据或整体不可用时，按上方开关尝试官方直连；直连需下载数 MB GRIB2，首次约 1–3 分钟，之后按 30 分钟缓存。'
        : '已关闭自动直连：某源失败时直接显示“数据获取失败”，不尝试官方档案。';
      note.textContent = missing.length
        ? `${base} 当前解释器缺少 ${missing.join(' / ')}，直连不可用。运行 setup.bat（Windows）或 ./setup.sh 创建带解码器的本地环境，再用 run.bat / ./run.sh 启动。`
        : base;
    }
  }

  async function saveDirect() {
    const auto = $('#dirAuto').checked;
    const src = {};
    $$('#dirGrid input[data-dir]').forEach(el => { src[el.dataset.dir] = el.checked; });
    try {
      const qs = `_method=post&auto=${auto ? 1 : 0}&ecmwf=${src.ecmwf ? 1 : 0}`
        + `&gfs=${src.gfs ? 1 : 0}&icon=${src.icon ? 1 : 0}`;
      const r = await api('/api/direct?' + qs);
      state.dirCfg = r.settings; state.dirReady = r.ready;
      state.dirEnv = { eccodes: r.eccodes !== false, numpy: r.numpy !== false };
      paintDirect();
      toast('直连设置已保存，点「开始预测」后生效');
      markDirty();
    } catch (e) { toast('保存失败：' + e.message); }
  }

  async function loadWeatherApi() {
    try {
      const r = await api('/api/weatherapi');
      fillWa(r);
      const row = $('#waKeyRow');
      if (row) row.hidden = false;
    } catch (e) { /* ignore */ }
  }

  function fillWa(r) {
    if (!r) return;
    const b = $('#waBadge');
    const u = r.usage || {};
    if (b) {
      if (!r.configured) { b.textContent = '未配置'; b.className = 'qw-badge err'; }
      else if (u.last_error) { b.textContent = '失败 · ' + String(u.last_error).slice(0, 24); b.className = 'qw-badge err'; }
      else if (u.last_ok) { b.textContent = '正常 · ' + String(u.last_ok).slice(11, 16); b.className = 'qw-badge ok'; }
      else { b.textContent = '已就绪'; b.className = 'qw-badge'; }
    }
    const el = $('#waUsage');
    if (el) {
      const parts = [
        `今日 ${u.count ?? 0} / ${u.cap ?? '—'} 次（剩余 ${u.remaining ?? '—'}）`,
        `成功 ${u.ok ?? 0} · 失败 ${u.fail ?? 0}`,
        `密钥 ${r.source || '—'}${r.key_hint ? ' ' + r.key_hint : ''}`,
      ];
      if (u.last_error) parts.push('最近错误 ' + String(u.last_error).slice(0, 40));
      el.textContent = parts.join(' · ');
    }
    const cap = $('#waCap');
    if (cap && r.cap) cap.value = r.cap;
  }

  async function loadQWeather() {
    try {
      const r = await api('/api/qweather');
      fillQw(r);
    } catch (e) { /* ignore */ }
  }

  function fillQw(r) {
    if (!r) return;
    const set = (id, v) => { const el = $(id); if (el && v != null) el.value = v; };
    set('#qwHost', r.host);
    set('#qwCap', r.daily_cap);
    set('#qwKid', r.kid);
    set('#qwIss', r.iss);
    set('#qwSub', r.sub);
    const b = $('#qwBadge');
    const u0 = r.usage || {};
    if (b) {
      if (!r.configured) {
        const miss = (r.missing || []).join('、');
        b.textContent = miss ? '缺 ' + miss : '未配置';
        b.className = 'qw-badge err';
      } else if (u0.last_error) {
        b.textContent = '调用失败 · ' + String(u0.last_error).slice(0, 28);
        b.className = 'qw-badge err';
      } else if (u0.last_ok) {
        b.textContent = '正常 · ' + (r.mode === 'jwt' ? 'JWT' : 'API Key')
          + ' · ' + String(u0.last_ok).slice(11, 16);
        b.className = 'qw-badge ok';
      } else {
        b.textContent = '已配置，尚未调用';
        b.className = 'qw-badge';
      }
    }
    const kf = $('#qwKeyFile');
    if (kf) {
      if (r.key_file) {
        kf.className = 'qw-keyfile ok';
        kf.textContent = '私钥：已加载 ' + r.key_file;
      } else {
        kf.className = 'qw-keyfile';
        kf.innerHTML = '私钥：未找到 · 将 Ed25519 私钥保存为 <code>skyglow/qweather-private.pem</code>';
      }
    }
    if (r.usage) {
      const el2 = $('#qwUsage');
      const u = r.usage;
      if (el2) {
        const parts = [
          `今日计费 ${u.count ?? 0} / ${u.cap ?? '—'} 次（剩余 ${u.remaining ?? '—'}）`,
          `成功 ${u.ok ?? 0} · 失败 ${u.fail ?? 0}`,
        ];
        if (u.last_ok) parts.push('最近成功 ' + String(u.last_ok).slice(11, 19));
        if (u.last_error) parts.push('最近错误 ' + String(u.last_error).slice(0, 40));
        el2.textContent = parts.join(' · ');
      }
    }
    $$('#qwMode button').forEach(x => x.classList.toggle('on', x.dataset.mode === (r.mode || 'jwt')));
    syncQwMode(r.mode || 'jwt');
    const u = r.usage || {};
    const el = $('#qwUsage');
    if (el) {
      el.textContent = `今日已用 ${u.count ?? 0} / ${u.cap ?? '—'} 次`
        + ` · 剩余 ${u.remaining ?? '—'} 次 · 每月免费 5 万次`;
    }
  }

  async function loadDirect() {
    try {
      const r = await api('/api/direct');
      state.dirCfg = r.settings; state.dirReady = r.ready;
      state.dirEnv = { eccodes: r.eccodes !== false, numpy: r.numpy !== false };
      paintDirect();
    } catch (e) { /* ignore */ }
  }

  function paintCache(c) {
    const line = $('#cacheLine');
    if (!line) return;
    if (!c) { line.hidden = true; return; }
    line.hidden = false;
    if (c.hit) {
      const mins = Math.max(0, Math.round(c.age / 60));
      const ttl = c.ttl ? `，有效 ${Math.round(c.ttl / 60)} 分钟` : '，永久有效';
      $('#cacheText').textContent = `本地缓存 · ${mins < 1 ? '刚刚写入' : mins + ' 分钟前'}${ttl}`;
      line.classList.remove('fresh');
    } else {
      $('#cacheText').textContent = '刚刚从数据源获取，已写入本地缓存';
      line.classList.add('fresh');
    }
    const relay = state.data && state.data.relay;
    if (relay && relay.down) {
      const mins = Math.max(1, Math.round(relay.retry_in / 60));
      $('#cacheText').textContent += ` · Open-Meteo 配额熔断中，约 ${mins} 分钟后恢复`;
    }
  }

  function chanceLabel(p) {
    if (p >= 0.7) return '高概率成霞';
    if (p >= 0.5) return '很可能成霞';
    if (p >= 0.35) return '较有希望';
    if (p >= 0.2) return '值得一试';
    if (p >= 0.1) return '机会偏小';
    return '机会很小';
  }
  function confLabel(c) {
    if (c >= 0.78) return '高';
    if (c >= 0.6) return '较高';
    if (c >= 0.42) return '中等';
    return '偏低';
  }
  const rgbCss = rgb => `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`;

  function countTo(el, target) {
    const from = +el.textContent || 0;
    const t0 = performance.now();
    const step = t => {
      const k = Math.min(1, (t - t0) / 520);
      el.textContent = Math.round(from + (target - from) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ---------------- cross-check cards ---------------- */
  function renderCross(ev, sid) {
    const meta = state.meta;
    const S = ev.sources;
    const okAll = Object.values(S).filter(s => s && !s.error);
    $('#crossPhys .cc-p').textContent = okAll.length
      ? fmtPct(okAll.reduce((a, b) => a + b.probability, 0) / okAll.length) + '%' : FAIL;
    $('#crossPhys .cc-sub').textContent = okAll.length ? `四源均值 · n=${okAll.length}` : '全部数据源不可用';
    $('#crossPhys .cc-bar i').style.width = okAll.length
      ? fmtPct(okAll.reduce((a, b) => a + b.probability, 0) / okAll.length) + '%' : '0';

    const cur = S[sid] || {};
    const nn = cur.nn;
    $('#crossNN .cc-p').textContent = nn ? fmtPct(nn.p) + '%' : FAIL;
    $('#crossNN .cc-sub').textContent = nn
      ? `${labelOf(meta, sid)} 输入 · ${Math.round(nn.delta * 100) >= 0 ? '高于' : '低于'}物理 ${Math.abs(Math.round(nn.delta * 100))}pp`
      : '未加载模型权重';
    $('#crossNN .cc-bar i').style.width = nn ? fmtPct(nn.p) + '%' : '0';

    $('#ensRows').innerHTML = (meta || []).map(m => {
      const s = S[m.id];
      if (!s || s.error) {
        return `<div class="ens-row"><span>${m.short}</span>
          <div class="bar2"><i style="width:0"></i></div><em class="err">${FAIL}</em></div>`;
      }
      return `<div class="ens-row"><span>${m.short}</span>
        <div class="bar2"><i style="width:${fmtPct(s.probability)}%;background:${m.color}"></i></div>
        <em>${fmtPct(s.probability)}%</em></div>`;
    }).join('');

    const cons = ev.consensus;
    if (!cons || cons.n < 2) {
      $('#agreeCard').innerHTML = `<span class="tag warn">样本不足</span>仅 ${cons ? cons.n : 0} 个数据源可用，无法交叉比对。`;
      return;
    }
    const gap = cons.max - cons.min;
    let tag, txt;
    if (gap <= 0.12) { tag = '<span class="tag">一致</span>'; txt = '四个数据源结论接近，可信度较高。'; }
    else if (gap <= 0.25) { tag = '<span class="tag warn">基本一致</span>'; txt = '存在一定分歧，建议结合临近实况判断。'; }
    else { tag = '<span class="tag warn">分歧较大</span>'; txt = '模式之间差异明显，按保守情形安排。'; }
    const miss = (meta || []).filter(m => !S[m.id] || S[m.id].error).map(m => m.label);
    let extra = '';
    // WeatherAPI is the primary verification source; QWeather only if it is absent.
    const wa = state.data && state.data.weatherapi;
    const qw = state.data && state.data.qweather;
    const waSample = wa && wa.samples ? wa.samples[state.mode] : null;
    const qwSample = qw && qw.samples ? qw.samples[state.mode] : null;
    const ours = Math.max(...Object.values(S).filter(x => x && !x.error)
      .map(x => Math.max(x.context.cloud_low, x.context.cloud_mid, x.context.cloud_high)), 0);

    if (waSample) {
      const q = waSample;
      const bits = [`总云量 ${q.cloud_total ?? '—'}%（我们 ${Math.round(ours)}%）`];
      if (q.visibility_km != null) bits.push(`能见度 ${q.visibility_km} km`);
      if (q.humidity != null) bits.push(`湿度 ${q.humidity}%`);
      if (q.precip_mm != null) bits.push(`降水 ${q.precip_mm} mm`);
      if (q.chance_of_rain != null) bits.push(`降水概率 ${q.chance_of_rain}%`);
      extra = ` WeatherAPI（${q.time ? hhmm(q.time) : '校验'}，${q.condition || '—'}）：`
        + bits.join('、') + '。';
    } else if (wa && wa.configured) {
      extra = wa.error ? ` WeatherAPI 调用失败：${wa.error}` : ' WeatherAPI 暂未返回该时刻数据。';
    } else if (wa) {
      extra = ' WeatherAPI 未启用。';
    }

    if (qwSample) {
      extra += ` 和风天气（备用校验）：总云量 ${qwSample.cloud_total ?? '—'}%、`
        + `能见度 ${qwSample.visibility_km ?? '—'} km。`;
    } else if (qw && qw.configured && qw.error) {
      extra += ` 和风天气调用失败：${qw.error}`;
    }

    $('#agreeCard').innerHTML = `${tag}四源 ${fmtPct(cons.min)}–${fmtPct(cons.max)}%（均值 ${fmtPct(cons.mean)}%，极差 ${(gap * 100).toFixed(0)}pp）。${txt}`
      + (miss.length ? ` ${miss.join('、')}：数据获取失败。` : '') + extra;
  }

  /* ---------------- week ---------------- */
  function renderWeek(w) {
    const days = w.days.slice(0, 8);
    $('#weekGrid').innerHTML = days.map(d => {
      const pick = state.mode === 'sunrise' ? (d.sunrise || d.sunset) : (d.sunset || d.sunrise);
      const p = pick && pick.probability != null ? fmtPct(pick.probability) : null;
      const on = d.date === state.date ? ' on' : '';
      const spread = pick && pick.sources
        ? (() => {
            const ps = Object.values(pick.sources).filter(x => !x.error && x.p != null).map(x => x.p);
            return ps.length > 1 ? Math.round((Math.max(...ps) - Math.min(...ps)) * 100) : null;
          })() : null;
      return `<div class="wday${on}" data-date="${d.date}">
        <div class="wd">${d.weekday}</div>
        <div class="wn">${d.date.slice(5).replace('-', '/')}</div>
        <div class="wp">${p == null ? '<span style="font-size:12px;color:var(--muted)">失败</span>'
          : p + '<span style="font-size:11px;color:var(--muted)">%</span>'}</div>
        <div class="wb"><i class="${p != null && p > 25 ? '' : 'dim'}" style="height:${clamp(p || 3, 3, 100)}%"></i></div>
        <div class="wl">${p == null ? '数据不足' : (spread == null ? '' : '极差 ' + spread + 'pp')}</div>
      </div>`;
    }).join('');
    $$('.wday').forEach(el => el.onclick = () => {
      state.date = el.dataset.date; $('#date').value = state.date; markDirty();
    });
    $('#weekHint').textContent =
      `按${state.mode === 'sunset' ? '日落' : '日出'}时刻 · 主源 Open-Meteo · 点击切换日期`;
  }

  /* ---------------- sky ---------------- */
  let skyRAF = null;
  function fitCanvas(cv) {
    const dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    const ctx = cv.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    return { ctx, w, h };
  }

  function drawSky(r) {
    const cv = $('#sky');
    const { ctx, w, h } = fitCanvas(cv);
    const rgb = r.colour.rgb;
    const lum = clamp(r.colour.luminance / 0.35, 0, 1);
    const horizon = h * 0.62;
    const t0 = performance.now();
    if (skyRAF) cancelAnimationFrame(skyRAF);
    const bands = r.decks.map((d, i) => ({
      y: horizon - [0.58, 0.36, 0.13][i] * h * 0.62,
      cover: d.cover / 100, rgb: d.rgb,
      hh: [0.030, 0.038, 0.032][i] * h + 10,
    }));

    function frame(t) {
      const k = (t - t0) / 1000;
      ctx.clearRect(0, 0, w, h);
      const g = ctx.createLinearGradient(0, 0, 0, h);
      const topR = Math.round(8 + rgb[0] * 0.05);
      const topG = Math.round(12 + rgb[1] * 0.06);
      const topB = Math.round(30 + rgb[2] * 0.10);
      g.addColorStop(0, `rgb(${Math.round(topR * 0.55)},${Math.round(topG * 0.55)},${Math.round(topB * 0.7)})`);
      g.addColorStop(0.45, `rgb(${Math.round(topR + 18 * lum)},${Math.round(topG + 10 * lum)},${Math.round(topB + 6)})`);
      g.addColorStop(0.78, `rgba(${rgb[0]},${rgb[1]},${rgb[2]},${0.30 + 0.55 * lum})`);
      g.addColorStop(1, `rgba(${Math.round(rgb[0] * 0.85)},${Math.round(rgb[1] * 0.8)},${Math.round(rgb[2] * 0.75)},${0.55 + 0.35 * lum})`);
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

      const cx = w * 0.5;
      const rg = ctx.createRadialGradient(cx, horizon + 8, 2, cx, horizon + 8, h * 0.36);
      rg.addColorStop(0, `rgba(255,236,200,${0.34 * lum + 0.08})`);
      rg.addColorStop(0.25, `rgba(${Math.min(255, rgb[0] + 20)},${Math.round(rgb[1] * 0.95)},${Math.round(rgb[2] * 0.7)},${0.22 * lum})`);
      rg.addColorStop(1, 'rgba(255,120,60,0)');
      ctx.fillStyle = rg; ctx.fillRect(0, 0, w, h);

      if (lum < 0.55) {
        ctx.fillStyle = `rgba(255,255,255,${(0.55 - lum) * 0.5})`;
        for (let i = 0; i < 26; i++) {
          ctx.fillRect(((i * 97.13) % 1) * w, ((i * 53.7) % 1) * horizon * 0.7, 1.1, 1.1);
        }
      }

      bands.forEach((b, bi) => {
        if (b.cover <= 0.02) return;
        const drift = Math.sin(k * 0.06 + bi) * 6;
        ctx.save();
        ctx.filter = `blur(${6 + bi * 3}px)`;
        for (let i = 0; i < 11; i++) {
          const x = ((i / 10) * w + drift + bi * 30) % (w + 120) - 60;
          const yy = b.y + Math.sin(i * 1.7 + bi) * 7;
          const ww = w * (0.16 + 0.10 * Math.sin(i * 1.3 + bi));
          const hh = b.hh * (0.6 + 0.5 * Math.abs(Math.cos(i)));
          ctx.fillStyle = `rgba(${b.rgb[0]},${b.rgb[1]},${b.rgb[2]},${0.07 + 0.42 * b.cover})`;
          ctx.beginPath();
          ctx.ellipse(x + ww / 2, yy, ww / 2, hh / 2, 0, 0, Math.PI * 2);
          ctx.fill();
        }
        ctx.restore();
      });

      ctx.fillStyle = 'rgba(255,255,255,.06)';
      ctx.fillRect(0, horizon, w, 1);
      skyRAF = requestAnimationFrame(frame);
    }
    skyRAF = requestAnimationFrame(frame);
  }

  /* ---------------- timeline ---------------- */
  function drawTimeline(tl, win) {
    const cv = $('#timeline');
    const { ctx, w, h } = fitCanvas(cv);
    const ml = 38, mr = 14, mt = 16, mb = 26;
    const iw = w - ml - mr, ih = h - mt - mb;
    ctx.clearRect(0, 0, w, h);
    if (!tl || !tl.length) {
      ctx.fillStyle = 'rgba(255,255,255,.45)';
      ctx.font = '13px -apple-system, "PingFang SC", sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(FAIL, w / 2, h / 2);
      return;
    }
    const x0 = tl[0].m, x1 = tl[tl.length - 1].m;
    const X = m => ml + (m - x0) / (x1 - x0) * iw;
    const Y = v => mt + (1 - v) * ih;

    ctx.strokeStyle = 'rgba(255,255,255,.06)';
    for (let p = 0; p <= 100; p += 25) {
      ctx.beginPath(); ctx.moveTo(ml, Y(p / 100)); ctx.lineTo(w - mr, Y(p / 100)); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.28)';
      ctx.font = '10px ui-monospace, monospace'; ctx.textAlign = 'right';
      ctx.fillText(p + '%', ml - 7, Y(p / 100) + 3);
    }

    if (win) {
      const a = X(win.start_min), b = X(win.end_min);
      const grad = ctx.createLinearGradient(0, mt, 0, mt + ih);
      grad.addColorStop(0, 'rgba(255,209,102,.16)');
      grad.addColorStop(1, 'rgba(255,209,102,.02)');
      ctx.fillStyle = grad;
      ctx.fillRect(a, mt, Math.max(2, b - a), ih);
      ctx.fillStyle = 'rgba(255,209,102,.65)';
      ctx.font = '9.5px ui-monospace, monospace'; ctx.textAlign = 'center';
      if (b - a > 42) ctx.fillText('最佳窗口', (a + b) / 2, mt + 10);
    }

    const grad = ctx.createLinearGradient(0, mt, 0, mt + ih);
    grad.addColorStop(0, 'rgba(255,138,76,.42)');
    grad.addColorStop(1, 'rgba(255,138,76,.02)');
    ctx.beginPath();
    tl.forEach((p, i) => i ? ctx.lineTo(X(p.m), Y(p.v)) : ctx.moveTo(X(p.m), Y(p.v)));
    ctx.lineTo(X(x1), Y(0)); ctx.lineTo(X(x0), Y(0)); ctx.closePath();
    ctx.fillStyle = grad; ctx.fill();
    ctx.beginPath();
    tl.forEach((p, i) => i ? ctx.lineTo(X(p.m), Y(p.v)) : ctx.moveTo(X(p.m), Y(p.v)));
    ctx.strokeStyle = '#ff8a4c'; ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.stroke();

    const EY = e => mt + (1 - (clamp(e, -8, 8) + 8) / 16) * ih;
    ctx.beginPath();
    tl.forEach((p, i) => i ? ctx.lineTo(X(p.m), EY(p.e)) : ctx.moveTo(X(p.m), EY(p.e)));
    ctx.strokeStyle = 'rgba(111,143,184,.75)'; ctx.lineWidth = 1.2;
    ctx.setLineDash([4, 4]); ctx.stroke(); ctx.setLineDash([]);
    ctx.strokeStyle = 'rgba(255,255,255,.16)';
    ctx.beginPath(); ctx.moveTo(ml, EY(0)); ctx.lineTo(w - mr, EY(0)); ctx.stroke();

    ctx.fillStyle = 'rgba(255,255,255,.30)';
    ctx.font = '10px ui-monospace, monospace'; ctx.textAlign = 'center';
    for (let m = -30; m <= 50; m += 10) {
      if (m < x0 || m > x1) continue;
      const label = m === 0 ? (state.mode === 'sunset' ? '日落' : '日出') : (m > 0 ? '+' + m : m);
      ctx.fillText(label, X(m), h - 7);
      ctx.strokeStyle = 'rgba(255,255,255,.05)';
      ctx.beginPath(); ctx.moveTo(X(m), mt); ctx.lineTo(X(m), mt + ih); ctx.stroke();
    }

    if (win) {
      const peak = tl.find(p => p.m === win.peak_min);
      if (peak) {
        ctx.beginPath(); ctx.arc(X(peak.m), Y(peak.v), 3.5, 0, Math.PI * 2);
        ctx.fillStyle = '#ffd166'; ctx.fill();
        ctx.font = '10px ui-monospace, monospace'; ctx.textAlign = 'center';
        ctx.fillText(`${fmtPct(peak.v)}%`, clamp(X(peak.m), ml + 30, w - mr - 30), Y(peak.v) - 9);
      }
    }
  }

  /* ---------------- validation ---------------- */
  async function loadValidation() {
    try {
      const v = await api('/api/validation');
      if (!v || !v.metrics) { $('#valNote').textContent = '尚无回测结果'; return; }
      state.val = v;
      const m = v.metrics.cv || v.metrics.test, b = v.benchmark.metrics;
      $('#valNote').innerHTML = `5 折交叉验证（按日期分组）· 样本 ${v.dataset.rows} 条 / ${v.dataset.dates} 天<br>
        上海 31.23°N 121.47°E · ${v.dataset.period[0]} 至 ${v.dataset.period[1]} · 基率 ${(v.dataset.base_rate * 100).toFixed(1)}%`;
      const cards = [
        ['AUC', m.auc.toFixed(3), '判别“是否成霞”的能力，0.5 为随机', true],
        ['等级命中（±1档）', (m.adjacent * 100).toFixed(1) + '%', `完全命中 ${(m.accuracy * 100).toFixed(1)}%`, true],
        ['Brier', m.brier.toFixed(3), `概率误差，越小越好（基准 ${(m.obs_rate * (1 - m.obs_rate)).toFixed(3)}）`, false],
        ['TSS（最优阈值）', m.tss_best.toFixed(3), `TPR−FPR @ P≥${m.tss_thr}`, true],
      ];
      $('#valMetrics').innerHTML = cards.map(([k, v2, s, good]) => `
        <div class="metric ${good ? 'good' : ''}"><div class="mk">${k}</div>
        <div class="mv">${v2}</div><div class="ms">${s}</div></div>`).join('');

      const labels = v.grades;
      let html = '<div></div>' + ['不烧', '微', '小', '小中', '中大', '大'].map(l => `<div class="mhead">${l}</div>`).join('');
      const max = Math.max(...v.confusion.flat());
      v.confusion.forEach((row, i) => {
        html += `<div class="mlab">${labels[i]}</div>`;
        row.forEach((n, j) => {
          const a = n === 0 ? 0.03 : 0.10 + 0.80 * (n / max) ** 0.55;
          html += `<div class="mcell${i === j ? ' diag' : ''}" style="background:rgba(255,138,76,${a});color:${a > .45 ? '#1b0d06' : 'rgba(255,255,255,.72)'}">${n || ''}</div>`;
        });
      });
      $('#matrix').innerHTML = html;
      drawReliability(v.cv_reliability || v.reliability);

      const rows = [
        ['AUC', m.auc.toFixed(3), b.auc.toFixed(3)],
        ['等级命中 ±1档', (m.adjacent * 100).toFixed(1) + '%', (b.adjacent * 100).toFixed(1) + '%'],
        ['完全命中', (m.accuracy * 100).toFixed(1) + '%', (b.accuracy * 100).toFixed(1) + '%'],
        ['TSS（最优阈值）', m.tss_best.toFixed(3), b.tss_best.toFixed(3)],
        ['召回率（最优阈值）', ((m.recall_best ?? m.recall) * 100).toFixed(1) + '%', ((b.recall_best ?? b.recall) * 100).toFixed(1) + '%'],
        ['空报率（最优阈值）', ((1 - (m.precision_best ?? m.precision)) * 100).toFixed(1) + '%', ((1 - (b.precision_best ?? b.precision)) * 100).toFixed(1) + '%'],
      ];
      $('#cmpTable').innerHTML = `<thead><tr><th>指标</th><th>霞光 Skyglow</th><th>sunsetbot（同期）</th></tr></thead>
        <tbody>${rows.map(([k, a, bb]) => `<tr class="us"><td>${k}</td><td>${a}</td><td>${bb}</td></tr>`).join('')}</tbody>`;

      if (v.nn && v.nn.metrics) {
        const n = v.nn.metrics.test;
        $('#valNote').innerHTML += `<br>神经网络（${v.nn.version}）时间切分 AUC ${n.auc.toFixed(3)} · Brier ${n.brier.toFixed(3)}`;
      }

      const cases = [
        ['命中：中烧以上', v.cases.hits],
        ['漏报：实际中烧以上', v.cases.misses],
        ['空报：实际未烧', v.cases.false_alarms],
      ];
      $('#caseWrap').innerHTML = cases.map(([title, list]) => `
        <div class="card case-card"><h3>${title} · ${list.length}</h3>
        ${list.length ? list.map(c => `<div class="case">
          <b>${c.date.slice(5)} ${c.event}</b><span>${c.actual}</span>
          <span class="cp">P=${Math.round(c.p * 100)}%</span>
          ${c.cases && c.cases !== '-' ? `<a href="${(c.cases.split('\\n')[0] || '').trim()}" target="_blank" rel="noopener">案例</a>` : ''}
        </div>`).join('') : '<div class="empty">无</div>'}</div>`).join('');
    } catch (e) {
      $('#valNote').textContent = '回测数据加载失败';
    }
  }

  function drawReliability(rel) {
    const cv = $('#reliability');
    if (!cv || !rel) return;
    const { ctx, w, h } = fitCanvas(cv);
    const ml = 40, mr = 18, mt = 18, mb = 30;
    const iw = w - ml - mr, ih = h - mt - mb;
    const X = v => ml + v * iw, Y = v => mt + (1 - v) * ih;
    ctx.clearRect(0, 0, w, h);
    ctx.strokeStyle = 'rgba(255,255,255,.06)';
    for (let i = 0; i <= 4; i++) {
      ctx.beginPath(); ctx.moveTo(ml, Y(i / 4)); ctx.lineTo(w - mr, Y(i / 4)); ctx.stroke();
      ctx.fillStyle = 'rgba(255,255,255,.28)'; ctx.font = '10px ui-monospace, monospace';
      ctx.textAlign = 'right'; ctx.fillText(Math.round(i * 25) + '%', ml - 7, Y(i / 4) + 3);
    }
    ctx.setLineDash([4, 4]); ctx.strokeStyle = 'rgba(255,255,255,.25)';
    ctx.beginPath(); ctx.moveTo(X(0), Y(0)); ctx.lineTo(X(1), Y(1)); ctx.stroke();
    ctx.setLineDash([]);
    ctx.beginPath();
    rel.forEach((p, i) => i ? ctx.lineTo(X(p.pred), Y(p.obs)) : ctx.moveTo(X(p.pred), Y(p.obs)));
    ctx.strokeStyle = '#ff8a4c'; ctx.lineWidth = 2; ctx.stroke();
    rel.forEach(p => {
      ctx.beginPath();
      ctx.arc(X(p.pred), Y(p.obs), Math.max(2.5, Math.min(7, Math.sqrt(p.n))), 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,209,102,.85)'; ctx.fill();
    });
    ctx.fillStyle = 'rgba(255,255,255,.35)'; ctx.font = '10px ui-monospace, monospace';
    ctx.textAlign = 'center'; ctx.fillText('预测概率', ml + iw / 2, h - 8);
    ctx.save(); ctx.translate(12, mt + ih / 2); ctx.rotate(-Math.PI / 2);
    ctx.fillText('实际频率', 0, 0); ctx.restore();
  }

  /* ---------------- events ---------------- */
  function bind() {
    $('#loc').addEventListener('input', e => {
      clearTimeout(sugTimer);
      sugTimer = setTimeout(() => searchLoc(e.target.value), 260);
    });
    $('#loc').addEventListener('keydown', async e => {
      if (e.key === 'Escape') { $('#suggest').hidden = true; return; }
      if (e.key === 'ArrowDown' && !$('#suggest').hidden) {
        const first = $('#suggest button');
        if (first) { e.preventDefault(); first.focus(); }
        return;
      }
      if (e.key !== 'Enter') return;
      e.preventDefault();
      clearTimeout(sugTimer);

      // an open dropdown means the user already picked a search: take the top hit
      if (!$('#suggest').hidden && sugItems.length) {
        applyPlace(sugItems[0]);
        return;
      }
      if (await resolveInput()) run();
    });
    document.addEventListener('click', e => {
      if (!e.target.closest('.loc-wrap')) $('#suggest').hidden = true;
    });
    $$('#quick button').forEach(b => b.onclick = () => {
      if (!b.dataset.city) return;          // the coordinate toggle lives here too
      applyPlace({ lat: +b.dataset.lat, lon: +b.dataset.lon, name: b.dataset.city });
    });
    $('#date').addEventListener('change', e => { state.date = e.target.value; markDirty(); });
    $$('#mode button').forEach(b => b.onclick = () => {
      $$('#mode button').forEach(x => x.classList.remove('on'));
      b.classList.add('on');
      state.mode = b.dataset.mode;
      if (state.data) render(state.data);
      if (state.week) renderWeek(state.week);
      // 方位/几何依赖事件时刻，必须重新取数：先清空为“正在计算”
      window.dispatchEvent(new CustomEvent('skyglow:pending', {
        detail: { reason: state.mode === 'sunset' ? '正在按日落时刻重算方位…' : '正在按日出时刻重算方位…' },
      }));
    });
    $('#go').onclick = async () => {
      if (await resolveInput()) run();
    };
    $('#coordToggle').onclick = () => openCoordPanel($('#coordPanel').hidden);
    $('#coordCancel').onclick = () => { openCoordPanel(false); coordHint(); };
    $('#coordApply').onclick = () => {
      if (applyCoord()) { openCoordPanel(false); markDirty(); }
    };
    ['#inLat', '#inLon'].forEach(sel => {
      const el = $(sel);
      el.addEventListener('keydown', e => {
        if (e.key === 'Enter') { e.preventDefault(); if (applyCoord()) { openCoordPanel(false); markDirty(); } }
        if (e.key === 'Escape') { openCoordPanel(false); coordHint(); }
      });
      el.addEventListener('input', () => {
        const v = readCoordInput();
        if (v) coordHint(`${v.lat.toFixed(4)}°N  ${v.lon.toFixed(4)}°E  ·  按回车或点击按钮应用`, false);
      });
    });
    $$('#qwMode button').forEach(btn => btn.onclick = () => {
      $$('#qwMode button').forEach(x => x.classList.remove('on'));
      btn.classList.add('on');
      syncQwMode(btn.dataset.mode);
    });
    const waTest = $('#waTest');
    if (waTest) waTest.onclick = async () => {
      waTest.disabled = true; waTest.textContent = '测试中…';
      try {
        const r = await api('/api/weatherapi?_method=test');
        if (r.ok) {
          const x = r.sample || {};
          toast(`WeatherAPI 正常 · ${x.time} ${x.condition || ''} 云量 ${x.cloud_total ?? '—'}% 能见度 ${x.visibility_km ?? '—'} km`);
          loadWeatherApi();
        } else { toast('WeatherAPI 失败：' + r.error); loadWeatherApi(); }
      } catch (e) { toast('测试失败：' + e.message); }
      finally { waTest.disabled = false; waTest.textContent = '测试'; }
    };
    const waSave = $('#waSave');
    if (waSave) waSave.onclick = async () => {
      const body = `_method=post&key=${encodeURIComponent($('#waKey').value.trim())}`
        + `&daily_cap=${encodeURIComponent($('#waCap').value)}`;
      try {
        const r = await api('/api/weatherapi?' + body);
        fillWa(r); $('#waKey').value = '';
        toast('WeatherAPI 设置已保存，点「开始预测」后生效');
        markDirty();
      } catch (e) { toast('保存失败：' + e.message); }
    };
    const qs = $('#qwSave');
    if (qs) qs.onclick = async () => {
      const body = `_method=post&host=${encodeURIComponent($('#qwHost').value.trim())}`
        + `&kid=${encodeURIComponent($('#qwKid').value.trim())}`
        + `&iss=${encodeURIComponent($('#qwIss').value.trim())}`
        + `&sub=${encodeURIComponent($('#qwSub').value.trim())}`
        + `&daily_cap=${encodeURIComponent($('#qwCap').value)}`;
      try {
        const r = await api('/api/qweather?' + body);
        fillQw(r);
        toast(r.configured ? '和风天气已启用，点「开始预测」后生效'
              : '已保存，仍缺：' + (r.missing || []).join('、'));
        markDirty();
      } catch (e) { toast('保存失败：' + e.message); }
    };
    const qt = $('#qwTest');
    if (qt) qt.onclick = async () => {
      qt.disabled = true; qt.textContent = '测试中…';
      try {
        const r = await api('/api/qweather?_method=test');
        if (r.ok) {
          const s2 = r.sample || {};
          toast(`签名成功 · 实况 ${s2.temp ?? '—'}° ${s2.text ?? ''} 湿度 ${s2.humidity ?? '—'}`);
        } else {
          toast(`失败（${r.stage}）：${r.error}`);
        }
      } catch (e) { toast('测试失败：' + e.message); }
      finally { qt.disabled = false; qt.textContent = '测试'; }
    };
    const qk = $('#qwSaveKey');
    if (qk) qk.onclick = async () => {
      const body = `_method=post&host=${encodeURIComponent($('#qwHost').value.trim())}`
        + `&key=${encodeURIComponent($('#qwKey').value.trim())}`;
      try {
        const r = await api('/api/qweather?' + body);
        fillQw(r); $('#qwKey').value = '';
        toast(r.configured ? '已切换到 API Key 模式' : '已保存');
      } catch (e) { toast('保存失败：' + e.message); }
    };
    const rb = $('#refreshNow');
    if (rb) rb.onclick = () => { toast('已忽略本地缓存，重新取数…'); run(true); };
    $('#geo').onclick = () => {
      if (!navigator.geolocation) { toast('当前浏览器不支持定位'); return; }
      toast('正在获取定位…');
      navigator.geolocation.getCurrentPosition(
        p => {
          state.lat = +p.coords.latitude.toFixed(4);
          state.lon = +p.coords.longitude.toFixed(4);
          state.name = `${state.lat.toFixed(3)},${state.lon.toFixed(3)}`;
          $('#loc').value = state.name;
          if ($('#inLat')) $('#inLat').value = state.lat.toFixed(4);
          if ($('#inLon')) $('#inLon').value = state.lon.toFixed(4);
          markDirty();
        },
        e => toast('定位失败：' + (e.message || '权限被拒绝')),
        { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 });
    };
    window.addEventListener('skyglow:pick', e => {
      applyPlace({ lat: e.detail.lat, lon: e.detail.lon,
                   name: `${(+e.detail.lat).toFixed(3)},${(+e.detail.lon).toFixed(3)}` });
    });
    window.addEventListener('resize', () => {
      if (!state.data) return;
      const ev = state.data.results[state.mode];
      const r = ev && ev.sources ? ev.sources[pickSource(ev, state.src)] : null;
      if (r && !r.error) { drawSky(r); drawTimeline(r.timeline, r.window); }
      if (state.val) drawReliability(state.val.cv_reliability || state.val.reliability);
    });
    $$('.nav a').forEach(a => a.onclick = () => {
      $$('.nav a').forEach(x => x.classList.remove('active'));
      a.classList.add('active');
    });
  }

  function init() {
    const p = new URLSearchParams(location.search);
    if (p.get('lat')) state.lat = +p.get('lat');
    if (p.get('lon')) state.lon = +p.get('lon');
    if (p.get('name')) state.name = p.get('name');
    if (p.get('mode') === 'sunrise') {
      state.mode = 'sunrise';
      $$('#mode button').forEach(x => x.classList.toggle('on', x.dataset.mode === 'sunrise'));
    }
    state.date = p.get('date') || todayStr(0);
    $('#loc').value = state.name;
    $('#date').value = state.date;
    $('#date').min = '1980-01-01';
    $('#date').max = todayStr(15);
    bind();
    const sy = p.get('y');
    if (sy) setTimeout(() => window.scrollTo(0, +sy), 1400);
    loadDirect();
    loadWeatherApi();
    loadQWeather();
    loadValidation();
    // 首屏不自动取数：等用户确认地点与日期后点「开始预测」
    clearDirty();
    if ($('#probNote')) {
      $('#probNote').textContent = '已选 ' + state.name + ' · ' + state.date
        + '，点「开始预测」获取结果';
    }
    if ($('#factors')) {
      $('#factors').innerHTML = '<div class="empty" style="padding:8px 0 16px">'
        + '尚未预测：确认地点与日期后点击「开始预测」</div>';
    }
    if (p.get('selftest') === '1') setTimeout(runSelftest, 2500);
  }

  /* ---------------- UI self test (only with ?selftest=1) ---------------- */
  async function runSelftest() {
    const cases = [
      { label: '输入地名+回车', q: '北京市海淀区', how: 'enter' },
      { label: '输入区县全名+计算按钮', q: '上海市浦东新区', how: 'button' },
      { label: '输入经纬度+计算按钮', q: '31.23,121.47', how: 'button' },
      { label: '输入不存在的名字', q: 'zzzz-no-such-place-9999', how: 'button' },
    ];
    const out = [];
    for (const c of cases) {
      const el = $('#loc');
      el.value = c.q;
      el.dispatchEvent(new Event('input', { bubbles: true }));
      $('#suggest').hidden = true;
      let ok;
      if (c.how === 'enter') {
        el.dispatchEvent(new KeyboardEvent('keydown',
          { key: 'Enter', bubbles: true, cancelable: true }));
        await new Promise(r => setTimeout(r, 1200));
        ok = state.name !== '上海';
      } else {
        ok = await resolveInput();
      }
      if (ok !== false) {
        // wait until the hero header reflects the new coordinates (run() finished)
        const want = state.lat.toFixed(4);
        for (let i = 0; i < 120; i++) {
          if (($('#heroCoord').textContent || '').indexOf(want) >= 0) break;
          await new Promise(r => setTimeout(r, 500));
        }
      }
      const coord = $('#heroCoord').textContent || '';
      out.push({
        case: c.label, input: c.q,
        resolved: ok !== false,
        stateName: state.name,
        lat: state.lat, lon: state.lon,
        heroCoord: coord.trim(),
        heroPlace: ($('#heroPlace').textContent || '').trim(),
      });
      await new Promise(r => setTimeout(r, 600));
    }
    const pre = document.createElement('pre');
    pre.id = 'selftestOut';
    pre.style.display = 'none';
    pre.textContent = JSON.stringify(out);
    document.body.appendChild(pre);
  }

  init();
})();
