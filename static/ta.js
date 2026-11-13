/* Auto technical analysis for the price chart (education only, not a signal service).
   Pure functions on OHLC bars -> swings (zigzag), support / resistance zones, trendlines,
   double bottom / top, Fibonacci retracement of the last big leg, RSI divergence and a plain-English summary.
   Drawn by LiveChart in live.js when the "TA" toggle is on. */
(function () {
  'use strict';

  /* ------------------------------------------------------------ basic indicators */
  function atr(bars, n = 14) {
    const tr = [];
    for (let i = 1; i < bars.length; i++) {
      const b = bars[i], pc = bars[i - 1].close;
      tr.push(Math.max(b.high - b.low, Math.abs(b.high - pc), Math.abs(b.low - pc)));
    }
    const recent = tr.slice(-n);
    return recent.length ? recent.reduce((a, v) => a + v, 0) / recent.length : 0;
  }
  function rsiSeries(closes, n = 14) {
    const out = closes.map(() => null);
    if (closes.length <= n) return out;
    let gain = 0, loss = 0;
    for (let i = 1; i <= n; i++) {
      const d = closes[i] - closes[i - 1];
      if (d >= 0) gain += d; else loss -= d;
    }
    gain /= n; loss /= n;
    out[n] = loss ? 100 - 100 / (1 + gain / loss) : 100;
    for (let i = n + 1; i < closes.length; i++) {
      const d = closes[i] - closes[i - 1];
      gain = (gain * (n - 1) + Math.max(d, 0)) / n;
      loss = (loss * (n - 1) + Math.max(-d, 0)) / n;
      out[i] = loss ? 100 - 100 / (1 + gain / loss) : 100;
    }
    return out;
  }

  /* ------------------------------------------------------------ swings */
  // Major swings: a high is confirmed once price falls `pct` from it (and vice versa).
  function zigzag(bars, pct) {
    const pivots = [];
    let dir = 0, hi = 0, lo = 0;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i];
      if (dir >= 0 && b.high >= bars[hi].high) hi = i;
      if (dir <= 0 && b.low <= bars[lo].low) lo = i;
      if (dir >= 0 && b.low <= bars[hi].high * (1 - pct)) {
        pivots.push({ kind: 'H', i: hi, price: bars[hi].high });
        dir = -1; lo = i;
      } else if (dir <= 0 && b.high >= bars[lo].low * (1 + pct)) {
        pivots.push({ kind: 'L', i: lo, price: bars[lo].low });
        dir = 1; hi = i;
      }
    }
    return pivots;
  }
  // Minor swing points (k bars each side) used for support / resistance touches.
  function fractals(bars, k) {
    const out = [];
    for (let i = k; i < bars.length - k; i++) {
      let isHigh = true, isLow = true;
      for (let j = i - k; j <= i + k; j++) {
        if (bars[j].high > bars[i].high) isHigh = false;
        if (bars[j].low < bars[i].low) isLow = false;
      }
      if (isHigh) out.push({ kind: 'H', i, price: bars[i].high });
      if (isLow) out.push({ kind: 'L', i, price: bars[i].low });
    }
    return out;
  }

  /* ------------------------------------------------------------ support / resistance zones */
  function zones(bars, atrValue, lookback = 120) {
    const start = Math.max(0, bars.length - lookback);
    const pts = fractals(bars, 2).filter(p => p.i >= start).sort((a, b) => a.price - b.price);
    const width = 0.8 * atrValue;
    const clusters = [];
    pts.forEach(p => {
      const c = clusters[clusters.length - 1];
      if (c && p.price - c.lo <= width) { c.pts.push(p); c.hi = p.price; }
      else clusters.push({ pts: [p], lo: p.price, hi: p.price });
    });
    return clusters.map(c => {
      const touches = c.pts.length;
      const last = Math.max(...c.pts.map(p => p.i));
      return {
        lo: c.lo, hi: c.hi, mid: c.pts.reduce((a, p) => a + p.price, 0) / touches, touches, last,
        score: touches + 1.5 * (last - start) / Math.max(1, bars.length - start),
      };
    }).filter(z => z.touches >= 2);
  }

  /* ------------------------------------------------------------ trendlines (hull through major swings) */
  function trendline(pivots, kind, bars) {
    const pts = pivots.filter(p => p.kind === kind);
    if (pts.length < 2) return null;
    const anchor = pts.reduce((a, p) => (kind === 'H' ? (p.price > a.price ? p : a) : (p.price < a.price ? p : a)), pts[0]);
    const later = pts.filter(p => p.i - anchor.i >= 8);
    if (!later.length) return null;
    // Resistance: the later high giving the flattest (max) slope keeps every later high under the line.
    // Support: the later low giving the steepest-down (min) slope keeps every later low above it.
    const slopeOf = p => (p.price - anchor.price) / (p.i - anchor.i);
    const second = later.reduce((a, p) => (kind === 'H' ? (slopeOf(p) > slopeOf(a) ? p : a) : (slopeOf(p) < slopeOf(a) ? p : a)), later[0]);
    const slope = slopeOf(second);
    if ((kind === 'H' && slope >= 0) || (kind === 'L' && slope <= 0)) return null;
    const last = bars.length - 1;
    const valueAt = i => anchor.price + slope * (i - anchor.i);
    // Broken = closed beyond the line after the second touch.
    let broken = null;
    for (let i = second.i + 1; i <= last; i++) {
      const v = valueAt(i);
      if ((kind === 'H' && bars[i].close > v) || (kind === 'L' && bars[i].close < v)) { broken = i; break; }
    }
    return { kind, a: anchor, b: second, slope, now: valueAt(last), broken,
      points: [{ i: anchor.i, value: anchor.price }, { i: last, value: valueAt(last) }] };
  }

  /* ------------------------------------------------------------ patterns */
  function doublePattern(pivots, bars) {
    const lows = pivots.filter(p => p.kind === 'L'), highs = pivots.filter(p => p.kind === 'H');
    const test = (pair, type) => {
      if (pair.length < 2) return null;
      const [p1, p2] = pair.slice(-2);
      if (p2.i - p1.i < 5) return null;
      const diff = Math.abs(p2.price - p1.price) / p1.price;
      if (diff > 0.04) return null;
      const between = bars.slice(p1.i + 1, p2.i);
      if (!between.length) return null;
      const neck = type === 'bottom' ? Math.max(...between.map(b => b.high)) : Math.min(...between.map(b => b.low));
      const extreme = type === 'bottom' ? Math.min(p1.price, p2.price) : Math.max(p1.price, p2.price);
      const depth = Math.abs(neck - extreme) / extreme;
      if (depth < 0.08) return null;
      let confirmed = null;
      for (let i = p2.i + 1; i < bars.length; i++) {
        if ((type === 'bottom' && bars[i].close > neck) || (type === 'top' && bars[i].close < neck)) { confirmed = i; break; }
      }
      const target = type === 'bottom' ? neck + (neck - extreme) : neck - (extreme - neck);
      return { type, p1, p2, neck, target, confirmed, recent: bars.length - 1 - p2.i };
    };
    const candidates = [test(lows, 'bottom'), test(highs, 'top')].filter(Boolean).filter(c => c.recent <= 40);
    return candidates.sort((a, b) => a.recent - b.recent)[0] || null;
  }

  function fibonacci(bars, lookback = 90) {
    const start = Math.max(0, bars.length - lookback);
    let hi = start, lo = start;
    for (let i = start; i < bars.length; i++) {
      if (bars[i].high > bars[hi].high) hi = i;
      if (bars[i].low < bars[lo].low) lo = i;
    }
    const high = bars[hi].high, low = bars[lo].low, range = high - low;
    if (range <= 0 || Math.abs(hi - lo) < 5) return null;
    const down = hi < lo;   // last big leg went down -> retracement levels measured up from the low
    const levels = [0.382, 0.5, 0.618].map(r => ({ ratio: r, price: down ? low + range * r : high - range * r }));
    return { down, high, low, hiIndex: hi, loIndex: lo, levels };
  }

  function divergence(pivots, rsi) {
    const lows = pivots.filter(p => p.kind === 'L').slice(-2), highs = pivots.filter(p => p.kind === 'H').slice(-2);
    const out = [];
    if (lows.length === 2 && rsi[lows[0].i] !== null && rsi[lows[1].i] !== null
        && lows[1].price < lows[0].price && rsi[lows[1].i] > rsi[lows[0].i] + 2) {
      out.push({ type: 'bullish', at: lows[1], from: lows[0], rsi: [rsi[lows[0].i], rsi[lows[1].i]] });
    }
    if (highs.length === 2 && rsi[highs[0].i] !== null && rsi[highs[1].i] !== null
        && highs[1].price > highs[0].price && rsi[highs[1].i] < rsi[highs[0].i] - 2) {
      out.push({ type: 'bearish', at: highs[1], from: highs[0], rsi: [rsi[highs[0].i], rsi[highs[1].i]] });
    }
    return out;
  }

  /* ------------------------------------------------------------ main */
  function analyse(allBars, ma = {}, fmt = v => Math.round(v).toLocaleString('en-US')) {
    const bars = allBars.slice(-200);
    if (bars.length < 40) return null;
    const offset = allBars.length - bars.length;
    const last = bars[bars.length - 1], price = last.close;
    const atrValue = atr(bars);
    const threshold = Math.max(0.06, 2.5 * atrValue / price);
    const pivots = zigzag(bars, threshold);
    const allZones = zones(bars, atrValue);
    const supports = allZones.filter(z => z.hi < price).sort((a, b) => b.mid - a.mid).slice(0, 3);
    const resistances = allZones.filter(z => z.lo > price).sort((a, b) => a.mid - b.mid).slice(0, 3);
    const down = trendline(pivots, 'H', bars), up = trendline(pivots, 'L', bars);
    const pattern = doublePattern(pivots, bars);
    const fib = fibonacci(bars);
    const rsi = rsiSeries(bars.map(b => b.close));
    const divs = divergence(pivots, rsi);
    const volAvg = bars.slice(-21, -1).reduce((a, b) => a + (b.volume || 0), 0) / 20;

    // structure from the last two major highs and lows
    const H = pivots.filter(p => p.kind === 'H').slice(-2), L = pivots.filter(p => p.kind === 'L').slice(-2);
    const struct = [];
    if (H.length === 2) struct.push(`${H[1].price < H[0].price ? 'lower' : 'higher'} highs ${fmt(H[0].price)} → ${fmt(H[1].price)}`);
    if (L.length === 2) struct.push(`${L[1].price < L[0].price ? 'lower' : 'higher'} lows ${fmt(L[0].price)} → ${fmt(L[1].price)}`);
    const lowerHighs = H.length === 2 && H[1].price < H[0].price, lowerLows = L.length === 2 && L[1].price < L[0].price;
    const higherHighs = H.length === 2 && H[1].price > H[0].price, higherLows = L.length === 2 && L[1].price > L[0].price;

    let trend = 'Sideways', trendClass = 'amber';
    const belowMas = ma.ma50 && ma.ma200 && price < ma.ma50 && price < ma.ma200;
    const aboveMas = ma.ma50 && ma.ma200 && price > ma.ma50 && price > ma.ma200;
    if ((lowerHighs && lowerLows) || (belowMas && lowerHighs)) { trend = 'Downtrend'; trendClass = 'red'; }
    else if ((higherHighs && higherLows) || (aboveMas && higherLows)) { trend = 'Uptrend'; trendClass = 'green'; }

    // trading range since the third-last major swing
    const rangeFrom = pivots.length >= 3 ? pivots[pivots.length - 3].i : Math.max(0, bars.length - 30);
    const rangeBars = bars.slice(rangeFrom);
    const range = { lo: Math.min(...rangeBars.map(b => b.low)), hi: Math.max(...rangeBars.map(b => b.high)), from: rangeFrom };

    const lines = [];
    const maText = ma.ma50 ? ` · price ${price < ma.ma20 ? 'below' : 'above'} MA20, ${price < ma.ma50 ? 'below' : 'above'} MA50, ${price < ma.ma200 ? 'below' : 'above'} MA200` : '';
    lines.push({ k: 'Trend', v: `<b class="${trendClass}">${trend}</b>${maText}` });
    if (struct.length) lines.push({ k: 'Structure', v: struct.join(' · ') });
    lines.push({ k: 'Range', v: `${fmt(range.lo)} – ${fmt(range.hi)} (price ${Math.round((price - range.lo) / Math.max(1, range.hi - range.lo) * 100)}% up the range)` });
    if (pattern) {
      const name = pattern.type === 'bottom' ? 'Double bottom' : 'Double top';
      const state = pattern.confirmed !== null ? '<b class="green">confirmed</b>' : `confirms on a close ${pattern.type === 'bottom' ? 'above' : 'below'} ${fmt(pattern.neck)}`;
      lines.push({ k: 'Pattern', v: `${name} ${fmt(pattern.p1.price)} / ${fmt(pattern.p2.price)} · neckline ${fmt(pattern.neck)} · ${state} · measured target ${fmt(pattern.target)}` });
    }
    if (down) lines.push({ k: 'Trendline', v: down.broken !== null ? `Down-trendline from ${fmt(down.a.price)} <b class="green">broken</b> (line now ≈ ${fmt(down.now)})`
      : `Down-trendline from ${fmt(down.a.price)} caps rallies ≈ <b>${fmt(down.now)}</b> today` });
    if (up) lines.push({ k: 'Trendline', v: up.broken !== null ? `Up-trendline from ${fmt(up.a.price)} <b class="red">broken</b> (≈ ${fmt(up.now)})`
      : `Up-trendline from ${fmt(up.a.price)} supports dips ≈ <b>${fmt(up.now)}</b>` });
    const rsiNow = rsi[rsi.length - 1];
    let momentum = `RSI ${rsiNow === null ? '–' : Math.round(rsiNow)} ${rsiNow >= 70 ? '(overbought)' : rsiNow <= 30 ? '(oversold)' : rsiNow >= 50 ? '(bullish side)' : '(bearish side)'}`;
    divs.forEach(d => { momentum += ` · <b class="${d.type === 'bullish' ? 'green' : 'red'}">${d.type} divergence</b> (price ${d.type === 'bullish' ? 'lower low' : 'higher high'}, RSI ${Math.round(d.rsi[0])} → ${Math.round(d.rsi[1])})`; });
    if (volAvg && last.volume) momentum += ` · volume ${(last.volume / volAvg).toFixed(1)}× 20-bar avg`;
    lines.push({ k: 'Momentum', v: momentum });
    const s1 = supports[0], r1 = resistances[0];
    const levelText = [
      s1 ? `S1 ${fmt(s1.lo)}–${fmt(s1.hi)}` : null, supports[1] ? `S2 ${fmt(supports[1].lo)}–${fmt(supports[1].hi)}` : null,
      r1 ? `R1 ${fmt(r1.lo)}–${fmt(r1.hi)}` : null, resistances[1] ? `R2 ${fmt(resistances[1].lo)}–${fmt(resistances[1].hi)}` : null,
    ].filter(Boolean).join(' · ');
    if (levelText) lines.push({ k: 'Levels', v: levelText });
    if (fib) {
      const legText = fib.down ? `drop ${fmt(fib.high)} → ${fmt(fib.low)}` : `rise ${fmt(fib.low)} → ${fmt(fib.high)}`;
      const where = fib.levels.filter(l => (fib.down ? price >= l.price : price <= l.price)).length;
      lines.push({ k: 'Fibonacci', v: `${legText}: ${fib.levels.map(l => `${(l.ratio * 100).toFixed(1)}% ${fmt(l.price)}`).join(' · ')}`
        + ` · price has retraced past ${where} of 3` });
    }

    // plain-English read
    let read;
    if (trend === 'Downtrend' && pattern && pattern.type === 'bottom' && pattern.confirmed === null) {
      read = `Basing inside a downtrend. Buy only near support (${s1 ? fmt(s1.lo) + '–' + fmt(s1.hi) : 'S1'}) with a tight exit, or wait for a close above ${fmt(pattern.neck)} to confirm the double bottom. A close below ${fmt(Math.min(pattern.p1.price, pattern.p2.price))} cancels it.`;
    } else if (trend === 'Downtrend') {
      read = `Sellers in control: rallies into ${r1 ? fmt(r1.lo) : 'resistance'} tend to fail. Wait for a higher low + a break of the down-trendline before buying.`;
    } else if (trend === 'Uptrend') {
      read = `Buyers in control: buy pullbacks to ${s1 ? fmt(s1.hi) : 'support'} / MA20, exit on a close below the last higher low${L.length ? ' (' + fmt(L[L.length - 1].price) + ')' : ''}.`;
    } else {
      read = `Range-bound: buy near ${fmt(range.lo)}, take profit near ${fmt(range.hi)}; trade the breakout only on a daily close outside the range.`;
    }

    // shift indices back to the full bar array for drawing
    const shift = p => (p ? Object.assign({}, p, { i: p.i + offset, time: allBars[p.i + offset].time }) : p);
    const shiftLine = l => (l ? Object.assign({}, l, { a: shift(l.a), b: shift(l.b),
      points: l.points.map(pt => ({ time: allBars[pt.i + offset].time, value: pt.value })) }) : null);
    return {
      price, atr: atrValue, threshold, trend, trendClass,
      pivots: pivots.map(shift), supports, resistances, range,
      down: shiftLine(down), up: shiftLine(up),
      pattern: pattern ? Object.assign({}, pattern, { p1: shift(pattern.p1), p2: shift(pattern.p2),
        neckFrom: allBars[pattern.p1.i + offset].time }) : null,
      fib, divergences: divs.map(d => Object.assign({}, d, { at: shift(d.at), from: shift(d.from) })),
      lines, read,
    };
  }

  window.LiveTA = { analyse, zigzag, fractals, zones, atr, rsiSeries };
})();
