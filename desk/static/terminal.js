"use strict";
// ---- Terminal tab: watched Solana coins that did NOT fully pass the safety check, and Jupiter's swap widget.
// The desk never holds a key and never sends a transaction: Jupiter's Plugin builds each swap in this page and you
// sign it in your own wallet. Token names and symbols are chosen by whoever made the coin, so every one is escaped.
// One top-level name (TERMINAL) so nothing here can clash with app.js. Pure helpers are exported for Node tests.
const TERMINAL = (() => {
  const JUP_SRC = "https://plugin.jup.ag/plugin-v1.js";   // Jupiter's documented script (major version 1)
  const MINT = /^[1-9A-HJ-NP-Za-km-z]{32,44}$/, TXID = /^[1-9A-HJ-NP-Za-km-z]{64,90}$/;
  const esc = s => String(s ?? "").replace(/[&<>"'`]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;"}[c]));
  const validMint = s => typeof s === "string" && MINT.test(s);
  const validTxid = s => typeof s === "string" && TXID.test(s);
  const pct = (x, d = 1) => x == null || isNaN(x) ? "–" : (x > 0 ? "+" : x < 0 ? "−" : "") + Math.abs(x).toFixed(d) + "%";
  const tone = x => x == null ? "" : x > 0 ? "pos" : x < 0 ? "neg" : "";
  const money = n => n == null ? "–" : n >= 1e6 ? "$" + (n / 1e6).toFixed(2) + "M" : n >= 1e3 ? "$" + (n / 1e3).toFixed(1) + "K" : "$" + Math.round(n);
  const price = n => n == null ? "–" : n >= 1 ? Number(n).toFixed(4) : Number(n).toPrecision(4);
  const age = h => h == null ? "–" : h < 48 ? h.toFixed(1) + " h" : (h / 24).toFixed(1) + " d";
  const ago = (ts, now) => { if (!ts) return "never"; const m = Math.max(0, Math.round((now - ts) / 60));
    return m < 60 ? m + " min ago" : m < 48 * 60 ? Math.round(m / 60) + " h ago" : Math.round(m / 1440) + " d ago"; };
  // only links the desk itself built from a validated address, and only to these three sites
  const LINK = {dexscreener: "https://dexscreener.com/solana/", solscan: "https://solscan.io/token/", rugcheck: "https://rugcheck.xyz/tokens/"};
  const safeLink = (kind, url) => typeof url === "string" && url.startsWith(LINK[kind]) && validMint(url.slice(LINK[kind].length)) ? url : null;
  const ext = (kind, url, label) => { const u = safeLink(kind, url);
    return u ? `<a class="btn small gray" href="${esc(u)}" target="_blank" rel="noopener noreferrer">${esc(label)}<span class="vh">, opens in a new window</span>&nbsp;↗</a>` : ""; };
  const rec = r => { const p = (n, x) => n ? `${n} · <span class="${tone(x)}">${pct(x)}</span>` : "none";
    return `Shadows ${p(r.shadows, r.shadows_net_pct)} · Practice ${p(r.practice, r.practice_net_pct)}`; };

  function coinRow(c, selected) {
    const checks = (c.failed || []).map(f => `<li>${esc(f.check === "strict" ? "Strict: " : "Safety: ")}${esc(f.reason)}</li>`).join("");
    return `<div class="t-row${selected ? " on" : ""}" data-tkey="${esc(c.key)}" role="button" tabindex="0" aria-pressed="${!!selected}">
      <div class="t-top"><b class="t-sym">${esc(c.symbol || "?")}</b><span class="t-name">${esc(c.name || "")}</span>
        ${c.swap === "blocked" ? '<span class="pill t-bad">Can\'t-sell risk</span>' : '<span class="pill">Failed checks</span>'}</div>
      <div class="t-nums num"><span>${price(c.px)}</span><span class="${tone(c.r1h)}">1h ${pct(c.r1h)}</span><span class="${tone(c.r6h)}">6h ${pct(c.r6h)}</span><span class="${tone(c.r24h)}">24h ${pct(c.r24h)}</span>
        <span>Pool ${money(c.liq)}</span><span>Vol 1h ${money(c.v1h)}</span><span>Age ${age(c.age_h)}</span><span>${esc(c.dex || "–")}</span></div>
      <ul class="t-checks">${checks}</ul>
      <div class="t-rec foot-note">Bots on this coin: ${rec(c.record || {})}</div></div>`;
  }

  // what the swap panel shows for one coin: blocked, an invalid address, or the warning box and checkbox
  function gate(c, confirmed) {
    if (c.swap === "invalid_mint" || !validMint(c.mint))
      return `<div class="callout bad"><b>Swap off:</b> this coin's address is not a valid Solana address.</div>`;
    if (c.swap === "blocked")
      return `<div class="callout bad t-block" role="alert"><b>Blocked: you may not be able to sell this token</b><ul>${c.cant_sell.map(w => `<li>${esc(w)}</li>`).join("")}</ul>
        <p class="t-small">To open the swap anyway, turn on "Open the swap for can't-sell coins" in the Memecoins tab's settings.</p></div>`;
    const all = (c.cant_sell || []).map(w => "Can't sell: " + w).concat((c.failed || []).map(f => (f.check === "strict" ? "Strict: " : "Safety: ") + f.reason));
    return `<div class="callout bad t-warn" role="alert"><b>This coin failed ${all.length} check${all.length === 1 ? "" : "s"}</b><ul>${all.map(w => `<li>${esc(w)}</li>`).join("")}</ul>
      <label class="t-ack"><input type="checkbox" id="t-ack"${confirmed ? " checked" : ""}> I understand this coin failed these checks</label></div>`;
  }

  function tickerItem(x, now) {
    return `<li class="t-tick"><b>${esc(x.symbol || "?")}</b><span class="num">${price(x.px)}</span><span class="num ${tone(x.r1h)}">${pct(x.r1h)}</span>
      <small>last strong: ${esc(ago(x.last_strong_ts, now))}</small></li>`;
  }

  function manualRow(m) {
    const tx = validTxid(m.txid) ? `<a href="https://solscan.io/tx/${esc(m.txid)}" target="_blank" rel="noopener noreferrer">${esc(m.txid.slice(0, 8))}…<span class="vh">, Solscan, opens in a new window</span></a>` : "–";
    return `<tr><td class="num">${esc(String(m.at || "").slice(5, 16))}</td><td>${esc(m.symbol || "?")}</td><td>${esc(m.side)}</td>
      <td class="num">${esc(m.in_amount)}</td><td class="num">${esc(m.out_amount)}</td><td class="num">${price(m.px)}</td>
      <td>${(m.failed || []).length}</td><td>${tx}</td></tr>`;
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

  // ---- in the browser only
  let D = null, SEL = null, ACK = {}, JUP = null, TIMER = 0;
  const $ = s => document.querySelector(s);
  async function load() {
    try { D = await api("/api/meme/terminal"); } catch (e) { $("#t-list").innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; return; }
    if (!SEL || !D.coins.some(c => c.key === SEL)) SEL = (D.coins[0] || {}).key || null;
    render();
    clearTimeout(TIMER); TIMER = setTimeout(() => { if ($("#v-terminal").classList.contains("on")) load(); }, 30000);
  }
  function render() {
    const now = Date.now() / 1000;
    $("#t-meta").textContent = `${D.coins.length} coins failed a check`;
    $("#t-list").innerHTML = D.coins.length ? D.coins.map(c => coinRow(c, c.key === SEL)).join("") : '<div class="empty"><b>No coins</b>Every watched Solana coin passed both checks, or none is tracked yet.</div>';
    const items = D.ticker.map(x => tickerItem(x, now)).join("");
    $("#t-ticker").innerHTML = items ? `<ul class="t-roll">${items}</ul><ul class="t-roll" aria-hidden="true">${items}</ul>` : '<div class="empty">Nothing watched yet</div>';
    $("#t-log").innerHTML = D.manual.length ? `<table><tr><th>When (UTC)</th><th>Coin</th><th>Side</th><th>In</th><th>Out</th><th>Price then</th><th>Failed checks</th><th>Tx</th></tr>${D.manual.map(manualRow).join("")}</table>`
      : '<div class="empty"><b>No swaps logged</b>Swaps you make in the widget below are listed here.</div>';
    detail(false);
  }
  async function detail(reloadChart) {
    const c = D && D.coins.find(x => x.key === SEL);
    if (!c) { $("#t-detail").hidden = true; closeJup(); return; }
    $("#t-detail").hidden = false;
    $("#t-dsym").textContent = (c.symbol || "?") + (c.name ? " · " + c.name : "");
    $("#t-links").innerHTML = ext("dexscreener", c.links.dexscreener, "DexScreener") + ext("solscan", c.links.solscan, "Solscan") + ext("rugcheck", c.links.rugcheck, "RugCheck");
    $("#t-gate").innerHTML = gate(c, !!ACK[c.key]);
    if (c.swap === "confirm" && ACK[c.key] && validMint(c.mint)) openJup(c); else closeJup();
    if (reloadChart !== false || !$("#t-chart").dataset.key || $("#t-chart").dataset.key !== c.key) chart(c);
  }
  async function chart(c) {
    const box = $("#t-chart"); box.dataset.key = c.key; box.innerHTML = '<div class="empty"><b>Loading candles…</b></div>';
    try { const d = await api(`/api/meme/chart?key=${encodeURIComponent(c.key)}&ivl=5m`); if (box.dataset.key !== c.key) return;
      box.innerHTML = candleSvg(d, box.clientWidth || 600); }
    catch (e) { if (box.dataset.key === c.key) box.innerHTML = `<div class="callout bad">${esc(e.message)}</div>`; }
  }
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
  if (typeof document !== "undefined") {
    document.addEventListener("click", e => { const r = e.target.closest && e.target.closest("[data-tkey]"); if (!r) return; SEL = r.dataset.tkey; render(); });
    document.addEventListener("keydown", e => { const r = e.target.closest && e.target.closest("[data-tkey]"); if (r && e.target === r && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); r.click(); } });
    document.addEventListener("change", e => { if (e.target.id !== "t-ack" || !SEL) return; ACK[SEL] = e.target.checked; detail(false); });
  }
  return {load, esc, validMint, validTxid, coinRow, gate, tickerItem, manualRow, tradeFrom, ago};
})();
if (typeof module !== "undefined") module.exports = TERMINAL;
