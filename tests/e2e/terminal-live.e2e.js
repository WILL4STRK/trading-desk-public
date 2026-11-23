// Live 5s/15s prices and the failed-checks line in the Terminal tab, run by hand against a demo desk:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal-live.e2e.js [http://127.0.0.1:8799]
// No real network: DexScreener and Jupiter are stand-ins (page.route); the coin list and candles are fixtures.
const { chromium } = require("playwright-core");
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const SOL = "So11111111111111111111111111111111111111112";
const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";
const addr = i => { let d = ""; for (let n = i, k = 0; k < 3; k++, n = Math.floor(n / 58)) d = B58[n % 58] + d; let s = d; for (let k = 0; s.length < 44; k++) s += B58[(k * 13 + 7) % 58]; return s; };
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok}); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const coin = (i, extra) => Object.assign({key: "solana:" + addr(i), symbol: "T" + i, name: "Test " + i, mint: addr(i), px: 0.001, r1h: 1, r6h: 1, r24h: 1, liq: 1000, v1h: 500, age_h: i, dex: "pumpswap",
  screen: "fail", strict: "fail", failed: [{check: "safety", reason: "RugCheck risk 54/100"}, {check: "safety", reason: "danger: Creator history of rugged tokens"},
    {check: "safety", reason: "danger: Single holder ownership"}, {check: "strict", reason: "top 10 holders 50%"}],
  cant_sell: [], swap: "confirm", last_strong_ts: null, seen: 1, record: {shadows: 0, practice: 0},
  links: {dexscreener: "https://dexscreener.com/solana/" + addr(i + 1000), solscan: "https://solscan.io/token/" + addr(i), rugcheck: "https://rugcheck.xyz/tokens/" + addr(i)}}, extra || {});
const COINS = [coin(1), coin(2), coin(3, {symbol: "BLOCKD", swap: "blocked", cant_sell: ["reported as a honeypot: buys work, sells may not"]})];
const bars = Array.from({length: 60}, (_, i) => ({time: 1795000000 + i * 300, open: 1e-3, high: 1.1e-3, low: 0.9e-3, close: 1e-3, volume: 10}));

async function run(browser, [w, h, dpr]) {
  const tag = `${w}x${h}@${dpr} `;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, deviceScaleFactor: dpr});
  const page = await ctx.newPage(), errors = [], ds = [], gecko = [];
  let mode = "ok", px = 0.001;
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource.*404/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("request", r => { if (/geckoterminal/.test(r.url())) gecko.push(r.url()); });
  await page.route("**/api/meme/terminal", route => route.fulfill({json: {coins: COINS, ticker: [], manual: [], allow_unsellable: false, sol_mint: SOL, strong: {}, now: 1}}));
  await page.route("**/api/meme/chart?*", route => route.fulfill({json: {bars, levels: [], trades: [], symbol: "X", url: "https://dexscreener.com/solana/x"}}));
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript",
    body: "window.Jupiter={init:function(o){window.__jupInit=JSON.parse(JSON.stringify(o));document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){window.__jupClosed=(window.__jupClosed||0)+1}};"}));
  await page.route("https://api.dexscreener.com/**", route => {
    const u = new URL(route.request().url()), pool = u.pathname.split("/").pop(); ds.push({pool, t: Date.now()});
    const c = COINS.find(x => x.links.dexscreener.endsWith(pool));
    if (mode === "garbage") return route.fulfill({contentType: "application/json", body: '{"pairs":[{"pairAddress":"' + pool + '","baseToken":{"address":"Wrong1111111111111111111111111111111"},"priceUsd":"1"}]}'});
    if (mode === "broken") return route.fulfill({contentType: "application/json", body: "not json {"});
    px *= 1.01;
    route.fulfill({json: {schemaVersion: "1.0.0", pairs: [{chainId: "solana", pairAddress: pool, baseToken: {address: c ? c.mint : "?"}, priceUsd: String(px)}]}});
  });
  await page.goto(BASE + "/"); await page.waitForSelector(".tabs button");
  await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(800);
  const live = () => page.evaluate(() => TERMINAL.inspect().live);
  const pick = async sym => { if (w < 900) await page.click('#tx-bar [data-tpane="list"]'); await page.click(`#t-list .wl-row:has(.wl-sym:text-is("${sym}"))`); await sleep(300); };

  // the first coin opens: one poller for its pool, every 2 s, status live, the seconds candles start empty
  await pick("T1"); const t0 = Date.now(); await sleep(5200);
  let L = await live(); const pool1 = addr(1001);
  const gaps = ds.filter(x => x.pool === pool1).map(x => x.t).map((t, i, a) => i ? t - a[i - 1] : 0).slice(1);
  check(tag + "a coin opens: polls its own pool on api.dexscreener.com about every 2 s, status live", L.status === "live" && L.pool === pool1 && L.samples >= 2 &&
        ds.every(x => x.pool === pool1) && gaps.every(g => g > 1700 && g < 2600), {status: L.status, samples: L.samples, gaps});
  check(tag + "the status dot is green and the label says price only, live since", await page.$eval("#t-live", e => e.dataset.s) === "live" &&
        /^live · price only · live since \d\d:\d\d:\d\d$/.test(await page.$eval("#t-livelbl", e => e.textContent)), await page.$eval("#t-livelbl", e => e.textContent));
  // 5s candles from those prices; the last candle updates live; no volume
  await page.click('[data-tivl="5s"]'); await sleep(300);
  const b0 = await page.evaluate(() => TERMINAL.inspect().bars); await sleep(6200);
  const b1 = await page.evaluate(() => TERMINAL.inspect().bars);
  const ro = await page.$eval("#t-ohlc", e => e.textContent);
  check(tag + "5s: candles from the polled prices grow live, price only (no volume)", b0 >= 1 && b1 > b0 && /V –$/.test(ro) && await page.$$eval("#t-chart canvas", c => c.length) > 0, {bars: [b0, b1], readout: ro});
  await page.click('[data-tivl="15s"]'); await sleep(300);
  check(tag + "15s: fewer, wider candles from the same prices", (await page.evaluate(() => TERMINAL.inspect().bars)) <= b1);
  // 5m keeps its history (from the desk's own endpoint, not live)
  await page.click('[data-tivl="5m"]'); await sleep(400);
  check(tag + "5m keeps its history", (await page.evaluate(() => TERMINAL.inspect().bars)) === 60);

  // another coin: the old pool is no longer polled, the new one is, the seconds candles start again
  const since1 = L.since; await sleep(1100); await pick("T2"); const tSwitch = Date.now(); await sleep(4500);
  L = await live(); const pool2 = addr(1002);
  const oldAfter = ds.filter(x => x.pool === pool1 && x.t > tSwitch + 50).length, newAfter = ds.filter(x => x.pool === pool2).length;
  check(tag + "another coin: the old pool stops, the new pool is polled, live since restarts", oldAfter === 0 && newAfter >= 2 && L.pool === pool2 && L.since > since1 && L.samples >= 1, {oldAfter, newAfter, since: [since1, L.since]});

  // a malformed or foreign response is dropped: no sample, status amber, backoff; then live again
  mode = "garbage"; let s0 = (await live()).samples; await sleep(2600);
  L = await live();
  check(tag + "a response for another mint is dropped: no new price, status reconnecting", L.samples === s0 && L.status === "reconnecting" && L.dropped >= 1 &&
        await page.$eval("#t-live", e => e.dataset.s) === "reconnecting", L);
  mode = "broken"; const tb = Date.now(); await sleep(9000);
  const tries = ds.filter(x => x.pool === pool2 && x.t > tb).map(x => x.t), backoff = tries.map((t, i, a) => i ? t - a[i - 1] : t - tb);
  L = await live();
  check(tag + "invalid JSON is dropped and the retries back off", L.samples === s0 && L.status === "reconnecting" && tries.length <= 3 && (backoff.length < 2 || backoff[1] > backoff[0]), {backoff, samples: L.samples});
  mode = "ok"; await sleep(17000);
  L = await live();
  check(tag + "after the backoff it is live again", L.status === "live" && L.samples > s0, {status: L.status, samples: [s0, L.samples]});

  // the page hidden: no polling at all; visible again: it resumes for the same coin
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "hidden"}); document.dispatchEvent(new Event("visibilitychange")); });
  const th = Date.now(); await sleep(5000);
  const whileHidden = ds.filter(x => x.t > th + 50).length; L = await live();
  check(tag + "page hidden: no requests, status stopped", whileHidden === 0 && L.status === "stopped" && !L.polling, {whileHidden, status: L.status});
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "visible"}); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(2500); L = await live();
  check(tag + "visible again: polling resumes for the same coin", L.status === "live" && L.pool === pool2 && L.polling);
  // another desk tab: the next poll sees the Terminal is closed and stops
  await page.click('[data-v="desk"]'); const td = Date.now(); await sleep(5000);
  const whileAway = ds.filter(x => x.t > td + 2200).length; L = await live();
  check(tag + "another desk tab: polling stops", whileAway === 0 && L.status === "stopped", {whileAway, status: L.status});
  await page.click('[data-v="terminal"]'); await sleep(2500);
  check(tag + "back on the Terminal: polling resumes", (await live()).status === "live");

  // the failed-checks line: no checkbox, the widget at once, danger items bold, expand lists all
  if (w < 900) await page.click('#tx-bar [data-tpane="swap"]');
  await sleep(300);
  const g = await page.evaluate(() => { const d = document.querySelector("#t-warnline"), s = d && d.querySelector("summary");
    return {box: !!document.querySelector("#t-ack"), summary: s && s.textContent, oneLine: s && s.getBoundingClientRect().height < 30,
      bold: s ? [...s.querySelectorAll(".t-danger")].map(b => [b.textContent, +getComputedStyle(b).fontWeight, getComputedStyle(b).color]) : [],
      plain: s ? [...s.querySelectorAll("span")].map(b => +getComputedStyle(b).fontWeight) : [], widget: document.querySelector("#jup-target").textContent, init: window.__jupInit && window.__jupInit.formProps}; });
  check(tag + "a warned coin: the widget is loaded at once, no checkbox", !g.box && g.widget === "JUPITER STUB" && g.init.initialOutputMint === addr(2) && g.init.initialInputMint === SOL, g.init);
  check(tag + "one compact red line, danger first: 4 failed checks · creator rugged before · single holder · RugCheck 54/100 · top 10 holders 50%",
        g.summary === "4 failed checks · creator rugged before · single holder · RugCheck 54/100 · top 10 holders 50%" && g.oneLine, g.summary);
  check(tag + "danger items are bold red, the others are not bold", g.bold.length === 2 && g.bold.every(b => b[1] >= 700) && g.plain.every(x => x < 600), {bold: g.bold, plain: g.plain});
  await page.click("#t-warnline summary"); await sleep(200);
  const items = await page.$$eval("#t-warnline[open] li", l => l.map(x => x.textContent));
  check(tag + "click expands the full list of every failed check", JSON.stringify(items) === JSON.stringify(["Safety: RugCheck risk 54/100", "Safety: danger: Creator history of rugged tokens",
        "Safety: danger: Single holder ownership", "Strict: top 10 holders 50%"]), items);
  await page.evaluate(() => TERMINAL.load()); await sleep(300);
  check(tag + "a refresh keeps the list expanded", await page.$eval("#t-warnline", d => d.open));
  // a blocked coin: the block as before, no widget, no line
  const closedBefore = await page.evaluate(() => window.__jupClosed || 0);
  await pick("BLOCKD"); if (w < 900) await page.click('#tx-bar [data-tpane="swap"]'); await sleep(300);
  const bl = await page.evaluate(() => ({text: document.querySelector("#t-gate").textContent, line: !!document.querySelector("#t-warnline"), swapHidden: document.querySelector("#t-swap").hidden,
    widget: document.querySelector("#jup-target").textContent, closed: window.__jupClosed || 0}));
  check(tag + "a blocked coin: the block, no widget, no line", bl.text.includes("Blocked: you may not be able to sell this token") && bl.text.includes("honeypot") && !bl.line && bl.swapHidden && bl.widget === "" && bl.closed > closedBefore, bl);
  check(tag + "nothing from GeckoTerminal in the page, no console errors", gecko.length === 0 && errors.length === 0, {gecko, errors});
  await page.unrouteAll({behavior: "ignoreErrors"}); await ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const s of [[960, 503, 1.75], [1280, 720, 1], [390, 844, 2]]) await run(browser, s); } finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
