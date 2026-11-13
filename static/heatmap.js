/* Market heatmap: squarified treemap, sectors -> stocks, coloured by % change. */
(function () {
  'use strict';
  const { fmt, signed, esc } = Live;
  let stocks = [];

  function color(change) {
    if (change === null || change === undefined || isNaN(change)) return '#3a4256';
    const t = Math.max(-1, Math.min(1, change / 5));
    const mix = (a, b, k) => Math.round(a + (b - a) * k);
    const grey = [58, 66, 86], green = [22, 163, 74], red = [190, 30, 60];
    const target = t >= 0 ? green : red, k = Math.abs(t) ** 0.8;
    return `rgb(${mix(grey[0], target[0], k)},${mix(grey[1], target[1], k)},${mix(grey[2], target[2], k)})`;
  }

  /* squarified treemap (Bruls et al.) */
  function squarify(items, x, y, w, h) {
    const total = items.reduce((a, b) => a + b.size, 0);
    if (!total || w <= 0 || h <= 0) return [];
    const scale = (w * h) / total;
    const nodes = items.map(item => ({ item, area: item.size * scale })).sort((a, b) => b.area - a.area);
    const out = [];
    let row = [], rect = { x, y, w, h };
    const worst = (row, side) => {
      const sum = row.reduce((a, b) => a + b.area, 0), max = Math.max(...row.map(r => r.area)), min = Math.min(...row.map(r => r.area));
      return Math.max((side * side * max) / (sum * sum), (sum * sum) / (side * side * min));
    };
    const layoutRow = (row, rect) => {
      const sum = row.reduce((a, b) => a + b.area, 0);
      if (rect.w >= rect.h) {
        const width = sum / rect.h; let cy = rect.y;
        row.forEach(r => { const hh = r.area / width; out.push({ item: r.item, x: rect.x, y: cy, w: width, h: hh }); cy += hh; });
        return { x: rect.x + width, y: rect.y, w: rect.w - width, h: rect.h };
      }
      const height = sum / rect.w; let cx = rect.x;
      row.forEach(r => { const ww = r.area / height; out.push({ item: r.item, x: cx, y: rect.y, w: ww, h: height }); cx += ww; });
      return { x: rect.x, y: rect.y + height, w: rect.w, h: rect.h - height };
    };
    nodes.forEach(node => {
      const side = Math.min(rect.w, rect.h);
      if (!row.length || worst(row.concat(node), side) <= worst(row, side)) row.push(node);
      else { rect = layoutRow(row, rect); row = [node]; }
    });
    if (row.length) layoutRow(row, rect);
    return out;
  }

  function render() {
    const box = document.getElementById('heatmap');
    const universe = document.getElementById('hm-universe').value;
    const sizeKey = document.getElementById('hm-size').value;
    const period = document.getElementById('hm-period').value;
    let rows = stocks.slice();
    if (universe === 'top') rows = rows.sort((a, b) => b.mcap_t - a.mcap_t).slice(0, 150);
    else if (universe === 'liquid') rows = rows.filter(s => s.value_bn >= 10);
    else rows = rows.filter(s => s.value_bn >= 1);
    rows = rows.filter(s => s[sizeKey] > 0);
    const sectors = {};
    rows.forEach(s => (sectors[s.sector] = sectors[s.sector] || []).push(s));
    const sectorItems = Object.entries(sectors).map(([name, list]) => ({ name, list, size: list.reduce((a, b) => a + b[sizeKey], 0) }));
    const W = box.clientWidth, H = box.clientHeight;
    let html = '';
    squarify(sectorItems, 0, 0, W, H).forEach(sec => {
      const head = sec.h > 40 && sec.w > 60 ? 16 : 0;
      html += `<div class="hm-sector" style="left:${sec.x}px;top:${sec.y}px;width:${sec.w}px;height:${sec.h}px">`
        + (head ? `<div class="hm-title">${esc(sec.item.name)}</div>` : '');
      squarify(sec.item.list.map(s => ({ ...s, size: s[sizeKey] })), 0, head, sec.w, sec.h - head).forEach(t => {
        const s = t.item, change = s[period];
        const big = t.w > 46 && t.h > 30, huge = t.w > 90 && t.h > 54;
        html += `<div class="hm-tile" data-code="${esc(s.code)}" style="left:${t.x}px;top:${t.y}px;width:${t.w}px;height:${t.h}px;background:${color(change)}">`
          + (big ? `<b style="font-size:${huge ? 16 : 12}px">${esc(s.code)}</b><small>${signed(change, period === 'change' ? 2 : 1)}</small>` : '')
          + `</div>`;
      });
      html += '</div>';
    });
    box.innerHTML = html;
    const up = rows.filter(s => s[period] > 0).length, down = rows.filter(s => s[period] < 0).length;
    document.getElementById('hm-info').innerHTML = `${rows.length} stocks · <span class="green">▲ ${up}</span> <span class="red">▼ ${down}</span>`;
  }

  async function load() {
    try {
      const data = await Live.api('/api/heatmap');
      stocks = data.stocks;
      render();
    } catch (e) { Live.toast('Heatmap data failed: ' + e.message, 'error'); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('heatmap'), tip = document.getElementById('hm-tip');
    ['hm-universe', 'hm-size', 'hm-period'].forEach(id => document.getElementById(id).addEventListener('change', render));
    window.addEventListener('resize', render);
    box.addEventListener('click', e => { const t = e.target.closest('.hm-tile'); if (t) location.href = '/stock/' + t.dataset.code; });
    box.addEventListener('mousemove', e => {
      const t = e.target.closest('.hm-tile');
      if (!t) { tip.style.display = 'none'; return; }
      const s = stocks.find(x => x.code === t.dataset.code);
      if (!s) return;
      tip.innerHTML = `<b>${esc(s.code)}</b> <span class="muted">${esc(s.sector)}</span><div class="muted">${esc(s.name)}</div>
        <div class="kv"><span>Price</span><b>${fmt(s.price)}</b></div>
        <div class="kv"><span>Today</span><b class="${s.change >= 0 ? 'green' : 'red'}">${signed(s.change, 2)}</b></div>
        <div class="kv"><span>1W / 1M</span><b>${signed(s.perf_1w, 1)} / ${signed(s.perf_1m, 1)}</b></div>
        <div class="kv"><span>Value today</span><b>Rp${fmt(s.value_bn, 1)}bn</b></div>
        <div class="kv"><span>Market cap</span><b>Rp${fmt(s.mcap_t, 1)}T</b></div>`;
      tip.style.display = 'block';
      tip.style.left = Math.min(e.clientX + 14, window.innerWidth - 230) + 'px';
      tip.style.top = Math.min(e.clientY + 14, window.innerHeight - 170) + 'px';
    });
    box.addEventListener('mouseleave', () => tip.style.display = 'none');
    load();
    setInterval(() => { if (!document.hidden) load(); }, 45000);
  });
})();
