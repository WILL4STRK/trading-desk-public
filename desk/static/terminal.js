"use strict";
// ---- Terminal tab: watched Solana coins that did NOT fully pass the safety check, and Jupiter's swap widget.
// The desk never holds a key and never sends a transaction: Jupiter's Plugin builds each swap in this page and you
// sign it in your own wallet. Token names and symbols are chosen by whoever made the coin, so every one is escaped.
// One top-level name (TERMINAL) so nothing here can clash with app.js. Pure helpers are exported for Node tests.
//
// The watchlist never moves by itself. A refresh with the same coins only rewrites numbers in place. A new coin is
// inserted at the top (a short slide-in and highlight, none under reduced motion); if you have scrolled down, your
// position is kept and a "N new ↑" pill appears instead. Order changes only when you pick a sort.
const TERMINAL = (() => {
  const JUP_SRC = "https://plugin.jup.ag/plugin-v1.js";   // Jupiter's documented script (major version 1)
  const MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/, TXID = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/;
  const esc = s => String(s ?? "").replace(/[&<>"'`]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;"}[c]));
  const validMint = s => typeof s === "string" && MINT.test(s);
  const validTxid = s => typeof s === "string" && TXID.test(s);
  const pct = (x, d = 1) => x == null || isNaN(x) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) + "%";
  const tone = x => x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "";
  const money = n => n == null ? "–" : n >= 1e6 ? "$" + (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? "$" + (n / 1e3).toFixed(1) + "K" : "$" + Math.round(n);
  // 4 significant digits, never in e-notation: 0.00000001148, not 1.148e-8
  const price = n => { if (n == null || !isFinite(n)) return "–"; n = Number(n); if (n >= 1) return n.toFixed(4); if (n <= 0) return "0";
    return n.toFixed(Math.min(20, Math.ceil(-Math.log10(n)) + 3)); };
  const age = h => h == null ? "–" : h < 48 ? h.toFixed(1) + " h" : (h / 24).toFixed(1) + " d";
  const ago = (ts, now) => { if (!ts) return "never"; const m = Math.max(0, Math.round((now - ts) / 60));
    return m < 60 ? m + " min ago" : m < 48 * 60 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago"; };
  const short = ts => { if (!ts) return "–"; return ago(ts, Date.now() / 1000).replace(" min ago", "m").replace(" h ago", "h").replace(" d ago", "d"); };
  // only links the desk itself built from a validated address, and only to these three sites
  const LINK = {dexscreener: "https://dexscreener.com/solana/", solscan: "https://solscan.io/token/", rugcheck: "https://rugcheck.xyz/tokens/"};
  const safeLink = (kind, url) => typeof url === "string" && url.startsWith(LINK[kind]) && validMint(url.slice(LINK[kind].length)) ? url : null;
  const ext = (kind, url, label) => { const u = safeLink(kind, url);
    return u ? `<a class="tx-link" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(label)}<span class="vh">, opens in a new window</span>&nbsp;↗</a>` : ""; };
  const rec = r => { const p = (n, x) => n ? `${n} · <span class="${tone(x)}">${pct(x)}</span>` : "none";
    return `Shadows ${p(r.shadows, r.shadows_net_pct)} · Practice ${p(r.practice, r.practice_net_pct)}`; };

  // ---- the watchlist: pure parts
  const SORTS = {
    newest: (a, b) => (a.age_h ?? 1e12) - (b.age_h ?? 1e12),
    r1h: (a, b) => (b.r1h ?? -1e12) - (a.r1h ?? -1e12),
    volume: (a, b) => (b.v1h ?? -1) - (a.v1h ?? -1),
    liquidity: (a, b) => (b.liq ?? -1) - (a.liq ?? -1),
    strong: (a, b) => (b.last_strong_ts ?? 0) - (a.last_strong_ts ?? 0),
  };
  const sortCoins = (coins, by) => coins.map((c, i) => [c, i]).sort((x, y) => (SORTS[by] || SORTS.newest)(x[0], y[0]) || x[1] - y[1]).map(x => x[0]);
  const matches = (c, q) => { q = String(q || "").trim().toLowerCase(); if (!q) return true;
    return String(c.symbol || "").toLowerCase().includes(q) || String(c.name || "").toLowerCase().includes(q) || String(c.mint || "").toLowerCase().includes(q); };
  // which coins are new since the last refresh and which have gone, in the order new ones are inserted at the top
  const diff = (shown, coins, by) => { const have = new Set(shown), now = new Set(coins.map(c => c.key));
    return {added: sortCoins(coins.filter(c => !have.has(c.key)), by), gone: shown.filter(k => !now.has(k))}; };
  // a 4-point sparkline from the coin's own 24 h, 6 h and 1 h changes and its price now (no extra data source)
  function sparkPoints(c) {
    if (c.px == null) return [];
    const at = r => r == null || r <= -100 ? null : c.px / (1 + r / 100);
    return [at(c.r24h), at(c.r6h), at(c.r1h), c.px].filter(v => v != null && isFinite(v));
  }
  function sparkPath(c, w = 52, h = 18) {
    const p = sparkPoints(c); if (p.length < 2) return "";
    const lo = Math.min(...p), hi = Math.max(...p), span = hi - lo || 1;
    return p.map((v, i) => (i ? "L" : "M") + (i * w / (p.length - 1)).toFixed(1) + " " + (h - 1 - (v - lo) / span * (h - 2)).toFixed(1)).join(" ");
  }
  const badge = c => { const n = (c.failed || []).length + (c.cant_sell || []).length, red = (c.cant_sell || []).length || c.screen === "fail";
    return {n, cls: red ? "bad" : "warn", label: n + " failed check" + (n === 1 ? "" : "s") + (red ? "" : " (strict or unknown only)")}; };

  function rowHtml(c, id) {
    const b = badge(c);
    return `<div class="wl-row" role="option" id="${esc(id)}" data-tkey="${esc(c.key)}" aria-selected="false">
      <div class="wl-a"><b class="wl-sym" data-f="sym" title="${esc(c.symbol || "?")}">${esc(c.symbol || "?")}</b><span class="wl-badge ${b.cls}" data-f="badge" title="${esc(b.label)}">${b.n}</span></div>
      <svg class="wl-spark" viewBox="0 0 52 18" aria-hidden="true"><path data-f="spark" d="${sparkPath(c)}"/></svg>
      <div class="wl-px num" data-f="px">${price(c.px)}</div>
      <div class="wl-b num"><span data-f="liq">${money(c.liq)}</span><span class="wl-strong" data-f="strong" title="last strong">${short(c.last_strong_ts)}</span></div>
      <div class="wl-chg num ${tone(c.r1h)}" data-f="r1h">${pct(c.r1h)}</div></div>`;
  }

  // short names for the summary line; the full reasons are in the expanded list
  const SHORT = {"creator history of rugged tokens": "creator rugged before", "single holder ownership": "single holder",
    "top 10 holders high ownership": "top 10 hold a lot", "large amount of lp unlocked": "LP unlocked", "low liquidity": "low liquidity",
    "mint authority still enabled": "mint authority on", "freeze authority still enabled": "freeze authority on"};
  const shortCheck = s => { s = String(s).replace(/^danger:\s*/i, "").replace(/^RugCheck risk /, "RugCheck "); return SHORT[s.toLowerCase()] || s; };
  const isDanger = s => /danger/i.test(String(s));
  function checkItems(c) {
    return (c.cant_sell || []).map(w => ({full: "Can't sell: " + w, short: "can't sell: " + w, danger: true}))
      .concat((c.failed || []).map(f => ({full: (f.check === "strict" ? "Strict: " : "Safety: ") + f.reason, short: shortCheck(f.reason), danger: isDanger(f.reason)})));
  }
  // what the swap panel shows for one coin: blocked, an invalid address, or one compact red line (click to expand).
  // The line lists danger items first, so a narrow panel's ellipsis never hides them; the expanded list keeps the order.
  function gate(c) {
    if (c.swap === "invalid_mint" || !validMint(c.mint))
      return `<div class="callout bad"><b>Swap off:</b> this coin's address is not a valid Solana address.</div>`;
    if (c.swap === "blocked")
      return `<div class="callout bad t-block" role="alert"><b>Blocked: you may not be able to sell this token</b><ul>${c.cant_sell.map(w => `<li>${esc(w)}</li>`).join("")}</ul>
        <p class="t-small">The page cannot change this. To open the swap anyway, set meme_terminal_allow_unsellable to true in the desk's settings.json by hand.</p></div>`;
    const all = checkItems(c), mark = (x, s) => x.danger ? `<b class="t-danger">${esc(s)}</b>` : `<span>${esc(s)}</span>`;
    return `<details class="t-warnline" id="t-warnline"><summary title="Show every failed check"><b>${all.length} failed check${all.length === 1 ? "" : "s"}</b>${all.filter(x => x.danger).concat(all.filter(x => !x.danger)).map(x => " · " + mark(x, x.short)).join("")}</summary>
      <ul>${all.map(x => `<li>${mark(x, x.full)}</li>`).join("")}</ul></details>`;
  }

  function manualRow(m) {
    const tx = validTxid(m.txid) ? `<a href="https://solscan.io/tx/${esc(m.txid)}" target="_blank" rel="noopener noreferrer">${esc(m.txid.slice(0, 8))}…<span class="vh">, Solscan, opens in a new window</span></a>` : "–";
    return `<tr><td class="num">${esc(String(m.at || "").slice(5, 16))}</td><td>${esc(m.symbol || "?")}</td><td>${esc(m.side)}</td>
      <td class="num">${esc(m.in_amount)}</td><td class="num">${esc(m.out_amount)}</td><td class="num">${price(m.px)}</td>
      <td>${(m.failed || []).length}</td><td>${tx}</td></tr>`;
  }
  const botRow = t => `<tr><td class="num">${esc(new Date(t.time * 1000).toISOString().slice(5, 16).replace("T", " "))}</td><td class="${t.side === "buy" ? "pos" : "neg"}">${esc(t.side)}</td><td>${esc(t.policy || "–")}</td><td>${esc(t.why || "")}</td></tr>`;

  function tokenPanel(c) {
    const kv = (k, v) => `<div class="tk-kv"><span>${k}</span><b class="num">${v}</b></div>`;
    const checks = (c.cant_sell || []).map(w => `<li class="bad">Can't sell: ${esc(w)}</li>`).join("") +
      (c.failed || []).map(f => `<li>${esc(f.check === "strict" ? "Strict" : "Safety")}: ${esc(f.reason)}</li>`).join("");
    return `<div class="tk-head"><b class="tk-sym">${esc(c.symbol || "?")}</b><span class="tk-name">${esc(c.name || "")}</span></div>
      <div class="tk-px num">${price(c.px)} <span class="${tone(c.r1h)}">${pct(c.r1h)}</span></div>
      <div class="tk-grid">${kv("1 h", `<span class="${tone(c.r1h)}">${pct(c.r1h)}</span>`)}${kv("6 h", `<span class="${tone(c.r6h)}">${pct(c.r6h)}</span>`)}${kv("24 h", `<span class="${tone(c.r24h)}">${pct(c.r24h)}</span>`)}
        ${kv("Liquidity", money(c.liq))}${kv("Volume 1 h", money(c.v1h))}${kv("Pool age", age(c.age_h))}${kv("DEX", esc(c.dex || "–"))}${kv("Last strong", esc(short(c.last_strong_ts)))}</div>
      <div class="tk-sec">Failed checks</div><ul class="tk-checks">${checks || "<li>none listed</li>"}</ul>
      <div class="tk-sec">Bots on this coin</div><p class="tk-rec">${rec(c.record || {})}</p>
      <div class="tk-links">${ext("dexscreener", (c.links || {}).dexscreener, "DexScreener")}${ext("solscan", (c.links || {}).solscan, "Solscan")}${ext("rugcheck", (c.links || {}).rugcheck, "RugCheck")}</div>`;
  }

  // the widget's success callback: what is logged, from Jupiter's swapResult (amounts in base units)
  function tradeFrom(result, mint, sol, now) {
    const r = (result && result.swapResult) || {}, a = String(r.inputAddress ?? ""), b = String(r.outputAddress ?? "");
    const txid = result && result.txid;
    if (!validTxid(txid) || !validMint(mint) || (a !== mint && b !== mint)) return null;
    const side = b === mint ? "buy" : "sell";
    return {txid, mint, side, in_mint: validMint(a) ? a : sol, out_mint: validMint(b) ? b : sol,
            in_amount: Number(r.inputAmount) || 0, out_amount: Number(r.outputAmount) || 0, time: Math.round(now / 1000)};
  }
  // price scale precision for coins worth fractions of a cent
  const precision = px => { if (!px || !isFinite(px)) return 4; return Math.min(12, Math.max(2, Math.ceil(-Math.log10(px)) + 3)); };

  // ---- live seconds candles: DexScreener's pair endpoint for the selected coin, every 2 s. Price only (no volume),
  // never GeckoTerminal (its per-IP limit is shared with the desk's scanner and P7). Every response is checked.
  const DS_PAIR = "https://api.dexscreener.com/latest/dex/pairs/solana/";
  const LIVE_IVL = {"5s": 5, "15s": 15}, LIVE_EVERY = 2000, LIVE_KEEP = 3600, LIVE_BACKOFF_MAX = 30000;
  // the validated USD price for this mint and pool from one response, or null (anything else is dropped)
  function livePrice(d, mint, pool) {
    if (!d || typeof d !== "object" || Array.isArray(d)) return null;
    const pairs = Array.isArray(d.pairs) ? d.pairs : d.pair && typeof d.pair === "object" ? [d.pair] : [];
    const p = pairs.find(x => x && typeof x === "object" && x.pairAddress === pool);
    if (!p || !p.baseToken || typeof p.baseToken !== "object" || p.baseToken.address !== mint) return null;
    if (p.chainId !== undefined && p.chainId !== "solana") return null;
    if (typeof p.priceUsd !== "string" && typeof p.priceUsd !== "number") return null;
    const v = Number(p.priceUsd);
    return Number.isFinite(v) && v > 0 && v < 1e9 ? v : null;
  }
  // candles from price samples [{t: seconds, p}], oldest first: one per sec-long bucket
  function priceCandles(samples, sec) {
    const out = [];
    for (const s of samples) {
      if (!Number.isFinite(s.t) || !Number.isFinite(s.p)) continue;
      const time = Math.floor(s.t / sec) * sec, last = out[out.length - 1];
      if (last && last.time === time) { last.high = Math.max(last.high, s.p); last.low = Math.min(last.low, s.p); last.close = s.p; }
      else if (!last || time > last.time) out.push({time, open: s.p, high: s.p, low: s.p, close: s.p});
    }
    return out;
  }
  const poolOf = c => { const u = (c.links || {}).dexscreener, pre = "https://dexscreener.com/solana/";
    return typeof u === "string" && u.startsWith(pre) && validMint(u.slice(pre.length)) ? u.slice(pre.length) : null; };

  // ---- in the browser only
  let D = null, SEL = null, ACTIVE = null, JUP = null, TIMER = 0, SORT = "newest", QUERY = "", PILL = 0, PANE = "list";
  let ROWS = new Map(), BYKEY = new Map(), SEQ = 0, IVL = "5m", CH = null, CHD = null, CHKEY = "", BOTTOM = "swaps";
  const $ = s => document.querySelector(s);
  // LIVE: the one poller. status: live (green), reconnecting (amber), stopped (gray)
  let LIVE = {key: null, mint: null, pool: null, since: 0, samples: [], timer: 0, ctrl: null, fails: 0, status: "stopped", polls: 0, dropped: 0};
  const hhmmss = s => new Date(s * 1000).toTimeString().slice(0, 8);
  function liveShow() {
    const dot = $("#t-live"), lbl = $("#t-livelbl"); if (!dot) return;
    const word = {live: "live", reconnecting: "reconnecting", stopped: "stopped"}[LIVE.status];
    dot.dataset.s = LIVE.status; dot.title = "Live prices: " + word; dot.setAttribute("aria-label", "Live prices: " + word);
    lbl.textContent = LIVE.key && LIVE.since ? `${word} · price only · live since ${hhmmss(LIVE.since)}` : word;
  }
  const liveAllowed = () => typeof document !== "undefined" && document.visibilityState === "visible" && $("#v-terminal").classList.contains("on");
  function liveStop(keepCoin) {
    clearTimeout(LIVE.timer); LIVE.timer = 0;
    if (LIVE.ctrl) { LIVE.ctrl.abort(); LIVE.ctrl = null; }
    if (!keepCoin) LIVE = Object.assign(LIVE, {key: null, mint: null, pool: null, since: 0, samples: [], fails: 0});
    LIVE.status = "stopped"; liveShow();
  }
  function liveStart(c) {                              // a new coin: the seconds candles start empty
    liveStop(false);
    const pool = poolOf(c);
    if (!c || !validMint(c.mint) || !pool) { liveShow(); return; }
    Object.assign(LIVE, {key: c.key, mint: c.mint, pool, since: Math.floor(Date.now() / 1000), samples: [], fails: 0, polls: 0, dropped: 0});
    liveResume();
  }
  function liveResume() {                              // the same coin again after the tab or page was hidden
    if (!LIVE.key || LIVE.timer || LIVE.ctrl || !liveAllowed()) { if (LIVE.key && !liveAllowed()) liveStop(true); return; }
    LIVE.status = LIVE.fails ? "reconnecting" : "live"; liveShow(); livePoll();
  }
  async function livePoll() {
    LIVE.timer = 0;
    const key = LIVE.key; if (!key) return;
    if (!liveAllowed() || key !== SEL) return liveStop(key === SEL);
    const ctrl = new AbortController(); LIVE.ctrl = ctrl; LIVE.polls++;
    let price = null;
    try {
      const r = await fetch(DS_PAIR + encodeURIComponent(LIVE.pool), {signal: ctrl.signal, cache: "no-store", credentials: "omit", referrerPolicy: "no-referrer"});
      price = r.ok ? livePrice(await r.json(), LIVE.mint, LIVE.pool) : null;
    } catch (e) { if (ctrl.signal.aborted) return; }
    if (LIVE.ctrl !== ctrl || LIVE.key !== key) return;       // stopped or another coin while this one was in flight
    LIVE.ctrl = null;
    if (price == null) { LIVE.dropped++; LIVE.fails++; LIVE.status = "reconnecting"; }
    else { LIVE.fails = 0; LIVE.status = "live"; LIVE.samples.push({t: Date.now() / 1000, p: price});
      if (LIVE.samples.length > LIVE_KEEP) LIVE.samples.splice(0, LIVE.samples.length - LIVE_KEEP);
      if (LIVE_IVL[IVL] && SEL === key) liveDraw(false); }
    liveShow();
    const wait = LIVE.fails ? Math.min(LIVE_BACKOFF_MAX, LIVE_EVERY * 2 ** LIVE.fails) : LIVE_EVERY;
    if (liveAllowed() && LIVE.key === key) LIVE.timer = setTimeout(livePoll, wait); else liveStop(true);
  }
  function liveDraw(fresh) {
    if (!makeChart()) return;
    const sec = LIVE_IVL[IVL], bars = LIVE.key === SEL ? priceCandles(LIVE.samples, sec) : [];
    const last = CHD && CHD.live && CHD.key === SEL && CHD.ivl === IVL && CHD.bars.length;
    CHD = {bars, key: SEL, ivl: IVL, live: true};
    if (fresh || !last || !bars.length) {
      const c = BYKEY.get(SEL) || {}, p = precision(bars.length ? bars.at(-1).close : c.px);
      CH.cs.applyOptions({priceFormat: {type: "price", precision: p, minMove: Math.pow(10, -p)}});
      CH.cs.setData(bars); CH.vol.setData([]); CH.cs.setMarkers([]);
      CH.lines.forEach(l => CH.cs.removePriceLine(l)); CH.lines = [];
      if (fresh) requestAnimationFrame(() => CH.chart.timeScale().fitContent());
    } else CH.cs.update(bars.at(-1));                         // only the last candle changes
    $("#t-cmsg").hidden = !!bars.length; if (!bars.length) $("#t-cmsg").textContent = LIVE.key ? "Waiting for the first live price…" : "No live prices for this coin.";
    readout(null);
  }
  if (typeof document !== "undefined") document.addEventListener("visibilitychange", () => { if (document.visibilityState === "visible") liveResume(); else liveStop(true); });
  const reduced = () => typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;
  async function load() {
    let d;
    try { d = await api("/api/meme/terminal"); } catch (e) { $("#t-status").textContent = e.message; return; }
    $("#t-status").textContent = "";
    D = d; BYKEY = new Map(d.coins.map(c => [c.key, c]));
    renderList();
    renderLog();
    liveResume();
    if (!SEL && d.coins.length) open(listOrder().find(k => BYKEY.has(k)) || d.coins[0].key, false);
    else if (SEL) detail();
    clearTimeout(TIMER); TIMER = setTimeout(() => { if ($("#v-terminal").classList.contains("on")) load(); }, 30000);
  }
  const listEl = () => $("#t-list");
  const listOrder = () => [...listEl().querySelectorAll(".wl-row")].map(r => r.dataset.tkey);
  function makeRow(c) { const t = document.createElement("template"); t.innerHTML = rowHtml(c, "wl-" + (++SEQ)).trim(); const r = t.content.firstChild; ROWS.set(c.key, r); return r; }
  function setText(el, v) { if (el && el.textContent !== v) el.textContent = v; }
  function updateRow(r, c) {                     // numbers only, in place: no node is replaced or moved
    const f = n => r.querySelector(`[data-f="${n}"]`);
    setText(f("sym"), c.symbol || "?"); if (f("sym").title !== (c.symbol || "?")) f("sym").title = c.symbol || "?"; setText(f("px"), price(c.px)); setText(f("liq"), money(c.liq)); setText(f("strong"), short(c.last_strong_ts));
    const ch = f("r1h"); setText(ch, pct(c.r1h)); const cls = "wl-chg num " + tone(c.r1h); if (ch.className.trim() !== cls.trim()) ch.className = cls;
    const b = badge(c), be = f("badge"); setText(be, String(b.n)); if (!be.classList.contains(b.cls)) be.className = "wl-badge " + b.cls; be.title = b.label;
    const d = sparkPath(c), sp = f("spark"); if (sp.getAttribute("d") !== d) sp.setAttribute("d", d);
    r.classList.remove("gone"); r.removeAttribute("aria-disabled");
  }
  function renderList() {
    const list = listEl(), coins = D.coins;
    $("#t-count").textContent = coins.length;
    if (!ROWS.size || list.dataset.built !== "1") return rebuild();
    const {added, gone} = diff(listOrder(), coins, SORT);
    for (const c of coins) { const r = ROWS.get(c.key); if (r) updateRow(r, c); }
    for (const k of gone) { const r = ROWS.get(k); if (r) { r.classList.add("gone"); r.setAttribute("aria-disabled", "true"); } }
    if (added.length) insertNew(added);
    empty();
  }
  function rebuild() {                           // first load, a sort or a search: the only times rows are re-ordered
    const list = listEl();
    const keep = new Map(ROWS); ROWS = new Map();
    const frag = document.createDocumentFragment();
    for (const c of sortCoins(D.coins, SORT)) { let r = keep.get(c.key); if (r) { ROWS.set(c.key, r); updateRow(r, c); } else r = makeRow(c); r.hidden = !matches(c, QUERY); frag.appendChild(r); }
    list.querySelectorAll(".wl-row").forEach(r => r.remove());
    list.appendChild(frag); list.dataset.built = "1";
    list.scrollTop = 0; setPill(0); mark(); empty();
  }
  function insertNew(added) {
    const list = listEl(), first = list.querySelector(".wl-row"), down = list.scrollTop > 2, frag = document.createDocumentFragment(), rows = [];
    for (const c of added) { const r = makeRow(c); r.hidden = !matches(c, QUERY); if (!down && !r.hidden) r.classList.add("wl-new"); rows.push(r); frag.appendChild(r); }
    // keep what you are looking at exactly where it is: anchor on the first row in view, measured before and after
    const lt = list.getBoundingClientRect().top;
    const anchor = down ? [...list.querySelectorAll(".wl-row")].find(r => !r.hidden && r.getBoundingClientRect().bottom > lt + 1) : null;
    const was = anchor ? anchor.getBoundingClientRect().top : 0;
    list.insertBefore(frag, first || null);
    rows.forEach(r => r.addEventListener("animationend", () => r.classList.remove("wl-new"), {once: true}));
    if (down) { if (anchor) list.scrollTop += anchor.getBoundingClientRect().top - was; setPill(PILL + rows.filter(r => !r.hidden).length); }
  }
  function setPill(n) { PILL = n; const p = $("#t-pill"); p.hidden = !n; p.textContent = n + " new ↑"; }
  function empty() {
    const vis = [...listEl().querySelectorAll(".wl-row")].some(r => !r.hidden);
    const e = $("#t-empty"); e.hidden = vis;
    e.innerHTML = D && D.coins.length ? "<b>No match</b>No coin's symbol or address contains that." : "<b>No coins</b>Every watched Solana coin passed both checks, or none is tracked yet.";
  }
  function mark() { ROWS.forEach((r, k) => { const on = k === SEL; r.classList.toggle("sel", on); r.setAttribute("aria-selected", on); r.classList.toggle("act", k === ACTIVE); });
    const a = ROWS.get(ACTIVE); if (a) listEl().setAttribute("aria-activedescendant", a.id); }
  function open(key, toChart = true) {
    SEL = key; ACTIVE = key; mark(); detail();
    if (LIVE.key === key) liveResume(); else liveStart(BYKEY.get(key));   // the coin already open keeps its live prices
    loadChart(true);
    if (toChart && matchMedia("(max-width: 899px)").matches) pane("chart");
  }
  function detail() {
    const c = BYKEY.get(SEL);
    if (!c) {                                     // left the list: it passed its checks or is no longer tracked
      $("#t-token").innerHTML = SEL ? '<div class="empty"><b>Coin left the list</b>It passed its checks or is no longer watched. Swap is off.</div>' : '<div class="empty"><b>Pick a coin</b></div>';
      $("#t-gate").innerHTML = ""; closeJup(); return;
    }
    $("#t-token").innerHTML = tokenPanel(c);
    const g = $("#t-gate"), html = gate(c), open = g.querySelector("details[open]") && g.dataset.key === c.key;
    if (g.dataset.html !== html) { g.innerHTML = html; g.dataset.html = html; if (open) g.querySelector("details").open = true; }   // a refresh keeps it expanded
    g.dataset.key = c.key;
    if (c.swap === "confirm" && validMint(c.mint)) openJup(c); else closeJup();   // not blocked: the widget loads at once
  }
  function renderLog() {
    $("#t-log").innerHTML = D.manual.length ? `<table class="tx-table"><tr><th>When (UTC)</th><th>Coin</th><th>Side</th><th>In</th><th>Out</th><th>Price then</th><th>Failed</th><th>Tx</th></tr>${D.manual.map(manualRow).join("")}</table>`
      : '<div class="empty"><b>No swaps logged</b>Swaps you make in the widget are listed here.</div>';
  }
  function renderBots(d) {
    const c = BYKEY.get(SEL) || {}, rows = ((d && d.trades) || []).slice().sort((a, b) => b.time - a.time);
    $("#t-bots").innerHTML = `<p class="tk-rec">${rec(c.record || {})}</p>` + (rows.length ? `<table class="tx-table"><tr><th>When (UTC)</th><th>Side</th><th>Rule</th><th>Why</th></tr>${rows.map(botRow).join("")}</table>`
      : '<div class="empty">No main-account practice trades on this coin.</div>');
  }
  // ---- chart: Lightweight Charts 4.2.3 from /vendor (no CDN), data from /api/meme/chart
  const COL = {up: "#26A69A", down: "#EF5350", grid: "rgba(255,255,255,.05)", text: "#A3A8B5", entry: "#5B9CF6", stop: "#EF5350", target: "#26A69A"};
  function makeChart() {
    if (CH || !window.LightweightCharts) return !!CH;
    const L = LightweightCharts, el = $("#t-chart"); el.innerHTML = "";
    const chart = L.createChart(el, {autoSize: true, layout: {background: {color: "transparent"}, textColor: COL.text, fontFamily: "Geist, system-ui, sans-serif", fontSize: 11},
      grid: {vertLines: {color: COL.grid}, horzLines: {color: COL.grid}}, rightPriceScale: {borderVisible: false},
      timeScale: {borderVisible: false, timeVisible: true, secondsVisible: false, rightOffset: 4}, crosshair: {mode: L.CrosshairMode.Normal},
      // the plain wheel scrolls the page (with both wheel options off the library adds no wheel listener); Ctrl+wheel
      // zooms (below); drag pans; pinch zooms on touch
      handleScroll: {mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false},
      handleScale: {mouseWheel: false, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true}});
    const cs = chart.addCandlestickSeries({upColor: COL.up, downColor: COL.down, borderVisible: false, wickUpColor: COL.up, wickDownColor: COL.down});
    cs.priceScale().applyOptions({scaleMargins: {top: .08, bottom: .24}});
    const vol = chart.addHistogramSeries({priceFormat: {type: "volume"}, priceScaleId: "vol", lastValueVisible: false, priceLineVisible: false});
    chart.priceScale("vol").applyOptions({scaleMargins: {top: .8, bottom: 0}});
    chart.subscribeCrosshairMove(p => readout(p && p.time != null ? p.seriesData.get(cs) : null, p && p.time != null ? p.seriesData.get(vol) : null));
    // Ctrl+wheel (and a trackpad pinch, which the browser sends as Ctrl+wheel) zooms around the pointer
    el.addEventListener("wheel", e => {
      if (!e.ctrlKey) return;
      e.preventDefault();                             // not the browser's page zoom
      const ts = chart.timeScale(), r = ts.getVisibleLogicalRange(); if (!r) return;
      const dy = e.deltaY * (e.deltaMode === 1 ? 32 : e.deltaMode === 2 ? 400 : 1);
      const f = Math.exp(Math.max(-1, Math.min(1, dy / 500))), at = ts.coordinateToLogical(e.clientX - el.getBoundingClientRect().left) ?? (r.from + r.to) / 2;
      const from = at - (at - r.from) * f, to = at + (r.to - at) * f;
      if (to - from >= 4) ts.setVisibleLogicalRange({from, to});
    }, {passive: false});
    CH = {chart, cs, vol, lines: []};
    return true;
  }
  function readout(bar, v) {
    if (!bar && CHD && CHD.bars.length) { const b = CHD.bars.at(-1); bar = b; v = {value: b.volume}; }
    const o = $("#t-ohlc"); if (!bar) { o.innerHTML = ""; return; }
    const up = bar.close >= bar.open, f = x => `<b class="${up ? "pos" : "neg"}">${price(x)}</b>`;
    o.innerHTML = `<span>O ${f(bar.open)}</span><span>H ${f(bar.high)}</span><span>L ${f(bar.low)}</span><span>C ${f(bar.close)}</span><span>V <b>${money(v && v.value)}</b></span>`;
  }
  async function loadChart(fresh) {
    const key = SEL, ivl = IVL; if (!key) return;
    const c = BYKEY.get(key) || {};
    setText($("#t-csym"), c.symbol || "?");
    if (!makeChart()) { $("#t-chart").textContent = "The chart library did not load."; return; }
    CH.chart.applyOptions({timeScale: {secondsVisible: !!LIVE_IVL[ivl]}});
    if (LIVE_IVL[ivl]) { renderBots(null); return liveDraw(true); }     // 5s and 15s: from the live prices, no history
    if (fresh) { $("#t-cmsg").hidden = false; $("#t-cmsg").textContent = "Loading candles…"; }
    let d;
    try { d = await api(`/api/meme/chart?key=${encodeURIComponent(key)}&ivl=${ivl}`); }
    catch (e) { if (key === SEL && ivl === IVL) { $("#t-cmsg").hidden = false; $("#t-cmsg").textContent = e.message; CH.cs.setData([]); CH.vol.setData([]); } return; }
    if (key !== SEL || ivl !== IVL) return;
    const bars = (d.bars || []).filter(b => [b.time, b.open, b.high, b.low, b.close].every(Number.isFinite)).sort((a, b) => a.time - b.time)
      .filter((b, i, a) => !i || b.time !== a[i - 1].time);
    CHD = {bars, key, ivl}; CHKEY = key + ivl;
    const p = precision(bars.length ? bars.at(-1).close : c.px);
    CH.cs.applyOptions({priceFormat: {type: "price", precision: p, minMove: Math.pow(10, -p)}});
    CH.cs.setData(bars.map(b => ({time: b.time, open: b.open, high: b.high, low: b.low, close: b.close})));
    CH.vol.setData(bars.map(b => ({time: b.time, value: Number.isFinite(b.volume) ? b.volume : 0, color: b.close >= b.open ? "rgba(38,166,154,.35)" : "rgba(239,83,80,.35)"})));
    // buy and sell marks snap to the candle they fall in; level lines for an open practice position
    const snap = t => { let s = null; for (const b of bars) { if (b.time <= t) s = b.time; else break; } return s; };
    CH.cs.setMarkers((d.trades || []).map(t => ({t, time: snap(t.time)})).filter(x => x.time != null).sort((a, b) => a.time - b.time).map(({t, time}) =>
      t.side === "buy" ? {time, position: "belowBar", color: COL.entry, shape: "arrowUp", text: "B"} : {time, position: "aboveBar", color: "#FFB340", shape: "arrowDown", text: "S"}));
    CH.lines.forEach(l => CH.cs.removePriceLine(l));
    CH.lines = (d.levels || []).map(l => CH.cs.createPriceLine({price: l.price, title: l.title, color: COL[l.kind] || COL.text, lineWidth: 1, lineStyle: 2, axisLabelVisible: true}));
    $("#t-cmsg").hidden = !!bars.length; if (!bars.length) $("#t-cmsg").textContent = "No candles for this pool yet.";
    if (fresh) requestAnimationFrame(() => CH.chart.timeScale().fitContent());
    readout(null); renderBots(d);
  }
  function pane(p) { PANE = p; $("#tx").dataset.pane = p; document.querySelectorAll("#tx-bar [data-tpane]").forEach(b => b.setAttribute("aria-selected", b.dataset.tpane === p));
    // a chart filled while its pane was hidden had no width to fit into: fit it once it can be seen
    if (p === "chart" && CH) requestAnimationFrame(() => requestAnimationFrame(() => CH.chart.timeScale().fitContent())); }
  function loadJup() {
    if (window.Jupiter) return Promise.resolve();
    return JUP || (JUP = new Promise((ok, bad) => { const s = document.createElement("script"); s.src = JUP_SRC; s.async = true;
      s.onload = () => ok(); s.onerror = () => { JUP = null; bad(new Error("Jupiter's widget did not load.")); }; document.head.appendChild(s); }));
  }
  let OPEN = null;
  async function openJup(c) {
    if (OPEN === c.key) return;
    if (OPEN) closeJup();
    OPEN = c.key; $("#t-swap").hidden = false;
    try { await loadJup(); } catch (e) { $("#jup-target").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
    if (OPEN !== c.key || !validMint(c.mint) || !validMint(D.sol_mint)) return;
    const mint = c.mint, sol = D.sol_mint;
    window.Jupiter.init({
      displayMode: "integrated", integratedTargetId: "jup-target", containerStyles: {height: "560px"}, defaultExplorer: "Solscan",
      formProps: {initialInputMint: sol, initialOutputMint: mint, fixedMint: mint},
      onSuccess: async result => { const row = tradeFrom(result, mint, sol, Date.now());
        if (!row) return toast("The swap went through, but the desk could not read it to log it. Check your wallet.", true);
        try { await api("/api/meme/manual", row); toast("Swap logged."); load(); } catch (e) { toast("Not logged: " + e.message, true); } },
    });
  }
  function closeJup() { if (!OPEN) return; OPEN = null; $("#t-swap").hidden = true; try { window.Jupiter && window.Jupiter.close && window.Jupiter.close(); } catch (e) {} $("#jup-target").innerHTML = ""; }
  function move(step) {
    const vis = [...listEl().querySelectorAll(".wl-row")].filter(r => !r.hidden); if (!vis.length) return;
    let i = vis.findIndex(r => r.dataset.tkey === ACTIVE);
    i = step === "home" ? 0 : step === "end" ? vis.length - 1 : i < 0 ? 0 : Math.max(0, Math.min(vis.length - 1, i + step));
    ACTIVE = vis[i].dataset.tkey; mark(); vis[i].scrollIntoView({block: "nearest"});
  }
  if (typeof document !== "undefined") {
    document.addEventListener("click", e => {
      const t = e.target;
      const r = t.closest && t.closest("#t-list .wl-row"); if (r) { listEl().focus({preventScroll: true}); return open(r.dataset.tkey); }
      const b = t.closest && t.closest("button"); if (!b) return;
      if (b.id === "t-pill") { listEl().scrollTo({top: 0, behavior: reduced() ? "auto" : "smooth"}); setPill(0); }
      else if (b.dataset.tivl) { IVL = b.dataset.tivl; document.querySelectorAll("[data-tivl]").forEach(x => x.setAttribute("aria-pressed", x === b)); loadChart(true); }
      else if (b.id === "t-fit") { if (CH) CH.chart.timeScale().fitContent(); }
      else if (b.id === "t-jump") {                   // 900 to 1199 px: the swap card is below the chart
        $(".tx-swap").scrollIntoView({behavior: reduced() ? "auto" : "smooth", block: "start"});
        const f = $("#t-warnline summary") || $("#t-gate .callout"); if (f) { if (!f.matches("summary")) f.tabIndex = -1; f.focus({preventScroll: true}); } }
      else if (b.dataset.tpane) pane(b.dataset.tpane);
      else if (b.dataset.tbottom) { BOTTOM = b.dataset.tbottom; document.querySelectorAll("[data-tbottom]").forEach(x => x.setAttribute("aria-selected", x === b));
        $("#t-log").hidden = BOTTOM !== "swaps"; $("#t-bots").hidden = BOTTOM !== "bots"; }
    });
    document.addEventListener("keydown", e => {
      if (e.target.id !== "t-list") return;
      const k = {ArrowDown: 1, ArrowUp: -1, Home: "home", End: "end"}[e.key];
      if (k != null) { e.preventDefault(); move(k); }
      else if ((e.key === "Enter" || e.key === " ") && ACTIVE) { e.preventDefault(); open(ACTIVE); }
    });
    document.addEventListener("change", e => {
      if (e.target.id === "t-sort" && D) { SORT = e.target.value; rebuild(); }
    });
    document.addEventListener("input", e => { if (e.target.id === "t-find" && D) { QUERY = e.target.value; rebuild(); } });
    document.addEventListener("scroll", e => { if (e.target.id === "t-list" && e.target.scrollTop <= 2 && PILL) setPill(0); }, true);
    document.addEventListener("focusin", e => { if (e.target.id === "t-list" && !ACTIVE) move(0); });
    // on phones the desk's own tab bar sits at the bottom; the List / Chart / Swap bar sits just above it
    const nav = document.querySelector(".tabs"), navh = () => document.documentElement.style.setProperty("--navh",
      (matchMedia("(max-width: 899px)").matches && nav ? nav.getBoundingClientRect().height : 0) + "px");
    if (nav && typeof ResizeObserver === "function") new ResizeObserver(navh).observe(nav);
    addEventListener("resize", navh);
    // the real-money banner wraps on narrow windows: the terminal's height is the window less its real height
    const banner = document.querySelector(".real-banner"), bannerh = () => document.documentElement.style.setProperty("--bannerh",
      (banner ? Math.ceil(banner.getBoundingClientRect().height) : 0) + "px");
    if (banner && typeof ResizeObserver === "function") new ResizeObserver(bannerh).observe(banner);
    bannerh();
  }
  // read-only state for the browser tests: what the chart shows, never the data itself
  const inspect = () => ({sel: SEL, active: ACTIVE, ivl: IVL, pill: PILL, pane: PANE, bars: CHD ? CHD.bars.length : 0,
    markers: CH ? CH.cs.markers().length : 0, lines: CH ? CH.lines.length : 0, range: CH ? CH.chart.timeScale().getVisibleLogicalRange() : null,
    live: {status: LIVE.status, key: LIVE.key, pool: LIVE.pool, since: LIVE.since, samples: LIVE.samples.length, polls: LIVE.polls, dropped: LIVE.dropped, polling: !!(LIVE.timer || LIVE.ctrl)}});
  return {load, inspect, price, livePrice, priceCandles, checkItems, shortCheck, esc, validMint, validTxid, gate, manualRow, tradeFrom, ago, rowHtml, tokenPanel, botRow, sortCoins, matches, diff, sparkPath, sparkPoints, badge, precision};
})();
if (typeof module !== "undefined") module.exports = TERMINAL;
