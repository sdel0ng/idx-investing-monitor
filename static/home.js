/* Home dashboard: breadth, sectors, movers, favorites (sparklines), portfolio, alerts. */
(function () {
  'use strict';
  const { fmt, signed, esc, rupiah } = Live;
  let moversTab = 'gainers';
  let data = null;

  const logo = (s, size = 26) => s.logo
    ? `<img class="stock-logo" src="${esc(s.logo)}" width="${size}" height="${size}" alt="" onerror="this.replaceWith(Object.assign(document.createElement('span'),{className:'logo-fallback',style:'width:${size}px;height:${size}px',textContent:'${esc(s.code).slice(0, 2)}'}))">`
    : `<span class="logo-fallback" style="width:${size}px;height:${size}px">${esc((s.code || '').slice(0, 2))}</span>`;
  const pill = v => `<span class="chg-pill ${v > 0 ? 'up' : v < 0 ? 'down' : 'flat'}">${signed(v, 2)}</span>`;
  const stockCell = s => `<div class="stock-cell">${logo(s)}<div><b>${esc(s.code)}</b> <span class="star" data-star="${esc(s.code)}">☆</span>
    <div class="nm">${esc(s.name || '')}</div></div></div>`;

  function moverTable(rows, tab) {
    if (!rows || !rows.length) return '<p class="muted">Nothing here right now.</p>';
    const extra = { volume: ['Volume', s => Live.volumeText(s.volume)], unusual: ['Rel. volume', s => fmt(s.rvol, 1) + '×'],
                    ara: ['ARA', s => fmt(s.ara)], arb: ['ARB', s => fmt(s.arb)] }[tab] || ['Mkt cap', s => fmt(s.mcap_t, 1) + 'T'];
    return `<table class="market"><thead><tr><th>#</th><th>Stock</th><th class="num">Price</th><th class="num">Change</th>
      <th class="num">Value</th><th class="num">${extra[0]}</th></tr></thead><tbody>${rows.map((s, i) => `
      <tr onclick="location.href='/stock/${esc(s.code)}'"><td class="muted">${i + 1}</td><td>${stockCell(s)}</td>
      <td class="num"><b>${fmt(s.price)}</b></td><td class="num">${pill(s.change)}</td>
      <td class="num">Rp${fmt(s.value_bn, s.value_bn < 10 ? 1 : 0)}bn</td><td class="num">${extra[1](s)}</td></tr>`).join('')}</tbody></table>`;
  }

  function renderMovers() {
    if (!data || !data.movers) return;
    document.querySelectorAll('#movers [data-panel]').forEach(panel => {
      panel.innerHTML = moverTable(data.movers[panel.dataset.panel], panel.dataset.panel);
    });
    Live.paintStars(document.getElementById('movers'));
  }

  function renderBreadth(b) {
    const total = b.advancers + b.decliners + b.unchanged || 1;
    document.getElementById('breadth').innerHTML = `
      <div class="kv"><span>Advancing</span><b class="green">${b.advancers}</b></div>
      <div class="kv"><span>Declining</span><b class="red">${b.decliners}</b></div>
      <div class="breadth-bar"><i style="width:${b.advancers / total * 100}%;background:#22c55e"></i>
        <i style="width:${b.unchanged / total * 100}%;background:#64748b"></i><i style="width:${b.decliners / total * 100}%;background:#f04461"></i></div>
      <div class="kv"><span>Unchanged</span><b>${b.unchanged}</b></div>
      <div class="kv"><span>Value traded</span><b>Rp${fmt(b.total_value_bn / 1000, 2)}T</b></div>
      <div class="kv"><span>Liquid above MA200</span><b>${b.above_ma200_pct}%</b></div>`;
    const max = Math.max(...b.sectors.map(s => Math.abs(s.change)), 1);
    document.getElementById('sectors').innerHTML = b.sectors.map(s => {
      const width = Math.abs(s.change) / max * 50;
      const bar = s.change >= 0 ? `left:50%;width:${width}%;background:#22c55e` : `right:50%;width:${width}%;background:#f04461`;
      return `<div class="sector-row" title="${s.up} up / ${s.down} down · Rp${fmt(s.value_bn, 0)}bn traded">
        <span>${esc(s.sector)}</span><div class="sbar"><i style="${bar}"></i></div><b class="num ${s.change >= 0 ? 'green' : 'red'}">${signed(s.change, 2)}</b></div>`;
    }).join('');
  }

  function renderPortfolio(p) {
    const box = document.getElementById('pf');
    if (!p || !p.holdings.length) { box.innerHTML = '<span class="muted">No holdings yet.</span>'; return; }
    const lines = p.holdings.map(h => `<div class="kv"><span>${esc(h.code)} ${h.lots ? h.lots + ' lots' : ''}</span>
      <b class="${(h.pnl_pct || 0) >= 0 ? 'green' : 'red'}">${signed(h.pnl_pct, 1)}</b></div>`).join('');
    box.innerHTML = (p.value ? `<div class="big-num">${rupiah(p.value)}</div>
      <div class="kv"><span>Today</span><b class="${p.today_rp >= 0 ? 'green' : 'red'}">${rupiah(p.today_rp)}</b></div>
      <div class="kv"><span>Unrealized</span><b class="${p.pnl_rp >= 0 ? 'green' : 'red'}">${rupiah(p.pnl_rp)} (${signed(p.pnl_pct, 1)})</b></div>` : '')
      + lines + (p.lots_known ? '' : '<div class="small">Add lots on the Portfolio page to see Rupiah values.</div>');
  }

  function renderAlerts(alerts) {
    const box = document.getElementById('alerts-box');
    box.innerHTML = alerts.length ? alerts.slice(0, 8).map(a => `<div class="kv"><span><a href="/stock/${esc(a.code)}">${esc(a.text)}</a></span>
      <b data-q="${esc(a.code)}" data-f="price">–</b></div>`).join('') : '<span class="muted">No active alerts. Open a stock and click 🔔.</span>';
  }

  function renderPlans(plans) {
    const box = document.getElementById('plans-box');
    if (!plans || !plans.length) { box.innerHTML = '<span class="muted">No queue plans. Open a stock → 📋 Queue plan tab.</span>'; return; }
    box.innerHTML = plans.map(p => {
      const s = p.summary, open = p.entries.filter(e => !e.filled);
      const next = open.length ? open.reduce((a, e) => (e.price > a.price ? e : a), open[0]) : null;
      const filled = p.entries.length - open.length;
      return `<a class="plan-mini" href="/stock/${esc(p.code)}">
        <div class="kv"><b>${esc(p.code)}</b><b data-q="${esc(p.code)}" data-f="price">${fmt(s.price)}</b></div>
        <div class="kv"><span>Next queue</span><b>${next ? fmt(next.price) + ' × ' + next.lots + ' lot · <span data-q="' + esc(p.code) + '" data-f="dist" data-level="' + next.price + '">' + signed((next.price / s.price - 1) * 100, 1) + '</span>' : 'all filled'}</b></div>
        <div class="kv"><span>${filled}/${p.entries.length} filled · avg if all</span><b>${fmt(s.avg)}</b></div>
        <div class="kv"><span>Exit (close) · T1</span><b><span class="red">${fmt(p.exit.price)}</span> · <span class="green">${p.targets[0] ? fmt(p.targets[0]) : '–'}</span></b></div>
        <div class="small ${s.status_class}">${esc(s.status)}</div></a>`;
    }).join('');
  }

  async function renderFavorites(favs) {
    const box = document.getElementById('favs');
    if (!favs.length) { box.innerHTML = '<span class="muted">No favorites yet.</span>'; return; }
    box.innerHTML = `<table class="market"><thead><tr><th>Stock</th><th class="num">Price</th><th class="num">Change</th><th>Today</th>
      <th class="num">Value</th><th class="num">Mkt cap</th></tr></thead><tbody>${favs.map(s => `
      <tr onclick="location.href='/stock/${esc(s.code)}'"><td>${stockCell(s)}</td>
      <td class="num"><b data-q="${esc(s.code)}" data-f="price">${fmt(s.price)}</b></td>
      <td class="num" data-q="${esc(s.code)}" data-f="chg">${signed(s.change, 2)}</td>
      <td id="sp-${esc(s.code)}"><span class="muted">…</span></td>
      <td class="num">${s.value_bn !== undefined ? 'Rp' + fmt(s.value_bn, s.value_bn < 10 ? 1 : 0) + 'bn' : '–'}</td>
      <td class="num">${s.mcap_t ? fmt(s.mcap_t, 1) + 'T' : '–'}</td></tr>`).join('')}</tbody></table>`;
    Live.paintStars(box);
    try {
      const sparks = await Live.api('/api/sparks?codes=' + favs.map(s => s.code).join(','));
      favs.forEach(s => {
        const cell = document.getElementById('sp-' + s.code);
        const prev = s.price !== undefined && s.change !== undefined ? s.price / (1 + s.change / 100) : null;
        if (cell) cell.innerHTML = Live.spark(sparks[s.code], 120, 30, prev);
      });
    } catch (e) { /* sparklines are optional */ }
  }

  async function load() {
    try {
      data = await Live.api('/api/home?min_value=' + document.getElementById('minval').value);
    } catch (e) { Live.toast('Dashboard data failed: ' + e.message, 'error'); return; }
    if (data.breadth) renderBreadth(data.breadth);
    renderMovers();
    if (data.favorites) renderFavorites(data.favorites);
    renderPortfolio(data.portfolio);
    renderAlerts(data.alerts || []);
    renderPlans(data.plans || []);
    Live.refresh();
  }

  document.addEventListener('DOMContentLoaded', () => {
    Live.miniChart(document.getElementById('mini-IHSG'), 'IHSG');
    Live.miniChart(document.getElementById('mini-LQ45'), 'LQ45');
    document.getElementById('minval').addEventListener('change', load);
    load();
    setInterval(() => { if (!document.hidden) load(); }, 45000);
  });
})();
