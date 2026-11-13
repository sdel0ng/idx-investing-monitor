/* Queue plan (antri beli) on the stock page: staggered buy queues, exit on daily close, targets, breakout option.
   Saving a plan creates alerts at every queue, the breakout trigger and the exit zone. */
(function () {
  'use strict';
  const S = window.STOCK;
  const { fmt, signed, esc, rupiah, tickSize } = Live;
  let plan = S.plan;
  let editing = !plan;

  const roundTick = (p, mode = 'nearest') => {
    const t = tickSize(p);
    return mode === 'down' ? Math.floor(p / t) * t : mode === 'up' ? Math.ceil(p / t) * t : Math.round(p / t) * t;
  };
  const livePrice = () => (Live.quotes[S.code] && Live.quotes[S.code].price) || S.price;
  const box = () => document.getElementById('qplan-box');

  /* ------------------------------------------------------------ maths shared by view + editor preview */
  function calc(entries, exit, targets) {
    const valid = entries.filter(e => e.price > 0 && e.lots > 0);
    const lots = valid.reduce((a, e) => a + e.lots, 0);
    if (!lots || !exit) return null;
    const avg = valid.reduce((a, e) => a + e.price * e.lots, 0) / lots;
    const risk = avg - exit;
    return {
      lots, avg, risk, riskPct: risk / avg * 100, riskRp: risk * lots * 100, cost: avg * lots * 100,
      targets: targets.filter(t => t > 0).map(t => ({ price: t, gain: (t / avg - 1) * 100, rr: risk > 0 ? (t - avg) / risk : null })),
    };
  }
  function statusOf(p, price) {
    const open = p.entries.filter(e => !e.filled);
    if (price <= p.exit.price) return ['Below the exit zone - the plan is invalid if it CLOSES here', 'red'];
    if (!open.length) return ['All queues filled - manage with the exit and targets', 'green'];
    if (open.some(e => price <= e.price)) return ['Price reached a queue level - check whether your order filled', 'amber'];
    if (p.breakout && p.breakout.trigger && price >= p.breakout.trigger) return ['Above the breakout trigger - valid only on a daily CLOSE above it', 'amber'];
    const next = open.reduce((a, e) => (e.price > a.price ? e : a), open[0]);
    return [`Waiting - next queue ${fmt(next.price)} is ${signed((next.price / price - 1) * 100, 1)} away`, 'grey'];
  }
  function capitalLine(riskRp) {
    const cap = S.settings.capital;
    return cap ? ` · ${fmt(riskRp / cap * 100, 2)}% of your capital` : ' · set capital in ⚙ Settings to see % of capital';
  }

  /* ------------------------------------------------------------ view */
  function viewHtml(p) {
    const price = livePrice();
    const c = calc(p.entries, p.exit.price, p.targets);
    const [status, cls] = statusOf(p, price);
    const rows = p.entries.map((e, i) => `
      <tr class="${e.filled ? 'filled' : ''}">
        <td><b>Queue ${i + 1}</b></td><td class="num"><b>${fmt(e.price)}</b></td><td class="num">${e.lots}</td>
        <td class="num">${rupiah(e.price * e.lots * 100)}</td>
        <td class="num" data-q="${esc(S.code)}" data-f="dist" data-level="${e.price}">${signed((e.price / price - 1) * 100, 1)}</td>
        <td class="num">${p.targets[0] ? fmt((p.targets[0] - e.price) / (e.price - p.exit.price), 1) + 'R' : '–'}</td>
        <td>${e.filled ? `<span class="pill-green">filled ${esc(e.filled_date || '')}${e.fill_price && e.fill_price !== e.price ? ' @ ' + fmt(e.fill_price) : ''}</span>`
          : `<button class="chip" data-fill="${i}">Mark filled</button>`}</td>
      </tr>`).join('');
    const targets = c.targets.map((t, i) => `<tr><td class="green">T${i + 1}</td><td class="num"><b>${fmt(t.price)}</b></td>
      <td class="num green">${signed(t.gain, 1)}</td><td class="num">${t.rr === null ? '–' : fmt(t.rr, 1) + 'R'}</td><td>sell ⅓</td></tr>`).join('');
    const b = p.breakout || {};
    return `
      <div class="plan-status ${cls}" id="plan-status">${esc(status)}</div>
      <div class="two-col" style="margin-top:12px">
        <div>
          <h4>Buy queues (limit orders)</h4>
          <table class="mini"><thead><tr><th></th><th class="num">Price</th><th class="num">Lots</th><th class="num">Cost</th>
            <th class="num">From now</th><th class="num">R:R to T1</th><th></th></tr></thead><tbody>${rows}</tbody></table>
          <div class="plan-kpis">
            <div><span>If all fill · avg</span><b>${fmt(c.avg)}</b></div>
            <div><span>Total</span><b>${c.lots} lots · ${rupiah(c.cost)}</b></div>
            <div><span>Risk to exit</span><b class="red">${fmt(c.risk)} (${fmt(c.riskPct, 1)}%)</b></div>
            <div><span>Max loss</span><b class="red">${rupiah(c.riskRp)}</b><small>${capitalLine(c.riskRp)}</small></div>
          </div>
        </div>
        <div>
          <h4>Exit &amp; targets</h4>
          <table class="mini"><tbody>
            <tr><td class="red"><b>EXIT</b></td><td class="num red"><b>${fmt(p.exit.price)}</b></td><td colspan="3">daily <b>close</b> below → sell all / cancel open queues</td></tr>
            ${targets}
          </tbody></table>
          ${b.trigger ? `<h4>Plan B - breakout</h4><div class="small">If the queues never fill: buy only after a daily <b>close above ${fmt(b.trigger)}</b>${b.stop ? `, exit on a close below <b>${fmt(b.stop)}</b>` : ''}. Don't chase intraday.</div>` : ''}
          ${p.note ? `<h4>Notes</h4><div class="small" style="white-space:pre-wrap">${esc(p.note)}</div>` : ''}
        </div>
      </div>
      <div class="plan-macro" id="plan-macro"><h4>Macro sync</h4><div class="muted">Loading gold / IHSG / USD-IDR…</div></div>
      <div class="plan-actions">
        <button class="btn" id="plan-edit">✎ Edit plan</button>
        <button class="chip" id="plan-done">✓ Plan finished</button>
        <button class="chip" id="plan-cancel">✕ Cancel plan</button>
        <span class="muted">Alerts are set at each queue, the breakout and the exit zone. IDX day orders expire at the close -
          re-enter your queues in the broker app each morning (or use its auto-order / GTC). Saved ${esc(p.updated)}.</span>
      </div>`;
  }

  /* ------------------------------------------------------------ macro sync */
  let macro = null;
  const num = (v, d) => (v === null || v === undefined ? '–' : fmt(v, d));
  const corrText = c => (c === null || c === undefined ? '–' : (c >= 0 ? '' : '−') + Math.abs(c).toFixed(2));
  const corrClass = c => (c === null || c === undefined ? '' : Math.abs(c) >= 0.6 ? 'green' : Math.abs(c) >= 0.3 ? 'amber' : 'muted');
  const stateCell = s => ({ up: '<span class="green">▲ up</span>', down: '<span class="red">▼ down</span>', mixed: '<span class="amber">◆ mixed</span>' }[s] || '–');

  function macroHtml(m) {
    if (m.error) return `<h4>Macro sync</h4><div class="t-warn">${esc(m.error)}</div>`;
    const rows = m.drivers.map(d => d.error ? `<tr><td>${esc(d.label)}</td><td colspan="8" class="muted">unavailable</td></tr>` : `
      <tr><td><b>${esc(d.label)}</b>${d.role === 'commodity' ? ' <span class="pill-green">driver</span>' : ''}</td>
        <td class="num"><b>${num(d.last, d.decimals)}</b></td>
        <td class="num ${d.chg_1d >= 0 ? 'green' : 'red'}">${signed(d.chg_1d, 1)}</td>
        <td class="num ${d.chg_5d >= 0 ? 'green' : 'red'}">${signed(d.chg_5d, 1)}</td>
        <td class="num">${num(d.ma20, d.decimals)}</td><td class="num">${num(d.ma50, d.decimals)}</td>
        <td>${stateCell(d.state)}</td>
        <td class="num ${corrClass(d.corr_60)}" title="20d ${corrText(d.corr_20)} · 120d ${corrText(d.corr_120)}"><b>${corrText(d.corr_60)}</b></td>
        <td class="small">${esc(d.read)}</td></tr>`).join('');
    const g = m.gold_idr;
    const goldRow = g ? `<tr><td><b>Gold in Rupiah</b> <span class="muted">/gram≈</span></td><td class="num"><b>Rp${fmt(Math.round(g.last / 1000) * 1000)}</b></td>
        <td class="num ${g.chg_1d >= 0 ? 'green' : 'red'}">${signed(g.chg_1d, 1)}</td><td class="num ${g.chg_5d >= 0 ? 'green' : 'red'}">${signed(g.chg_5d, 1)}</td>
        <td class="num">${fmt(Math.round(g.ma20 / 1000) * 1000)}</td><td class="num">${fmt(Math.round(g.ma50 / 1000) * 1000)}</td><td>${stateCell(g.state)}</td><td></td>
        <td class="small">What ${esc(m.code)} actually sells at (XAU × USD/IDR ÷ 31.1 g). Approximation, not Antam's Logam Mulia price.</td></tr>` : '';
    const rules = (m.rules || []).map((r, i) => `
      <tr><td>${r.action === 'pause' ? '<span class="red"><b>PAUSE</b></span>' : '<span class="green"><b>CONFIRM</b></span>'}</td>
        <td>${esc(r.label)} closes <b>${r.op}</b> <b>${num(r.level, r.level > 100 ? 0 : 2)}</b></td>
        <td class="num">now ${num(r.last, r.last > 100 ? 0 : 2)}</td>
        <td class="num">${r.distance_pct === null || r.distance_pct === undefined ? '' : signed(r.distance_pct, 1) + ' away'}</td>
        <td>${r.hit ? '<span class="pill-green" style="background:#7f1d1d;color:#fecaca">HIT</span>' : '<span class="muted">not yet</span>'}</td>
        <td class="small">${esc(r.why || '')}</td>
        <td>${m.rules_suggested ? '' : `<button class="link-btn" data-mrule-edit="${i}" title="change level">✎</button><button class="link-btn" data-mrule-del="${i}" title="remove">✕</button>`}</td></tr>`).join('');
    return `
      <h4>Macro sync <span class="muted" style="font-weight:400">· ${esc(m.sector_note)}</span></h4>
      <div class="two-col" style="gap:10px">
        <div class="plan-status ${m.verdict_class}">${esc(m.verdict)}</div>
        <div class="plan-status ${m.gate ? m.gate.class : 'grey'}">${esc(m.gate ? m.gate.text : '')}</div>
      </div>
      <table class="mini" style="margin-top:10px"><thead><tr><th>Driver</th><th class="num">Last</th><th class="num">1d</th><th class="num">5d</th>
        <th class="num">MA20</th><th class="num">MA50</th><th>Trend</th><th class="num" title="correlation of daily % changes with ${esc(m.code)}, last 60 days (1 = moves together)">Corr 60d</th><th>Read</th></tr></thead>
        <tbody>${rows}${goldRow}</tbody></table>
      ${m.lessons.length ? `<ul class="small macro-lessons">${m.lessons.map(l => `<li>${esc(l)}</li>`).join('')}</ul>` : ''}
      <h4>Macro rules for this plan ${m.rules_suggested ? '<span class="amber" style="font-weight:400">· suggested, not saved yet</span>' : ''}</h4>
      <table class="mini"><tbody>${rules || '<tr><td class="muted">No rules.</td></tr>'}</tbody></table>
      <div class="plan-actions" style="border:0;padding:6px 0 0">
        ${m.rules_suggested ? '<button class="btn buy" id="mrule-save" style="width:auto">Save macro rules &amp; alert me</button>' : '<button class="chip" id="mrule-reset">↺ Reset to suggested</button>'}
        <span class="muted">PAUSE = don't add to the queues (cancel open orders) while the rule is hit; your price exit still rules the position.
          CONFIRM = macro tailwind is real. Checked every 15 min in market hours with a popup. ${esc(m.source)} · ${esc(m.updated)}.</span>
      </div>`;
  }

  async function loadMacro(force) {
    const el = document.getElementById('plan-macro');
    if (!el) return;
    try {
      if (!macro || force) macro = await Live.api(`/api/plans/${S.code}/macro`);
    } catch (e) {
      macro = { error: 'Macro data unavailable: ' + e.message };
    }
    el.innerHTML = macroHtml(macro);
    const saveBtn = el.querySelector('#mrule-save');
    if (saveBtn) saveBtn.onclick = () => saveRules(macro.rules);
    const resetBtn = el.querySelector('#mrule-reset');
    if (resetBtn) resetBtn.onclick = () => { if (confirm('Replace your macro rules with fresh suggested levels?')) saveRules([]); };
    el.querySelectorAll('[data-mrule-del]').forEach(b => b.onclick = () => {
      const rules = macro.rules.filter((_, i) => i !== +b.dataset.mruleDel);
      saveRules(rules);
    });
    el.querySelectorAll('[data-mrule-edit]').forEach(b => b.onclick = () => {
      const rule = macro.rules[+b.dataset.mruleEdit];
      const answer = prompt(`${rule.label}: ${rule.action.toUpperCase()} when it closes ${rule.op} …`, rule.level);
      if (answer === null || !(+answer > 0)) return;
      saveRules(macro.rules.map((r, i) => (i === +b.dataset.mruleEdit ? Object.assign({}, r, { level: +answer, last_hit: null }) : r)));
    });
  }
  async function saveRules(rules) {
    try {
      macro = await Live.api(`/api/plans/${S.code}/macro`, { method: 'POST', body: JSON.stringify({ rules }) });
      Live.toast(macro.rules_suggested ? 'Macro rules reset to suggested levels (press Save to keep them)' : `🌐 ${S.code} macro rules saved · popup alerts on`);
      loadMacro();
    } catch (e) { Live.toast('Macro rules not saved: ' + e.message, 'error', true); }
  }

  /* ------------------------------------------------------------ editor */
  function defaults() {
    const price = livePrice();
    const q1 = roundTick(price * 0.99, 'down');
    let exit = S.exit && S.exit < q1 * 0.97 ? S.exit : roundTick(q1 * 0.94, 'down');
    const q2 = roundTick(q1 - (q1 - exit) * 0.55, 'down');
    exit = Math.min(exit, roundTick(q2 * 0.98, 'down'));
    const avg = (q1 + 2 * q2) / 3;
    let total = 3;
    if (S.settings.capital) total = Math.max(3, Math.floor(S.settings.capital * S.settings.risk_pct / 100 / ((avg - exit) * 100)));
    const first = Math.max(1, Math.round(total / 3));
    return {
      entries: [{ price: q1, lots: first }, { price: q2, lots: Math.max(1, total - first) }],
      exit: { price: exit }, targets: S.targets.slice(0, 3), breakout: { trigger: null, stop: null }, note: '',
    };
  }
  function editorHtml(p) {
    const row = (e, i) => `<div class="q-row" data-row="${i}">
        <b>Q${i + 1}</b><label>Price<input data-k="price" value="${e.price || ''}" inputmode="numeric"></label>
        <label>Lots<input data-k="lots" value="${e.lots || ''}" inputmode="numeric"></label>
        <input type="hidden" data-k="filled" value="${e.filled ? 1 : ''}">
        ${e.filled ? '<span class="pill-green">filled</span>' : '<button class="link-btn" data-del-row title="remove">✕</button>'}</div>`;
    const t = p.targets || [];
    const b = p.breakout || {};
    return `
      <div class="two-col">
        <div>
          <h4>Buy queues</h4><div id="q-rows">${p.entries.map(row).join('')}</div>
          <button class="chip" id="q-add">+ Add queue</button>
          <h4>Exit (daily close below)</h4><input id="q-exit" value="${p.exit.price || ''}" inputmode="numeric" size="10">
          <h4>Targets</h4>
          <div class="q-targets">${[0, 1, 2].map(i => `<label>T${i + 1}<input data-t="${i}" value="${t[i] || ''}" inputmode="numeric"></label>`).join('')}</div>
          <h4>Plan B - breakout (optional)</h4>
          <div class="q-targets"><label>Buy on close above<input id="q-btrig" value="${b.trigger || ''}" inputmode="numeric"></label>
            <label>Its exit (close below)<input id="q-bstop" value="${b.stop || ''}" inputmode="numeric"></label></div>
          <h4>Notes</h4><textarea id="q-note" rows="3" style="width:100%" placeholder="why this plan, what would cancel it">${esc(p.note || '')}</textarea>
        </div>
        <div><h4>Preview</h4><div id="q-preview" class="t-sum"></div>
          <div class="plan-actions" style="border:0;padding:0">
            <button class="btn buy" id="q-save" style="width:auto">Save plan &amp; set alerts</button>
            ${plan ? '<button class="chip" id="q-cancel-edit">Back</button>' : ''}</div>
          <p class="muted">Prices snap to the IDX tick (Rp${tickSize(livePrice())} here). Size is your choice - the preview shows the
          loss if every queue fills and the exit is hit. Keep it within your risk per trade.</p></div>
      </div>`;
  }
  function readEditor() {
    const entries = [...document.querySelectorAll('.q-row')].map(r => ({
      price: roundTick(+r.querySelector('[data-k=price]').value || 0), lots: Math.round(+r.querySelector('[data-k=lots]').value || 0),
      filled: !!r.querySelector('[data-k=filled]').value,
    }));
    const keep = plan ? plan.entries : [];
    entries.forEach(e => { const old = keep.find(k => k.filled && k.price === e.price); if (old) Object.assign(e, { filled_date: old.filled_date, fill_price: old.fill_price }); });
    return {
      code: S.code, entries, exit: { price: roundTick(+document.getElementById('q-exit').value || 0) },
      targets: [...document.querySelectorAll('[data-t]')].map(i => +i.value || 0).filter(Boolean),
      breakout: { trigger: +document.getElementById('q-btrig').value || null, stop: +document.getElementById('q-bstop').value || null },
      note: document.getElementById('q-note').value,
    };
  }
  function preview() {
    const p = readEditor();
    const c = calc(p.entries, p.exit.price, p.targets);
    const out = document.getElementById('q-preview');
    if (!c) { out.innerHTML = '<span class="muted">Enter queue prices, lots and an exit.</span>'; return; }
    const warn = [];
    const low = Math.min(...p.entries.filter(e => e.price).map(e => e.price));
    if (p.exit.price >= low) warn.push('Exit must be below the lowest queue.');
    if (S.arb && low < S.arb) warn.push(`A queue is below today's ARB ${fmt(S.arb)} - it can't fill today.`);
    if (c.riskPct > 10) warn.push('Risk to exit is above 10% - consider a closer exit or smaller size.');
    if (S.settings.capital && c.riskRp > S.settings.capital * S.settings.risk_pct / 100 * 1.05)
      warn.push(`Max loss is above your ${S.settings.risk_pct}% risk budget (${rupiah(S.settings.capital * S.settings.risk_pct / 100)}).`);
    out.innerHTML = `
      <div class="kv"><span>Average if all fill</span><b>${fmt(c.avg)}</b></div>
      <div class="kv"><span>Total</span><b>${c.lots} lots · ${rupiah(c.cost)}</b></div>
      <div class="kv"><span>Risk per share</span><b class="red">${fmt(c.risk)} (${fmt(c.riskPct, 1)}%)</b></div>
      <div class="kv"><span>Max loss at exit</span><b class="red">${rupiah(c.riskRp)}</b></div>
      ${c.targets.map((t, i) => `<div class="kv"><span>T${i + 1} ${fmt(t.price)}</span><b class="green">${signed(t.gain, 1)} · ${t.rr === null ? '–' : fmt(t.rr, 1) + 'R'}</b></div>`).join('')}
      ${warn.length ? `<div class="t-warn" style="margin-top:6px">${warn.map(esc).join('<br>')}</div>` : ''}`;
  }

  /* ------------------------------------------------------------ render + actions */
  function render() {
    const el = box();
    if (!el) return;
    if (editing || !plan) {
      el.innerHTML = (plan ? '' : `<p class="small">No queue plan for ${esc(S.code)} yet. A queue plan = staggered limit buys at support,
        one exit on the daily close, and targets - decided <b>before</b> you trade. Suggested values below - edit them.</p>`)
        + editorHtml(plan || defaults());
      el.querySelectorAll('input, textarea').forEach(i => i.addEventListener('input', preview));
      el.querySelector('#q-add').onclick = () => {
        const rows = document.getElementById('q-rows');
        const index = rows.children.length;
        rows.insertAdjacentHTML('beforeend', `<div class="q-row" data-row="${index}"><b>Q${index + 1}</b>
          <label>Price<input data-k="price" inputmode="numeric"></label><label>Lots<input data-k="lots" inputmode="numeric"></label>
          <input type="hidden" data-k="filled" value=""><button class="link-btn" data-del-row>✕</button></div>`);
        rows.lastElementChild.querySelectorAll('input').forEach(i => i.addEventListener('input', preview));
      };
      el.onclick = e => { if (e.target.matches('[data-del-row]')) { e.target.closest('.q-row').remove(); preview(); } };
      el.querySelector('#q-save').onclick = save;
      const back = el.querySelector('#q-cancel-edit');
      if (back) back.onclick = () => { editing = false; render(); };
      preview();
      return;
    }
    el.innerHTML = viewHtml(plan);
    el.querySelector('#plan-edit').onclick = () => { editing = true; render(); };
    el.querySelector('#plan-done').onclick = () => close('done');
    el.querySelector('#plan-cancel').onclick = () => close('cancelled');
    el.querySelectorAll('[data-fill]').forEach(b => b.onclick = () => fill(+b.dataset.fill));
    loadMacro();
  }
  async function save() {
    try {
      const saved = await Live.api('/api/plans', { method: 'POST', body: JSON.stringify(readEditor()) });
      Live.toast(`📋 ${S.code} queue plan saved · alerts set at ${saved.entries.filter(e => !e.filled).length} queue(s), exit${saved.breakout.trigger ? ' and breakout' : ''}`);
      setTimeout(() => location.reload(), 900);
    } catch (e) { Live.toast('Plan not saved: ' + e.message, 'error', true); }
  }
  async function fill(index) {
    const entry = plan.entries[index];
    const answer = prompt(`Your Queue ${index + 1} order (${entry.lots} lots) was filled in the broker app?\nEnter the fill price to record it in your portfolio and journal:`, entry.price);
    if (answer === null) return;
    try {
      await Live.api(`/api/plans/${S.code}/fill`, { method: 'POST', body: JSON.stringify({ index, price: +answer }) });
      Live.toast(`Recorded BUY ${S.code} ${entry.lots} lots @ ${fmt(+answer)} (queue ${index + 1})`);
      setTimeout(() => location.reload(), 900);
    } catch (e) { Live.toast('Not recorded: ' + e.message, 'error', true); }
  }
  async function close(status) {
    const text = status === 'done' ? 'Mark this plan as finished?' : 'Cancel this plan? Its alerts will be removed (your holdings are not changed).';
    if (!confirm(text)) return;
    await Live.api(`/api/plans/${S.code}/close`, { method: 'POST', body: JSON.stringify({ status }) });
    Live.toast(status === 'done' ? 'Plan finished.' : 'Plan cancelled.');
    setTimeout(() => location.reload(), 700);
  }

  document.addEventListener('DOMContentLoaded', () => {
    render();
    Live.listeners.push(quotes => {
      const q = quotes[S.code];
      const el = document.getElementById('plan-status');
      if (q && el && plan) {
        const [status, cls] = statusOf(plan, q.price);
        el.textContent = status;
        el.className = 'plan-status ' + cls;
      }
    });
  });
})();
