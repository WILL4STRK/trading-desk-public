"use strict";
const $ = (s, r=document) => r.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const usd = (n, sign) => n == null ? "–" : (sign && n > 0 ? "+" : n < 0 ? "−" : "") + "$" + Math.abs(n).toLocaleString("en-US", {minimumFractionDigits:2, maximumFractionDigits:2});
const px = n => n == null ? "–" : Number(n).toFixed(2);
const pct = p => Math.round(p * 1000) / 10 + "%";
const mins = t => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
const t12 = t => { if (!t) return ""; const [h, m] = t.split(":").map(Number); return ((h + 11) % 12 + 1) + ":" + String(m).padStart(2, "0") + (h < 12 ? " AM" : " PM"); };
let S = null, drafts = {}, formDirty = false, FILTER = "ready";
const TITLES = {desk:"Desk", account:"Account", journal:"Journal", research:"Research", graph:"Graph", market:"Vs market", meme:"Memecoins", terminal:"Terminal", settings:"Settings"};

// The mark: 12 overlapping circles in a ring.
(function () {
  const p = [];
  for (let i = 0; i < 12; i++) {
    const a = i / 12 * Math.PI * 2;
    p.push(`<circle cx="${(12 + 4.6 * Math.cos(a)).toFixed(2)}" cy="${(11.5 + 4.6 * Math.sin(a)).toFixed(2)}" r="4.4" fill="none" stroke="#0A84FF" stroke-width=".7" opacity=".85"/>`);
  }
  $("#mark").innerHTML = `<svg viewBox="0 0 24 24">${p.join("")}</svg>`;
})();

function toast(msg, err) { const t = $("#toast"); t.textContent = msg; t.className = err ? "err" : ""; t.style.display = "block"; clearTimeout(toast.t); toast.t = setTimeout(() => t.style.display = "none", err ? 7000 : 3500); }
// ---- Graph tab: running made-or-lost lines, inline SVG
const GCOL = ["#0A84FF", "#30D158", "#FFB340", "#FF9F7A", "#64D2FF", "#FF6961"];
const tms = at => Date.parse(String(at).replace(" ", "T")) || 0;
function lineChart(series, label, fmt = v => usd(Math.round(v * 100) / 100, true), empty = "No closed trades yet. The line starts with the first one.") {
  const all = series.flatMap(x => x.pts);
  if (!all.length) return `<p class="empty" style="padding:8px">${esc(empty)}</p>`;
  const W = 640, H = 220, L = 58, R = 10, T = 12, B = 26;
  const xs = all.map(p => p.t), ys = all.map(p => p.y).concat([0]);
  let x0 = Math.min(...xs), x1 = Math.max(...xs); if (x1 === x0) { x0 -= 3.6e6; x1 += 3.6e6; }
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (y1 === y0) { y0 -= 1; y1 += 1; }
  const pad = (y1 - y0) * .08; y0 -= pad; y1 += pad;
  const X = t => L + (t - x0) / (x1 - x0) * (W - L - R), Y = v => T + (y1 - v) / (y1 - y0) * (H - T - B);
  const ticks = [y0 + pad, (y0 + y1) / 2, y1 - pad].map(v => `<text x="${L - 6}" y="${Y(v) + 4}" text-anchor="end" font-size="11" style="fill:var(--label2)">${esc(fmt(v))}</text>`).join("");
  const day = t => new Date(t).toLocaleDateString("en-US", {month: "short", day: "numeric"});
  const lines = series.map(x => x.pts.length ? `<polyline fill="none" stroke="${x.color}" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round" points="${[{t: x.pts[0].t, y: 0}].concat(x.pts).map(p => X(p.t).toFixed(1) + "," + Y(p.y).toFixed(1)).join(" ")}"/>` +
      `<circle cx="${X(x.pts.at(-1).t).toFixed(1)}" cy="${Y(x.pts.at(-1).y).toFixed(1)}" r="3.5" fill="${x.color}"/>` : "").join("");
  const key = series.map(x => `<span style="display:inline-flex;align-items:center;gap:6px;margin-right:14px"><i style="width:10px;height:10px;border-radius:3px;background:${x.color}"></i>${esc(x.name)} <b class="num">${esc(fmt(x.pts.length ? x.pts.at(-1).y : 0))}</b></span>`).join("");
  return `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(label)}" style="width:100%;height:auto;display:block">
      <line x1="${L}" x2="${W - R}" y1="${Y(0)}" y2="${Y(0)}" style="stroke:var(--sep)" stroke-dasharray="3 4"/>${ticks}
      <text x="${L}" y="${H - 6}" font-size="11" style="fill:var(--label2)">${day(x0)}</text><text x="${W - R}" y="${H - 6}" text-anchor="end" font-size="11" style="fill:var(--label2)">${day(x1)}</text>
      ${lines}</svg><div class="foot-note" style="margin:10px 0 0">${key}</div>`;
}
async function loadGraph() {
  let g; try { g = await api("/api/graph"); } catch (e) { return toast(e.message, true); }
  const me = g.accounts.find(a => a.current) || g.accounts[0];
  const pts = k => me.points.map(p => ({t: tms(p.at), y: p[k]}));
  $("#g-me").innerHTML = lineChart([{name: "Bot (auto paper)", color: "#0A84FF", pts: pts("bot")}, {name: "You", color: "#30D158", pts: pts("you")},
      {name: "Total", color: "#AEAEB2", pts: pts("total")}], "Made or lost over time, this account") +
    `<p class="foot-note" style="margin:6px 0 0">${me.trades} closed trades, ${me.wins} winners${me.trades ? " (" + Math.round(me.wins / me.trades * 100) + "%)" : ""}${me.live ? " · LIVE record" : ""}.</p>`;
  $("#g-all").innerHTML = lineChart(g.accounts.map((a, i) => ({name: a.name + (a.current ? " (this one)" : ""), color: GCOL[i % GCOL.length],
      pts: a.points.map(p => ({t: tms(p.at), y: p.total}))})), "Made or lost over time, every paper account");
  const days = me.days.slice(-20).reverse();
  $("#g-days").innerHTML = days.length ? days.map(d => `<div class="row-kv num"><span>${esc(d.date)} · ${d.trades} trade${d.trades === 1 ? "" : "s"}</span><b style="color:${d.pnl < 0 ? "var(--stop)" : "var(--go)"}">${usd(d.pnl, true)}</b></div>`).join("") : `<p class="empty" style="padding:8px">No closed trades yet.</p>`;
}
// ---- Vs market tab: the bot's % return next to the index funds, plus TradingView charts
const TV = {SPY: "AMEX:SPY", QQQ: "NASDAQ:QQQ", DIA: "AMEX:DIA", IWM: "AMEX:IWM"};
const pctM = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(Math.round(v * 100) / 100).toFixed(2) + "%";
let tvSym = "SPY";
function tvChart(sym) {
  tvSym = sym;
  // a cross-origin frame, not TradingView's script: nothing of theirs runs on this desk's page
  const cfg = {autosize: true, symbol: TV[sym] || sym, interval: "D", timezone: "America/New_York", theme: "dark", style: "1",
    locale: "en", allow_symbol_change: true, hide_side_toolbar: false, studies: ["STD;SMA"], support_host: "https://www.tradingview.com"};
  $("#tv").innerHTML = `<iframe title="TradingView chart of ${esc(sym)}" style="width:100%;height:100%;border:0;display:block" referrerpolicy="no-referrer"
    src="https://www.tradingview-widget.com/embed-widget/advanced-chart/?locale=en#${esc(encodeURIComponent(JSON.stringify(cfg)))}"></iframe>`;
  $("#tv-seg").innerHTML = Object.keys(TV).map(s => `<button data-tv="${s}" aria-pressed="${s === sym}">${s}</button>`).join("");
}
async function loadMarket() {
  if (!$("#tv iframe")) tvChart(tvSym);
  let m; try { m = await api("/api/benchmarks"); } catch (e) { return toast(e.message, true); }
  const t = d => Date.parse(d + "T16:00") || 0;
  const rows = [m.bot].concat(m.groups);
  $("#bm-chart").innerHTML = lineChart(rows.filter(r => r.points.length).map((r, i) => ({name: r.name, color: r === m.bot ? GCOL[0] : GCOL[1 + (i - 1) % (GCOL.length - 1)],
      pts: r.points.map(p => ({t: t(p.date), y: p.pct}))})), "Percent return since " + m.start + ", bot against the index funds", pctM, "No market data yet.");
  const ranked = rows.filter(r => r.pct != null).sort((a, b) => b.pct - a.pct);
  const beat = m.groups.filter(g => g.pct != null && m.bot.pct > g.pct).length, known = m.groups.filter(g => g.pct != null).length;
  $("#bm-board").innerHTML = (known ? `<p style="margin:0 0 10px;font-weight:600">${beat === known ? "The bot is beating every grouping." : beat ? `The bot is beating ${beat} of ${known} groupings.` : "The bot is behind every grouping."}</p>` : "") +
    `<div style="overflow-x:auto"><table class="num" style="font-size:14.5px"><tr><th>Who</th><th>Return</th><th>Bot ahead</th></tr>` +
    ranked.map((r, i) => `<tr${r === m.bot ? ' style="color:var(--tint);font-weight:700"' : ""}><td style="white-space:normal">${i + 1}. ${esc(r.name)}${r === m.bot ? "" : ` <small style="color:var(--label2)">${esc(r.symbol)}</small>`}</td>
      <td style="text-align:right" class="${r.pct < 0 ? "neg" : "pos"}">${pctM(r.pct)}</td><td style="text-align:right">${r === m.bot ? `<small style="color:var(--label2)">${usd(m.bot.pnl, true)}</small>` : `<span class="${m.bot.pct - r.pct < 0 ? "neg" : "pos"}">${pctS(m.bot.pct - r.pct)}</span>`}</td></tr>`).join("") + `</table></div>` +
    m.groups.filter(g => g.error).map(g => `<p class="foot-note">${esc(g.name)}: ${esc(g.error)}</p>`).join("") +
    `<p class="foot-note">Since ${esc(m.start)}. The bot's return is a percent of your ${usd(m.balance)} starting balance.</p>`;
}
// ---- Desk tab chart: TradingView Lightweight Charts, stored in /vendor (no CDN). Data from /api/chart.
const C = {up: "#26A69A", down: "#EF5350", vwap: "#F5C518", e9: "#22D3EE", e20: "#FF9F7A", grid: "rgba(255,255,255,.05)", text: "#A3A8B5",
  lv: {pm: ["#9AA0AE", 2], pd: ["#5B9CF6", 2], or: ["#F59E0B", 1], slip: [null, 0]}, slip: {Entry: "#5B9CF6", Stop: "#EF5350", Target: "#26A69A"}};
const CH = {sym: null, int: "5m", chart: null, lines: [], last: null, busy: false};
const RISKY_ATR = 5;                      // % a day: a mover that swings this much gets the Risky tag
// Lightweight Charts throws "Value is null" on setData when two bars share a time, times run backwards, or a value is not a number.
// The feed can repeat its last print, so every series is cleaned first: finite values only, one row per time (the later wins), ascending.
// Time is epoch seconds on intraday charts and a YYYY-MM-DD string on the daily chart; both sort correctly with < and >.
function cleanBars(rows, keys) { const m = new Map(); for (const r of rows || []) if (r && (Number.isFinite(r.time) || (typeof r.time === "string" && r.time)) && keys.every(k => Number.isFinite(r[k]))) m.set(r.time, r); return [...m.values()].sort((a, b) => a.time < b.time ? -1 : a.time > b.time ? 1 : 0); }
function ema(cs, n) { const k = 2 / (n + 1); let e = null; return cs.map(c => ({time: c.time, value: e = e == null ? c.close : c.close * k + e * (1 - k)})); }
function makeChart() {
  if (CH.chart || !window.LightweightCharts) return !!CH.chart;
  const L = LightweightCharts, el = $("#chart");
  CH.chart = L.createChart(el, {autoSize: true, layout: {background: {color: "transparent"}, textColor: C.text, fontFamily: "Geist, system-ui, sans-serif", fontSize: 11},
    grid: {vertLines: {color: C.grid}, horzLines: {color: C.grid}}, rightPriceScale: {borderVisible: false},
    timeScale: {borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 4}, crosshair: {mode: L.CrosshairMode.Normal},
    handleScroll: {vertTouchDrag: false}});
  CH.cs = CH.chart.addCandlestickSeries({upColor: C.up, downColor: C.down, borderVisible: false, wickUpColor: C.up, wickDownColor: C.down});
  CH.cs.priceScale().applyOptions({scaleMargins: {top: .08, bottom: .24}});
  CH.vol = CH.chart.addHistogramSeries({priceFormat: {type: "volume"}, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false});
  CH.chart.priceScale("vol").applyOptions({scaleMargins: {top: .8, bottom: 0}});
  const line = color => CH.chart.addLineSeries({color, lineWidth: color === C.vwap ? 2 : 1, priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false});
  CH.vw = line(C.vwap); CH.e9 = line(C.e9); CH.e20 = line(C.e20);
  return true;
}
async function loadChart(sym, quiet) {
  sym = String(sym || "").trim().toUpperCase();
  if (!/^[A-Z]{1,5}$/.test(sym)) return quiet || toast("Type a ticker like AAPL.", true);
  if (!makeChart()) { $("#chart").textContent = "The chart library did not load."; return; }
  if (CH.busy && quiet) return;
  CH.busy = true;
  let d; try { d = await api("/api/chart?symbol=" + encodeURIComponent(sym) + "&interval=" + CH.int); }
  catch (e) { CH.busy = false; return quiet || toast(e.message, true); }
  CH.busy = false;
  const fresh = CH.sym !== sym || CH.shown !== CH.int; CH.sym = sym; CH.shown = CH.int;
  const bars = cleanBars(d.candles, ["open", "high", "low", "close"]);
  const cs = bars.map(c => ({time: c.time, open: c.open, high: c.high, low: c.low, close: c.close}));
  CH.cs.setData(cs);
  CH.vol.setData(bars.map(c => ({time: c.time, value: Number.isFinite(c.volume) ? c.volume : 0, color: c.close >= c.open ? "rgba(38,166,154,.35)" : "rgba(239,83,80,.35)"})));
  CH.vw.setData(cleanBars(d.vwap, ["value"])); CH.e9.setData(ema(cs, 9)); CH.e20.setData(ema(cs, 20));
  CH.lines.forEach(l => CH.cs.removePriceLine(l));
  CH.lines = d.levels.map(l => CH.cs.createPriceLine({price: l.price, title: l.name, lineWidth: 1, axisLabelVisible: true,
    color: l.kind === "slip" ? C.slip[l.name] || C.lv.pd[0] : C.lv[l.kind][0], lineStyle: l.kind === "slip" ? 0 : C.lv[l.kind][1]}));
  if (fresh) requestAnimationFrame(() => CH.chart.timeScale().fitContent());     // after autoSize has measured the box
  const last = cs.at(-1), prev = cs.length > 1 ? cs.at(-2).close : null;
  CH.last = last ? last.close : null;
  $("#ch-sym").textContent = sym;
  $("#ch-last").innerHTML = last ? `${px(last.close)} <span class="${last.close >= (prev ?? last.close) ? "pos" : "neg"}">${prev ? (last.close >= prev ? "+" : "−") + px(Math.abs(last.close - prev)) : ""}</span>` : "no candles";
  $("#ch-key").innerHTML = [["VWAP", C.vwap], ["EMA 9", C.e9], ["EMA 20", C.e20]].map(([n, c]) => `<span><i style="background:${c}"></i>${n}</span>`).join("") +
    d.levels.map(l => `<span>${esc(l.name)} <b>${px(l.price)}</b></span>`).join("");
  document.querySelectorAll("#tlist [data-sym]").forEach(b => b.setAttribute("aria-current", b.dataset.sym === sym));
  renderBuy();
}
// every ticker the desk knows about today, once each: open trades, signals, movers, pre-market, your list
function tickers() {
  const s = S, out = new Map(), add = (sym, row) => { if (sym && !out.has(sym)) out.set(sym, row); };
  s.open_trades.forEach(t => add(t.symbol, {tag: ["Open", "open"], sub: `${t.qty} at ${px(t.entry_price)}`}));
  s.signals.filter(x => ["ready", "weak"].includes(x.status) && !x.off).forEach(x => add(x.payload.symbol, {tag: x.status === "ready" ? ["Ready", "go"] : ["Weak", "weak"], sub: `${x.payload.side === "short" ? "Short" : "Buy"} at ${px(x.payload.limit)}`}));
  const wr = s.watchlist_research;
  ((wr && wr.picks) || []).forEach(p => add(p.symbol, {tag: p.atr_pct >= RISKY_ATR ? ["Risky", "bad"] : ["Mover", "weak"], sub: `moves ${p.atr_pct.toFixed(1)}% a day`, why: p.why, atr: p.atr_pct}));
  ((s.premarket && s.premarket.hits) || []).forEach(h => add(h.symbol, {tag: ["Setup", ""], sub: (h.setups || []).join(", ").replace(/_/g, " ")}));
  (s.settings.watchlist || []).forEach(sym => add(sym, {tag: null, sub: "watchlist"}));
  return out;
}
function renderList() {
  const rows = tickers();
  $("#tlist").innerHTML = [...rows].map(([sym, r]) => `<button class="trow" data-sym="${esc(sym)}" aria-current="${sym === CH.sym}"${r.why ? ` title="${esc(r.why)}"` : ""}>
      <span><b>${esc(sym)}</b><small>${esc(r.sub)}</small></span>${r.tag ? `<span class="tag ${r.tag[1]}">${r.tag[0]}</span>` : ""}</button>`).join("") ||
    `<p class="empty">No tickers yet. Type one above.</p>`;
  if (!CH.sym && rows.size) loadChart(rows.keys().next().value, true);
  else renderBuy();
}
function renderBuy() {
  if (!S || !CH.sym) return;
  const r = tickers().get(CH.sym), paper = S.mode === "paper", risky = r && r.atr >= RISKY_ATR;
  $("#ch-risk").innerHTML = (risky ? `<b class="neg">Risky:</b> ${esc(CH.sym)} moves about ${r.atr.toFixed(1)}% a day. ` : "") +
    (paper ? `Paper buy at the last finished candle's close, stop 2% under, target 4% over, sized to your limits. Practice only: no order is sent.`
           : `Paper buy is off in live mode. The live record only holds real trades.`) + (r && r.why ? `<small>${esc(r.why)}</small>` : "");
  $("#ch-buy").disabled = !paper || !CH.last;
  $("#buy-qty").disabled = !paper;
}
async function paperBuy() {
  const q = $("#buy-qty").value.trim();
  const d = await act("/api/paper/buy", {symbol: CH.sym, qty: q || null});
  if (d) { $("#buy-qty").value = ""; toast(`Paper bought ${d.qty} ${d.symbol} at ${px(d.price)}. Stop ${px(d.stop)}, target ${px(d.target)}.`); }
}
document.addEventListener("submit", e => { if (e.target.id !== "find") return; e.preventDefault(); loadChart($("#find-in").value); $("#find-in").value = ""; $("#find-in").blur(); });
setInterval(() => { if (!document.hidden && CH.sym && $("#v-desk").classList.contains("on")) loadChart(CH.sym, true); }, 60000);

async function api(path, body) {
  const r = await fetch(path, body ? {method:"POST", headers:{"Content-Type":"application/json", "X-Desk":"1"}, body:JSON.stringify(body)} : {});
  const d = await r.json().catch(() => ({error:"The desk did not answer."}));
  if (!r.ok) throw new Error(d.error || "Something went wrong.");
  return d;
}
async function act(path, body, okMsg) { try { const d = await api(path, body); if (okMsg) toast(okMsg); await load(); return d; } catch (e) { toast(e.message, true); } }

const TAGS = {ready:["Ready","pill go"], weak:["Weak odds","pill weak"], blocked:["Blocked","pill"], missed:["Missed","pill"], expired:["Expired","pill"], skipped:["Skipped","pill"], taken:["Taken","pill open"]};
const initials = s => esc(String(s).replace(/[^A-Z]/gi, "").slice(0, 2).toUpperCase());

function ring(left, total) {
  const c = 2 * Math.PI * 8, f = Math.max(0, Math.min(1, left / (total || 1)));
  return `<svg class="ring" viewBox="0 0 20 20" aria-hidden="true"><circle cx="10" cy="10" r="8" fill="none" stroke="var(--fill)" stroke-width="3"/><circle cx="10" cy="10" r="8" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${(c * (1 - f)).toFixed(1)}" transform="rotate(-90 10 10)"/></svg>`;
}
function odds(o) {
  if (!o) return "";
  const h = o.history || {};
  return `<div class="odds">
      <div class="odds-bar" role="img" aria-label="Target first ${pct(o.p_target)}, neither ${pct(o.p_neither)}, stop first ${pct(o.p_stop)}">
        <div style="width:${o.p_target*100}%;background:var(--go)"></div><div style="width:${o.p_neither*100}%;background:var(--fill)"></div><div style="width:${o.p_stop*100}%;background:var(--stop)"></div></div>
      <div class="odds-key num"><span><i class="dot" style="background:var(--go)"></i>Target first <b>${pct(o.p_target)}</b></span><span><i class="dot" style="background:var(--stop)"></i>Stop first <b>${pct(o.p_stop)}</b></span><span>Neither by ${esc(t12(o.until))} <b>${pct(o.p_neither)}</b></span></div>
    </div>
    <details class="disc"><summary>Odds detail</summary><div class="inner">
      <div class="list num" style="margin-bottom:8px">
        <div class="li"><span>Average result</span><b class="${o.avg_r >= 0 ? "pos" : "neg"}">${o.avg_r > 0 ? "+" : ""}${o.avg_r}R</b></div>
        <div class="li"><span>Edge over break-even</span><b class="${o.edge_points >= 0 ? "pos" : "neg"}">${o.edge_points > 0 ? "+" : ""}${o.edge_points} pts</b></div>
        <div class="li"><span>Same rule, last ${h.sessions} days</span><b>${h.target} wins, ${h.stop} losses of ${h.triggered}</b></div>
      </div>
      ${esc(o.verdict)} From ${o.replays.toLocaleString()} replays of the rest of today.</div></details>
    ${o.thin ? `<p class="verdict">${esc(o.thin)}</p>` : ""}`;
}
function ruleCard(p) {
  if (!p.rule || !p.rule.length) return "";
  const st = p.stamp ? `<p class="num" style="margin:8px 0 0">Rule set ${esc(p.stamp.rules)} · desk ${esc(p.stamp.desk)} · skills ${esc(p.stamp.skills)}</p>` : "";
  return `<details class="disc"><summary>The rule behind this</summary><div class="inner"><dl class="rule">${p.rule.map(r => `<div><dt>${esc(r.part)}</dt><dd>${esc(r.text)}</dd></div>`).join("")}</dl>${st}</div></details>`;
}
function chartLink(sym) {
  const u = S.chart_urls && S.chart_urls[sym];
  return u ? `<a class="chart-link" href="${esc(u)}" target="_blank" rel="noopener noreferrer">Chart in TradingView<span class="vh"> for ${esc(sym)}, opens in a new window</span>&nbsp;↗</a>` : "";
}
const sideTags = (short, paper) => short ? `<span class="side-tag">SHORT</span>${paper ? `<span class="side-tag paper">PRACTICE ONLY</span>` : ""}` : "";
const practiceTrade = t => t.side === "short" && (t.note === "practice short" || t.note === "auto");
const factsList = () => `<ul class="facts">${((S.shorts || {}).facts || []).map(f => `<li>${esc(f)}</li>`).join("")}</ul>`;
function slip(s, now) {
  const p = s.payload, live = s.status === "ready" && !s.off, sh = p.side === "short";
  const [label, cls] = s.off && ["ready","weak"].includes(s.status) ? ["Not now", "pill"] : TAGS[s.status] || [s.status, "pill"];
  const left = p.valid_until ? mins(p.valid_until) - mins(now.slice(11)) : null;
  const dim = s.off || ["blocked","missed","expired","skipped"].includes(s.status) ? " dim" : "";
  const head = `<div class="chead"><div class="logo" aria-hidden="true">${initials(p.symbol)}</div>
      <div style="min-width:0"><div class="ticker">${esc(p.symbol)}${sideTags(sh, p.paper_only)}</div><div class="sub num">Broke ${sh ? "down" : "out"} ${esc(p.trigger_time)} at ${px(p.trigger_price)}</div></div>
      <span class="${cls}">${label}</span></div>`;
  const probs = (s.off ? `<div class="callout bad">${esc(s.off)}</div>` : "") + (p.problems || []).map(x => `<div class="callout bad">${esc(x)}</div>`).join("") + (p.warnings || []).map(x => `<div class="callout">${esc(x)}</div>`).join("");
  if (p.qty == null || !p.qty) return `<article class="card sig ${esc(s.status)}${dim}">${head}${probs}${chartLink(p.symbol)}${ruleCard(p)}</article>`;
  const d = drafts[s.id] || {qty:p.qty, price:""};
  const news = (p.news || []).map(n => `<li>${n.link ? `<a href="${esc(n.link)}" target="_blank" rel="noopener noreferrer">${esc(n.title)}</a>` : esc(n.title)}${(n.tags || []).length ? ` <span class="num">· ${esc(n.tags.join(", "))}</span>` : ""}</li>`).join("");
  const timer = live && left != null ? `<div class="timer num ${left <= 3 ? "soon" : ""}">${ring(left, S.settings.signal_valid_minutes)}Good until ${esc(t12(p.valid_until))} · ${Math.max(0, left)} min left</div>` : "";
  return `<article class="card sig ${esc(s.off ? "off" : s.status)}${dim}">${head}
    <div class="order num">${sh ? "Sell short" : "Buy"} ${p.qty} at <em>${px(p.limit)}</em> or ${sh ? "higher" : "lower"}</div>
    <div class="list num">
      <div class="li"><span><i class="dot" style="background:var(--go)"></i>Target</span><b>${px(p.target)}</b></div>
      <div class="li"><span><i class="dot" style="background:var(--stop)"></i>${sh ? "Buy stop (above)" : "Stop"}</span><b>${px(p.stop)}</b></div>
      <div class="li"><span>${sh ? "Cash set aside" : "Cost"}</span><b>${usd(p.cost)}</b></div>
      ${sh && p.trend && p.trend.direction ? `<div class="li"><span>${p.trend.days}-day average</span><b>${px(p.trend.average)} · price is ${p.trend.direction === "down" ? "below" : "above"}</b></div>` : ""}
      <div class="li"><span>Reward to risk</span><b>${p.reward_risk} to 1</b></div>
      ${p.after_cost && p.after_cost.per_share ? `<div class="li"><span>After about ${usd(p.after_cost.total)} of slippage</span><b>${p.after_cost.reward_risk} to 1</b></div>` : ""}
      <div class="li"><span>Out by</span><b>${esc(t12(p.flat_by))}</b></div>
    </div>
    <div class="pl num"><div class="l"><small>If the stop fills${sh ? " at its price" : ""}</small><b class="neg">${usd(-p.max_loss)}</b></div><div class="w"><small>If the target fills</small><b class="pos">${usd(p.reward, true)}</b></div></div>
    ${sh ? `<div class="callout bad">A short's loss is not capped. If the price gaps over the stop you lose more than ${usd(p.max_loss)}.</div>` : ""}
    ${sh && p.paper_only ? `<div class="callout"><b>Practice only: this account cannot short for real.</b> ${S.demo ? "This is demo data: do not place it anywhere." : "Practise it in your broker's paper account with the desk in paper mode, never in your real account."} ${(p.paper_why || []).map(esc).join(" ")}</div>` : ""}
    ${probs}${odds(p.odds)}
    <p class="foot-note" style="margin:10px 0 0">Sized by your ${esc(p.bound_by || "limits")}. If the price is ${sh ? "below" : "above"} ${px(p.limit)}, skip it.</p>
    ${chartLink(p.symbol)}
    ${timer}
    <details class="disc" ${live ? "open" : ""}><summary>How to enter ${sh ? "the short" : "it"} at your broker</summary><div class="inner"><ol class="steps">${p.ticket.steps.map(x => `<li>${esc(x)}</li>`).join("")}</ol></div></details>
    ${ruleCard(p)}
    ${news ? `<details class="disc"><summary>Headlines</summary><div class="inner"><ul class="plain">${news}</ul><p style="margin:8px 0 0">Headlines are leads. Open the story before you act on one.</p></div></details>` : ""}
    ${sh ? `<details class="disc"><summary>Short-selling rules and risks</summary><div class="inner">${factsList()}</div></details>` : ""}
    ${live ? `<div class="fill num">
        <label class="field"><span>Shares</span><input inputmode="numeric" data-d="qty" data-id="${s.id}" value="${esc(d.qty)}"></label>
        <label class="field"><span>Your fill price</span><input inputmode="decimal" data-d="price" data-id="${s.id}" value="${esc(d.price)}" placeholder="${px(p.limit)}"></label></div>
      <div class="actions"><button class="btn" data-take="${s.id}">${sh ? "I sold short and it filled" : "I placed it and it filled"}</button><button class="btn text" data-skip="${s.id}">Skip</button></div>` : ""}
  </article>`;
}
function openTrade(t) {
  const d = drafts["x" + t.id] || {price:"", reason:"target"}, sh = t.side === "short";
  return `<article class="card sig open"><div class="chead"><div class="logo" aria-hidden="true">${initials(t.symbol)}</div>
      <div style="min-width:0"><div class="ticker">${esc(t.symbol)}${sideTags(sh, practiceTrade(t))}</div><div class="sub num">${t.qty} shares ${sh ? "sold short " : ""}at ${px(t.entry_price)}</div></div><span class="pill open">Open</span></div>
    <div class="order num">${t.last ? `Last ${px(t.last)} <span class="${t.unrealised >= 0 ? "pos" : "neg"}">${usd(t.unrealised, true)}</span> <span class="sub" style="display:inline">delayed</span>` : `<span class="sub" style="display:inline;font-size:16px">Waiting for a quote</span>`}</div>
    <div class="list num">
      <div class="li"><span><i class="dot" style="background:var(--go)"></i>Target</span><b>${px(t.target)} <span class="pos">${usd(t.reward, true)}</span></b></div>
      <div class="li"><span><i class="dot" style="background:var(--stop)"></i>${sh ? "Buy stop (above)" : "Stop"}</span><b>${px(t.stop)} <span class="neg">${usd(-t.max_loss)}</span></b></div>
      <div class="li"><span>Out by</span><b>${esc(t12(t.flat_by))}</b></div>
    </div>
    ${sh ? `<div class="callout bad">This is a short: it loses when the price rises, and the loss is not capped. Buy to cover by ${esc(t12(t.flat_by))}; never hold it overnight.</div>` : ""}
    ${chartLink(t.symbol)}
    <div class="fill num">
      <label class="field"><span>${sh ? "Bought back at" : "Exit price"}</span><input inputmode="decimal" data-d="price" data-id="x${t.id}" value="${esc(d.price)}"></label>
      <label class="field"><span>Why</span><select data-d="reason" data-id="x${t.id}">${[["target","Target filled"],["stop","Stop filled"],["flat","Closed for the day"],["manual","Closed it early"]].map(([v, l]) => `<option value="${v}" ${d.reason === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
    <div class="actions"><button class="btn" data-exit="${t.id}">Record my exit</button></div></article>`;
}
function greeting(now) {
  const h = Number(now.slice(11, 13));
  return h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
}
function render() {
  const s = S, a = s.account, L = s.limits, now = s.now;
  $("#greet").innerHTML = `${greeting(now)}`;
  $("#demo-banner").hidden = !s.demo;
  const live = s.mode === "live";
  document.title = (s.demo ? "DEMO · " : "") + (live ? "LIVE · " : "Paper · ") + "Trading Desk";
  const mb = $("#mode-badge"); mb.hidden = false; mb.className = "mode-badge" + (live ? " live" : "");
  mb.textContent = (live ? "LIVE · real money" : "PAPER · practice") + (s.account_name ? " · " + s.account_name : "");
  $("#acct-cap").textContent = live ? "Live account" : "Paper account";
  $("#clock").textContent = t12(now.slice(11)) + " New York · " + (s.clock.open ? "market open" : "market closed");
  $("#mode-chip").className = "chip" + (s.demo ? "" : " live");
  $("#mode-text").textContent = s.demo ? "Demo · " + now.slice(5, 10).replace("-", "/") : "Today's market";
  $("#f-equity").textContent = usd(a.equity); $("#f-spend").textContent = usd(a.spendable);
  $("#f-risk").textContent = usd(L.max_loss_per_trade);
  $("#f-day").textContent = usd(s.day.realised, true); $("#f-day").className = s.day.realised < 0 ? "neg" : s.day.realised > 0 ? "pos" : "";
  const so = s.shorts || {};
  $("#shorts-note").innerHTML = so.on ? `<details class="disc card" style="padding:0;margin:0 0 12px"><summary>Short signals are on${so.long_on ? "" : " (longs are off)"}${so.paper_only ? " · practice only" : ""}</summary><div class="inner">
      ${so.paper_only ? `<p style="margin:0 0 8px">Short signals are practice only: this account cannot short for real${S.demo ? " (and this is the demo: place nothing from it)" : ""}. In live mode they are shown but cannot be recorded. ${so.why.map(esc).join(" ")}</p>` : `<p class="neg" style="margin:0 0 8px">Your settings say this account can short. The desk cannot check that with your broker.</p>`}
      ${factsList()}</div></details>` : "";
  const ls = s.last_scan, st = $("#status");
  st.className = "card status" + (s.halt ? " halt" : "");
  st.innerHTML = `<div class="grow">${s.halt ? `<b>${esc(s.halt)}</b>` : `<b>Looking for setups.</b> `}
    ${ls && !ls.halt ? (ls.error ? esc(ls.error) : `Last scan ${esc(t12(ls.at.slice(11)))}: ${ls.directions === "short" ? "" : `${ls.scanned} charts, ${ls.hits} breakouts, ${ls.stale} too old, ${ls.too_expensive} too expensive.`}${ls.directions && ls.directions !== "long" ? (ls.shorts_skipped ? " " + esc(ls.shorts_skipped) : ` Shorts: ${ls.short_hits} breakdowns, ${ls.short_new} new.`) : ""}`) : ""}</div>
    <button class="btn small gray" id="pause" aria-pressed="${!!s.settings.paused}">${s.settings.paused ? "Resume" : "Pause"}</button>${s.settings.paused ? "" : `<button class="btn small gray" id="scan">Scan now</button>`}`;
  const au = s.auto || {}, ac = $("#auto-card");
  ac.innerHTML = `<div class="swrow"><div><b>Auto paper</b><small>${au.paper
      ? (au.on ? "On. The desk takes each ready signal itself in the practice account, with the same limits as you, and sells by the stop, the target or the flat-by time. No real money."
               : "Off. Turn it on and the desk trades ready signals itself in the practice account. No real money.")
        + (au.working || au.open ? ` Now: ${au.working} buy${au.working === 1 ? "" : "s"} waiting, ${au.open} open.` : "")
      : "Paper mode only. In live mode it never trades."}</small></div>
    <button class="switch" role="switch" aria-checked="${!!au.on}" aria-label="Auto paper" id="sw-auto" ${au.paper ? "" : "disabled"}></button></div>${au.paper ? `<div class="swrow"><div><b>Take weak signals too</b><small>${s.settings.auto_paper_weak
      ? "On. It also practises the signals whose odds are under your floor, so it learns from more trades. The review shows how they did."
      : "Off. It only takes ready signals."}</small></div>
    <button class="switch" role="switch" aria-checked="${!!s.settings.auto_paper_weak}" aria-label="Take weak signals too" id="sw-auto-weak"></button></div>` : ""}`;
  // filter
  const order = {ready:0, weak:1, taken:2, blocked:3, missed:4, expired:5, skipped:6};
  const all = [...s.signals].sort((x, y) => order[x.status] - order[y.status] || ((y.payload.odds || {}).edge_points ?? -99) - ((x.payload.odds || {}).edge_points ?? -99));
  const isReady = x => (x.status === "ready" || x.status === "weak") && !x.off;
  const nReady = all.filter(isReady).length;
  const counts = {ready:nReady, all:all.length, open:s.open_trades.length};
  document.querySelectorAll(".seg button[data-f]").forEach(b => { b.textContent = `${b.dataset.f[0].toUpperCase() + b.dataset.f.slice(1)} · ${counts[b.dataset.f]}`; b.setAttribute("aria-pressed", b.dataset.f === FILTER); });
  $("#open").innerHTML = FILTER !== "all" || s.open_trades.length ? s.open_trades.map(openTrade).join("") : "";
  const shown = FILTER === "open" ? [] : FILTER === "ready" ? all.filter(isReady) : all;
  const none = `<div class="card empty"><b>No signals yet today</b>The desk scans after every 5-minute candle closes, from ${esc(t12(s.settings.no_entries_before))} to ${esc(t12(s.settings.no_entries_after))} New York time. Most days have only a few. No signal is a fine day.</div>`;
  if (FILTER === "open") $("#signals").innerHTML = s.open_trades.length ? "" : `<div class="card empty"><b>Nothing open</b>Trades you record as filled show up here until you record the exit.</div>`;
  else $("#signals").innerHTML = shown.length ? shown.map(x => slip(x, now)).join("") : all.length ? `<div class="card empty"><b>Nothing ready right now</b>${all.length} signal${all.length === 1 ? "" : "s"} today ${all.length === 1 ? "is" : "are"} taken, blocked, missed, skipped or not affordable right now. Tap All to see why.</div>` : none;
  // side
  const used = s.day.trades, lost = Math.max(0, -s.day.realised);
  $("#limits").innerHTML = `<div class="row-kv num"><span>Trades</span><b>${used} of ${L.trades_cap}${s.settings.mode === "paper" ? " (no cap in paper)" : ""}</b></div><div class="meter"><i style="width:${Math.min(100, used / L.trades_cap * 100)}%"></i></div>
    <div class="row-kv num"><span>Lost today</span><b>${usd(lost)} of ${usd(L.max_daily_loss)}</b></div><div class="meter ${lost >= L.max_daily_loss * .75 ? "hot" : ""}"><i style="width:${Math.min(100, lost / (L.max_daily_loss || 1) * 100)}%"></i></div>
    ${stopsRows(s.stops)}
    <div class="row-kv num"><span>Settled cash</span><b>${usd(a.settled_cash)}</b></div>
    ${a.unsettled.map(u => `<p class="foot-note num" style="margin:6px 0 0">${usd(u.amount)} settles ${esc(u.settles_on)}</p>`).join("")}`;
  $("#events").innerHTML = s.events.length ? s.events.slice(0, 12).map(e => `<div class="ev ${esc(e.kind)}"><time class="num">${esc(e.ts.slice(5))}</time>${esc(e.message)}</div>`).join("") : `<p class="empty" style="padding:8px">Signals, fills and alerts show up here.</p>`;
  const wr = s.watchlist_research;
  $("#movers").innerHTML = wr && wr.picks && wr.picks.length ? wr.picks.map(p => `<div class="watch"><div><b>${esc(p.symbol)}</b><small>moves ${p.atr_pct.toFixed(1)}% a day · ${esc(p.why || p.from.join(", ").replace(/_/g, " "))}</small></div><b class="num">${px(p.price)}</b></div>`).join("") + `${wr.summary ? `<p class="foot-note" style="margin:8px 0 0">${esc(wr.summary)}</p>` : ""}<p class="foot-note" style="margin:8px 0 0">${wr.by === "claude" ? "Claude picked" : "Picked"} ${esc(wr.at)} from Yahoo's screeners, added to your ${wr.core.length} core tickers. High fluctuation cuts both ways.</p>` : `<p class="empty" style="padding:8px">${wr && wr.kept_old ? "Research failed this morning, so yesterday's list stays." : "Picked each morning at 9:00 AM New York."}</p>`;
  const pm = s.premarket;
  $("#premarket").innerHTML = pm && pm.hits && pm.hits.length ? pm.hits.slice(0, 10).map(h => `<div class="watch"><div><b>${S.chart_urls && S.chart_urls[h.symbol] ? `<a href="${esc(S.chart_urls[h.symbol])}" target="_blank" rel="noopener noreferrer" style="color:inherit">${esc(h.symbol)}<span class="vh"> chart in TradingView, opens in a new window</span></a>` : esc(h.symbol)}</b><small>${esc((h.setups || []).join(", ").replace(/_/g, " "))}</small></div><b class="num">${px(h.price)}</b></div>`).join("") + `<p class="foot-note" style="margin:8px 0 0">Daily-chart setups as of ${esc(pm.as_of)}. A list to watch, not to buy.</p>` : `<p class="empty" style="padding:8px">${pm && pm.error ? esc(pm.error) : "The pre-market scan runs once each morning."}</p>`;
  const unk = s.export_unknown || [];
  $("#export-note").hidden = !unk.length;
  $("#export-note").textContent = unk.length ? `TradingView needs the exchange for ${unk.join(", ")}. After the upload, fix ${unk.length === 1 ? "it" : "them"} if TradingView marks ${unk.length === 1 ? "it" : "them"} not found.` : "";
  // journal
  const k = s.stats;
  $("#stats").innerHTML = [["Closed trades", k.closed], ["Win rate", k.win_rate == null ? "–" : k.win_rate + "%"], ["Total", usd(k.total_pnl, true)], ["Average", k.avg_r == null ? "–" : k.avg_r + "R"], ["Best", usd(k.best, true)], ["Worst", usd(k.worst, true)]].map(([l, v]) => `<div class="tile"><b class="num">${v}</b><span>${l}</span></div>`).join("");
  $("#trades").innerHTML = s.trades.length ? `<table><tr><th>Date</th><th>Stock</th><th>By</th><th>Shares</th><th>In</th><th>Out</th><th>Why</th><th>Result</th><th>R</th></tr>${s.trades.map(t => `<tr><td class="num">${esc(t.date)}</td><td style="text-align:left"><b>${esc(t.symbol)}</b>${sideTags(t.side === "short", practiceTrade(t))}</td><td>${t.note === "auto" ? "Auto" : "You"}</td><td class="num">${t.qty}</td><td class="num">${px(t.entry_price)}</td><td class="num">${t.exit_price ? px(t.exit_price) : "open"}</td><td>${esc(t.exit_reason || "")}</td><td class="num ${t.pnl >= 0 ? "pos" : "neg"}">${t.pnl == null ? "" : usd(t.pnl, true)}</td><td class="num">${t.r_multiple ?? ""}</td></tr>`).join("")}</table>` : `<div class="empty"><b>No trades yet</b>When you take a signal and record the fill, it lands here.</div>`;
  $("#cashnote").textContent = `Cash ${usd(a.cash)}, of which ${usd(a.settled_cash)} is settled.` + (a.type === "cash" ? " In a cash account, money from a sale can buy again the next business day." : "");
  renderList();
  renderAccount();
  renderMode();
  renderHealth();
  if (!formDirty) renderForm();
}
// ---- Results: fetched when the Journal tab is open (it reads the whole record)
let RV = null;
async function loadReview() { try { RV = await api("/api/review"); renderReview(); } catch (e) { $("#review").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; } }
function renderReview() {
  if (!RV) return;
  const v = RV.summary, r = v.results, p = v.plan, sg = v.signals, box = $("#review");
  if (box.querySelector("details[open]")) return;                 // do not close the report while it is being read
  const n = (x, unit) => x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(2) + (unit || "");
  const li = (a, b) => `<div class="li"><span>${a}</span><b class="num">${b}</b></div>`;
  const sigs = ["taken","skipped","expired","ready","weak","blocked","missed"].filter(k => sg[k]).map(k => `${sg[k]} ${k}`).join(", ");
  box.innerHTML = `<p style="margin:0;font-weight:600">${esc(v.standing.label)} <span class="pill" style="margin-left:6px">${RV.mode === "live" ? "Live record" : "Paper record"}</span></p>
    <p class="foot-note" style="margin:4px 0 10px">${esc(v.standing.note)}</p>
    <div class="list">
      ${li("Average win", usd(r.avg_win, true))}${li("Average loss", usd(r.avg_loss, true))}
      ${li("Win size against loss size", r.payoff == null ? "–" : r.payoff + " to 1")}
      ${li("Worst drawdown", usd(r.max_drawdown, true))}${li("Longest losing run", r.losing_run)}
      ${li("Won for each $1 lost", r.profit_factor == null ? "–" : "$" + r.profit_factor.toFixed(2))}
      ${li("Worst day", r.worst_day ? usd(r.worst_day.pnl, true) : "–")}
      ${li("Your fill against the slip's limit", p.from_signals ? n(p.avg_fill_vs_limit, " a share") : "–")}
      ${li("Fills worse than the limit", p.fills_over_limit)}
      ${li("Stop exits against the planned stop", p.stop_exits ? n(p.avg_stop_vs_plan, " a share") : "–")}
      ${li("Closed early by hand", p.closed_early)}
      ${li("Signals so far", v.signals_total ? esc(sigs) : "none")}
    </div>
    ${v.by_side && v.by_side.short && (v.by_side.short.closed || v.by_side.short.open) ? `<p class="foot-note" style="margin:10px 0 0">By side: ${["long","short"].map(k => `${k === "short" ? "Shorts" : "Longs"} ${v.by_side[k].closed} closed ${usd(v.by_side[k].total, true)}${v.by_side[k].avg_r == null ? "" : ", " + v.by_side[k].avg_r + "R"}`).join(" · ")}</p>` : ""}
    ${v.by_rules.length > 1 ? `<p class="foot-note" style="margin:10px 0 0">By rule set: ${v.by_rules.map(g => `${esc(g.rules)} ${g.closed} trades ${usd(g.total, true)}`).join(" · ")}</p>` : ""}
    <details class="disc"><summary>Full review report</summary><div class="inner"><pre class="report num">${esc(RV.text)}</pre>
      <p style="margin:8px 0 0">The same text prints with <span class="num">python3 -m desk review</span>. Read it, or share it with whoever helps you, before you change a rule.</p></div></details>
    <p class="foot-note" style="margin:10px 0 0">${RV.mode === "paper" ? "Paper fills are simulated. Good paper results show the routine works; they are not proof live results will match." : "These are the trades you recorded as real money."}</p>`;
}
// ---- risk stops over more than one day (desk/breaker.py): shown under Today's limits
function stopsRows(k) {
  if (!k) return "";
  const rows = k.items.filter(i => i.on).map(i => {
    const shown = i.key === "streak" ? `${i.used} of ${i.limit}` : i.key === "drawdown" ? `${usd(i.used)} of ${usd(i.limit)}` : `${usd(i.used)} of ${usd(i.limit)}`;
    const share = i.limit ? Math.min(100, i.used / i.limit * 100) : 0;
    return `<div class="row-kv num"><span>${esc(i.label)}</span><b>${shown}</b></div><div class="meter ${share >= 75 ? "hot" : ""}"><i style="width:${share}%"></i></div>`;
  }).join("");
  const reset = k.needs_reset ? `<div class="callout bad" style="margin:8px 0 0">${esc(k.tripped.reason)}</div>
    <div class="actions"><button class="btn small gray" id="stops-reset">Reset stops</button></div>` : "";
  return rows + reset;
}
function renderHealth() {
  const h = S.health; if (!h) return;
  const col = {ok:"var(--go)", warn:"var(--warn)", bad:"var(--stop)"}, word = {ok:"Working", warn:"Note", bad:"Broken"};
  $("#health-warn").innerHTML = h.trust ? `<div class="card callout bad" role="alert" style="margin:0 0 12px">${esc(h.trust)}</div>` : "";
  $("#health").innerHTML = `<p style="margin:0 0 8px;font-weight:600">${esc(h.headline)}</p><ul class="plain">${h.items.map(i =>
    `<li><b><i class="dot" style="background:${col[i.level]}"></i>${esc(i.name)}<span class="vh">: ${word[i.level]}</span></b><br><span class="foot-note" style="margin:0">${esc(i.detail)}</span></li>`).join("")}</ul>
    <p class="foot-note num" style="margin:8px 0 0">Checked ${esc(t12(h.checked_at.slice(11)))} New York.</p>`;
}
function renderMode() {
  const live = S.mode === "live", box = $("#mode-card");
  if (box.contains(document.activeElement) && document.activeElement.tagName === "INPUT") return;   // do not wipe the word being typed
  box.innerHTML = live
    ? `<p style="margin:0 0 6px;font-weight:600">You are in LIVE mode</p>
       <p class="foot-note" style="margin:0">Trades you record now count as real money and go in the live record. Your paper record is untouched and comes back when you switch. The desk never sends an order by itself.</p>
       <div class="actions"><button class="btn gray" id="mode-paper">Switch back to paper</button></div>`
    : `<p style="margin:0 0 6px;font-weight:600">You are in PAPER mode</p>
       <p class="foot-note" style="margin:0">Use this with your broker's practice (paper) account, or turn on auto paper to let the desk practise by itself. Paper and live keep separate cash, trades and results, so practice never mixes with real money. Live starts at $0 until you type in your real cash. When to move to live is your call; the desk has no opinion.</p>
       <div class="fill num" style="grid-template-columns:1fr auto;align-items:end">
         <label class="field"><span>Type LIVE to switch to real-money records</span><input id="mode-word" autocomplete="off" autocapitalize="characters" spellcheck="false"></label>
         <button class="btn gray" id="mode-live">Switch to live</button></div>`;
}
const FIELDS = [
  ["account_type","Account type","Cash accounts wait a day for sales to settle.","select",["cash","margin"]],
  ["directions","Signals to look for","long = breakouts only. short adds opening-range breakdowns. Read the rules and risks below first.","select",["long","short","both"]],
  ["short_approved","My broker approved this account for short selling","Leave false unless your broker says so. Shorts stay paper only without it, a margin account and $2,000.","select",["false","true"]],
  ["risk_per_trade_pct","Risk per trade, %","Most you can lose on one trade, as a share of the account. Live mode only: paper mode trades bolder."],
  ["max_daily_loss_pct","Daily loss limit, %","The desk stops looking once you are down this much. Live mode only: paper mode trades bolder."],
  ["max_weekly_loss_pct","Weekly loss limit, %","No new entries for the rest of the week (from Monday) once down this much. 0 turns it off. Live mode only: paper mode trades bolder."],
  ["max_drawdown_pct","Drop from your best, %","No new entries once the record is this far under its best point, until you press Reset stops. 0 turns it off. Live mode only: paper mode trades bolder."],
  ["max_losing_streak","Losers in a row","No new entries after this many losing trades in a row, until you press Reset stops. 0 turns it off."],
  ["max_trades_per_day","Trades per day","Cap in live mode. 0 = no cap. Paper mode has none, so auto paper can learn."],
  ["target_r","Target, in R","2 means the target is twice as far as the stop."],
  ["stop_rule","Stop rule","or-mid, or-low, pct:1 or atr:1.5"],
  ["chase_r","Chase room, in R","How far above the breakout close the buy limit sits."],
  ["signal_valid_minutes","Signal life, minutes","After this a breakout is marked missed."],
  ["min_edge_points","Lowest edge to show as ready","Time Machine points over break-even."],
  ["est_cost_per_share","Slippage guess, $ a share","Spread and slippage, in and out together. Shown on each slip; never blocks one."],
  ["cash_buffer_pct","Cash kept aside, %","Never invested. Live mode only: paper mode trades bolder."],
  ["max_position_pct","Largest position, %","Share of the account in one stock. Live mode only: paper mode trades bolder."],
  ["min_price","Lowest share price","Cheaper stocks are skipped."],
  ["min_stop_pct","Closest stop, % of price","A stop nearer than this blocks the signal. 0 turns it off."],
  ["min_volume_x","Breakout volume, x","0 turns it off. 1 needs the breakout candle to match the opening candles."],
  ["no_entries_before","No entries before","New York time."],
  ["no_entries_after","No entries after","New York time."],
  ["flat_by","Out by","Every trade is closed by this time."],
  ["scan_universe","Scan every US stock too","About 300 data requests a pass. Slower.","select",["false","true"]],
  ["pdt_guard","Count day trades","Turn on if your broker still applies the pattern day trader rule to you.","select",["false","true"]],
  ["sec_user_agent","SEC contact","Your name and email. Turns on filings and earnings. Sent only to sec.gov."],
  ["ntfy_topic","Phone alerts (ntfy topic)","Phone alerts. Install the ntfy app, subscribe to a hard-to-guess topic, paste it here. Empty = off. Anyone who knows the topic can read the alerts."],
  ["ntfy_server","ntfy server","https://ntfy.sh, or your own ntfy server."],
  ["auto_watchlist","Daily watchlist research","9:00 AM New York: scan list = your core tickers + the day's most volatile movers from Yahoo's screeners.","select",["true","false"]],
  ["watchlist_agent","Watchlist agent (Claude)","Claude reads the candidates and their headlines and picks the movers, with a reason each. Uses your Claude plan via the claude command. Off = the formula picks.","select",["true","false"]],
  ["auto_watchlist_movers","Movers added each day","High-fluctuation stocks that join the core list. 0 = core only."],
  ["breaking_news","Breaking news (Claude)","While the market is open, Claude reads the new headlines for your watchlist, your trades and the market. On news that can move a stock hard it adds it to today's list (the usual rules still decide), closes auto paper's trade, or drops it, and alerts your phone. Never trades live money.","select",["true","false"]],
  ["breaking_news_minutes","Breaking news every (min)","How often it reads the new headlines. Claude is only asked when a heavy headline (earnings, deal, halt, FDA, lawsuit, macro) is new."],
  ["core_watchlist","Core tickers (the usuals)","Always scanned. Tickers separated by spaces.","area"],
  ["watchlist","Stocks to scan today","Rewritten at 9:00 AM while daily research is on. Tickers separated by spaces.","area"],
  ["blocked","Never signal","Tickers separated by spaces.","area"],
];
function renderForm() {
  const st = S.settings, so = S.shorts || {};
  $("#shorts-facts").innerHTML = `<p class="foot-note" style="margin:0">Short signals are ${so.on ? "on" : "off"}. ${so.paper_only ? "They are practice only (this account cannot short for real): " + so.why.map(esc).join(" ") : "Your settings say this account can short."} The desk never places an order, long or short.</p>${factsList()}`;
  $("#form").innerHTML = FIELDS.map(([k, l, h, type, opts]) => {
    const v = Array.isArray(st[k]) ? st[k].join(" ") : String(st[k]);
    const id = "set-" + k;
    const ctl = type === "select" ? `<select id="${id}" data-k="${k}">${opts.map(o => `<option value="${o}" ${o === v ? "selected" : ""}>${k === "meme_policy" ? esc(polName(o)) : o}</option>`).join("")}</select>`
      : type === "area" ? `<textarea id="${id}" data-k="${k}" rows="3">${esc(v)}</textarea>` : `<input id="${id}" data-k="${k}" value="${esc(v)}">`;
    const box = k === "ntfy_topic" ? `<div style="display:grid;gap:6px">${ctl}<div class="hrow" style="margin:0;gap:6px;flex-wrap:wrap"><button class="btn small gray" id="ntfy-test" type="button">Send test alert</button><button class="btn small gray" id="ntfy-test-meme" type="button">Send test meme alert</button></div></div>` : ctl;
    return `<div class="frow ${type === "area" ? "full" : ""}"><label for="${id}">${l}<small>${h}</small></label>${box}</div>`;
  }).join("");
}
// ---- Account tab: typed or screenshot-read balance and trade outcomes
const shots = {};   // per box: {busy, out, err}
function dropZone(key, what) {
  const sh = shots[key] || {};
  return `<label class="drop" data-drop="${esc(key)}">
      <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="2.5"/><circle cx="9" cy="10" r="1.6"/><path d="m21 16-5-5-7 7"/></svg>
      <span><b>Drop a screenshot</b> of ${what}, or click to pick one</span><span>You can also paste one with Cmd+V. Take it with Cmd+Shift+4.</span>
      <input type="file" accept="image/png,image/jpeg,image/heic" data-file="${esc(key)}"></label>
    ${sh.busy ? `<p class="read-msg">Reading the screenshot… The first one takes about half a minute while the reader is set up.</p>` : ""}
    ${sh.err ? `<div class="callout bad">${esc(sh.err)}</div>` : ""}
    ${sh.out ? `<p class="read-msg">${esc(sh.out.message)}</p>` : ""}`;
}
function renderAccount() {
  const a = S.account, b = shots.balance || {}, d = drafts.acct || {};
  const chips = b.out && b.out.found && b.out.found.length ? `<div class="found">${b.out.found.map(f => `<button data-use-cash="${f.value}">${esc(f.label)} <b class="num">${usd(f.value)}</b></button>`).join("")}</div>` : "";
  $("#acct-balance").innerHTML = `<div class="row-kv"><span>Cash the desk is using</span></div><div class="big-now num">${usd(a.cash)}</div>
    <p class="foot-note" style="margin:4px 0 0">${usd(a.settled_cash)} settled. Use the cash figure your broker shows (on thinkorswim: Cash &amp; Sweep Vehicle).</p>
    ${dropZone("balance", "your broker's balance screen")}${chips}
    <div class="fill num" style="grid-template-columns:1fr auto;align-items:end">
      <label class="field"><span>Cash at your broker now</span><input inputmode="decimal" data-d="cash" data-id="acct" value="${esc(d.cash ?? "")}" placeholder="${px(a.cash)}"></label>
      <button class="btn" id="acct-save-cash">Save</button></div>`;
  const open = S.open_trades;
  $("#acct-trades").innerHTML = open.length ? open.map(t => {
    const key = "trade-" + t.id, dr = drafts["x" + t.id] || {price:"", reason:"target"}, sh = t.side === "short";
    return `<article class="card sig open"><div class="chead"><div class="logo" aria-hidden="true">${initials(t.symbol)}</div>
        <div style="min-width:0"><div class="ticker">${esc(t.symbol)}${sideTags(sh, practiceTrade(t))}</div><div class="sub num">${t.qty} shares ${sh ? "sold short" : "bought"} at ${px(t.entry_price)} · stop ${px(t.stop)} · target ${px(t.target)}</div></div><span class="pill open">Open</span></div>
      ${dropZone(key, `the filled ${sh ? "buy (cover)" : "sell"} order`)}
      <div class="fill num">
        <label class="field"><span>${sh ? "Bought back at" : "Sold at"}</span><input inputmode="decimal" data-d="price" data-id="x${t.id}" value="${esc(dr.price)}"></label>
        <label class="field"><span>What happened</span><select data-d="reason" data-id="x${t.id}">${[["target","Target filled"],["stop","Stop filled"],["flat","Closed for the day"],["manual","Closed it early"]].map(([v, l]) => `<option value="${v}" ${dr.reason === v ? "selected" : ""}>${l}</option>`).join("")}</select></label></div>
      <div class="actions"><button class="btn" data-exit="${t.id}">Save what happened</button></div></article>`;
  }).join("") : `<div class="card empty"><b>No open trades</b>When you buy from a Ready card on the Desk and press "I placed it", the trade shows up here so you can record how it ended.</div>`;
}
function readShot(key, file) {
  if (!file) return;
  if (!/^image\/(png|jpeg|heic)$/.test(file.type) && !/\.(png|jpe?g|heic)$/i.test(file.name || "")) return toast("That isn't a screenshot. Use a PNG or JPEG.", true);
  if (file.size > 12 * 1024 * 1024) return toast("That image is too big. A screenshot is well under 12 MB.", true);
  const fr = new FileReader();
  fr.onload = async () => {
    shots[key] = {busy:true}; renderAccount();
    const isTrade = key.startsWith("trade-"), id = isTrade ? +key.slice(6) : null;
    try {
      const out = await api("/api/screenshot", {kind:isTrade ? "trade" : "balance", image:fr.result, trade_id:id});
      shots[key] = {out};
      if (!isTrade && out.suggest != null) (drafts.acct ||= {}).cash = out.suggest.toFixed(2);
      if (isTrade && out.suggest) drafts["x" + id] = {price:out.suggest.price.toFixed(2), reason:out.suggest.reason};
    } catch (e) { shots[key] = {err:e.message}; }
    renderAccount();
  };
  fr.readAsDataURL(file);
}
document.addEventListener("change", e => { const t = e.target; if (t.dataset && t.dataset.file) { readShot(t.dataset.file, t.files[0]); t.value = ""; } });
document.addEventListener("dragover", e => { const z = e.target.closest && e.target.closest(".drop"); if (z) { e.preventDefault(); z.classList.add("over"); } });
document.addEventListener("dragleave", e => { const z = e.target.closest && e.target.closest(".drop"); if (z) z.classList.remove("over"); });
document.addEventListener("drop", e => { const z = e.target.closest && e.target.closest(".drop"); if (!z) return; e.preventDefault(); z.classList.remove("over"); readShot(z.dataset.drop, e.dataTransfer.files[0]); });
document.addEventListener("paste", e => {
  if (!$("#v-account").classList.contains("on")) return;
  const item = [...(e.clipboardData || {}).items || []].find(i => i.type.startsWith("image/")); if (!item) return;
  const card = document.activeElement && document.activeElement.closest && document.activeElement.closest("article.sig.open");
  const exitBtn = card && card.querySelector("[data-exit]");
  readShot(exitBtn ? "trade-" + exitBtn.dataset.exit : "balance", item.getAsFile()); e.preventDefault();
});

async function load() { try { S = await api("/api/state"); render(); if ($("#v-journal").classList.contains("on")) loadReview(); } catch (e) { $("#clock").textContent = "The desk is not answering. Is it still running?"; } }

document.addEventListener("input", e => {
  const t = e.target;
  if (t.dataset.d) { (drafts[t.dataset.id] ||= {})[t.dataset.d] = t.value; }
  if (t.dataset.k) formDirty = true;
  if (t.dataset.mk) MMDIRTY = true;
  if (t.id === "m-find") renderMemeList();
});
document.addEventListener("change", e => { const t = e.target; if (t.dataset.d) { (drafts[t.dataset.id] ||= {})[t.dataset.d] = t.value; } });
async function copyForTos() {
  let b;
  try { b = await api("/api/export"); } catch (e) { return toast(e.message, true); }
  const ta = $("#export-text");
  try {
    await navigator.clipboard.writeText(b.thinkorswim);
    ta.hidden = true;
    toast(`Copied ${b.count} tickers. In thinkorswim: watchlist menu, Import, paste from clipboard.`);
  } catch (e) {
    ta.value = b.thinkorswim; ta.hidden = false; ta.focus(); ta.select();
    toast("Couldn't copy by itself. The tickers are selected below: press Cmd+C.", true);
  }
}
function centerTab(t) { const nav = t.parentElement; if (nav && nav.scrollWidth > nav.clientWidth) nav.scrollTo({left: t.offsetLeft - (nav.clientWidth - t.offsetWidth) / 2, behavior: "smooth"}); }
document.addEventListener("click", async e => {
  if (e.target.closest && e.target.closest("#copy-tos")) return copyForTos();
  const mrow = e.target.closest && e.target.closest("[data-mkey]");
  if (mrow && mrow.tagName !== "BUTTON") { MSEL = mrow.dataset.mkey; const inList = !!mrow.closest("#m-list"); renderMemeList();
    if (inList) { const n = [...document.querySelectorAll("#m-list [data-mkey]")].find(r => r.dataset.mkey === MSEL); if (n) n.focus({preventScroll: true}); }
    return loadMemeChart(true); }
  const t = e.target.closest("button"); if (!t) return;
  if (t.dataset.v) {
    document.querySelectorAll(".tabs button").forEach(b => b.setAttribute("aria-selected", b === t));
    document.querySelectorAll(".view").forEach(v => v.classList.toggle("on", v.id === "v-" + t.dataset.v));
    $("#title").textContent = TITLES[t.dataset.v];
    window.scrollTo(0, 0);
    centerTab(t);
    if (t.dataset.v === "research") congress(false);
    if (t.dataset.v === "journal") loadReview();
    if (t.dataset.v === "graph") loadGraph();
    if (t.dataset.v === "market") loadMarket();
    if (t.dataset.v === "meme") { loadMeme(); loadMemebot(); }
    if (t.dataset.v === "terminal") TERMINAL.load();
  }
  else if (t.dataset.mivl) { MIVL = t.dataset.mivl; document.querySelectorAll("[data-mivl]").forEach(b => b.setAttribute("aria-pressed", b === t)); loadMemeChart(true); }
  else if (t.id === "m-fw-toggle") { MFW = !MFW; loadMeme(); }
  else if (t.dataset.mf) { MF = t.dataset.mf; document.querySelectorAll("[data-mf]").forEach(b => b.setAttribute("aria-pressed", b === t)); renderMemePos(); }
  else if (t.id === "meme-save") {
    const ch = {}; document.querySelectorAll("#meme-form [data-mk]").forEach(el => { const k = el.dataset.mk, cur = S.settings[k]; const v = el.value.trim();
      ch[k] = k === "meme_news_words" ? v.split("\n").map(x => x.trim()).filter(Boolean) : Array.isArray(cur) ? v.split(/[\s,]+/).filter(Boolean) : typeof cur === "boolean" ? v === "true" : typeof cur === "number" ? Number(v) : v; });
    if (await act("/api/settings", {changes:ch}, "Saved memecoin settings.")) { MMDIRTY = false; loadMeme(); }
  }
  else if (t.dataset.maSeason) { MASEASON[t.dataset.maSeason] = Number(t.dataset.n); renderAccountCards(); }
  else if (t.dataset.maRefill) { const a = (MA && MA.accounts.find(x => x.name === t.dataset.maRefill)) || {}; refillAccounts(t.dataset.maRefill, a.label || t.dataset.maRefill); }
  else if (t.id === "m-refill-all") refillAccounts("all");
  else if (t.id === "meme-reset") {
    if (!confirm("Start the main memecoin practice account over at its starting cash? Its trades are cleared; the forward test record and the other practice accounts are kept.")) return;
    try { await api("/api/meme/reset", {sure:true}); toast("The memecoin account starts over."); loadMeme(); } catch (err) { toast(err.message, true); }
  }
  else if (t.dataset.tv) tvChart(t.dataset.tv);
  else if (t.dataset.useCash) { (drafts.acct ||= {}).cash = Number(t.dataset.useCash).toFixed(2); renderAccount(); }
  else if (t.id === "acct-save-cash") { const v = (drafts.acct || {}).cash; if (!v) return toast("Type your cash, or drop a screenshot of it.", true); const d = await act("/api/cash", {kind:"set", amount:v}, "Saved your balance."); if (d) { drafts.acct = {}; shots.balance = null; render(); } }
  else if (t.id === "sw-auto") { const on = t.getAttribute("aria-checked") !== "true";
    await act("/api/settings", {changes:{auto_paper: on}}, on ? "Auto paper is on. It trades ready signals in the practice account." : "Auto paper is off. Trades it already has finish by their own rules."); }
  else if (t.id === "sw-auto-weak") { const on = t.getAttribute("aria-checked") !== "true";
    await act("/api/settings", {changes:{auto_paper_weak: on}}, on ? "Auto paper also takes weak signals now. Practice only." : "Auto paper takes ready signals only."); }
  else if (t.id === "ntfy-test") { if (formDirty) return toast("Save settings first, then send the test.", true);
    try { await api("/api/notify/test", {}); toast("Sent. Check your phone."); } catch (e) { toast(e.message, true); } }
  else if (t.id === "ntfy-test-meme") { if (formDirty || MMDIRTY) return toast("Save settings first, then send the test.", true);
    try { await api("/api/notify/test-meme", {}); toast("Sent a sample memecoin alert. Check your phone."); } catch (e) { toast(e.message, true); } }
  else if (t.id === "paper-reset") {
    if (!confirm("Start the practice account over? Its practice cash goes back to your starting balance and its practice positions and orders are removed. Your journal is not touched.")) return;
    try { await api("/api/paper/reset", {}); toast("The practice account starts over."); } catch (e) { toast(e.message, true); } }
  else if (t.dataset.int) { CH.int = t.dataset.int; document.querySelectorAll("[data-int]").forEach(b => b.setAttribute("aria-pressed", b === t)); loadChart(CH.sym); }
  else if (t.dataset.sym) loadChart(t.dataset.sym);
  else if (t.id === "ch-buy") paperBuy();
  else if (t.dataset.f) { FILTER = t.dataset.f; if (S) render(); }
  else if (t.dataset.take) { const id = +t.dataset.take, sig = S.signals.find(x => x.id === id), d = drafts[id] || {}; if (!d.price) return toast("Type the price you were filled at.", true); await act("/api/take", {signal_id:id, qty:d.qty ?? sig.payload.qty, price:d.price}, "Recorded your fill."); }
  else if (t.dataset.skip) await act("/api/skip", {signal_id:+t.dataset.skip}, "Skipped.");
  else if (t.dataset.exit) { const d = drafts["x" + t.dataset.exit] || {}; if (!d.price) return toast("Type the price you sold at.", true); await act("/api/exit", {trade_id:+t.dataset.exit, price:d.price, reason:d.reason || "target"}, "Recorded your exit."); }
  else if (t.dataset.cash) { await act("/api/cash", {kind:t.dataset.cash, amount:$("#cash-amt").value}, "Cash updated."); $("#cash-amt").value = ""; }
  else if (t.id === "mode-live") { const d = await act("/api/mode", {mode:"live", confirm:$("#mode-word").value}, "Now in LIVE mode. Enter your real cash in the Account tab."); if (d) { document.activeElement.blur(); renderMode(); } }
  else if (t.id === "mode-paper") await act("/api/mode", {mode:"paper"}, "Back in paper mode.");
  else if (t.id === "pause") await act("/api/settings", {changes:{paused: !S.settings.paused}}, S.settings.paused ? "Resumed. The desk is looking for setups again." : "Paused. No new signals until you resume.");
  else if (t.id === "scan") { await act("/api/scan", {}, "Scanning."); setTimeout(load, 4000); }
  else if (t.id === "stops-reset") { if (!confirm("Reset the stops? Read the review in the Journal tab first. The losing run and the drop from your best point will count again from now.")) return; await act("/api/stops/reset", {}, "Stops reset. The desk is looking for setups again."); }
  else if (t.id === "save") {
    const ch = {}; document.querySelectorAll("#form [data-k]").forEach(el => { const k = el.dataset.k, cur = S.settings[k]; let v = el.value.trim();
      ch[k] = Array.isArray(cur) ? v.split(/[\s,]+/).filter(Boolean) : typeof cur === "boolean" ? v === "true" : typeof cur === "number" ? Number(v) : v; });
    formDirty = false; const d = await act("/api/settings", {changes:ch}, "Saved settings."); if (!d) formDirty = true;
  }
  else if (t.id === "earn-go") { try { const d = await api("/api/earnings?symbol=" + encodeURIComponent($("#earn-sym").value));
      const big = n => n == null ? "–" : "$" + (n / 1e9).toFixed(2) + "B", g = n => n == null ? "–" : (n > 0 ? "+" : "") + n + "%";
      $("#earnings").innerHTML = `<p style="margin:4px 0 8px"><b>${esc(d.entity)}</b></p><table><tr><th>Quarter</th><th>Revenue</th><th>vs last year</th><th>Profit</th><th>vs last year</th><th>EPS</th><th>Net margin</th><th>Filed</th></tr>${d.rows.map(r => `<tr><td>${esc(r.fiscal)}</td><td class="num">${big(r.revenue)}</td><td class="num">${g(r.revenue_yoy)}</td><td class="num">${big(r.net_income)}</td><td class="num">${g(r.net_income_yoy)}</td><td class="num">${r.eps_diluted ?? "–"}</td><td class="num">${r.net_margin ?? "–"}%</td><td class="num">${esc(r.filed)}</td></tr>`).join("")}</table>`; } catch (err) { toast(err.message, true); } }
  else if (t.id === "cong-go") congress(true);
});
// ---- Memecoins tab: model 4 (desk/meme.py, docs/MEME.md). Paper only; reads /api/meme and /api/meme/chart.
const MEME_FIELDS = [
  ["meme_enabled","Run the memecoin model","Scans DexScreener every minute while the desk runs, day and night, and keeps the forward record.","select",["true","false"]],
  ["meme_paused","Pause new practice buys","Open positions still close by the rules, and every rule is still recorded.","select",["false","true"]],
  ["meme_policy","Rule the main account trades","careful = any rule, only coins that pass the strict check and no research filter (the least-bad in the tests). learn = any rule, plain check, trades often. Every rule is recorded either way.","select",["careful","learn","auto","aged_liquid","calm_aged","dip_aged","news","breakout","hype"]],
  ["meme_starting_balance","Starting cash, $","Changing it starts every memecoin practice account over."],
  ["meme_position_pct","Size of each buy, % of the account",""],
  ["meme_max_open","Most coins held at once",""],
  ["meme_cost_pct","Round-trip cost, %","Fees, priority fee, MEV and spread, in and out together. Measured near 4% in real trading; price impact is added on top. Ethereum is charged at least 6% (gas)."],
  ["meme_chains","Chains","Any of: solana base bsc ethereum.","area"],
  ["meme_attention","Rule P11 Helius reads","P11 reads distinct buyers from Helius on its own key (C:\Trading\secrets\helius-p11.key) under a 30,000-credit daily cap. Off until the Helius plan is decided.","select",["false","true"]],
  ["meme_search_new","Also search for brand-new pools","Adds DexScreener search results under a day old to the coins it watches. Same rules and checks.","select",["true","false"]],
  ["meme_news_words","News you are watching","One phrase per line, like GTA 6 launch. A coin named after one counts for the news rule.","area"],
  ["meme_ntfy","Phone alerts for paper trades","One ntfy push per practice buy and sell. off, main (the main account only) or all (every practice account). Never for shadows. Paper only, not advice. Test it from Settings with Send test meme alert.","select",["off","main","all"]],
  ["meme_ntfy_topic","Memecoin ntfy topic","Empty = the ntfy topic in Settings. Letters, digits, - and _ only. Anyone who knows the topic can read the alerts."],
  ["meme_ntfy_max_per_hour","Most alerts an hour","Over this, alerts are dropped and one \"N alerts skipped\" push follows at the next free slot."],
];
const RULE_NAME = {careful:"Careful: strict check, no research filter", learn:"Learning: any of the six rules", auto:"The backtest's pick"};
const RULE_INFO = {all:"Buy everything (the baseline)", hype:"Hype (the old memebot rule)", breakout:"Breakout (the stocks rule)", calm_aged:"Calm, 1 to 7 days old", dip_aged:"Dip, 1 to 7 days old", aged_liquid:"Aged and liquid, 3 to 14 days", news:"In the news or a trending narrative", migration:"After migration, steady flow (P7)", migration_base:"After migration, any (P7 control)", moonshot:"Moonshot: young coin taking off (P8)", moonshot_base:"Young coin, any (P8 control)", quick:"Quick: +10% / -5% / 30 min (P9)", attention:"Attention: P0 on boosted (paid) coins with many distinct buyers (P11)", attention_count:"Boosted, raw buy count instead of buyers (P11 control b)", attention_crowd:"Many buyers, not boosted, sampled (P11 control c)"};
const VARIANTS = [["", "any coin"], ["+screen", "passed the check"], ["+strict", "passed the strict check"], ["+evidence", "check + research filters"]];
// Display-only plain names for internal tags. Values sent to the API and saved in settings never change.
const RULE_SHORT = {all:"Buy everything", hype:"Hype", breakout:"Breakout", calm_aged:"Calm, 1–7 days", dip_aged:"Dip, 1–7 days", aged_liquid:"Aged and liquid, 3–14 days", news:"News or trend", quick:"Quick (P9)", attention:"Attention (P11)", careful:"Careful", learn:"Learning", auto:"Backtest's pick", main:"Main account"};
const EXIT_NAME = {sl:"Stop", stop:"Stop", tp:"Target", target:"Target", trail:"Trailing stop", time:"Time limit", eod:"End of run", rug:"Pool pulled", "liquidity pulled":"Liquidity pulled"};
const cap1 = s => { s = String(s ?? ""); return s.charAt(0).toUpperCase() + s.slice(1); };
const pretty = s => cap1(String(s ?? "").replace(/[-_]+/g, " ").trim());
const exitName = w => EXIT_NAME[String(w).toLowerCase()] || pretty(w);
const feedName = s => { s = String(s ?? ""); const m = /^meta:(.+)$/.exec(s); if (m) return "Trend: " + m[1];
  if (/^boosts?\b/.test(s)) return "Boosted"; if (/^profiles?\b/.test(s)) return "Profile"; if (/^community-takeovers?/.test(s)) return "Community takeover";
  if (/^ads?\b/.test(s)) return "Ad"; if (s === "search-new") return "New-pool search"; return pretty(s); };
const polName = x => RULE_INFO[x] || RULE_NAME[x] || pretty(x);
const holdTxt = n => { if (n == null || n === "" || isNaN(n)) return ""; const t = Math.max(0, Math.round(Number(n))), h = Math.floor(t / 60), m = t % 60; return h ? h + " h" + (m ? " " + m + " min" : "") : t + " min"; };
const friendly = s => String(s ?? "").replace(/\b(calm_aged|dip_aged|aged_liquid)\b/g, k => RULE_SHORT[k])
  .replace(/\bmeta:([\w-]+)/g, "Trend: $1").replace(/\b(boosts-(?:latest|top)|profiles-[\w-]+|community-takeovers-latest|ads-latest|search-new)\b/g, m => feedName(m));
const cpx = n => n == null ? "–" : n >= 1 ? Number(n).toFixed(4) : Number(n).toPrecision(4);
const pctS = (x, d = 2) => x == null ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) + "%";
const tone = x => x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "";
const money = n => n == null ? "–" : n >= 1e9 ? "$" + (n / 1e9).toFixed(2) + "B" : n >= 1e6 ? "$" + (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? "$" + (n / 1e3).toFixed(1) + "K" : "$" + Math.round(n);
const SAFE = {pass: ["pos", "Pass"], fail: ["neg", "Fail"], unknown: ["", "Unknown"]};
let MM = null, MFW = false, MSEL = null, MIVL = "5m", MF = "open", MCHKEY = "", MCHAT = 0, MMDIRTY = false;
async function loadMeme() {
  try { MM = await api("/api/meme"); } catch (e) { memePulse(MM, true); $("#m-status").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; if (!MM) $("#m-list").innerHTML = `<div class="empty"><b>Couldn't load coins</b>The desk did not answer. It will try again in a few seconds.</div>`; return; }
  const m = MM, a = m.account, st = S && S.settings;
  memePulse(m);
  if (!MSEL || !m.candidates.some(c => c.key === MSEL)) MSEL = (m.candidates[0] || {}).key || null;
  $("#m-eq").textContent = usd(a.equity); $("#m-cash").textContent = usd(a.cash);
  $("#m-ret").innerHTML = `<span class="${tone(a.return_pct)}">${pctS(a.return_pct)}</span>`; $("#m-won").textContent = `${a.trades}, ${a.wins}`;
  $("#m-account-source").textContent = "Main paper account · " + a.trades + " closed trades" + (a.trades < 30 ? " · too few" : "") + ".";
  $("#m-meta").textContent = `${m.tracked} watched · ${m.discoveries} found`;
  renderMemeList();
  const pass = m.verdict.every(c => c.ok);
  const kv = (k, v) => `<div class="li"><span>${k}</span><b>${v}</b></div>`;
  $("#m-status").innerHTML = `<div class="list">${kv("Trades", esc(polName(m.policy)))}
    ${kv("Safety checks", `${m.screens.pass} pass · ${m.screens.fail} fail · ${m.screens.unknown} unknown`)}
    ${kv("Rules watching", m.open_shadows + " open")}
    ${kv("Last look", esc(m.checked ? m.checked.slice(11, 16) + " UTC" : "not yet") + (m.enabled ? (m.paused ? " · buys paused" : "") : " · model off"))}</div>
    ${m.error ? `<div class="callout bad">Last try failed: ${esc(m.error)}</div>` : ""}
    <div class="callout ${pass ? "" : "bad"}">${pass ? "The account has met the pass bar on its own record. That earns a fresh test, not real money."
      : "Not proven: " + m.verdict.filter(c => !c.ok).map(c => esc(c.check)).join("; ") + "."}</div>
    <p class="foot-note">${esc(cap1(friendly(m.policy_why)))}. ${st ? st.meme_position_pct : 20}% a coin, at most ${st ? st.meme_max_open : 4}. Stop ${m.rules.stop}%, target +${m.rules.target}%, out after ${m.rules.hold_min / 60} h. Costs ${m.cost_pct}% round trip plus price impact.</p>`;
  renderMemePos();
  renderMemeModels(m);
  renderPracticeCurves(m);
  renderMemeFunnels(m);
  renderMemeQuick(m);
  renderMemeAttention(m);
  const rng = (lo, hi) => lo == null ? "" : `<small style="display:block;color:var(--label2)">${lo}% to ${hi}%</small>`;
  const fr = x => !x || !x.n ? `<td colspan="7" style="text-align:left;color:var(--label2)">0 closed · too few</td>` :
    `<td class="num">${x.n} / ${x.tokens}${memeThin(x.n)}</td><td class="num ${tone(x.mean)}">${pctS(x.mean)}${rng(x.mean_lo, x.mean_hi)}</td><td class="num ${tone(x.median)}">${pctS(x.median)}</td><td class="num">${x.win_pct}%${rng(x.win_lo, x.win_hi)}</td><td class="num ${tone(x.mean_no_top3)}">${pctS(x.mean_no_top3)}</td><td class="num">${(x.folds || []).length ? x.folds_pos + " of 4" : "–"}</td><td class="num neg">${pctS(x.tail5)}</td>`;
  const fwRows = Object.keys(RULE_INFO).flatMap(k => VARIANTS.map(([v, label]) => ({k, label, x: m.forward[k + v]})));
  const fwShown = fwRows.filter(r => MFW || r.k === "all" || (r.x && r.x.n)), fwHidden = fwRows.length - fwShown.length, fwAny = fwRows.some(r => r.x && r.x.n);
  const fwSeen = {};
  $("#m-fw").innerHTML = `${fwAny ? "" : `<p class="foot-note" style="margin:0 0 8px">Nothing has closed yet on any rule, so there are no results to compare.</p>`}<table><tr><th style="text-align:left">Rule</th><th style="text-align:left">Coins it counts</th><th>Trades / coins</th><th>Average (95% range)</th><th>Middle</th><th>Won (95% range)</th><th>Without best 3</th><th>Slices up</th><th>Worst 5%</th></tr>
    ${fwShown.map(r => { const first = !fwSeen[r.k]; fwSeen[r.k] = 1; return `<tr${first ? ' class="grp"' : ""}><td style="text-align:left">${first ? `<b>${esc(RULE_INFO[r.k])}</b>` : ""}</td><td style="text-align:left">${r.label}</td>${fr(r.x)}</tr>`; }).join("")}</table>
    ${!MFW && fwHidden ? `<p class="foot-note">${fwHidden} more lines have no closed trades yet and are hidden.</p>` : ""}
    <div class="hrow" style="margin:10px 0 0"><button class="btn gray small tap" id="m-fw-toggle" aria-expanded="${MFW}">${MFW ? "Show fewer lines" : "Show all " + fwRows.length + " lines"}</button></div>
    <p class="foot-note">Source: closed forward shadows. Exits and modeled costs follow each rule in docs/MEME.md. A candidate needs 100 trades across 50 coins, positive mean and median, positive mean excluding the best three, at least three positive slices, and a higher mean than P0. P7 and P8 also have their own control and per-slice minimum.</p>`;
  if (st && !MMDIRTY && !$("#meme-form").contains(document.activeElement)) $("#meme-form").innerHTML = MEME_FIELDS.map(([k, l, h, type, opts]) => {
    const v = Array.isArray(st[k]) ? st[k].join(k === "meme_news_words" ? "\n" : " ") : String(st[k]), id = "mset-" + k;
    const ctl = type === "select" ? `<select id="${id}" data-mk="${k}">${opts.map(o => `<option value="${o}" ${o === v ? "selected" : ""}>${k === "meme_policy" ? esc(polName(o)) : o}</option>`).join("")}</select>`
      : type === "area" ? `<textarea id="${id}" data-mk="${k}" rows="2">${esc(v)}</textarea>` : `<input id="${id}" data-mk="${k}" value="${esc(v)}"${typeof st[k] === "number" ? ' inputmode="decimal"' : ""}>`;
    return `<div class="frow ${type === "area" ? "full" : ""}"><label for="${id}">${l}<small>${h}</small></label>${ctl}</div>`;
  }).join("");
  drawMemeResearchPlots();
  loadMemeChart();
}
function renderMemeList() {
  if (!MM) return;
  $("#m-list").removeAttribute("aria-busy");
  const q = ($("#m-find").value || "").trim().toLowerCase();
  const rows = MM.candidates.filter(c => !q || (c.symbol || "").toLowerCase().includes(q) || c.chain.includes(q) || c.key.toLowerCase().includes(q)).slice(0, 150);
  $("#m-list").innerHTML = rows.length ? `<table><tr><th style="text-align:left">Coin</th><th>1 hour</th><th>Safety</th></tr>${rows.map(c => {
    const r1 = c.f && c.f.r1h, [cls, word] = SAFE[c.screen] || SAFE.unknown, rules = c.rules.filter(r => r !== "all");
    return `<tr data-mkey="${esc(c.key)}" role="button" tabindex="0" aria-current="${c.key === MSEL}" aria-label="${esc((c.symbol || "?") + ", " + c.chain + ", " + word + " safety. Show its chart")}"><td><b>${esc(c.symbol || "?")}</b>${c.held ? ' <span class="chip live held">HELD</span>' : ""}<small class="mm-sub">${esc(c.chain + (rules.length ? " \u00b7 " + rules.map(r => (RULE_SHORT[r] || RULE_INFO[r] || r)).join(", ") : ""))}</small></td><td class="num ${tone(r1)}">${pctS(r1, 1)}</td><td class="${cls}">${word}</td></tr>`;
  }).join("")}</table>` : `<div class="empty"><b>${q ? "No matching coins" : "No coins yet"}</b>${q ? "Try another symbol, chain or address." : "The desk has no indexed pools to show."}</div>`;
}
function renderMemePos() {
  const m = MM; if (!m) return;
  if (MF === "open") $("#m-pos").innerHTML = m.positions.length ? m.positions.map(p => `<article class="card pos-card" data-mkey="${esc(p.key)}" role="button" tabindex="0" aria-label="${esc((p.symbol || "coin") + ": show its chart")}"><div class="chead"><div class="logo" aria-hidden="true">${initials(p.symbol || "?")}</div><div><b>${esc(p.symbol)}</b> <small style="color:var(--label2)">${esc(p.chain)} · ${esc(polName(p.policy))}</small></div><span class="${tone(p.pnl)} num" style="margin-left:auto;font-weight:600">${pctS(p.move_pct, 1)}</span></div>
      <div class="list" style="margin-top:8px">
      <div class="li num"><span>Bought ${esc(p.at.slice(11, 16))} UTC at</span><b>${cpx(p.px)}</b></div>
      <div class="li num"><span>Now</span><b>${cpx(p.now_px)}</b></div>
      <div class="li num"><span>Stop · target</span><b>${cpx(p.stop)} · ${cpx(p.target)}</b></div>
      <div class="li num"><span>Worth after costs</span><b class="${tone(p.pnl)}">${usd(p.value)} (${usd(p.pnl, true)})</b></div>
      <div class="li num"><span>Sells by</span><b>${esc(p.ends.slice(11, 16))} UTC</b></div></div></article>`).join("")
    : `<div class="card empty"><b>Nothing held</b>A buy happens when a rule fires on a coin that passed the safety check. Careful mode trades rarely.</div>`;
  else $("#m-pos").innerHTML = m.closed.length ? `<div class="card scroll"><table><tr><th style="text-align:left">Coin</th><th>Why</th><th>Return</th><th>Result</th></tr>${m.closed.map(t => `<tr><td style="text-align:left"><b>${esc(t.symbol)}</b> <small style="color:var(--label2)">${esc(t.exit_at.slice(5, 16))}</small></td><td>${esc(exitName(t.why))}</td><td class="num ${tone(t.ret_pct)}">${pctS(t.ret_pct, 1)}</td><td class="num ${tone(t.pnl)}">${usd(t.pnl, true)}</td></tr>`).join("")}</table></div>`
    : `<div class="card empty"><b>No closed trades yet</b></div>`;
}
const MEME_MODEL_ROWS = [
  ["all", "P0", "Buy everything"], ["hype", "P1", "Hype"],
  ["breakout", "P2", "Breakout"], ["calm_aged", "P3", "Calm aged"],
  ["dip_aged", "P4", "Aged dip"], ["aged_liquid", "P5", "Aged and liquid"],
  ["news", "P6", "News"], ["migration", "P7", "Migration"],
  ["moonshot", "P8", "Moonshot"], ["quick", "P9", "Quick"], ["attention", "P11", "Attention"], ["careful", "", "Careful"],
];
const memeThin = n => n < 30 ? '<span class="tag weak">Too few</span>' : "";
function memeUtc(value) {
  if (!value) return "not recorded";
  const date = new Date(value.replace(" ", "T") + (/Z$|[+-]\d{2}:\d{2}$/.test(value) ? "" : "Z"));
  return isNaN(date.getTime()) ? "not recorded" : date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}
function memePulse(m, failed = false) {
  const stamp = memeUtc(m?.checked);
  const age = Date.now() - Date.parse(stamp.replace(" ", "T").replace(" UTC", "Z"));
  const fresh = age >= 0 && age <= 180000;
  let label = "Desk data unavailable";
  if (failed) label = m ? "Refresh failed · showing saved data" : "Refresh failed";
  else if (m && !m.enabled) label = "Scanner off";
  else if (m?.error) label = "Last scan failed";
  else if (fresh) label = m.paused ? "Current · buys paused" : "Desk data current";
  else if (m?.checked) label = "Desk data stale";
  const pulse = `<div><b>Paper only</b><p class="mm-source">Scanner state · last completed scan ${esc(stamp)}</p></div>
    <span class="chip ${fresh && !m.error && !failed && m.enabled ? "live" : ""}"><i></i>${label}</span>`;
  if ($("#m-pulse").innerHTML !== pulse) $("#m-pulse").innerHTML = pulse;
}
function memeModelStats(m, key) {
  return (key === "careful" ? m.model_results?.careful?.stats : m.forward[key]) || {};
}
function renderMemeModels(m) {
  const rows = MEME_MODEL_ROWS.map(([key, id, label]) => {
    const x = memeModelStats(m, key), n = x.n || 0, coins = x.tokens || 0;
    const sliceCount = (x.folds_n || []).filter(count => count > 0).length;
    const positive = x.folds_pos || 0, min = m.model_min_per_slice || 10;
    const controlKey = key === "migration" ? "migration_base" : key === "moonshot" ? "moonshot_base" : key === "quick" ? "quick_control" : key === "attention" ? "attention_count" : "all";
    const control = m.forward[controlKey] || {};
    const controlName = controlKey === "all" ? "P0 · Buy everything" : key === "quick" ? "Same entries, P0 exits" : key === "attention" ? "P11b · Raw buy count" : id + "b · " + (key === "migration" ? "Graduates" : "Young coins");
    const status = key === "all" ? "Control" : m.model_results?.[key]?.passed ? "Passed"
      : n < 100 || coins < 50 || sliceCount < 4 ? "Collecting" : "Testing";
    const ci = x.mean_lo == null || x.mean_hi == null ? "95% range unavailable" : `95% ${pctS(x.mean_lo)} to ${pctS(x.mean_hi)}`;
    const progress = Math.floor(Math.min(1, n / 100, coins / 50, positive / 3,
      controlKey === "all" ? 1 : Math.min(...(x.folds_n?.length ? x.folds_n : [0])) / min) * 100);
    return `<div class="mm-model num">
      <div><b>${id ? id + " · " : ""}${label}</b><span class="tag ${status === "Passed" ? "go" : ""}">${status}</span>${memeThin(n)}</div>
      <div><b class="${tone(x.mean)}">${pctS(x.mean)}</b><small>Net mean · ${ci}</small></div>
      <div><b>${n} / 100 trades · ${coins} / 50 coins</b>
      ${key === "all" ? "" : `<progress class="mm-progress" max="100" value="${progress}" aria-label="${label} sample and slice progress"></progress>`}
      <small>${positive} / 4 slices positive${controlKey === "all" ? "" : ` · min ${min} trades/slice (${(x.folds_n || [0, 0, 0, 0]).join(" / ")})`}</small></div>
      <div>${key === "all" ? "<small>Baseline for all candidates</small>" : `<b>${controlName}</b>
        <small>${pctS(control.mean)} net · n=${control.n || 0}${control.n >= 30 ? "" : " · too few"}${controlKey === "all" ? "" : " · also compare P0"}</small>`}</div></div>`;
  });
  $("#m-models").innerHTML = `<p class="mm-source">Closed forward shadows without extra screening, except Careful uses its own closed paper trades. Ranges resample by coin. Progress is the lowest fraction reached across trade, coin and positive-slice goals. Passing also checks returns and controls.</p>
    ${rows.join("")}<p class="mm-source">Only rules present in this desk's forward record are shown.</p>`;
  const select = $("#m-dist-select");
  if (!select.options.length) select.innerHTML = MEME_MODEL_ROWS.map(([key, id, label]) =>
    `<option value="${key}">${id ? id + " · " : ""}${label}</option>`).join("");
  $("#m-dist").hidden = !m.distributions;
  renderMemeDistribution(m);
}
function renderMemeDistribution(m) {
  const key = $("#m-dist-select").value, limits = m.distributions?.boundaries || [];
  const counts = m.distributions?.counts?.[key] || limits.concat(0).map(() => 0);
  const n = counts.reduce((sum, count) => sum + count, 0);
  const labels = limits.length ? [
    `< ${limits[0]}%`,
    ...limits.slice(1).map((edge, i) => `${limits[i]} to <${edge}%`),
    `≥ ${limits.at(-1)}%`,
  ] : [];
  $("#m-dist-plot").innerHTML = "";
  $("#m-dist-bins").innerHTML = n ? counts.map((count, i) => `<span>${esc(labels[i])}<b>${count}</b></span>`).join("") : "";
  $("#m-dist-source").textContent = `${key === "careful" ? "Careful paper account" : "Forward shadows"} · ${n} closed trades${n < 30 ? " · too few" : ""}${n ? " · exact counts per return band" : " · no distribution yet"}.`;
}
$("#m-dist-select").addEventListener("change", () => {
  if (MM) {
    renderMemeDistribution(MM);
    drawMemeResearchPlots();
  }
});
let MA = null; const MASEASON = {};
async function renderPracticeCurves() {
  try { MA = await api("/api/meme/accounts"); } catch (e) { $("#m-books").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  renderAccountCards();
}
// L1's predictions after its warm-up: is any prediction near 0 (a take is a predicted net above 0)? Descriptive only.
function predLine(p) {
  if (!p) return "";
  if (!p.n) return `<small>Predictions after warm-up: none yet${p.warmup ? " (" + p.warmup + " in warm-up)" : ""}.</small>`;
  const f = x => (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(2) + "%";
  return `<small class="mm-preds">Predicted net, ${p.n} after warm-up: median ${f(p.median)} · p90 ${f(p.p90)} · max <b class="${p.max > 0 ? "pos" : ""}">${f(p.max)}</b> · ${p.takes} take${p.takes === 1 ? "" : "s"} (above 0)${p.warmup ? " · " + p.warmup + " in warm-up" : ""}</small>`;
}
function renderAccountCards() {
  if (!MA) return;
  const start = MA.start, accts = MA.accounts || [];
  const card = (a, idx) => {
    const ss = a.seasons || [], cur = ss.length ? ss.at(-1).n : 1;
    const sel = ss.find(x => x.n === MASEASON[a.name]) || ss.at(-1);
    const tabs = ss.length > 1 ? `<div class="mm-seasons" role="tablist">${ss.map(x => `<button class="btn text" role="tab" data-ma-season="${esc(a.name)}" data-n="${x.n}" aria-pressed="${sel && x.n === sel.n}">S${x.n}</button>`).join("")}</div>` : "";
    if (a.hidden) return `<article class="mm-book num"><div class="mm-book-head"><b>${esc(a.label)}</b><small>Study view · read only</small></div><small>${esc(a.note)}</small></article>`;
    const body = sel ? `<b class="mm-book-equity ${tone(sel.pnl)}">${usd(sel.equity)} <span>${usd(sel.pnl, true)} · ${pctS(sel.pnl_pct)}</span></b>
      <small>Season ${sel.n}${sel.n === cur ? " (now)" : " (closed " + esc((sel.ended || "").slice(0, 16)) + " UTC)"} · ${sel.trades} trades · ${sel.win_rate == null ? "no wins yet" : sel.win_rate + "% won"} · ${sel.open} open</small>
      <div class="mm-plot" data-ma-plot="${idx}"></div>` : "";
    return `<article class="mm-book num"><div class="mm-book-head"><b>${esc(a.label)}</b><small>${a.kind === "view" ? "Study view · read only" : "Practice account"}</small></div>
      ${tabs}${body}${a.note ? `<small>${esc(a.note)}</small>` : ""}${a.sizing ? `<small>${esc(a.sizing)}</small>` : ""}${predLine(a.preds)}
      ${a.refillable ? `<button class="btn gray" data-ma-refill="${esc(a.name)}">Refill to ${usd(start)}</button>` : ""}</article>`;
  };
  $("#m-books").innerHTML = `<div class="hrow"><button class="btn gray" id="m-refill-all">Refill all to ${usd(start)}</button></div>
    <div class="mm-book-grid">${accts.map(card).join("")}</div>
    <p class="mm-source">Every account starts at ${usd(start)}. A refill ends the season (open coins sold at the last price with the normal costs, "season end") and keeps it under its season tab; nothing is deleted. A refill changes balances only: never the shadows, P0, the screen, the exits or the costs. Study views are never traded by the desk; they read the studies' own logs. D1's forward window ends ${esc(MA.d1_window_ends)} UTC.</p>`;
  drawAccountPlots();
}
function drawAccountPlots() {
  if (!MA) return;
  document.querySelectorAll("[data-ma-plot]").forEach(el => {
    const a = MA.accounts[Number(el.dataset.maPlot)], ss = a.seasons || [];
    const sel = ss.find(x => x.n === MASEASON[a.name]) || ss.at(-1);
    const pts = (sel && sel.curve || []).filter(c => Number.isFinite(c.ts) && Number.isFinite(c.equity));
    const w = el.clientWidth;
    if (pts.length < 2 || !w) { el.innerHTML = ""; return; }
    const t0 = pts[0].ts, ts = Math.max(1, pts.at(-1).ts - t0), base = sel.start;
    const lo = Math.min(base, ...pts.map(p => p.equity)), hi = Math.max(base, ...pts.map(p => p.equity)), span = hi - lo || 1;
    const y = v => 4 + 32 * (hi - v) / span;
    const xy = pts.map(p => `${(4 + (w - 8) * (p.ts - t0) / ts).toFixed(1)},${y(p.equity).toFixed(1)}`).join(" ");
    el.innerHTML = `<svg width="${w}" height="40" viewBox="0 0 ${w} 40" role="img" aria-label="${esc(a.label)} season ${sel.n} equity, ${pts.length} marks"><line x1="0" x2="${w}" y1="${y(base)}" y2="${y(base)}" stroke="var(--label2)" stroke-dasharray="3 3" opacity=".6"/><polyline fill="none" stroke="var(--tint)" stroke-width="2" points="${xy}"/></svg>`;
  });
}
async function refillAccounts(who, label) {
  const start = MA ? usd(MA.start) : "$100";
  const msg = who === "all"
    ? `Refill every memecoin account to ${start}? Each one's current season ends and is kept under its season tab; open coins are sold at the last price with the normal costs. Shadows and the forward record are not touched.`
    : `Refill ${label} to ${start}? Its current season ends and is kept under its season tab; open coins are sold at the last price with the normal costs.`;
  if (!confirm(msg)) return;
  try { await api("/api/meme/refill", {sure:true, account:who}); toast(who === "all" ? "Every account starts a new season." : label + " starts a new season."); Object.keys(MASEASON).forEach(k => delete MASEASON[k]); loadMeme(); }
  catch (err) { toast(err.message, true); }
}
function renderMemeFunnels(m) {
  const mo = m.moonshots || {}, g = m.migrations || {};
  const stages = (items, total) => items.map(([label, count]) =>
    `<div class="mm-funnel-row"><div class="mm-funnel-stat"><span>${label}</span><b class="num">${count || 0} / ${total}</b></div>
      <div class="mm-plot" data-stage="${count || 0}" data-stage-total="${total}"></div></div>`).join("");
  const reasons = (rows, total) => rows?.length ? `<details class="mm-research-details"><summary>Reasons logged</summary><ul>
    ${rows.map(([name, count]) => `<li>${esc(name)} · ${count} of ${total} decisions</li>`).join("")}</ul></details>` : "";
  const seen = g.seen || 0, fired = mo.fired || 0;
  $("#m-mig").innerHTML = stages([["Graduates seen", seen], ["On-time decisions", g.decided],
    ["P7 signals", g.yes], ["P7b control signals", g.yes_base]], seen)
    + `<p class="mm-source">migrations.jsonl · ${g.missed || 0} of ${seen} arrived too late. ${g.filled || 0} fills and ${g.cancelled || 0} cancels across P7 and P7b, counted separately.</p>`
    + reasons(g.top_no, g.decided || 0)
    + '<p class="mm-source">Rule and control definitions in docs/MEME.md §9.</p>';
  $("#m-moon").innerHTML = stages([["Take-off signals", fired], ["Cleared entry checks", mo.passed],
    ["P8 fills", mo.filled], ["P8 cancels", mo.cancelled]], fired)
    + `<p class="mm-source">moonshots.jsonl · ${fired} signals across ${mo.coins || 0} distinct coins. Latest records can include pending fills.</p>`
    + reasons(mo.blocked, fired)
    + `<p class="mm-source">P8 closed shadows · n=${m.forward.moonshot?.n || 0}${(m.forward.moonshot?.n || 0) < 30 ? " · too few" : ""}. Best ${pctS(mo.best)} · at least +100% ${mo.share_100 == null ? "–" : mo.share_100 + "%"} · mean excluding best 10 ${pctS(mo.mean_no_top10)}. Rule in docs/MEME.md §10.</p>`;
}
function renderMemeQuick(m) {
  const q = m.quick || {}, x = q.stats || {}, c = q.control || {}, e = q.ends || {}, n = x.n || 0;
  const row = (label, value) => `<div class="mm-funnel-row"><div class="mm-funnel-stat"><span>${label}</span><b class="num">${value}</b></div></div>`;
  const dex = Object.entries(q.dex || {}).map(([k, v]) => `${esc(k)} ${v}`).join(" · ");
  $("#m-quick").innerHTML = row("Closed shadows", `${n} · ${q.open || 0} open`)
    + row("Net mean", `${pctS(x.mean)}${x.mean_lo == null ? "" : ` · 95% ${pctS(x.mean_lo)} to ${pctS(x.mean_hi)}`}`)
    + row("Win rate", `${x.win_pct == null ? "–" : x.win_pct + "%"} vs ${q.breakeven_pct ?? "57"}% break-even (Ethereum ${q.breakeven_eth_pct ?? "75"}%)`)
    + row("Ended at +10% / -5% / time", `${e.target || 0} / ${e.stop || 0} / ${e.time || 0}${(e.rug || 0) + (e["liquidity pulled"] || 0) ? ` · pulled ${(e.rug || 0) + (e["liquidity pulled"] || 0)}` : ""}`)
    + row("Control: same entries, P0 exits", `${pctS(c.mean)} · n=${c.n || 0}${(c.n || 0) < 30 ? " · too few" : ""}`)
    + row("Buy everything, same window", `${pctS((q.everything || {}).mean)} · n=${(q.everything || {}).n || 0}`)
    + `<p class="mm-source">Closed shadows since the first P9 entry · ${x.folds_pos || 0} / 4 slices positive · min 10 trades/slice (${(x.folds_n || [0, 0, 0, 0]).join(" / ")})${dex ? " · venues: " + dex : ""}. Rule in docs/MEME.md §15.</p>`
    + (q.error ? `<p class="mm-source">Last P9 error: ${esc(q.error)}</p>` : "");
}
function renderMemeAttention(m) {
  const a = m.attention || {}, x = a.stats || {}, c = a.controls || {}, e = a.ends || {}, o = a.open || {}, h = a.helius || {}, r = a.rules || {}, n = x.n || 0;
  const row = (label, value) => `<div class="mm-funnel-row"><div class="mm-funnel-stat"><span>${label}</span><b class="num">${value}</b></div></div>`;
  const line = s => `${pctS((s || {}).mean)} · n=${(s || {}).n || 0} · ${(s || {}).tokens || 0} coins${((s || {}).n || 0) < 30 ? " · too few" : ""}`;
  const state = !a.on ? "Off (setting meme_attention)" : !a.key ? "On, but no P11 key file" : "On";
  $("#m-attention").innerHTML = row("Status", state)
    + row("Closed shadows", `${n} · ${o.attention || 0} open`)
    + row("Net mean", `${pctS(x.mean)}${x.mean_lo == null ? "" : ` · 95% ${pctS(x.mean_lo)} to ${pctS(x.mean_hi)}`}`)
    + row("Median · win rate", `${pctS(x.median)} · ${x.win_pct == null ? "–" : x.win_pct + "%"}`)
    + row("Ended at target / stop / time", `${e.target || 0} / ${e.stop || 0} / ${e.time || 0}${(e.rug || 0) ? ` · dead pool ${e.rug}` : ""}`)
    + row("a · Buy everything, same window (must beat)", line(a.everything))
    + row("b · Raw buy count (must beat)", line(c.attention_count))
    + row("c · Many buyers, not boosted", line(c.attention_crowd))
    + row("Helius today", `${(h.credits || 0).toLocaleString()} / ${(h.cap || 30000).toLocaleString()} credits · ${h.reads || 0} reads${h.capped ? ` · ${h.capped} skipped at cap` : ""}`)
    + `<p class="mm-source">P0 entries on busy (m5 buys ≥ ${r.min_m5_buys ?? 20}) ${(r.dexes || ["pumpfun", "pumpswap"]).join("/")} coins that are boosted on DexScreener. A boost is <b>paid promotion</b>, not views. Buys when distinct buyers per covered minute ≥ ${r.n_bpm ?? 34.66} (last ≤ 300 transactions; creator and bot-like wallets excluded). b uses m5 buys ≥ ${r.n_buys ?? 370}; c is a ${Math.round((r.sample ?? 0.15) * 100)}% seeded sample of not-boosted coins. Exits ${r.stop ?? -15}% / +${r.target ?? 30}% / ${(r.hold_min ?? 240) / 60} h. ${x.folds_pos || 0} / 4 slices positive · min ${a.min_per_slice || 10} trades/slice (${(x.folds_n || [0, 0, 0, 0]).join(" / ")}) · ${a.passed ? "passed the bar" : "not passed"}. Rule in docs/MEME.md §22.</p>`
    + (a.error ? `<p class="mm-source">Last P11 error: ${esc(a.error)}</p>` : "");
}
function drawMemeResearchPlots() {
  if (!MM || !$("#v-meme").classList.contains("on")) return;
  const svg = (el, height, label, contents) => {
    const w = el.clientWidth;
    if (w) el.innerHTML = `<svg width="${w}" height="${height}" viewBox="0 0 ${w} ${height}" role="img" aria-label="${esc(label)}">${contents(w)}</svg>`;
  };
  const dist = MM.distributions?.counts?.[$("#m-dist-select").value];
  if (dist?.some(n => n)) svg($("#m-dist-plot"), 88, "Trade count per return band, values listed below", w => {
    const dx = w / dist.length, ymax = Math.max(...dist), baseline = 85;
    return `<path d="M0 ${baseline}H${w}" stroke="var(--label2)" opacity=".45"/>`
      + dist.map((n, i) => {
        const h = n / ymax * 78, color = MM.distributions.boundaries[i] <= 0 ? "var(--stop)" : "var(--go)";
        return `<rect x="${i * dx + 4}" y="${baseline - h}" width="${Math.max(0, dx - 8)}" height="${h}" rx="3" fill="${color}"/>`;
      }).join("");
  });
  const books = MM.books || [];
  const series = books.map(b => (b.curve || []).map(c => [c.ts, (c.equity / b.start - 1) * 100])
    .filter(([ts, pct]) => Number.isFinite(ts) && Number.isFinite(pct)));
  const points = series.filter(s => s.length >= 2).flat();
  const ticks = points.map(([ts]) => ts), levels = points.map(([, value]) => value);
  const minTs = Math.min(...ticks), timeSpan = Math.max(1, Math.max(...ticks) - minTs);
  const lo = Math.min(0, ...levels), hi = Math.max(0, ...levels), span = hi - lo || 1;
  const y = p => hi === lo ? 20 : 4 + 32 * (hi - p) / span;
  document.querySelectorAll("[data-meme-book]").forEach(el => {
    const i = Number(el.dataset.memeBook), pts = series[i];
    if (pts.length < 2) return;
    svg(el, 40, `Paper equity for ${polName(books[i].name)} · ${pts.length} marks`, w => {
      const xy = pts.map(([ts, p]) => `${(4 + (w - 8) * (ts - minTs) / timeSpan).toFixed(1)},${y(p).toFixed(1)}`).join(" ");
      return `<line x1="0" x2="${w}" y1="${y(0)}" y2="${y(0)}" stroke="var(--label2)" stroke-dasharray="3 3" opacity=".6"/>
        <polyline fill="none" stroke="var(--tint)" stroke-width="2" points="${xy}"/>`;
    });
  });

  const full = $("#m-full-equity");
  if (full.open) {
    const curve = MM.curve || [], start = MM.account.start;
    const n = curve.length;
    const changes = curve.map(mark => (mark.equity / start - 1) * 100);
    const low = Math.min(0, ...changes), high = Math.max(0, ...changes), range = high - low || 1;
    const from = curve[0]?.ts || 0, span = (curve.at(-1)?.ts || from) - from || 1;
    const yFull = p => 10 + (high - p) / range * 126;
    if (n >= 2) svg($("#m-full-plot"), 150, "Main paper equity, " + n + " saved marks", w => {
      const d = curve.map((mark, i) => `${i ? "L" : "M"}${(5 + (w - 10) * (mark.ts - from) / span).toFixed(1)} ${yFull(changes[i]).toFixed(1)}`).join(" ");
      return `<line x1="0" x2="${w}" y1="${yFull(0)}" y2="${yFull(0)}" stroke="var(--label2)" stroke-dasharray="3 3" opacity=".6"/>
        <path d="${d}" fill="none" stroke="var(--tint)" stroke-width="2"/>`;
    });
    else $("#m-full-plot").innerHTML = "";
    $("#m-full-source").textContent = `Main paper account · ${n} saved marks returned by the desk (up to 500) · ${MM.account.trades} closed trades${MM.account.trades < 30 ? " · too few" : ""}.
      ${n < 2 ? "Curve pending." : `Range ${pctS(low)} to ${pctS(high)} from start. Dotted line is starting equity.`}`;
  }
  document.querySelectorAll("[data-stage]").forEach(el => svg(el, 6,
    `${el.dataset.stage} of ${el.dataset.stageTotal} events`, w => {
      const portion = Math.min(1, el.dataset.stage / (el.dataset.stageTotal || 1)) || 0;
      return `<rect width="${w}" height="6" rx="3" fill="var(--fill)"/><rect width="${w * portion}" height="6" rx="3" fill="var(--tint)"/>`;
    }));
}
$("#m-full-equity").addEventListener("toggle", drawMemeResearchPlots);
let memePlotWidth = 0;
new ResizeObserver(([entry]) => {
  if (entry.contentRect.width === memePlotWidth) return;
  memePlotWidth = entry.contentRect.width;
  drawMemeResearchPlots();
  drawAccountPlots();
}).observe($("#v-meme"));
function renderMemeWhy(c) {
  const standardRules = c.rules.filter(rule => rule !== "all");
  const strict = SAFE[c.strict] || SAFE.unknown;
  const blockers = [
    ...(c.screen !== "pass" ? ["Plain safety: " + (SAFE[c.screen] || SAFE.unknown)[1]] : []),
    ...(c.strict !== "pass" ? ["Strict safety: " + strict[1], ...(c.strict_why || [])] : []),
    ...(c.vetoes || []),
  ];
  return `<div class="mm-why"><b>Why this coin may stay unbought</b>
    <p class="mm-source">${standardRules.length ? "Matched standard rules: " + esc(standardRules.map(polName).join(", ")) : "No standard entry rule matched this snapshot."}</p>
    ${blockers.length ? `<ul>${blockers.map(reason => `<li>${esc(friendly(reason))}</li>`).join("")}</ul>` :
      '<p class="mm-source">No recorded safety or research veto for Careful on this snapshot.</p>'}
    <p class="mm-source">One stored DexScreener snapshot · ${esc(memeUtc(c.snapshot_at))}. Safety cache · ${esc(memeUtc(c.screen_at))}.
      This is a snapshot explanation. Slot limits, cooldown and P7/P8 decision windows are evaluated separately.</p></div>`;
}
// Label narrow-screen tables from their existing column headers, including tables refreshed by the API.
function updateCompactTables() {
  document.querySelectorAll(".view.on .scroll:not(#m-list) table:not(.compact-table)").forEach(table => {
    const headers = [...(table.rows[0]?.cells || [])];
    if (headers.length < 3 || headers.some(cell => cell.tagName !== "TH")) return;
    table.classList.add("compact-table");
    table.setAttribute("role", "table");
    [...table.rows].forEach(row => {
      row.setAttribute("role", "row");
      let column = 0;
      [...row.cells].forEach(cell => {
        cell.setAttribute("role", cell.tagName === "TH" ? "columnheader" : "cell");
        if (cell.tagName === "TD" && cell.colSpan === 1) cell.dataset.column = headers[column]?.textContent.trim() || "";
        column += cell.colSpan;
      });
    });
  });
}
new MutationObserver(updateCompactTables).observe(document.querySelector("main"), {childList: true, subtree: true, attributes: true, attributeFilter: ["class"]});
function candleSvg(d, cwPx) {
  if (!d.bars.length) return `<div class="empty"><b>No candles for this coin yet</b>GeckoTerminal has no trades for its pool in this window.</div>`;
  // Drawn at the card's real pixel width (1 SVG unit = 1 px) so text stays 12px at any width; price tags live in the right gutter.
  const W = Math.max(280, Math.round(cwPx || 720)), H = W < 520 ? 300 : 340, FS = 12, L = 6, compact = W < 500, tagTxt = v => (compact ? v.title.slice(0, 1) : v.title) + " " + cpx(v.price);
  const R = Math.max(74, Math.ceil(Math.max(0, ...d.levels.map(v => tagTxt(v).length)) * 7.4) + 14), VB = 44;
  // Buy and sell marks in view get a legend row above the plot, so the legend never sits on a candle.
  const seenFrom = d.bars.length ? d.bars[Math.max(0, d.bars.length - Math.max(30, Math.min(120, Math.floor((W - L - R) / 3))))].time : 0;
  const marks = d.trades.filter(x => x.time >= seenFrom), T = marks.length ? 28 : 10, PB = H - VB - 24;
  const plotW = W - L - R, bars = d.bars.slice(-Math.max(30, Math.min(120, Math.floor(plotW / 3))));
  const prices = bars.flatMap(b => [b.high, b.low]).concat(d.levels.map(v => v.price));
  let lo = Math.min(...prices), hi = Math.max(...prices); if (hi === lo) { hi *= 1.01; lo *= 0.99; }
  const pad = (hi - lo) * 0.04; lo -= pad; hi += pad;
  const n = bars.length, cw = plotW / n, X = i => L + i * cw + cw / 2, Y = p => T + (hi - p) / (hi - lo) * (PB - T);
  const vmax = Math.max(...bars.map(b => b.volume || 0)) || 1;
  const COLS = {entry: "var(--tint)", stop: "var(--stop)", target: "var(--go)"}, TAGTXT = {entry: "#06121f", stop: "#2a0605", target: "#032118"};
  // level tags: sorted top to bottom and nudged apart so none overlap
  const tags = d.levels.map(v => ({v, y: Y(v.price)})).sort((a, b) => a.y - b.y); const TH = 18;
  tags.forEach((t, i) => { if (i && t.y < tags[i - 1].y + TH + 2) t.y = tags[i - 1].y + TH + 2; });
  const over = tags.length ? tags.at(-1).y + TH / 2 - PB : 0; if (over > 0) tags.forEach(t => t.y -= over);
  let g = "";
  // five gridlines; each price label is skipped when a level tag sits on it, then in-between labels fill in so at least 3 show
  const axis = k => { const p = lo + (hi - lo) * k / 8; return {p, y: Y(p)}; }, shown = [];
  for (const k of [0, 2, 4, 6, 8, 1, 3, 5, 7]) { const a = axis(k);
    if (k % 2 === 0) g += `<line x1="${L}" x2="${W - R}" y1="${a.y}" y2="${a.y}" stroke="var(--sep)" stroke-width=".5"/>`;
    if (k % 2 && shown.length >= 3) continue;
    if (tags.some(t => Math.abs(t.y - a.y) < TH) || shown.some(y => Math.abs(y - a.y) < 18)) continue;
    shown.push(a.y); g += `<text x="${W - R + 6}" y="${a.y + 4}" font-size="${FS}" fill="var(--label2)">${cpx(a.p)}</text>`; }
  bars.forEach((b, i) => {
    const up = b.close >= b.open, c = up ? "var(--go)" : "var(--stop)", bw = Math.max(1, cw * 0.7);
    g += `<line x1="${X(i)}" x2="${X(i)}" y1="${Y(b.high)}" y2="${Y(b.low)}" stroke="${c}" stroke-width="1"/>`;
    g += `<rect x="${X(i) - bw / 2}" y="${Math.min(Y(b.open), Y(b.close))}" width="${bw}" height="${Math.max(1, Math.abs(Y(b.open) - Y(b.close)))}" fill="${c}"/>`;
    const vh = (b.volume || 0) / vmax * VB;
    g += `<rect x="${X(i) - bw / 2}" y="${H - 24 - vh}" width="${bw}" height="${vh}" fill="${c}" opacity=".35"/>`;
  });
  tags.forEach(({v, y}) => { const col = COLS[v.kind] || "var(--label2)", py = Y(v.price), txt = tagTxt(v);
    g += `<line x1="${L}" x2="${W - R}" y1="${py}" y2="${py}" stroke="${col}" stroke-dasharray="5 4"/>`;
    g += `<rect x="${W - R + 2}" y="${y - TH / 2}" width="${R - 4}" height="${TH}" rx="4" fill="${col}"><title>${esc(v.title + " " + cpx(v.price))}</title></rect><text x="${W - R + 2 + (R - 4) / 2}" y="${y + 4}" text-anchor="middle" font-size="${FS}" font-weight="600" fill="${TAGTXT[v.kind] || "#000"}">${esc(txt)}</text>`; });
  const t0 = bars[0].time, idx = t => { let j = 0; bars.forEach((b, i) => { if (b.time <= t) j = i; }); return j; };
  const fmt = t => new Date(t * 1000).toISOString().slice(11, 16);
  d.trades.filter(x => x.time >= t0).forEach(x => { const i = idx(x.time), b = bars[i], buy = x.side === "buy";
    const tip = (buy ? "Bought" : "Sold") + (x.policy ? " · " + String(x.policy).replace(/_/g, " ") : "") + (x.why ? " · " + x.why : "") + " · " + fmt(x.time) + " UTC";
    g += `<text x="${X(i)}" y="${buy ? Y(b.low) + 14 : Y(b.high) - 6}" text-anchor="middle" font-size="13" fill="${buy ? "var(--tint)" : "#FFB340"}"><title>${esc(tip)}</title>${buy ? "▲" : "▼"}</text>`; });
  if (marks.length) g += `<text x="${L}" y="14" font-size="${FS}" fill="var(--label2)"><tspan fill="var(--tint)">▲</tspan> bought · <tspan fill="#FFB340">▼</tspan> sold</text>`;
  // time axis: first, middle and last bar (fewer when there are fewer bars), text kept inside the plot
  const tl = n >= 3 ? [[0, "start"], [(n - 1) >> 1, "middle"], [n - 1, "end"]] : n === 2 ? [[0, "start"], [1, "end"]] : [[0, "end"]];
  tl.forEach(([i, a]) => { const x = a === "start" ? L : a === "end" ? W - R : X(i);
    g += `<text x="${x}" y="${H - 4}" text-anchor="${a}" font-size="${FS}" fill="var(--label2)">${fmt(bars[i].time)} UTC</text>`; });
  return `<svg viewBox="0 0 ${W} ${H}" width="${W}" height="${H}" style="display:block;max-width:100%" aria-hidden="true">${g}</svg>`;
}
let MCHD = null, MCHW = 0;
function drawMemeChart() { if (!MCHD) return; const w = $("#m-chart").clientWidth; if (!w) return; MCHW = w; $("#m-chart").innerHTML = candleSvg(MCHD, w); }
let MRT = 0; addEventListener("resize", () => { clearTimeout(MRT); MRT = setTimeout(() => { if (MCHD && $("#v-meme").classList.contains("on") && Math.abs($("#m-chart").clientWidth - MCHW) > 4) drawMemeChart(); }, 150); });
async function loadMemeChart(force) {
  if (!MM || !MSEL) return;
  const key = MSEL + MIVL, c = MM.candidates.find(x => x.key === MSEL) || {};
  renderMemeFacts(c);
  $("#mch-sym").textContent = (c.symbol || "?") + " · " + (c.chain || "");
  if (!force && key === MCHKEY && Date.now() - MCHAT < 55000) return;
  if (key !== MCHKEY) {
    MCHD = null;
    $("#mch-last").textContent = "";
    $("#mch-dex").hidden = true;
    $("#m-chart").setAttribute("aria-label", "Loading coin candles");
    $("#m-chart").innerHTML = '<div class="empty"><b>Loading candles…</b></div>';
  }
  MCHKEY = key; MCHAT = Date.now();
  let d;
  try { d = await api(`/api/meme/chart?key=${encodeURIComponent(MSEL)}&ivl=${MIVL}`); }
  catch (e) { if (key !== MSEL + MIVL) return; $("#m-chart").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; MCHKEY = ""; return; }
  if (key !== MSEL + MIVL) return;
  MCHD = d; drawMemeChart();
  $("#m-chart").setAttribute("aria-label", `${d.symbol || "coin"} ${MIVL} candles` + (d.bars.length ? `, last ${cpx(d.bars.at(-1).close)}` : ""));
  $("#mch-last").textContent = d.bars.length ? cpx(d.bars.at(-1).close) : "";
  const dx = $("#mch-dex"); dx.href = d.url; dx.hidden = false;
}
function renderMemeFacts(c) {
  if (!c.key) { $("#m-facts").innerHTML = ""; return; }
  const f = c.f || {}, age = f.age_h == null ? "–" : f.age_h < 48 ? f.age_h.toFixed(1) + " h" : (f.age_h / 24).toFixed(1) + " days";
  const kv = (k, v) => `<div class="li num"><span>${k}</span><b>${v}</b></div>`, [cls, word] = SAFE[c.screen] || SAFE.unknown;
  $("#m-facts").innerHTML = `<div class="list" style="margin-top:10px">${kv("Age", age)}${kv("Pool", c.liq == null ? "bonding curve" : money(c.liq))}${kv("Value (FDV)", money(c.fdv))}${kv("Volume, 1 h · 24 h", money(f.v1h) + " · " + money(c.v24))}
    ${kv("Move, 5 min · 1 h · 6 h", `<span class="${tone(f.r5)}">${pctS(f.r5, 1)}</span> · <span class="${tone(f.r1h)}">${pctS(f.r1h, 1)}</span> · <span class="${tone(f.r6h)}">${pctS(f.r6h, 1)}</span>`)}
    ${kv("Buys, last 5 min", f.buy_ratio_m5 == null ? "–" : Math.round(f.buy_ratio_m5 * 100) + "% of " + f.tx_m5)}
    ${kv("Safety", `<span class="${cls}">${word}</span>`)}
    ${kv("Rules that would buy now", esc(c.rules.filter(r => r !== "all").map(r => RULE_INFO[r]).join(", ") || "none"))}</div>
    <p class="foot-note">${esc(cap1(friendly(c.why.join("; "))))}. Found ${esc(c.first_seen.slice(5, 16))} UTC via ${esc([...new Set(c.sources.map(feedName))].join(", "))}.</p>${renderMemeWhy(c)}`;
}
document.addEventListener("keydown", e => { const r = e.target.closest && e.target.closest("[data-mkey]"); if (r && e.target === r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); r.click(); } });

// ---- Memebot tab: read-only view of the separate memecoin paper-research loop
const ago = ts => { if (!ts) return "never"; const s = Math.max(0, Date.now() / 1000 - ts); return s < 90 ? Math.round(s) + " s ago" : s < 5400 ? Math.round(s / 60) + " min ago" : s < 172800 ? Math.round(s / 3600) + " h ago" : Math.round(s / 86400) + " days ago"; };
async function loadMemebot() {
  let d; try { d = await api("/api/memebot"); } catch (e) { $("#mb-body").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
  if (!d.found) { $("#mb-body").innerHTML = `<div class="empty"><b>Memebot folder not found</b>${esc(d.hint)}${d.dir ? `<br><span class="num">Looked in: ${esc(d.dir)}</span>` : ""}</div>`; return; }
  const L = d.log || {}, T = d.trade_summary || {}, cyc = (L.cycles || {});
  const chip = `<span class="chip ${d.running ? "live" : ""}"><i></i>${d.running ? "Running" : "Not running"}</span>`;
  const table = cyc.head && cyc.head.length ? `<div class="scroll"><table><tr>${cyc.head.map(h => `<th>${esc(friendly(h))}</th>`).join("")}</tr>${cyc.rows.map(r => `<tr>${r.map(c => `<td>${esc(friendly(c))}</td>`).join("")}</tr>`).join("")}</table></div>` : `<div class="empty"><b>No cycles yet</b>The loop log has no cycle table.</div>`;
  const trades = d.trades && d.trades.length ? `<div class="scroll"><table><tr><th>Token</th><th>Result</th><th>Return</th><th>Exit</th><th>Held</th><th>When</th></tr>${d.trades.slice(0, 50).map(t => `<tr><td style="text-align:left"><b>${esc(t.symbol)}</b></td><td class="num ${t.pnl >= 0 ? "pos" : "neg"}">${typeof t.pnl === "number" ? usd(t.pnl, true) : esc(t.pnl)}</td><td class="num">${typeof t.ret === "number" ? (t.ret * 100).toFixed(1) + "%" : ""}</td><td>${esc(exitName(t.why))}</td><td class="num">${esc(holdTxt(t.hold_min))}</td><td class="num">${esc(ago(t.t_out))}</td></tr>`).join("")}</table></div>` : `<div class="empty"><b>No closed paper trades yet</b></div>`;
  $("#mb-body").innerHTML = `
    <div class="card hero">
      <div class="cap"><span>Loop</span>${chip}</div>
      <p style="margin:8px 0 4px"><b>${esc(L.status || "No LOOP_LOG.md found yet")}</b></p>
      <p class="foot-note" style="margin:0">Last file written ${esc(ago(d.last_write))}${L.updated ? ` · log updated ${esc(L.updated)}` : ""}</p>
      <div class="three num" style="margin-top:12px">
        <div><b>${d.discoveries ?? "–"}</b><span>Tokens discovered</span></div>
        <div><b>${T.count ? `${T.wins}/${T.count}` : "–"}</b><span>Paper wins / trades${(T.count || 0) < 30 ? " · too few" : ""}</span></div>
        <div><b class="${T.pnl >= 0 ? "pos" : "neg"}">${T.count ? usd(T.pnl, true) : "–"}</b><span>Paper result, all files</span></div>
      </div>
    </div>
    ${L.verdict ? `<h2 class="section-title">Verdict</h2><div class="card"><p style="margin:0;white-space:pre-wrap">${esc(L.verdict)}</p></div>` : ""}
    <h2 class="section-title">Cycles</h2><div class="card">${table}</div>
    ${d.frozen_params ? `<h2 class="section-title">Frozen parameters</h2><div class="card"><pre class="num" style="margin:0;white-space:pre-wrap;font-size:13px">${esc(JSON.stringify(d.frozen_params, null, 1))}</pre></div>` : ""}
    <h2 class="section-title">Recent paper trades</h2><div class="card">${trades}</div>
    ${L.text ? `<h2 class="section-title">Full loop log</h2><div class="card"><details><summary>Show LOOP_LOG.md</summary><pre style="white-space:pre-wrap;font-size:13px;margin:10px 0 0">${esc(L.text)}</pre></details></div>` : ""}
    <p class="foot-note">Reading ${esc(d.dir)}. Refreshes every 30 seconds while this tab is open.</p>`;
}
setInterval(() => { if ($("#v-meme").classList.contains("on") && !document.hidden) { loadMeme(); loadMemebot(); } }, 30000);

async function congress(refresh) { try { const d = await api("/api/congress" + (refresh ? "?refresh=1" : ""));
  $("#congress").innerHTML = d.filings.length ? `<ul class="plain">${d.filings.map(f => `<li><span class="num">${esc(f.filed)}</span> <b>${esc(f.name)}</b> ${f.url ? `<a href="${esc(f.url)}" target="_blank" rel="noopener noreferrer">${esc(f.type)}</a>` : esc(f.type)}</li>`).join("")}</ul>` : `<div class="empty">${esc(d.error || "No filings found for the members in your settings.")}</div>`; } catch (err) { toast(err.message, true); } }
// refresh every 10 s, but not while you are typing in a field (it would wipe what you typed)
const typing = () => { const a = document.activeElement; return !!(a && /^(INPUT|SELECT|TEXTAREA)$/.test(a.tagName) && a.type !== "file"); };
load(); setInterval(() => { if (!document.hidden && !typing()) load(); }, 10000);
