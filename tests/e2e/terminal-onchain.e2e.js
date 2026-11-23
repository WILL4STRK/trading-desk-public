// On-chain live prices in the Terminal tab, run by hand against a demo desk:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal-onchain.e2e.js [http://127.0.0.1:8799]
// No real network: the Solana websocket is a fake (window.WebSocket replaced before the page loads), the RPC,
// Jupiter and DexScreener answers come from tests/fixtures/onchain/pump-curve-and-pool.json and stand-ins.
const { chromium } = require("playwright-core");
const path = require("path");
const F = require(path.join(__dirname, "..", "fixtures", "onchain", "pump-curve-and-pool.json"));
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const SOL = "So11111111111111111111111111111111111111112";
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok}); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const ACC = {};
for (const a of [F.curve.account, F.curve.mint_account, F.pool.account, F.pool.base_vault, F.pool.quote_vault, F.pool.mint_account]) ACC[a.address] = {owner: a.owner, data: [a.data, "base64"]};
const coin = (key, mint, pool, dex, sym) => ({key, symbol: sym, name: sym, mint, px: 0.00002, r1h: 1, r6h: 1, r24h: 1, liq: 1000, v1h: 500, age_h: 1, dex,
  screen: "fail", strict: "fail", failed: [{check: "strict", reason: "top 10 holders 50%"}], cant_sell: [], swap: "confirm", last_strong_ts: null, seen: 1, record: {},
  links: {dexscreener: "https://dexscreener.com/solana/" + pool, solscan: "https://solscan.io/token/" + mint, rugcheck: "https://rugcheck.xyz/tokens/" + mint}});
const RAY_MINT = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", RAY_POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2";
const COINS = [coin("solana:" + F.curve.mint, F.curve.mint, F.curve.account.address, "pumpfun", "CURVE"),
               coin("solana:" + F.pool.mint, F.pool.mint, F.pool.account.address, "pumpswap", "POOL"),
               coin("solana:" + RAY_MINT, RAY_MINT, RAY_POOL, "raydium", "RAY")];
const bars = Array.from({length: 60}, (_, i) => ({time: Math.floor(Date.now() / 1000 / 300) * 300 - (59 - i) * 300, open: 2e-5, high: 2.1e-5, low: 1.9e-5, close: 2e-5, volume: 10}));

// the fake websocket: records what the page sends; the test answers through window.__fakeWs
const FAKE_WS = `(() => {
  const all = []; window.__fakeWs = {all, sent: [], last: () => all[all.length - 1]};
  class FakeWS {
    constructor(url) { this.url = url; this.readyState = 0; all.push(this); setTimeout(() => { if (this.readyState === 0) { this.readyState = 1; this.onopen && this.onopen({}); } }, 20); }
    send(s) { window.__fakeWs.sent.push({url: this.url, n: all.indexOf(this), msg: JSON.parse(s)}); }
    close() { if (this.readyState < 2) { this.readyState = 3; } }
    emit(o) { this.onmessage && this.onmessage({data: typeof o === "string" ? o : JSON.stringify(o)}); }
    drop() { this.readyState = 3; this.onclose && this.onclose({}); }
  }
  FakeWS.OPEN = 1; window.WebSocket = FakeWS;
})();`;

async function run(browser, [w, h, dpr]) {
  const tag = `${w}x${h}@${dpr} `;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, deviceScaleFactor: dpr});
  await ctx.addInitScript(FAKE_WS);
  const page = await ctx.newPage(), errors = [], ds = [], gecko = [], rpcCalls = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource.*404/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("request", r => { if (/geckoterminal/.test(r.url())) gecko.push(r.url()); });
  await page.route("**/api/meme/terminal", route => route.fulfill({json: {coins: COINS, ticker: [], manual: [], allow_unsellable: false, sol_mint: SOL, strong: {}, now: 1}}));
  await page.route("**/api/meme/chart?*", route => route.fulfill({json: {bars, levels: [], trades: [], symbol: "X", url: "https://dexscreener.com/solana/x"}}));
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript", body: "window.Jupiter={init:function(o){document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){}};"}));
  await page.route("https://lite-api.jup.ag/**", route => route.fulfill({json: {[SOL]: {usdPrice: F.sol_usd_jupiter}}}));
  await page.route("https://api.dexscreener.com/**", route => { ds.push(route.request().url());
    route.fulfill({json: {pairs: [{chainId: "solana", pairAddress: RAY_POOL, baseToken: {address: RAY_MINT}, priceUsd: "1.5"}]}}); });
  await page.route("https://api.mainnet-beta.solana.com/", route => { const b = JSON.parse(route.request().postData()); rpcCalls.push(b.method + " " + b.params[0].join(","));
    route.fulfill({json: {jsonrpc: "2.0", id: b.id, result: {context: {slot: 1}, value: b.params[0].map(a => ACC[a] || null)}}}); });
  await page.goto(BASE + "/"); await page.waitForSelector(".tabs button");
  await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(600);
  const live = () => page.evaluate(() => TERMINAL.inspect().live);
  const sent = () => page.evaluate(() => window.__fakeWs.sent);
  const pick = async sym => { if (w < 900) await page.click('#tx-bar [data-tpane="list"]'); await page.click(`#t-list .wl-row:has(.wl-sym:text-is("${sym}"))`); await sleep(500); };
  const ack = () => page.evaluate(() => { const ws = window.__fakeWs.last(); window.__fakeWs.sent.filter(s => s.n === window.__fakeWs.all.length - 1 && s.msg.method === "accountSubscribe")
    .forEach((s, i) => ws.emit({jsonrpc: "2.0", id: s.msg.id, result: 100 + s.msg.id})); });
  // a reserve change for one subscribed account: bytes from the fixture with u64 fields changed
  const notify = (sub, base64, owner) => page.evaluate(([sub, base64, owner]) => window.__fakeWs.last().emit({jsonrpc: "2.0", method: "accountNotification",
    params: {subscription: sub, result: {context: {slot: 2}, value: {owner, lamports: 1, executable: false, rentEpoch: 0, data: [base64, "base64"]}}}}), [sub, base64, owner]);
  const patched = (base64, edits) => page.evaluate(([base64, edits]) => { const b = TERMINAL.b64bytes(base64), v = new DataView(b.buffer);
    for (const [off, add] of edits) v.setBigUint64(off, v.getBigUint64(off, true) + BigInt(add), true);
    let s = ""; for (const x of b) s += String.fromCharCode(x); return btoa(s); }, [base64, edits]);

  // ---- a pump.fun bonding curve: one websocket, one subscription, ticks with real volume
  await pick("CURVE"); await sleep(400); await ack(); await sleep(300);
  let L = await live(), S = await sent();
  const subs = S.filter(s => s.msg.method === "accountSubscribe");
  check(tag + "curve: one websocket, one accountSubscribe for the curve (base64, confirmed), status live, on-chain label",
        L.mode === "chain" && L.opened === 1 && subs.length === 1 && subs[0].msg.params[0] === F.curve.account.address && subs[0].msg.params[1].encoding === "base64" &&
        subs[0].msg.params[1].commitment === "confirmed" && L.subs === 1 && L.status === "live" && /^live · on-chain · live since \d\d:\d\d:\d\d$/.test(await page.$eval("#t-livelbl", e => e.textContent)),
        {mode: L.mode, subs: subs.map(s => s.msg.params[0]), status: L.status, label: await page.$eval("#t-livelbl", e => e.textContent)});
  const expect = Number(F.curve.dexscreener.priceUsd);
  check(tag + "curve: the price at open matches the recorded curve (within 2% of DexScreener's USD price then)", Math.abs(L.last / expect - 1) < 0.02, {last: L.last, ds: expect});
  check(tag + "curve: checked owner, layout, mint decimals and the bonding-curve address before subscribing", rpcCalls.some(c => c.includes(F.curve.account.address)) && L.why === "");
  await page.click('[data-tivl="1s"]'); await sleep(200);
  const t0 = L.ticks;
  // a buy of 1 SOL: virtual quote +1e9, virtual token down
  await notify(101, await patched(F.curve.account.data, [[16, 1e9], [8, -1290000000000]]), "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"); await sleep(500);
  L = await live();
  const lb = await page.evaluate(() => TERMINAL.inspect().lastBar);
  check(tag + "curve: a reserve change is a tick: price up, volume = 1 SOL in USD, 1s candle has that volume",
        L.ticks === t0 + 1 && L.last > expect && Math.abs(L.volume - F.sol_usd_jupiter) < 0.01 && lb && lb.volume >= 0,
        {ticks: [t0, L.ticks], volume: L.volume, lastBar: lb});
  await sleep(2300);
  const bars1 = await page.evaluate(() => TERMINAL.inspect().bars);
  check(tag + "curve: seconds with no trade show as flat dojis (the 1s chart keeps moving)", bars1 >= 3, {bars: bars1});
  // malformed or foreign messages: all dropped, no tick
  const d0 = L.dropped, k0 = L.ticks;
  await notify(999, F.curve.account.data, "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");                         // not our subscription
  await notify(101, F.curve.account.data, "11111111111111111111111111111111");                                      // wrong owner
  await notify(101, "%%%not base64", "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");                                // bad bytes
  await notify(101, F.pool.account.data, "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");                            // wrong layout
  await page.evaluate(() => { const ws = window.__fakeWs.last(); ws.emit("not json {"); ws.emit({jsonrpc: "2.0", method: "slotNotification", params: {}}); });
  await notify(101, await patched(F.curve.account.data, [[16, 10000e9]]), "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P");   // price jumps over 100x
  await sleep(500); L = await live();
  check(tag + "curve: malformed, foreign and over-50x messages are dropped, no tick", L.ticks === k0 && L.dropped >= d0 + 7, {dropped: [d0, L.dropped], ticks: [k0, L.ticks]});

  // ---- 5m: the last candle keeps moving with the live ticks
  await page.click('[data-tivl="5m"]'); await sleep(500);
  const before = await page.evaluate(() => TERMINAL.inspect().lastBar);
  await notify(101, await patched(F.curve.account.data, [[16, 3e9], [8, -3800000000000]]), "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"); await sleep(500);
  const after = await page.evaluate(() => TERMINAL.inspect().lastBar);
  L = await live();
  check(tag + "5m: the last candle's close follows the live tick between server refreshes", after.time === before.time && Math.abs(after.close - L.last) < 1e-15 && after.close !== before.close && after.volume > before.volume,
        {before: [before.close, before.volume], after: [after.close, after.volume], last: L.last});

  // ---- another coin (a PumpSwap pool): the curve is unsubscribed and its socket closed; 3 subscriptions for the pool
  const unsub0 = L.unsubs;
  await pick("POOL"); await sleep(400);
  S = await sent();
  const unsubs = S.filter(s => s.msg.method === "accountUnsubscribe");
  const firstClosed = await page.evaluate(() => window.__fakeWs.all[0].readyState);
  await ack(); await sleep(300); L = await live(); S = await sent();
  const psubs = S.filter(s => s.msg.method === "accountSubscribe" && s.n === 1).map(s => s.msg.params[0]);
  check(tag + "coin change: accountUnsubscribe sent and the old socket closed", unsubs.length === 1 && unsubs[0].msg.params[0] === 101 && firstClosed === 3 && L.unsubs === unsub0 + 1, {unsubs: unsubs.map(u => u.msg.params), firstClosed});
  check(tag + "pool: one new socket, 3 subscriptions (pool, base vault, quote vault), never more", L.opened === 2 && psubs.length === 3 && L.subs === 3 &&
        JSON.stringify(psubs) === JSON.stringify([F.pool.account.address, F.pool.base_vault.address, F.pool.quote_vault.address]), {psubs, subs: L.subs});
  const pexp = Number(F.pool.jupiter_usd);
  check(tag + "pool: the price at open uses effective quote reserves (within 2% of Jupiter's price then)", Math.abs(L.last / pexp - 1) < 0.02, {last: L.last, jupiter: pexp});
  // one trade moves both vaults; the two notifications make one tick with volume = the quote change
  const k1 = L.ticks;
  await notify(103, await patched(F.pool.quote_vault.data, [[64, 2e9]]), "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");
  await notify(102, await patched(F.pool.base_vault.data, [[64, -2300000000000]]), F.pool.base_vault.owner);
  await sleep(600); L = await live();
  check(tag + "pool: a trade's two vault updates make one tick, volume = 2 SOL in USD", L.ticks === k1 + 1 && Math.abs(L.volume - 2 * F.sol_usd_jupiter) < 0.01, {ticks: [k1, L.ticks], volume: L.volume});
  await notify(103, await patched(F.pool.base_vault.data, [[64, 1]]), "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA");      // a base-mint account on the quote subscription
  await sleep(400);
  check(tag + "pool: a vault message for the wrong mint is dropped", (await live()).ticks === k1 + 1);

  // ---- the socket drops: reconnecting, then a new socket and fresh subscriptions after the backoff
  await page.evaluate(() => window.__fakeWs.last().drop()); await sleep(200);
  const rc = await live();
  await sleep(4800); await ack(); await sleep(300); L = await live();
  check(tag + "a dropped socket: amber, then reconnects with backoff and resubscribes", rc.status === "reconnecting" && L.opened === 3 && L.subs === 3 && L.status === "live", {after: rc.status, opened: L.opened, status: L.status});

  // ---- hidden page: unsubscribed and closed, nothing open; visible again: a new socket
  const o0 = L.opened, u0 = L.unsubs;
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "hidden"}); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(3000); L = await live();
  check(tag + "page hidden: unsubscribed, socket closed, no new socket", !L.wsOpen && L.unsubs === u0 + 3 && L.opened === o0 && L.status === "stopped", {unsubs: [u0, L.unsubs], opened: L.opened, status: L.status});
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "visible"}); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(500); await ack(); await sleep(300); L = await live();
  check(tag + "visible again: a new socket for the same coin", L.wsOpen && L.opened === o0 + 1 && L.status === "live");

  // ---- another venue: DexScreener fallback, price only and delayed, no websocket
  const o1 = L.opened; await pick("RAY"); await sleep(2600); L = await live();
  check(tag + "Raydium: DexScreener fallback, labelled price only · delayed, no websocket", L.mode === "poll" && L.opened === o1 && !L.wsOpen && ds.length >= 1 &&
        /^live · price only · delayed · live since/.test(await page.$eval("#t-livelbl", e => e.textContent)), {mode: L.mode, label: await page.$eval("#t-livelbl", e => e.textContent)});
  check(tag + "nothing from GeckoTerminal in the page, no console errors", gecko.length === 0 && errors.length === 0, {gecko, errors});
  await page.unrouteAll({behavior: "ignoreErrors"}); await ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const s of [[1280, 720, 1], [390, 844, 2]]) await run(browser, s); } finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
