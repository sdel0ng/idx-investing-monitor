/* IDX Investing Monitor - live engine shared by every page.
   Quotes: /api/quotes (TradingView scanner), candles: /api/candles (Yahoo) - both ~10 min delayed for IDX.
   Provides: live price painting, market badge, toasts for fired alerts, favorites stars, sparklines,
   gauges and the interactive chart widget (candles/line/area, MAs, Bollinger, VWAP, RSI/MACD pane,
   plan levels, click-to-set price alerts, fullscreen). */
(function () {
  'use strict';

  const INDEX_CODES = new Set(['IHSG', 'LQ45']);
  const C = {
    up: '#22c55e', down: '#f04461', text: '#8b9bb4', grid: 'rgba(42,58,92,.45)', border: '#22304d',
    ma20: '#38bdf8', ma50: '#f5b942', ma200: '#a78bfa', vwap: '#f472b6', bb: 'rgba(148,163,184,.75)', alert: '#c084fc',
  };
  const fmt = (v, d = 0) => (v === null || v === undefined || isNaN(v)) ? '–'
    : Number(v).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
  const signed = (v, d = 1, suffix = '%') => (v === null || v === undefined || isNaN(v)) ? '–'
    : (v > 0 ? '+' : '') + fmt(v, d) + suffix;
  const digitsFor = code => INDEX_CODES.has(code) ? 2 : 0;
  const volumeText = v => v >= 1e9 ? fmt(v / 1e9, 2) + 'B' : v >= 1e6 ? fmt(v / 1e6, 1) + 'M' : v >= 1e3 ? fmt(v / 1e3, 0) + 'K' : fmt(v);
  const rupiah = v => (v === null || v === undefined || isNaN(v)) ? '–'
    : (v < 0 ? '−' : '') + 'Rp' + (Math.abs(v) >= 1e9 ? fmt(Math.abs(v) / 1e9, 2) + 'B' : Math.abs(v) >= 1e6 ? fmt(Math.abs(v) / 1e6, 1) + 'M' : fmt(Math.abs(v)));
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const tickSize = p => p < 200 ? 1 : p < 500 ? 2 : p < 2000 ? 5 : p < 5000 ? 10 : 25;

  const Live = window.Live = { quotes: {}, market: null, listeners: [], extra: new Set(), fmt, signed, esc, rupiah, volumeText, tickSize };

  /* ------------------------------------------------------------ toasts + sound */
  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [880, 1175].forEach((freq, i) => {
        const osc = ctx.createOscillator(), gain = ctx.createGain();
        osc.frequency.value = freq; osc.connect(gain); gain.connect(ctx.destination);
        gain.gain.setValueAtTime(0.12, ctx.currentTime + i * 0.18);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.18 + 0.16);
        osc.start(ctx.currentTime + i * 0.18); osc.stop(ctx.currentTime + i * 0.18 + 0.17);
      });
    } catch (e) { /* audio not allowed */ }
  }
  function toast(message, kind = 'info', sticky = false) {
    let box = document.getElementById('toasts');
    if (!box) { box = document.createElement('div'); box.id = 'toasts'; document.body.appendChild(box); }
    const el = document.createElement('div');
    el.className = 'toast ' + kind;
    el.innerHTML = `<div>${esc(message).replace(/\n/g, '<br>')}</div><button aria-label="close">×</button>`;
    el.querySelector('button').onclick = () => el.remove();
    box.appendChild(el);
    if (!sticky) setTimeout(() => el.remove(), 7000);
  }
  Live.toast = toast;

  async function api(url, options = {}) {
    const response = await fetch(url, Object.assign({ headers: { 'Content-Type': 'application/json' } }, options));
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.error) throw new Error(data.error || response.statusText);
    return data;
  }
  Live.api = api;

  /* ------------------------------------------------------------ quotes */
  function pageCodes() {
    const codes = new Set(Live.extra);
    document.querySelectorAll('[data-q]').forEach(el => codes.add(el.dataset.q));
    return [...codes].filter(Boolean);
  }
  function flash(el, direction) {
    el.classList.remove('flash-up', 'flash-down');
    void el.offsetWidth;
    el.classList.add(direction > 0 ? 'flash-up' : 'flash-down');
  }
  function paint(el, q, previous) {
    const field = el.dataset.f || 'price', code = el.dataset.q, digits = digitsFor(code);
    let html = null, cls = null;
    if (field === 'price') {
      html = fmt(q.price, digits);
      if (previous && previous.price !== q.price) flash(el, q.price - previous.price);
      if (el.dataset.color !== undefined) cls = q.change_pct >= 0 ? 'green' : 'red';
    } else if (field === 'chg') { html = signed(q.change_pct, 2); cls = q.change_pct >= 0 ? 'green' : 'red'; }
    else if (field === 'chgabs') { html = signed(q.change, digits, ''); cls = q.change >= 0 ? 'green' : 'red'; }
    else if (field === 'pnl') { const p = (q.price / +el.dataset.avg - 1) * 100; html = signed(p, 1); cls = p >= 0 ? 'green' : 'red'; }
    else if (field === 'pnlrp') {
      const lots = +el.dataset.lots;
      if (lots) { const rp = (q.price - +el.dataset.avg) * lots * 100; html = rupiah(rp); cls = rp >= 0 ? 'green' : 'red'; }
    } else if (field === 'dist') { html = signed((+el.dataset.level / q.price - 1) * 100, 1); }
    else if (field === 'range') { html = fmt(q.low, digits) + ' – ' + fmt(q.high, digits); }
    else if (field === 'open') { html = fmt(q.open, digits); }
    else if (field === 'high') { html = fmt(q.high, digits); }
    else if (field === 'low') { html = fmt(q.low, digits); }
    else if (field === 'value') { html = q.value_bn ? 'Rp' + fmt(q.value_bn, q.value_bn < 10 ? 1 : 0) + 'bn' : '–'; }
    else if (field === 'volume') { html = q.volume ? volumeText(q.volume) : '–'; }
    if (html !== null) el.innerHTML = html;
    if (cls) { el.classList.remove('green', 'red'); el.classList.add(cls); }
  }
  function paintMarket(market, quotes) {
    const badge = document.getElementById('mkt-badge');
    if (!badge || !market) return;
    const delays = Object.values(quotes).map(q => q.delay_min).filter(v => v !== null && v !== undefined);
    const delay = delays.length ? Math.max(...delays) : 10;
    badge.className = 'mkt ' + market.state;
    badge.querySelector('b').textContent = market.live ? (delay ? 'LIVE · ' + delay + ' min delay' : 'LIVE') : market.label;
    badge.querySelector('small').textContent = (market.live ? market.label + ' · ' : '') + 'updated ' + market.time + ' WIB';
  }
  function showFired(events) {
    const seen = new Set(JSON.parse(localStorage.getItem('fired-seen') || '[]'));
    let fresh = false;
    events.forEach(event => {
      if (seen.has(event.id)) return;
      seen.add(event.id); fresh = true;
      toast('🔔 ' + event.message, 'alert', true);
    });
    if (fresh) { beep(); localStorage.setItem('fired-seen', JSON.stringify([...seen].slice(-200))); }
  }

  let timer = null;
  async function poll() {
    clearTimeout(timer);
    if (document.hidden) { timer = setTimeout(poll, 5000); return; }
    let wait = 15000;
    try {
      const data = await api('/api/quotes?codes=' + encodeURIComponent(pageCodes().join(',')));
      const previous = Live.quotes;
      Live.quotes = Object.assign({}, previous, data.quotes);
      Live.market = data.market;
      document.querySelectorAll('[data-q]').forEach(el => {
        const q = data.quotes[el.dataset.q];
        if (q) paint(el, q, previous[el.dataset.q]);
      });
      paintMarket(data.market, data.quotes);
      if (data.fired && data.fired.length) showFired(data.fired);
      Live.listeners.forEach(fn => { try { fn(data.quotes, data.market); } catch (e) { console.error(e); } });
      wait = data.market && data.market.live ? 15000 : 60000;
    } catch (error) {
      const badge = document.getElementById('mkt-badge');
      if (badge) { badge.className = 'mkt offline'; badge.querySelector('b').textContent = 'Offline'; }
      wait = 30000;
    }
    timer = setTimeout(poll, wait);
  }
  Live.refresh = poll;
  document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

  /* exit-watch banners */
  Live.listeners.push(quotes => {
    document.querySelectorAll('[data-exitwatch]').forEach(el => {
      const q = quotes[el.dataset.exitwatch];
      if (!q) return;
      const exit = +el.dataset.exit, last = +el.dataset.lastline, price = q.price, code = el.dataset.exitwatch;
      let text = '';
      if (last && price < last) text = `⚠ ${code} ${fmt(price)} is below the LAST LINE ${fmt(last)} right now. The rule is the daily close - be ready to act at the close.`;
      else if (exit && price < exit) text = `⚠ ${code} ${fmt(price)} is trading below the EXIT level ${fmt(exit)}. Exit rule = daily CLOSE below it - watch the last 15 minutes.`;
      else if (exit && price < exit * 1.03) text = `${code} ${fmt(price)} is within 3% of the exit level ${fmt(exit)}.`;
      el.textContent = text;
      el.style.display = text ? '' : 'none';
      el.classList.toggle('danger', price < exit);
    });
  });

  /* ------------------------------------------------------------ favorites (stars) */
  let favorites = null;
  async function loadFavorites() {
    if (favorites) return favorites;
    try { favorites = new Set(await api('/api/favorites')); } catch (e) { favorites = new Set(); }
    return favorites;
  }
  async function paintStars(root = document) {
    const stars = root.querySelectorAll('[data-star]');
    if (!stars.length) return;
    const favs = await loadFavorites();
    stars.forEach(star => {
      const on = favs.has(star.dataset.star);
      star.classList.toggle('on', on);
      star.textContent = on ? '★' : '☆';
      star.title = on ? 'Remove from favorites' : 'Add to favorites';
    });
  }
  Live.paintStars = paintStars;
  document.addEventListener('click', async event => {
    const star = event.target.closest('[data-star]');
    if (!star) return;
    event.preventDefault(); event.stopPropagation();
    const code = star.dataset.star;
    try {
      const result = await api('/api/favorites', { method: 'POST', body: JSON.stringify({ code }) });
      const favs = await loadFavorites();
      result.on ? favs.add(code) : favs.delete(code);
      paintStars();
      toast(result.on ? `★ ${code} added to favorites` : `${code} removed from favorites`);
    } catch (e) { toast('Could not update favorites: ' + e.message, 'error'); }
  }, true);

  /* ------------------------------------------------------------ sparkline + gauge */
  Live.spark = function (values, width = 110, height = 32, base = null) {
    if (!values || values.length < 2) return '<span class="muted">–</span>';
    const ref = base ?? values[0];
    const min = Math.min(...values, ref), max = Math.max(...values, ref), span = max - min || 1;
    const x = i => (i / (values.length - 1)) * width, y = v => height - 2 - ((v - min) / span) * (height - 4);
    const points = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
    const color = values[values.length - 1] >= ref ? C.up : C.down;
    const area = `0,${height} ${points} ${width},${height}`;
    return `<svg class="spark" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
      <polygon points="${area}" fill="${color}" opacity=".12"/><line x1="0" x2="${width}" y1="${y(ref)}" y2="${y(ref)}" stroke="#5b6b8c" stroke-dasharray="2 3" stroke-width="1"/>
      <polyline points="${points}" fill="none" stroke="${color}" stroke-width="1.6"/></svg>`;
  };
  Live.gauge = function (value, label, counts, title) {
    const v = Math.max(-1, Math.min(1, value ?? 0));
    const angle = Math.PI * (1 - (v + 1) / 2);
    const cx = 90, cy = 86, r = 70;
    const nx = cx + (r - 12) * Math.cos(angle), ny = cy - (r - 12) * Math.sin(angle);
    const arc = (from, to, color) => {
      const a1 = Math.PI * (1 - from), a2 = Math.PI * (1 - to);
      return `<path d="M ${cx + r * Math.cos(a1)} ${cy - r * Math.sin(a1)} A ${r} ${r} 0 0 1 ${cx + r * Math.cos(a2)} ${cy - r * Math.sin(a2)}" stroke="${color}" stroke-width="9" fill="none" stroke-linecap="round"/>`;
    };
    const cls = /buy/i.test(label) ? 'green' : /sell/i.test(label) ? 'red' : 'amber';
    return `<div class="gauge"><div class="gauge-title">${esc(title)}</div>
      <svg width="180" height="100" viewBox="0 0 180 100">
        ${arc(0.02, 0.25, '#f04461')}${arc(0.27, 0.45, '#fb7185')}${arc(0.47, 0.53, '#64748b')}${arc(0.55, 0.73, '#4ade80')}${arc(0.75, 0.98, '#22c55e')}
        <line x1="${cx}" y1="${cy}" x2="${nx}" y2="${ny}" stroke="#e6edf7" stroke-width="3" stroke-linecap="round"/>
        <circle cx="${cx}" cy="${cy}" r="5" fill="#e6edf7"/></svg>
      <div class="gauge-label ${cls}">${esc(label)}</div>
      ${counts ? `<div class="gauge-counts"><span class="red">Sell ${counts.Sell}</span><span>Neutral ${counts.Neutral}</span><span class="green">Buy ${counts.Buy}</span></div>` : ''}</div>`;
  };

  /* ------------------------------------------------------------ jump box */
  function setupJump() {
    const input = document.getElementById('jump');
    if (!input) return;
    let loaded = false;
    input.addEventListener('focus', async () => {
      if (loaded) return;
      loaded = true;
      try {
        const codes = await api('/api/codes');
        document.getElementById('jump-list').innerHTML = codes.map(c => `<option value="${c.code}">${c.name}</option>`).join('');
      } catch (e) { loaded = false; }
    });
    input.addEventListener('keydown', e => {
      if (e.key === 'Enter' && input.value.trim()) location.href = '/stock/' + input.value.trim().toUpperCase().split(/\s/)[0];
    });
    document.addEventListener('keydown', e => {
      const tag = document.activeElement.tagName;
      if (e.key === '/' && tag !== 'INPUT' && tag !== 'TEXTAREA' && tag !== 'SELECT') { e.preventDefault(); input.focus(); }
    });
  }

  /* ------------------------------------------------------------ indicators */
  function sma(values, n) {
    const out = []; let sum = 0;
    values.forEach((v, i) => { sum += v; if (i >= n) sum -= values[i - n]; out.push(i >= n - 1 ? sum / n : null); });
    return out;
  }
  function ema(values, n) {
    const k = 2 / (n + 1), out = []; let prev = null;
    values.forEach((v, i) => {
      if (i < n - 1) { out.push(null); return; }
      if (prev === null) { prev = values.slice(0, n).reduce((a, b) => a + b, 0) / n; out.push(prev); return; }
      prev = v * k + prev * (1 - k); out.push(prev);
    });
    return out;
  }
  function rsi(values, n = 14) {
    const out = values.map(() => null);
    let gain = 0, loss = 0;
    for (let i = 1; i < values.length; i++) {
      const change = values[i] - values[i - 1];
      const g = Math.max(change, 0), l = Math.max(-change, 0);
      if (i <= n) { gain += g; loss += l; if (i === n) { gain /= n; loss /= n; out[i] = 100 - 100 / (1 + gain / (loss || 1e-9)); } }
      else { gain = (gain * (n - 1) + g) / n; loss = (loss * (n - 1) + l) / n; out[i] = 100 - 100 / (1 + gain / (loss || 1e-9)); }
    }
    return out;
  }
  function bollinger(values, n = 20, mult = 2) {
    const mid = sma(values, n);
    return values.map((v, i) => {
      if (mid[i] === null) return null;
      const slice = values.slice(i - n + 1, i + 1);
      const sd = Math.sqrt(slice.reduce((a, b) => a + (b - mid[i]) ** 2, 0) / n);
      return { mid: mid[i], upper: mid[i] + mult * sd, lower: mid[i] - mult * sd };
    });
  }

  /* ------------------------------------------------------------ chart widget */
  const TIMEFRAMES = ['1m', '5m', '15m', '1H', '1D', '1W'];
  const baseOptions = digits => ({
    autoSize: true,
    layout: { background: { type: 'solid', color: 'transparent' }, textColor: C.text, fontFamily: 'Segoe UI, Inter, sans-serif', fontSize: 11 },
    grid: { vertLines: { color: C.grid }, horzLines: { color: C.grid } },
    rightPriceScale: { borderColor: C.border, minimumWidth: 72 },
    timeScale: { borderColor: C.border, timeVisible: true, secondsVisible: false, rightOffset: 4 },
    crosshair: { mode: 0, vertLine: { color: '#5b6b8c', labelBackgroundColor: '#1e2b47' }, horzLine: { color: '#5b6b8c', labelBackgroundColor: '#1e2b47' } },
    localization: { priceFormatter: p => fmt(p, digits) },
  });

  function LiveChart(el) {
    const code = el.dataset.code;
    const levels = JSON.parse(el.dataset.levels || '[]');
    const digits = digitsFor(code);
    const full = el.dataset.full !== undefined;   // full trading toolbar
    let tf = el.dataset.tf || localStorage.getItem('lc-tf') || '1D';
    if (!TIMEFRAMES.includes(tf)) tf = '1D';
    let type = localStorage.getItem('lc-type') || 'candles';
    let pane = localStorage.getItem('lc-pane') || (full ? 'rsi' : 'none');
    let showBB = localStorage.getItem('lc-bb') === '1';
    let showTA = full ? localStorage.getItem('lc-ta') !== '0' : localStorage.getItem('lc-ta') === '1';
    el.classList.add('lc');
    el.innerHTML = `
      <div class="lc-bar">
        <div class="lc-tfs">${TIMEFRAMES.map(t => `<button data-tf="${t}">${t}</button>`).join('')}</div>
        <select class="lc-sel" data-s="type" title="Chart type"><option value="candles">Candles</option><option value="line">Line</option><option value="area">Area</option></select>
        <select class="lc-sel" data-s="pane" title="Lower indicator"><option value="none">No pane</option><option value="rsi">RSI 14</option><option value="macd">MACD</option></select>
        <div class="lc-toggles">
          <label><input type="checkbox" data-t="levels" checked> Levels</label>
          <label><input type="checkbox" data-t="mas" checked> MAs</label>
          <label><input type="checkbox" data-t="bb" ${showBB ? 'checked' : ''}> BB</label>
          <label title="Auto technical analysis: swings, support/resistance zones, trendline, patterns, Fibonacci"><input type="checkbox" data-t="ta" ${showTA ? 'checked' : ''}> TA</label>
          <button class="lc-btn" data-a="alert" title="Click the chart to set a price alert">🔔 Alert</button>
          <button class="lc-btn" data-a="full" title="Fullscreen">⛶</button>
        </div>
      </div>
      <div class="lc-legend"></div>
      <div class="lc-box ${full ? 'tall' : ''}"><div class="lc-loading">Loading ${esc(code)}…</div></div>
      <div class="lc-pane"></div>
      <div class="lc-foot"></div>`;
    const box = el.querySelector('.lc-box'), paneBox = el.querySelector('.lc-pane');
    const legend = el.querySelector('.lc-legend'), foot = el.querySelector('.lc-foot');
    el.querySelector('[data-s="type"]').value = type;
    el.querySelector('[data-s="pane"]').value = pane;

    const chart = LightweightCharts.createChart(box, baseOptions(digits));
    chart.priceScale('right').applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    const priceFormat = { type: 'price', precision: digits, minMove: digits ? 0.01 : 1 };
    const candles = chart.addCandlestickSeries({ upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down, priceFormat });
    const line = chart.addLineSeries({ color: C.ma20, lineWidth: 2, priceFormat, visible: false });
    const area = chart.addAreaSeries({ lineColor: C.ma20, topColor: 'rgba(56,189,248,.35)', bottomColor: 'rgba(56,189,248,0)', lineWidth: 2, priceFormat, visible: false });
    const volume = chart.addHistogramSeries({ priceFormat: { type: 'volume' }, priceScaleId: 'vol', lastValueVisible: false, priceLineVisible: false });
    chart.priceScale('vol').applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    const lineOpts = (color, extra = {}) => Object.assign({ color, lineWidth: 1.5, priceLineVisible: false, lastValueVisible: false,
      crosshairMarkerVisible: false, autoscaleInfoProvider: () => null }, extra);
    const mas = { ma20: chart.addLineSeries(lineOpts(C.ma20)), ma50: chart.addLineSeries(lineOpts(C.ma50)),
                  ma200: chart.addLineSeries(lineOpts(C.ma200)), vwap: chart.addLineSeries(lineOpts(C.vwap)) };
    const bb = { upper: chart.addLineSeries(lineOpts(C.bb, { lineWidth: 1, lineStyle: 2 })),
                 mid: chart.addLineSeries(lineOpts(C.bb, { lineWidth: 1 })),
                 lower: chart.addLineSeries(lineOpts(C.bb, { lineWidth: 1, lineStyle: 2 })) };
    const priceSeries = () => type === 'candles' ? candles : type === 'line' ? line : area;

    /* auto technical analysis overlay (ta.js) */
    const taSeries = {
      down: chart.addLineSeries(lineOpts('#f87171', { lineWidth: 2 })),
      up: chart.addLineSeries(lineOpts('#4ade80', { lineWidth: 2 })),
      neck: chart.addLineSeries(lineOpts('#fbbf24', { lineWidth: 1.5, lineStyle: 2 })),
    };
    const zoneLayer = document.createElement('div');
    zoneLayer.className = 'lc-zones';
    const taBox = document.createElement('div');
    taBox.className = 'lc-ta-box';
    box.appendChild(zoneLayer);
    box.appendChild(taBox);
    let ta = null, taLines = [], taCollapsed = localStorage.getItem('lc-ta-collapsed') === '1';

    function positionZones() {
      zoneLayer.innerHTML = '';
      if (!ta || !showTA) return;
      const series = priceSeries(), width = chart.timeScale().width();
      const add = (zone, label, cls) => {
        const top = series.priceToCoordinate(zone.hi), bottom = series.priceToCoordinate(zone.lo);
        if (top === null || bottom === null) return;
        const div = document.createElement('div');
        div.className = 'lc-zone ' + cls;
        div.style.top = Math.min(top, bottom) - 2 + 'px';
        div.style.height = Math.max(4, Math.abs(bottom - top) + 4) + 'px';
        div.style.width = width + 'px';
        div.innerHTML = `<span>${esc(label)}</span>`;
        zoneLayer.appendChild(div);
      };
      ta.supports.slice(0, 2).forEach((z, i) => add(z, `S${i + 1} ${fmt(z.lo, digits)}–${fmt(z.hi, digits)} · ${z.touches} touches`, 'sup'));
      ta.resistances.slice(0, 2).forEach((z, i) => add(z, `R${i + 1} ${fmt(z.lo, digits)}–${fmt(z.hi, digits)} · ${z.touches} touches`, 'res'));
      if (ta.fib) ta.fib.levels.forEach(level => {
        const y = series.priceToCoordinate(level.price);
        if (y === null) return;
        const tag = document.createElement('div');
        tag.className = 'lc-fib-tag';
        tag.style.top = y - 7 + 'px';
        tag.style.right = (box.clientWidth - width + 150) + 'px';
        tag.textContent = `Fib ${(level.ratio * 100).toFixed(1)}% ${fmt(level.price, digits)}`;
        zoneLayer.appendChild(tag);
      });
    }

    function renderTaBox() {
      if (!ta || !showTA) { taBox.style.display = 'none'; return; }
      taBox.style.display = '';
      taBox.classList.toggle('collapsed', taCollapsed);
      taBox.innerHTML = `<div class="lc-ta-head"><b>📐 Technical analysis</b> <span class="muted">auto · ${esc(tf)}</span>
          <button class="link-btn" data-ta-collapse title="${taCollapsed ? 'expand' : 'collapse'}">${taCollapsed ? '▸' : '▾'}</button></div>
        <div class="lc-ta-body">${ta.lines.map(l => `<div><span>${esc(l.k)}</span><p>${l.v}</p></div>`).join('')}
          <div class="lc-ta-read">💡 ${esc(ta.read)}</div>
          <div class="muted" style="font-size:10px">Rules-based drawing (zigzag swings ${fmt(ta.threshold * 100, 0)}%, ATR ${fmt(ta.atr, digits)}). A guide, not a signal - confirm on the daily close.</div></div>`;
      taBox.querySelector('[data-ta-collapse]').onclick = () => {
        taCollapsed = !taCollapsed; localStorage.setItem('lc-ta-collapsed', taCollapsed ? '1' : '0'); renderTaBox();
      };
    }

    function clearTA() {
      taLines.forEach(([s, l]) => s.removePriceLine(l));
      taLines = [];
      Object.values(taSeries).forEach(s => s.setData([]));
      [candles, line, area].forEach(s => s.setMarkers([]));
      zoneLayer.innerHTML = '';
      taBox.style.display = 'none';
    }

    function drawTA() {
      clearTA();
      if (!showTA || !window.LiveTA || bars.length < 40) { ta = null; return; }
      const maNow = {};
      if (current && !current.intraday) {
        ['ma20', 'ma50', 'ma200'].forEach(n => { const arr = current.lines[n] || []; if (arr.length) maNow[n] = arr[arr.length - 1].value; });
      }
      try { ta = window.LiveTA.analyse(bars, maNow, v => fmt(v, digits)); } catch (e) { console.error(e); ta = null; }
      if (!ta) return;
      if (ta.down) taSeries.down.setData(ta.down.points);
      if (ta.up) taSeries.up.setData(ta.up.points);
      if (ta.pattern) {
        const endTime = bars[bars.length - 1].time;
        taSeries.neck.setData([{ time: ta.pattern.neckFrom, value: ta.pattern.neck }, { time: endTime, value: ta.pattern.neck }]);
      }
      if (ta.fib) {
        [candles, line, area].forEach(series => ta.fib.levels.forEach(level => taLines.push([series, series.createPriceLine({
          price: level.price, color: 'rgba(192,132,252,.75)', lineWidth: 1, lineStyle: 3, axisLabelVisible: false,
          title: `Fib ${(level.ratio * 100).toFixed(1)}% ${fmt(level.price, digits)}` })])));
      }
      if (ta.pattern) {
        [candles, line, area].forEach(series => taLines.push([series, series.createPriceLine({
          price: ta.pattern.target, color: 'rgba(251,191,36,.6)', lineWidth: 1, lineStyle: 1, axisLabelVisible: false,
          title: `${ta.pattern.type === 'bottom' ? 'DB' : 'DT'} target ${fmt(ta.pattern.target, digits)}` })]));
      }
      // markers: last major swings, pattern points, divergence
      const markers = new Map();
      const put = (time, marker) => { const key = time + marker.position; if (!markers.has(key)) markers.set(key, Object.assign({ time }, marker)); };
      if (ta.pattern) {
        const below = ta.pattern.type === 'bottom';
        [ta.pattern.p1, ta.pattern.p2].forEach((p, i) => put(p.time, { position: below ? 'belowBar' : 'aboveBar', color: '#fbbf24',
          shape: below ? 'arrowUp' : 'arrowDown', text: `${below ? 'Bottom' : 'Top'} ${i + 1} ${fmt(p.price, digits)}` }));
      }
      ta.divergences.forEach(d => put(d.at.time, { position: d.type === 'bullish' ? 'belowBar' : 'aboveBar',
        color: d.type === 'bullish' ? '#4ade80' : '#f87171', shape: 'circle', text: `${d.type === 'bullish' ? 'Bull' : 'Bear'} RSI div` }));
      ta.pivots.slice(-8).forEach(p => put(p.time, { position: p.kind === 'H' ? 'aboveBar' : 'belowBar',
        color: p.kind === 'H' ? '#f87171' : '#4ade80', shape: p.kind === 'H' ? 'arrowDown' : 'arrowUp', text: fmt(p.price, digits) }));
      const list = [...markers.values()].sort((a, b) => a.time - b.time);
      [candles, line, area].forEach(s => s.setMarkers(list));
      renderTaBox();
      requestAnimationFrame(positionZones);
    }
    chart.timeScale().subscribeVisibleLogicalRangeChange(() => requestAnimationFrame(positionZones));
    new ResizeObserver(() => requestAnimationFrame(positionZones)).observe(box);
    setInterval(positionZones, 2000);

    /* lower pane chart, time-synced with the main chart */
    let paneChart = null, paneSeries = {};
    function buildPane() {
      if (paneChart) { paneChart.remove(); paneChart = null; paneSeries = {}; }
      paneBox.style.display = pane === 'none' ? 'none' : '';
      if (pane === 'none') return;
      paneChart = LightweightCharts.createChart(paneBox, Object.assign(baseOptions(2), {
        timeScale: { visible: false, borderColor: C.border, rightOffset: 4 }, localization: { priceFormatter: p => fmt(p, pane === 'rsi' ? 0 : 1) } }));
      if (pane === 'rsi') {
        paneSeries.rsi = paneChart.addLineSeries({ color: '#a78bfa', lineWidth: 1.5, priceLineVisible: false });
        [70, 30].forEach(level => paneSeries.rsi.createPriceLine({ price: level, color: level === 70 ? C.down : C.up, lineStyle: 2, lineWidth: 1, axisLabelVisible: true, title: '' }));
      } else {
        paneSeries.hist = paneChart.addHistogramSeries({ priceLineVisible: false, lastValueVisible: false });
        paneSeries.macd = paneChart.addLineSeries({ color: C.ma20, lineWidth: 1.5, priceLineVisible: false });
        paneSeries.signal = paneChart.addLineSeries({ color: C.ma50, lineWidth: 1.2, priceLineVisible: false });
      }
      paneChart.timeScale().subscribeVisibleLogicalRangeChange(range => {
        // only follow the pane when the user is scrolling/zooming it (its own auto-fit must not move the main chart)
        if (range && paneActive && !syncing) { syncing = true; chart.timeScale().setVisibleLogicalRange(range); syncing = false; }
      });
    }
    let syncing = false, paneActive = false;
    paneBox.addEventListener('mouseenter', () => { paneActive = true; });
    paneBox.addEventListener('mouseleave', () => { paneActive = false; });
    chart.timeScale().subscribeVisibleLogicalRangeChange(range => {
      if (range && paneChart && !syncing) { syncing = true; paneChart.timeScale().setVisibleLogicalRange(range); syncing = false; }
    });

    /* levels + alert lines (drawn on all three price series so they survive a type switch) */
    let levelLines = [], alertLines = [], alertPrices = JSON.parse(el.dataset.alerts || '[]');
    function drawLevels(show) {
      levelLines.forEach(([s, l]) => s.removePriceLine(l));
      levelLines = [];
      if (!show) return;
      [candles, line, area].forEach(series => levels.forEach(level => levelLines.push([series, series.createPriceLine({
        price: level.price, color: level.color || '#94a3b8', lineWidth: level.width || 1,
        lineStyle: level.style === undefined ? 2 : level.style, axisLabelVisible: true, title: level.title || '' })])));
    }
    function drawAlerts() {
      alertLines.forEach(([s, l]) => s.removePriceLine(l));
      alertLines = [];
      [candles, line, area].forEach(series => alertPrices.forEach(price => alertLines.push([series, series.createPriceLine({
        price, color: C.alert, lineWidth: 1, lineStyle: 1, axisLabelVisible: true, title: '🔔' })])));
    }
    drawLevels(true); drawAlerts();

    let bars = [], lastTime = 0, showMas = true, request = 0, current = null;
    const volumeBar = b => ({ time: b.time, value: b.volume, color: b.close >= b.open ? 'rgba(34,197,94,.35)' : 'rgba(240,68,97,.35)' });

    function showLegend(i) {
      const bar = bars[i]; if (!bar) { legend.innerHTML = ''; return; }
      const prev = bars[i - 1], base = prev ? prev.close : bar.open, change = (bar.close / base - 1) * 100, cls = change >= 0 ? 'green' : 'red';
      const extra = [];
      if (current && !current.intraday && showMas) {
        ['ma20', 'ma50', 'ma200'].forEach(n => { const p = (current.lines[n] || []).find(x => x.time === bar.time); if (p) extra.push(`<span style="color:${C[n]}">${n.toUpperCase()} ${fmt(p.value, digits)}</span>`); });
      }
      legend.innerHTML = `<b>${esc(code)}</b> <span class="muted">${tf}</span>
        O <b>${fmt(bar.open, digits)}</b> H <b>${fmt(bar.high, digits)}</b> L <b>${fmt(bar.low, digits)}</b>
        C <b class="${cls}">${fmt(bar.close, digits)}</b> <span class="${cls}">${signed(change, 2)}</span>
        ${bar.volume ? ' Vol <b>' + volumeText(bar.volume) + '</b>' : ''} ${extra.join(' ')}`;
    }

    function computeIndicators() {
      const closes = bars.map(b => b.close), times = bars.map(b => b.time);
      const bands = bollinger(closes);
      const pick = key => bands.map((b, i) => b ? { time: times[i], value: b[key] } : { time: times[i] });
      bb.upper.setData(pick('upper')); bb.mid.setData(pick('mid')); bb.lower.setData(pick('lower'));
      Object.values(bb).forEach(s => s.applyOptions({ visible: showBB }));
      if (!paneChart) return;
      if (pane === 'rsi') {
        paneSeries.rsi.setData(rsi(closes).map((v, i) => v === null ? { time: times[i] } : { time: times[i], value: v }));
      } else {
        const fast = ema(closes, 12), slow = ema(closes, 26);
        const macdLine = closes.map((_, i) => fast[i] !== null && slow[i] !== null ? fast[i] - slow[i] : null);
        const valid = macdLine.filter(v => v !== null), sig = ema(valid, 9), offset = macdLine.length - valid.length;
        const signal = macdLine.map((v, i) => i >= offset ? sig[i - offset] : null);
        paneSeries.macd.setData(macdLine.map((v, i) => v === null ? { time: times[i] } : { time: times[i], value: v }));
        paneSeries.signal.setData(signal.map((v, i) => v === null ? { time: times[i] } : { time: times[i], value: v }));
        paneSeries.hist.setData(macdLine.map((v, i) => v === null || signal[i] === null ? { time: times[i] }
          : { time: times[i], value: v - signal[i], color: v - signal[i] >= 0 ? 'rgba(34,197,94,.6)' : 'rgba(240,68,97,.6)' }));
      }
      paneChart.timeScale().setVisibleLogicalRange(chart.timeScale().getVisibleLogicalRange());
    }

    function applyLines(data) {
      const visible = data.intraday ? { ma20: true, vwap: true } : { ma20: true, ma50: true, ma200: true };
      Object.entries(mas).forEach(([name, series]) => {
        series.applyOptions({ visible: showMas && !!visible[name] });
        series.setData(data.lines[name] || []);
      });
      const names = Object.keys(visible).map(n => `<i style="background:${C[n]}"></i>${n === 'vwap' ? 'VWAP' : n.toUpperCase().replace('MA', 'MA ')}`).join(' ');
      foot.innerHTML = `<span class="lc-keys">${names}${showBB ? `<i style="background:${C.bb}"></i>BB 20,2` : ''}</span>
        <span>${esc(data.source)} · ${esc(data.market.label)} ${data.market.time} WIB · scroll = zoom, drag = pan, double-click axis = reset</span>`;
    }

    function setPriceData(replace) {
      const closes = bars.map(b => ({ time: b.time, value: b.close }));
      if (replace) { candles.setData(bars); line.setData(closes); area.setData(closes); volume.setData(bars.map(volumeBar)); }
      [candles, line, area].forEach(s => s.applyOptions({ visible: s === priceSeries() }));
    }

    async function load(replace) {
      const id = ++request;
      let data;
      try { data = await api(`/api/candles/${encodeURIComponent(code)}?tf=${tf}`); }
      catch (e) { foot.textContent = 'Chart data unavailable: ' + e.message; return; }
      if (id !== request) return;
      const loading = box.querySelector('.lc-loading');
      if (loading) loading.remove();
      current = data;
      if (replace || !bars.length) {
        bars = data.bars;
        setPriceData(true);
        const n = bars.length, span = { '1m': 240, '5m': 160, '15m': 140, '1H': 120, '1D': 66, '1W': 104 }[tf] || 120;
        chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, n - span), to: n + 3 });
      } else {
        const fresh = data.bars.filter(b => b.time >= lastTime);
        fresh.forEach(b => { candles.update(b); line.update({ time: b.time, value: b.close }); area.update({ time: b.time, value: b.close }); volume.update(volumeBar(b)); });
        bars = data.bars;
      }
      applyLines(data);
      computeIndicators();
      const newBar = !bars.length || bars[bars.length - 1].time !== lastTime;
      if (replace || newBar || !ta) drawTA();
      lastTime = bars.length ? bars[bars.length - 1].time : 0;
      showLegend(bars.length - 1);
    }

    chart.subscribeCrosshairMove(param => {
      if (!param || !param.time) { showLegend(bars.length - 1); return; }
      const i = bars.findIndex(b => b.time === param.time);
      if (i >= 0) showLegend(i);
    });

    /* click-to-alert */
    let alertMode = false;
    const alertButton = el.querySelector('[data-a="alert"]');
    alertButton.addEventListener('click', () => {
      alertMode = !alertMode;
      alertButton.classList.toggle('on', alertMode);
      box.classList.toggle('picking', alertMode);
      if (alertMode) toast('Click on the chart at the price where you want an alert.');
    });
    chart.subscribeClick(async param => {
      if (!alertMode || !param.point) return;
      const raw = priceSeries().coordinateToPrice(param.point.y);
      if (raw === null) return;
      const price = digits ? Math.round(raw * 100) / 100 : Math.round(raw / tickSize(raw)) * tickSize(raw);
      const last = bars.length ? bars[bars.length - 1].close : price;
      const kind = price >= last ? 'above' : 'below';
      alertMode = false; alertButton.classList.remove('on'); box.classList.remove('picking');
      try {
        await api('/api/alerts', { method: 'POST', body: JSON.stringify({ code, type: kind, value: price, note: 'set from chart' }) });
        alertPrices.push(price); drawAlerts();
        toast(`🔔 Alert set: ${code} ${kind === 'above' ? '≥' : '≤'} ${fmt(price, digits)}`);
        document.dispatchEvent(new CustomEvent('alerts-changed'));
      } catch (e) { toast('Could not set alert: ' + e.message, 'error'); }
    });

    function selectTf(next) {
      tf = next; localStorage.setItem('lc-tf', tf);
      el.querySelectorAll('[data-tf]').forEach(b => b.classList.toggle('on', b.dataset.tf === tf));
      bars = []; lastTime = 0; load(true);
    }
    el.querySelectorAll('[data-tf]').forEach(b => b.addEventListener('click', () => selectTf(b.dataset.tf)));
    el.querySelector('[data-s="type"]').addEventListener('change', e => { type = e.target.value; localStorage.setItem('lc-type', type); setPriceData(false); });
    el.querySelector('[data-s="pane"]').addEventListener('change', e => { pane = e.target.value; localStorage.setItem('lc-pane', pane); buildPane(); computeIndicators(); });
    el.querySelector('[data-t="levels"]').addEventListener('change', e => drawLevels(e.target.checked));
    el.querySelector('[data-t="ta"]').addEventListener('change', e => { showTA = e.target.checked; localStorage.setItem('lc-ta', showTA ? '1' : '0'); drawTA(); });
    el.querySelector('[data-t="mas"]').addEventListener('change', e => { showMas = e.target.checked; if (current) applyLines(current); });
    el.querySelector('[data-t="bb"]').addEventListener('change', e => { showBB = e.target.checked; localStorage.setItem('lc-bb', showBB ? '1' : '0'); if (current) { applyLines(current); computeIndicators(); } });
    el.querySelector('[data-a="full"]').addEventListener('click', () => {
      el.classList.toggle('fullscreen');
      document.body.classList.toggle('noscroll', el.classList.contains('fullscreen'));
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && el.classList.contains('fullscreen')) { el.classList.remove('fullscreen'); document.body.classList.remove('noscroll'); } });
    buildPane();
    selectTf(tf);
    (function tick() {
      const live = Live.market ? Live.market.live : true;
      setTimeout(() => { if (!document.hidden) load(false); tick(); }, live ? 15000 : 120000);
    })();
    el.liveChart = { chart, addAlert: price => { alertPrices.push(price); drawAlerts(); }, setAlerts: prices => { alertPrices = prices; drawAlerts(); } };
    return el.liveChart;
  }
  Live.LiveChart = LiveChart;

  /* small area chart (dashboard cards) */
  Live.miniChart = async function (el, code, tf = '5m') {
    const chart = LightweightCharts.createChart(el, Object.assign(baseOptions(digitsFor(code)), {
      rightPriceScale: { visible: false }, timeScale: { visible: false }, grid: { vertLines: { visible: false }, horzLines: { visible: false } },
      handleScroll: false, handleScale: false, crosshair: { vertLine: { visible: false }, horzLine: { visible: false } } }));
    chart.applyOptions({ layout: { attributionLogo: false } });
    const series = chart.addAreaSeries({ lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    async function load() {
      try {
        const data = await api(`/api/candles/${code}?tf=${tf}`);
        const lastDay = data.bars.length ? Math.floor(data.bars[data.bars.length - 1].time / 86400) : 0;
        const today = data.bars.filter(b => Math.floor(b.time / 86400) === lastDay);
        const up = today.length && today[today.length - 1].close >= (Live.quotes[code] ? Live.quotes[code].price - Live.quotes[code].change : today[0].open);
        series.applyOptions({ lineColor: up ? C.up : C.down, topColor: up ? 'rgba(34,197,94,.3)' : 'rgba(240,68,97,.3)', bottomColor: 'rgba(0,0,0,0)' });
        series.setData(today.map(b => ({ time: b.time, value: b.close })));
        chart.timeScale().fitContent();
      } catch (e) { /* ignore */ }
    }
    load();
    setInterval(() => { if (!document.hidden) load(); }, 60000);
  };

  /* ------------------------------------------------------------ tabs */
  document.addEventListener('click', event => {
    const tab = event.target.closest('[data-tab]');
    if (!tab) return;
    const group = tab.closest('.tabs');
    group.querySelectorAll('[data-tab]').forEach(t => t.classList.toggle('on', t === tab));
    group.querySelectorAll('[data-panel]').forEach(p => p.style.display = p.dataset.panel === tab.dataset.tab ? '' : 'none');
    group.dispatchEvent(new CustomEvent('tab', { detail: tab.dataset.tab }));
  });

  /* ------------------------------------------------------------ start */
  document.addEventListener('DOMContentLoaded', () => {
    setupJump();
    paintStars();
    document.querySelectorAll('[data-chart]').forEach(el => {
      try { LiveChart(el); } catch (e) { el.innerHTML = '<div class="muted">Chart failed to load: ' + esc(e) + '</div>'; console.error(e); }
    });
    poll();
  });
})();
