/* Stock page: order ticket (simulation + journal), position sizer, technical gauges, lazy tabs. */
(function () {
  'use strict';
  const S = window.STOCK;
  const { fmt, signed, esc, rupiah, tickSize } = Live;
  const settings = S.settings;
  let side = 'buy';

  /* ------------------------------------------------------------ ticket */
  function stepPrice(price, dir) {
    if (dir > 0) return price + tickSize(price);
    const down = price - tickSize(price - 1);
    return Math.max(down, 1);
  }

  function ticketHtml() {
    return `
      <div class="seg-tabs"><button data-side="buy" class="buy on">Buy</button><button data-side="sell" class="sell">Sell</button></div>
      <div class="t-row">Price<div class="stepper"><button data-step="-1">−</button><input id="t-price" inputmode="numeric"><button data-step="1">+</button></div></div>
      <div class="quick" id="t-pxq"></div>
      <div class="t-warn" id="t-warn"></div>
      <div class="t-row">Lots (1 lot = 100 shares)<div class="stepper"><button data-lot="-1">−</button><input id="t-lots" inputmode="numeric" value="1"><button data-lot="1">+</button></div></div>
      <div class="quick" id="t-lotq"></div>
      <div class="t-sum" id="t-sum"></div>
      <button class="btn buy" id="t-submit">Record BUY in my portfolio</button>
      <div class="sizer">
        <b style="font-size:13px">Position sizer</b>
        <div class="grid2" style="margin-top:6px">
          <label class="t-row">Capital (Rp)<input id="z-cap" value="${settings.capital || ''}" placeholder="e.g. 50000000"></label>
          <label class="t-row">Risk % / trade<input id="z-risk" value="${settings.risk_pct}"></label>
          <label class="t-row">Stop / exit<input id="z-stop" value="${S.exit && S.exit < S.price ? S.exit : ''}"></label>
          <label class="t-row">Target<input id="z-target" value="${S.targets[0] || ''}"></label>
        </div>
        <div id="z-out" class="t-sum"></div>
      </div>
      <p class="muted">Simulation &amp; journal only - this app never sends orders. Trade in your broker app, then record it here
        so your average, lots, P/L and alerts stay correct. Prices ~10 min delayed.</p>`;
  }

  const val = id => parseFloat(String(document.getElementById(id).value).replace(/[^0-9.]/g, '')) || 0;
  const livePrice = () => (Live.quotes[S.code] && Live.quotes[S.code].price) || S.price;

  function update() {
    const price = val('t-price'), lots = Math.max(0, Math.round(val('t-lots')));
    const shares = lots * 100, gross = price * shares;
    const feePct = side === 'buy' ? settings.fee_buy_pct : settings.fee_sell_pct;
    const fee = gross * feePct / 100, total = side === 'buy' ? gross + fee : gross - fee;
    let rows = `<div class="kv"><span>Shares</span><b>${fmt(shares)}</b></div>
      <div class="kv"><span>Gross</span><b>${rupiah(gross)}</b></div>
      <div class="kv"><span>Fee (${feePct}%)</span><b>${rupiah(fee)}</b></div>
      <div class="kv"><span>${side === 'buy' ? 'You pay' : 'You receive'}</span><b>${rupiah(total)}</b></div>`;
    if (side === 'sell' && S.held && S.held.avg) {
      const pl = (price - S.held.avg) * shares - fee - S.held.avg * shares * settings.fee_buy_pct / 100;
      rows += `<div class="kv"><span>Est. realized P/L</span><b class="${pl >= 0 ? 'green' : 'red'}">${rupiah(pl)} (${signed(pl / (S.held.avg * shares || 1) * 100, 1)})</b></div>`;
    }
    if (side === 'buy' && S.held && S.held.lots && S.held.avg) {
      const avg = (S.held.avg * S.held.lots + price * lots) / (S.held.lots + lots);
      rows += `<div class="kv"><span>New average</span><b>${fmt(avg, 1)}</b></div>`;
    }
    document.getElementById('t-sum').innerHTML = rows;

    const warn = [];
    if (price && price % tickSize(price) !== 0) warn.push(`Not a valid price step - IDX tick here is Rp${tickSize(price)}.`);
    if (S.ara && price > S.ara) warn.push(`Above today's ARA ${fmt(S.ara)} - the exchange would reject it.`);
    if (S.arb && price < S.arb) warn.push(`Below today's ARB ${fmt(S.arb)} - the exchange would reject it.`);
    if (side === 'sell' && (!S.held || !S.held.lots)) warn.push('No lots recorded for this stock - set lots on the Portfolio page first.');
    if (side === 'sell' && S.held && S.held.lots && lots > S.held.lots) warn.push(`You only hold ${S.held.lots} lots.`);
    document.getElementById('t-warn').innerHTML = warn.join('<br>');

    /* position sizer */
    const cap = val('z-cap'), risk = val('z-risk'), stop = val('z-stop'), target = val('z-target');
    let z = '';
    if (stop && stop < price) {
      const perShare = price - stop;
      const riskPct = perShare / price * 100;
      z += `<div class="kv"><span>Risk per share</span><b>${fmt(perShare)} (${fmt(riskPct, 1)}%)</b></div>`;
      if (cap) {
        const maxLoss = cap * risk / 100;
        const byRisk = Math.floor(maxLoss / (perShare * 100));
        const byCash = Math.floor(cap / (price * 100 * (1 + settings.fee_buy_pct / 100)));
        const size = Math.max(0, Math.min(byRisk, byCash));
        z += `<div class="kv"><span>Max loss allowed</span><b>${rupiah(maxLoss)}</b></div>
          <div class="kv"><span>Lots by risk / by cash</span><b>${fmt(byRisk)} / ${fmt(byCash)}</b></div>
          <div class="kv"><span>Suggested size</span><b class="green">${fmt(size)} lots · ${rupiah(size * price * 100)}</b></div>
          <div class="quick" style="margin:6px 0 0"><button id="z-use" data-lots="${size}">Use ${fmt(size)} lots</button></div>`;
      } else {
        z += '<div class="small">Enter your capital to get a lot size.</div>';
      }
      if (target > price) z += `<div class="kv"><span>Reward : risk</span><b class="${(target - price) / perShare >= 2 ? 'green' : 'amber'}">${fmt((target - price) / perShare, 2)} : 1</b></div>`;
      if (riskPct > 10) z += '<div class="small amber">Stop is more than 10% away - consider a smaller size.</div>';
    } else {
      z = '<div class="small">Set a stop below the price to size the position by risk.</div>';
    }
    document.getElementById('z-out').innerHTML = z;
    const use = document.getElementById('z-use');
    if (use) use.onclick = () => { document.getElementById('t-lots').value = use.dataset.lots; update(); };
  }

  function quickButtons() {
    const px = [['Last', () => livePrice()], ['−1 tick', () => stepPrice(val('t-price'), -1)], ['+1 tick', () => stepPrice(val('t-price'), 1)]];
    if (S.exit) px.push(['Exit ' + fmt(S.exit), () => S.exit]);
    if (S.targets[0]) px.push(['T1 ' + fmt(S.targets[0]), () => S.targets[0]]);
    const pxBox = document.getElementById('t-pxq');
    pxBox.innerHTML = px.map((p, i) => `<button data-i="${i}">${p[0]}</button>`).join('');
    pxBox.querySelectorAll('button').forEach(b => b.onclick = () => { document.getElementById('t-price').value = px[+b.dataset.i][1](); update(); });
    const lotBox = document.getElementById('t-lotq');
    const fractions = [0.25, 0.5, 0.75, 1];
    lotBox.innerHTML = fractions.map(f => `<button data-f="${f}">${f * 100}%</button>`).join('');
    lotBox.querySelectorAll('button').forEach(b => b.onclick = () => {
      const f = +b.dataset.f, price = val('t-price');
      let lots = 0;
      if (side === 'sell') lots = Math.floor(((S.held && S.held.lots) || 0) * f);
      else {
        const cap = val('z-cap');
        if (!cap) { Live.toast('Enter your capital in the position sizer first.'); return; }
        lots = Math.floor(cap * f / (price * 100 * (1 + settings.fee_buy_pct / 100)));
      }
      document.getElementById('t-lots').value = Math.max(lots, 0);
      update();
    });
  }

  function setSide(next) {
    side = next;
    document.querySelectorAll('.seg-tabs button').forEach(b => b.classList.toggle('on', b.dataset.side === side));
    const submit = document.getElementById('t-submit');
    submit.className = 'btn ' + side;
    submit.textContent = `Record ${side.toUpperCase()} in my portfolio`;
    if (side === 'sell' && S.held && S.held.lots) document.getElementById('t-lots').value = S.held.lots;
    update();
  }

  function setupTicket() {
    const box = document.getElementById('ticket');
    box.innerHTML = ticketHtml();
    document.getElementById('t-price').value = S.price || '';
    box.querySelectorAll('[data-side]').forEach(b => b.onclick = () => setSide(b.dataset.side));
    box.querySelectorAll('[data-step]').forEach(b => b.onclick = () => {
      document.getElementById('t-price').value = stepPrice(val('t-price'), +b.dataset.step); update();
    });
    box.querySelectorAll('[data-lot]').forEach(b => b.onclick = () => {
      document.getElementById('t-lots').value = Math.max(1, Math.round(val('t-lots')) + +b.dataset.lot); update();
    });
    box.querySelectorAll('input').forEach(i => i.addEventListener('input', update));
    document.getElementById('t-submit').onclick = async () => {
      const price = val('t-price'), lots = Math.round(val('t-lots'));
      if (!price || !lots) { Live.toast('Enter a price and lots.', 'error'); return; }
      if (!confirm(`Record ${side.toUpperCase()} ${S.code} ${lots} lots @ ${fmt(price)} in your portfolio and journal?\n\n(Only do this after the order is actually filled in your broker app.)`)) return;
      try {
        const entry = await Live.api('/api/trade', { method: 'POST', body: JSON.stringify({ code: S.code, side, price, lots }) });
        Live.toast(`Recorded ${side.toUpperCase()} ${S.code} ${lots} lots @ ${fmt(price)}` +
          (entry.realized !== null && entry.realized !== undefined ? ` · realized ${rupiah(entry.realized)}` : ''));
        setTimeout(() => location.reload(), 1200);
      } catch (e) { Live.toast('Not recorded: ' + e.message, 'error', true); }
    };
    quickButtons();
    update();
  }

  /* ------------------------------------------------------------ gauges */
  function setupGauges() {
    const t = S.technicals || {};
    if (!t.summary) { document.getElementById('gauges').innerHTML = '<span class="muted">No technical data.</span>'; return; }
    document.getElementById('gauges').innerHTML = Live.gauge(t.oscillators.value, t.oscillators.label, t.oscillators.counts, 'Oscillators')
      + Live.gauge(t.summary.value, t.summary.label, t.summary.counts, 'Summary')
      + Live.gauge(t.moving.value, t.moving.label, t.moving.counts, 'Moving avgs');
  }

  /* ------------------------------------------------------------ lazy tabs */
  const loaded = {};
  async function loadDividends() {
    const box = document.getElementById('div-box');
    try {
      const rows = await Live.api('/api/dividends/' + S.code);
      if (!rows.length) { box.innerHTML = '<p class="muted">No dividends recorded in the last 10 years.</p>'; return; }
      const years = {};
      rows.forEach(r => years[r.year] = (years[r.year] || 0) + r.amount);
      const entries = Object.entries(years).sort(), max = Math.max(...Object.values(years));
      box.innerHTML = `<div class="two-col"><div><h4>Dividend per share by year</h4><div class="dps" style="height:120px;margin:22px 0 26px">
        ${entries.map(([y, v]) => `<div style="height:${Math.max(3, v / max * 100)}px"><b>${fmt(v, v < 10 ? 1 : 0)}</b><span>${String(y).slice(2)}</span></div>`).join('')}</div>
        <p class="muted">Amounts are per share in Rupiah (Yahoo Finance). Yield = dividend ÷ close before the ex-date.</p></div>
        <div><h4>History</h4><table class="mini"><thead><tr><th>Ex-date</th><th class="num">Dividend</th><th class="num">Yield</th></tr></thead>
        <tbody>${rows.map(r => `<tr><td>${esc(r.ex_date)}</td><td class="num">${fmt(r.amount, r.amount < 10 ? 2 : 0)}</td><td class="num">${fmt(r.yield_pct, 2)}%</td></tr>`).join('')}</tbody></table></div></div>`;
    } catch (e) { box.innerHTML = `<p class="muted">Dividend data unavailable: ${esc(e.message)}</p>`; }
  }
  async function loadNews() {
    const box = document.getElementById('news-box');
    try {
      const items = await Live.api('/api/news/' + S.code);
      box.innerHTML = items.length ? `<ul class="news" style="list-style:none;padding:0">${items.map(n => `<li style="margin-bottom:10px">
        <span class="dot ${esc(n.tone)}"></span><a href="${esc(n.link)}" target="_blank" rel="noopener">${esc(n.title)}</a>
        <div class="muted">${esc(n.source)} · ${esc(n.time || '')}</div></li>`).join('')}</ul>
        <p class="muted">Google News, last 30 days. Dot = rough headline tone - read before acting.</p>` : '<p class="muted">No recent news found.</p>';
    } catch (e) { box.innerHTML = `<p class="muted">News unavailable: ${esc(e.message)}</p>`; }
  }
  async function loadAlerts() {
    const list = document.getElementById('al-list');
    const alerts = await Live.api('/api/alerts?code=' + S.code);
    list.innerHTML = alerts.length ? `<table class="mini"><thead><tr><th>Condition</th><th>Status</th><th>Note</th><th></th></tr></thead><tbody>
      ${alerts.map(a => `<tr><td>${esc(a.text)}</td><td>${a.active ? '<span class="pill-green">active</span>' : `<span class="pill-grey">hit ${esc(a.triggered)} @ ${fmt(a.trigger_price)}</span>`}</td>
      <td class="small">${esc(a.note)}</td><td><button class="link-btn" data-del="${a.id}">✕</button></td></tr>`).join('')}</tbody></table>`
      : '<p class="muted">No alerts for this stock yet.</p>';
    list.querySelectorAll('[data-del]').forEach(b => b.onclick = async () => {
      await Live.api('/api/alerts?id=' + b.dataset.del, { method: 'DELETE' });
      const chart = document.querySelector('[data-chart]').liveChart;
      const remaining = await Live.api('/api/alerts?code=' + S.code);
      if (chart) chart.setAlerts(remaining.filter(a => a.active && (a.type === 'above' || a.type === 'below')).map(a => a.value));
      loadAlerts();
    });
  }
  function setupAlerts() {
    document.getElementById('al-value').placeholder = fmt(S.price);
    document.getElementById('al-type').addEventListener('change', e => {
      document.getElementById('al-value').placeholder = e.target.value.startsWith('change') ? 'e.g. 5' : fmt(S.price);
    });
    document.getElementById('al-add').onclick = async () => {
      const type = document.getElementById('al-type').value, value = val('al-value');
      if (!value) { Live.toast('Enter a value.', 'error'); return; }
      try {
        const alert = await Live.api('/api/alerts', { method: 'POST', body: JSON.stringify({ code: S.code, type, value, note: document.getElementById('al-note').value }) });
        Live.toast('🔔 Alert set: ' + alert.text);
        const chart = document.querySelector('[data-chart]').liveChart;
        if (chart && (type === 'above' || type === 'below')) chart.addAlert(value);
        document.getElementById('al-value').value = ''; document.getElementById('al-note').value = '';
        loadAlerts();
      } catch (e) { Live.toast('Not saved: ' + e.message, 'error'); }
    };
    document.addEventListener('alerts-changed', loadAlerts);
  }

  document.addEventListener('DOMContentLoaded', () => {
    setupTicket();
    setupGauges();
    setupAlerts();
    document.getElementById('stock-tabs').addEventListener('tab', e => {
      const tab = e.detail;
      if (loaded[tab]) return;
      loaded[tab] = true;
      if (tab === 'div') loadDividends();
      if (tab === 'news') loadNews();
      if (tab === 'alerts') loadAlerts();
    });
    Live.listeners.push(quotes => {
      const q = quotes[S.code];
      if (q && q.price) S.price = q.price;
    });
  });
})();
