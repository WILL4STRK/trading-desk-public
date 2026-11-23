// Browser checks for the Terminal tab (desk/static/terminal.js), run by hand against a demo desk:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal.e2e.js [http://127.0.0.1:8799]
// Uses the installed Chrome (channel "chrome"). The coin list and candles are served from the page's own requests,
// edited in the browser (page.route), so no data file is changed; Jupiter's script is replaced by a stub, so nothing
// is sent to a third party. Every check runs at 1440x900 and at 390x844 (deviceScaleFactor 2), console capture first.
const { chromium } = require("playwright-core");
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const EVIL = "<img src=x onerror=alert(1)>";
const SOL = "So11111111111111111111111111111111111111112";
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
// unique per i: i written in base 58 (3 digits), then filler, 44 characters in all
const mintFor = i => { let d = ""; for (let n = i, k = 0; k < 3; k++, n = Math.floor(n / 58)) d = B58[n % 58] + d; let s = d; for (let k = 0; s.length < 44; k++) s += B58[(k * 13 + 7) % 58]; return s; };
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok, detail}); console.log((ok ? "PASS " : "FAIL ") + name + (detail ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));

function chartFixture(ivl) {
  const step = {"5m": 300, "15m": 900, "1h": 3600}[ivl] || 300, t0 = 1795000000 - 80 * step, bars = [];
  let p = 0.0001;
  for (let i = 0; i < 80; i++) { const o = p, c = p * (1 + Math.sin(i / 5) * 0.04); bars.push({time: t0 + i * step, open: o, high: Math.max(o, c) * 1.02, low: Math.min(o, c) * 0.98, close: c, volume: 1000 + i * 37}); p = c; }
  return {bars, levels: [{kind: "entry", title: "Bought", price: bars[40].close}, {kind: "stop", title: "Stop", price: bars[40].close * 0.8}, {kind: "target", title: "Target", price: bars[40].close * 1.4}],
          trades: [{time: bars[40].time + 10, side: "buy", policy: "careful"}, {time: bars[60].time + 10, side: "sell", why: "target"}], symbol: "X", url: "https://dexscreener.com/solana/x"};
}
const coin = (i, extra) => Object.assign({key: "solana:" + mintFor(i), symbol: "T" + i, name: "Test " + i, mint: mintFor(i), px: 0.001 * (1 + i % 7), r1h: (i % 13) - 6, r6h: 2, r24h: 5,
  liq: 1000 * (i % 17 + 1), v1h: 500 * (i % 11 + 1), age_h: i, dex: "raydium", screen: "fail", strict: "fail", failed: [{check: "safety", verdict: "fail", reason: "RugCheck risk 80/100"}],
  cant_sell: [], swap: "confirm", last_strong_ts: i % 3 ? null : 1795000000 - i * 60, seen: 1795000000, record: {shadows: 0, practice: 0},
  links: {dexscreener: "https://dexscreener.com/solana/" + mintFor(i + 1000), solscan: "https://solscan.io/token/" + mintFor(i), rugcheck: "https://rugcheck.xyz/tokens/" + mintFor(i)}}, extra || {});

async function session(browser, vp, opts = {}) {
  const ctx = await browser.newContext({viewport: {width: vp[0], height: vp[1]}, deviceScaleFactor: 2, reducedMotion: opts.reduced ? "reduce" : "no-preference"});
  const page = await ctx.newPage();
  const errors = [], bad = [], dialogs = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });        // registered before the page loads
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("response", r => { if (r.status() >= 400) bad.push(r.status() + " " + new URL(r.url()).pathname); });
  page.on("dialog", d => { dialogs.push(d.message()); d.dismiss(); });
  const S = {payload: null, chartCalls: []};
  await page.route("**/api/meme/terminal", async route => {
    if (!S.payload) { const r = await route.fetch(); S.real = await r.json(); S.payload = JSON.parse(JSON.stringify(S.real)); }
    route.fulfill({json: S.payload});
  });
  await page.route("**/api/meme/chart?*", route => { const u = new URL(route.request().url()); S.chartCalls.push([u.searchParams.get("key"), u.searchParams.get("ivl")]);
    route.fulfill({json: chartFixture(u.searchParams.get("ivl"))}); });
  await page.route("https://api.mainnet-beta.solana.com/", route => { const b = JSON.parse(route.request().postData()); route.fulfill({json: {jsonrpc: "2.0", id: b.id, result: {context: {slot: 1}, value: b.params[0].map(() => null)}}}); });   // on-chain: unreadable, so the DexScreener stand-in
  await page.route("https://lite-api.jup.ag/**", route => route.fulfill({json: {So11111111111111111111111111111111111111112: {usdPrice: 72.8}}}));
  await page.route("https://api.dexscreener.com/**", route => route.fulfill({json: {pairs: []}}));     // live prices: a stand-in, no network
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript",
    body: "window.Jupiter={init:function(o){window.__jupInit=JSON.parse(JSON.stringify(o));document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){window.__jupClosed=(window.__jupClosed||0)+1}};"}));
  await page.goto(BASE + "/");
  await page.waitForSelector(".tabs button");
  return {ctx, page, errors, bad, dialogs, S};
}
const openTerminal = async page => { await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(300); };
const reload = async (page, S, payload) => { S.payload = payload; await page.evaluate(() => TERMINAL.load()); await sleep(150); };
const phone = vp => vp[0] < 900;
const toList = async (page, vp) => { if (phone(vp)) await page.click('#tx-bar [data-tpane="list"]'); };

async function run(browser, vp) {
  const tag = vp[0] + "x" + vp[1] + " ";
  // ---- the real data (a copy of the live memecoin folder), every tab, console clean
  let s = await session(browser, vp);
  for (const v of ["desk", "account", "journal", "research", "graph", "market", "meme", "settings", "terminal"]) { await s.page.click(`[data-v="${v}"]`); await sleep(1200); }
  const realRows = await s.page.$$eval("#t-list .wl-row", r => r.length);
  check(tag + "real data: the watchlist lists every coin", realRows === s.S.real.coins.length && realRows > 0, {rows: realRows});
  const otherBad = s.bad.filter(x => !x.endsWith("/favicon.ico"));
  check(tag + "all tabs load: no console errors besides the favicon 404", s.errors.filter(e => !/Failed to load resource.*404/.test(e)).length === 0 && otherBad.length === 0, {errors: s.errors, bad: s.bad});
  await s.page.unrouteAll({behavior: "ignoreErrors"}); await s.ctx.close();

  // ---- a fixed set of 60 coins from here on
  const base = {coins: Array.from({length: 60}, (_, i) => coin(i)), ticker: [], manual: [], allow_unsellable: false, sol_mint: SOL, strong: {}, now: 1795000000};
  s = await session(browser, vp); const {page, S} = s;
  S.payload = base; await openTerminal(page);

  // click a row: selected, highlighted, chart loaded for it
  await toList(page, vp);
  const third = await page.$$("#t-list .wl-row");
  const key3 = await third[3].getAttribute("data-tkey");
  S.chartCalls = []; await third[3].click(); await sleep(400);
  let st = await page.evaluate(() => TERMINAL.inspect());
  check(tag + "click selects the coin and loads its chart", st.sel === key3 && S.chartCalls.some(c => c[0] === key3) && st.bars === 80 &&
        await page.$eval(`#t-list .wl-row[data-tkey="${key3}"]`, r => r.classList.contains("sel") && r.getAttribute("aria-selected") === "true"), {st, calls: S.chartCalls});
  if (phone(vp)) check(tag + "phone: a tap opens the Chart pane", await page.$eval("#tx", t => t.dataset.pane) === "chart");
  // keyboard: down, down, up, Enter
  await toList(page, vp);
  await page.focus("#t-list"); await page.keyboard.press("Home"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowDown"); await page.keyboard.press("ArrowUp");
  const actKey = await page.evaluate(() => TERMINAL.inspect().active);
  const row1 = await page.$$eval("#t-list .wl-row", r => r[1].dataset.tkey);
  S.chartCalls = []; await page.keyboard.press("Enter"); await sleep(300);
  st = await page.evaluate(() => TERMINAL.inspect());
  check(tag + "keyboard: up/down move, Enter opens", actKey === row1 && st.sel === row1 && S.chartCalls.some(c => c[0] === row1), {actKey, row1, sel: st.sel});
  await toList(page, vp);

  // 10 refreshes, same coins, new numbers: scrollTop, every row's position and node unchanged, no DOM node added or removed
  await page.$eval("#t-list", l => { l.scrollTop = 600; });
  const snap = () => page.evaluate(() => { const l = document.querySelector("#t-list"); return {top: l.scrollTop, rows: [...l.querySelectorAll(".wl-row")].map(r => [r.dataset.tkey, r.offsetTop, r.id])}; });
  const before = await snap();
  await page.evaluate(() => { window.__mut = 0; window.__mo = new MutationObserver(m => { for (const x of m) window.__mut += x.addedNodes.length + x.removedNodes.length; });
    window.__mo.observe(document.querySelector("#t-list"), {childList: true}); });
  for (let k = 0; k < 10; k++) { const p = JSON.parse(JSON.stringify(base)); p.coins.forEach(c => { c.px *= 1 + (k + 1) / 100; c.r1h += k; }); await reload(page, S, p); }
  const after = await snap(), muts = await page.evaluate(() => window.__mut);
  const priceNow = await page.$eval("#t-list .wl-row [data-f=px]", e => e.textContent);
  check(tag + "10 refreshes with the same coins: scrollTop and every row unchanged", before.top === 600 && after.top === before.top && JSON.stringify(after.rows) === JSON.stringify(before.rows) && muts === 0,
        {scrollTop: [before.top, after.top], rowsSame: JSON.stringify(after.rows) === JSON.stringify(before.rows), domAddsRemoves: muts, firstPrice: priceNow});
  check(tag + "…and the numbers did update in place", priceNow !== "–" && priceNow !== (await page.evaluate(() => null)), {firstPrice: priceNow});

  // a new coin while scrolled down: position kept, pill shown, pill scrolls to the top
  const visTop = () => page.evaluate(() => { const l = document.querySelector("#t-list"), lt = l.getBoundingClientRect().top;
    const r = [...l.querySelectorAll(".wl-row")].find(r => r.getBoundingClientRect().bottom > lt + 1); return [r.dataset.tkey, Math.round(r.getBoundingClientRect().top - lt)]; });
  const seenBefore = await visTop();
  const withNew = JSON.parse(JSON.stringify(base)); withNew.coins.push(coin(900, {symbol: "NEWONE"})); base.coins = withNew.coins;
  await reload(page, S, withNew);
  const seenAfter = await visTop(), pill = await page.$eval("#t-pill", p => [p.hidden, p.textContent]);
  check(tag + "new coin while scrolled down: the rows in view do not move", JSON.stringify(seenBefore) === JSON.stringify(seenAfter), {seenBefore, seenAfter});
  check(tag + "…and the pill says 1 new", pill[0] === false && pill[1] === "1 new ↑", {pill});
  await page.click("#t-pill"); await sleep(900);
  check(tag + "pill click scrolls to the top and hides", await page.$eval("#t-list", l => l.scrollTop) === 0 && await page.$eval("#t-pill", p => p.hidden) &&
        await page.$eval("#t-list .wl-row", r => r.querySelector(".wl-sym").textContent) === "NEWONE");

  // a new coin at the top animates once
  const withNew2 = JSON.parse(JSON.stringify(base)); withNew2.coins.push(coin(901, {symbol: "TOPNEW"})); base.coins = withNew2.coins;
  await reload(page, S, withNew2);
  const anim = await page.$eval("#t-list .wl-row", r => ({sym: r.querySelector(".wl-sym").textContent, cls: r.classList.contains("wl-new"), n: r.getAnimations().length}));
  await sleep(2300);
  const animLater = await page.$eval("#t-list .wl-row", r => ({cls: r.classList.contains("wl-new"), n: r.getAnimations().length}));
  await reload(page, S, withNew2);
  const animAgain = await page.$eval("#t-list .wl-row", r => r.getAnimations().length);
  check(tag + "new coin at the top: slides in and highlights once", anim.sym === "TOPNEW" && anim.cls && anim.n === 2 && !animLater.cls && animLater.n === 0 && animAgain === 0, {anim, animLater, animAgain});

  // sorting and search
  const order = () => page.$$eval("#t-list .wl-row:not([hidden])", r => r.map(x => x.querySelector(".wl-sym").textContent));
  const want = by => page.evaluate(([c, by]) => TERMINAL.sortCoins(c, by).map(x => x.symbol), [withNew2.coins, by]);
  let sortsOk = true; const sortDetail = {};
  for (const by of ["r1h", "volume", "liquidity", "strong", "newest"]) { await page.selectOption("#t-sort", by); await sleep(100); const got = await order(), w = await want(by); sortDetail[by] = got.slice(0, 3); if (JSON.stringify(got) !== JSON.stringify(w)) sortsOk = false; }
  check(tag + "sorting: newest, 1h, volume, liquidity, last strong", sortsOk, sortDetail);
  await page.fill("#t-find", "T1"); await sleep(100); let got = await order();
  const symOk = got.length > 0 && got.every(x => x.toLowerCase().includes("t1"));
  await page.fill("#t-find", mintFor(7).slice(0, 10)); await sleep(100); const byMint = await order();
  await page.fill("#t-find", "zzzz"); await sleep(100);
  const noMatch = [await order(), await page.$eval("#t-empty", e => [e.hidden, e.textContent])];
  await page.fill("#t-find", ""); await sleep(100);
  check(tag + "search by symbol and by mint", symOk && JSON.stringify(byMint) === JSON.stringify(["T7"]) && noMatch[0].length === 0 && !noMatch[1][0], {bySym: got.length, byMint, noMatch: noMatch[1]});

  // malicious symbol and name render as text
  const evil = JSON.parse(JSON.stringify(withNew2)); evil.coins.unshift(coin(902, {symbol: EVIL, name: EVIL, failed: [{check: "safety", reason: EVIL}]})); base.coins = evil.coins;
  await reload(page, S, evil);
  await page.click(`#t-list .wl-row[data-tkey="solana:${mintFor(902)}"]`); await sleep(300);
  const xss = await page.evaluate(e => ({imgs: document.querySelectorAll("#v-terminal img").length, row: document.querySelector(`#t-list .wl-row .wl-sym`).textContent,
    token: document.querySelector("#t-token .tk-sym").textContent, gate: document.querySelector("#t-gate").textContent.includes(e)}), EVIL);
  check(tag + "a symbol like <img src=x onerror=alert(1)> renders as text", xss.imgs === 0 && xss.row === EVIL && xss.token === EVIL && xss.gate && s.dialogs.length === 0, xss);

  // the chart: readout, interval, markers and levels, resize
  if (phone(vp)) await page.click('#tx-bar [data-tpane="chart"]');
  await sleep(300);
  st = await page.evaluate(() => TERMINAL.inspect());
  await page.$eval("#t-chart", e => e.scrollIntoView({block: "center"})); await sleep(200);
  const box = await page.$eval("#t-chart", e => { const r = e.getBoundingClientRect(); return {x: r.x, y: r.y, w: r.width, h: r.height}; });
  const lastReadout = await page.$eval("#t-ohlc", e => e.textContent);
  await page.mouse.move(box.x + box.w * 0.3, box.y + box.h * 0.4); await sleep(200);
  const hoverReadout = await page.$eval("#t-ohlc", e => e.textContent);
  check(tag + "chart: crosshair readout shows the hovered candle's OHLC and volume", /^O .+H .+L .+C .+V .+/.test(hoverReadout) && hoverReadout !== lastReadout, {lastReadout, hoverReadout});
  check(tag + "chart: buy/sell markers and the three level lines", st.markers === 2 && st.lines === 3 && st.bars === 80, st);
  S.chartCalls = []; await page.click('[data-tivl="15m"]'); await sleep(400);
  st = await page.evaluate(() => TERMINAL.inspect());
  check(tag + "chart: interval switch to 15m", st.ivl === "15m" && S.chartCalls.some(c => c[1] === "15m") && await page.$eval('[data-tivl="15m"]', b => b.getAttribute("aria-pressed")) === "true", {calls: S.chartCalls});
  const w0 = await page.$eval("#t-chart canvas", c => c.getBoundingClientRect().width);
  await page.setViewportSize({width: vp[0] - 120 > 320 ? vp[0] - 120 : 330, height: vp[1]}); await sleep(500);
  const w1 = await page.$eval("#t-chart canvas", c => c.getBoundingClientRect().width);
  await page.setViewportSize({width: vp[0], height: vp[1]}); await sleep(500);
  const w2 = await page.$eval("#t-chart canvas", c => c.getBoundingClientRect().width);
  check(tag + "chart: resizes with the window", w1 < w0 && Math.abs(w2 - w0) < 2, {w0, w1, w2});

  // the swap card: blocked, warned, invalid mint
  const sw = JSON.parse(JSON.stringify(base));
  sw.coins.unshift(coin(903, {symbol: "BLOCKD", swap: "blocked", cant_sell: ["freeze authority is enabled: the creator can freeze your tokens"]}),
                   coin(904, {symbol: "WARNED"}), coin(905, {symbol: "BADMINT", swap: "invalid_mint", mint: null, links: {dexscreener: null, solscan: null, rugcheck: null}}));
  await reload(page, S, sw);
  const pick = async sym => { await toList(page, vp); await page.click(`#t-list .wl-row:has(.wl-sym:text-is("${sym}"))`); if (phone(vp)) await page.click('#tx-bar [data-tpane="swap"]'); await sleep(300); };
  // a warned coin: no checkbox, the compact red line, and the widget at once, prefilled SOL to the coin
  await pick("WARNED"); await sleep(400);
  const w = await page.evaluate(() => ({box: !!document.querySelector("#t-ack"), line: (document.querySelector("#t-warnline summary") || {}).textContent,
    swapHidden: document.querySelector("#t-swap").hidden, init: window.__jupInit && window.__jupInit.formProps, target: document.querySelector("#jup-target").textContent}));
  check(tag + "swap: a warned coin loads the widget at once, no checkbox, with the failed-checks line", !w.box && /^1 failed check · RugCheck 80\/100$/.test(w.line || "") &&
        !w.swapHidden && w.target === "JUPITER STUB" && w.init.initialInputMint === SOL && w.init.initialOutputMint === mintFor(904) && w.init.fixedMint === mintFor(904), w);
  // a blocked coin: the block exactly as before, and the widget closes
  const closedBefore = await page.evaluate(() => window.__jupClosed || 0);
  await pick("BLOCKD");
  const b = await page.evaluate(() => ({text: document.querySelector("#t-gate").textContent, box: !!document.querySelector("#t-ack"), line: !!document.querySelector("#t-warnline"),
    swapHidden: document.querySelector("#t-swap").hidden, target: document.querySelector("#jup-target").textContent, closed: window.__jupClosed || 0}));
  check(tag + "swap: a blocked coin shows the block and no widget", b.text.includes("Blocked: you may not be able to sell this token") && b.text.includes("freeze authority") &&
        !b.box && !b.line && b.swapHidden && b.target === "" && b.closed > closedBefore, b);
  await pick("BADMINT");
  const m = await page.evaluate(() => ({text: document.querySelector("#t-gate").textContent, box: !!document.querySelector("#t-ack"), swapHidden: document.querySelector("#t-swap").hidden, links: document.querySelectorAll("#t-token .tx-link").length}));
  check(tag + "swap: an invalid mint has no swap and no links", m.text.includes("Swap off") && !m.box && m.swapHidden && m.links === 0, m);

  // 0 coins: an empty state
  await toList(page, vp);
  await reload(page, S, Object.assign({}, base, {coins: []}));
  await page.selectOption("#t-sort", "newest"); await sleep(100);
  const em = await page.$eval("#t-empty", e => [e.hidden, e.textContent]);
  check(tag + "0 coins: empty state", !em[0] && em[1].includes("No coins") && (await page.$$("#t-list .wl-row:not([hidden])")).length === 0, {em});

  // 500 coins: a full scroll, timed frame by frame
  await reload(page, S, Object.assign({}, base, {coins: Array.from({length: 500}, (_, i) => coin(i + 2000))}));
  await page.selectOption("#t-sort", "r1h"); await sleep(200);
  // 60 px a frame from the top to the bottom; the bottom is re-measured each frame (rows out of view have an estimated height)
  const scrollAll = () => page.evaluate(() => new Promise(done => { const l = document.querySelector("#t-list"), frames = [];
    let last = performance.now(), prev = -1; const t0 = last; l.scrollTop = 0;
    const step = () => { const now = performance.now(); frames.push(now - last); last = now; l.scrollTop += 60;
      const end = l.scrollTop >= l.scrollHeight - l.clientHeight - 1 || l.scrollTop === prev || frames.length > 3000; prev = l.scrollTop;
      if (!end) requestAnimationFrame(step); else { frames.shift(); frames.sort((a, b) => a - b);
        done({rows: l.querySelectorAll(".wl-row").length, px: Math.round(l.scrollTop), ms: Math.round(now - t0), frames: frames.length,
              median: +frames[Math.floor(frames.length / 2)].toFixed(1), p95: +frames[Math.floor(frames.length * .95)].toFixed(1), worst: +frames.at(-1).toFixed(1)}); } };
    requestAnimationFrame(step); }));
  const perf = await scrollAll();
  // the same scroll on 40 coins is the baseline: this headless browser's own frame time
  await reload(page, S, Object.assign({}, base, {coins: Array.from({length: 40}, (_, i) => coin(i + 4000))}));
  await page.selectOption("#t-sort", "newest"); await sleep(200);
  const small = await scrollAll();
  check(tag + "500 coins: full scroll, frames no slower than the 40-coin baseline (p95 within 1.25x)", perf.rows >= 500 && perf.p95 <= Math.max(small.p95 * 1.25, 20), {perf, baseline40: small});

  check(tag + "Terminal checks: no console errors", s.errors.filter(e => !/Failed to load resource.*404/.test(e)).length === 0 && s.bad.filter(x => !x.endsWith("/favicon.ico")).length === 0, {errors: s.errors, bad: s.bad});
  await s.page.unrouteAll({behavior: "ignoreErrors"}); await s.ctx.close();

  // reduced motion: a new coin at the top does not animate
  s = await session(browser, vp, {reduced: true});
  const rb = {coins: Array.from({length: 20}, (_, i) => coin(i)), ticker: [], manual: [], allow_unsellable: false, sol_mint: SOL, strong: {}, now: 1795000000};
  s.S.payload = rb; await openTerminal(s.page); await toList(s.page, vp);
  const rb2 = JSON.parse(JSON.stringify(rb)); rb2.coins.push(coin(950, {symbol: "CALM"}));
  await reload(s.page, s.S, rb2);
  const ra = await s.page.$eval("#t-list .wl-row", r => ({sym: r.querySelector(".wl-sym").textContent, n: r.getAnimations().length}));
  check(tag + "reduced motion: a new coin is inserted with no animation", ra.sym === "CALM" && ra.n === 0, ra);
  await s.page.unrouteAll({behavior: "ignoreErrors"}); await s.ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const vp of [[1440, 900], [390, 844]]) await run(browser, vp); }
  finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
