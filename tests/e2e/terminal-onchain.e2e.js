// On-chain live prices in the Terminal tab, run by hand against a demo desk:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal-onchain.e2e.js [http://127.0.0.1:8799]
// No real network: the desk's /api/meme/chain answers come from real account bytes recorded in
// tests/fixtures/onchain/pump-curve-and-pool.json (edited here to make trades); Jupiter and DexScreener are stand-ins.
const { chromium } = require("playwright-core");
const path = require("path");
const F = require(path.join(__dirname, "..", "fixtures", "onchain", "pump-curve-and-pool.json"));
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const SOL = "So11111111111111111111111111111111111111112", PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P", TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok}); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
// u64 edits on base64 account data: [[offset, add], ...]
const patch = (b64, edits) => { const b = Buffer.from(b64, "base64"); for (const [o, add] of edits) b.writeBigUInt64LE(b.readBigUInt64LE(o) + BigInt(add), o); return b.toString("base64"); };
const coin = (key, mint, pool, dex, sym) => ({key, symbol: sym, name: sym, mint, px: 0.00002, r1h: 1, r6h: 1, r24h: 1, liq: 1000, v1h: 500, age_h: 1, dex,
  screen: "fail", strict: "fail", failed: [{check: "strict", reason: "top 10 holders 50%"}], cant_sell: [], swap: "confirm", last_strong_ts: null, seen: 1, record: {},
  links: {dexscreener: "https://dexscreener.com/solana/" + pool, solscan: "https://solscan.io/token/" + mint, rugcheck: "https://rugcheck.xyz/tokens/" + mint}});
const RAY_MINT = "4k3Dyjzvzp8eMZWUXbBCjEvwSkkk59S5iCNLY3QrkX6R", RAY_POOL = "58oQChx4yWmvKdwLLZzBi4ChoCc2fqCUWBkwMihLYQo2";
const CK = "solana:" + F.curve.mint, PK = "solana:" + F.pool.mint;
const COINS = [coin(CK, F.curve.mint, F.curve.account.address, "pumpfun", "CURVE"), coin(PK, F.pool.mint, F.pool.account.address, "pumpswap", "POOL"),
               coin("solana:" + RAY_MINT, RAY_MINT, RAY_POOL, "raydium", "RAY")];
const bars = Array.from({length: 60}, (_, i) => ({time: Math.floor(Date.now() / 1000 / 300) * 300 - (59 - i) * 300, open: 2e-5, high: 2.1e-5, low: 1.9e-5, close: 2e-5, volume: 10}));

async function run(browser, [w, h, dpr]) {
  const tag = `${w}x${h}@${dpr} `;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, deviceScaleFactor: dpr});
  const page = await ctx.newPage(), errors = [], ds = [], gecko = [], chainCalls = [], direct = [];
  // the desk's answers: mutable account data, and a switch to fail or misbehave
  const acc = {}; for (const a of [F.curve.account, F.curve.mint_account, F.pool.account, F.pool.base_vault, F.pool.quote_vault, F.pool.mint_account]) acc[a.address] = {owner: a.owner, data: [a.data, "base64"]};
  let mode = "ok";
  const answer = key => {
    const pool = key === CK ? {kind: "curve", addrs: {pool: F.curve.account.address, mint: F.curve.mint}}
      : {kind: "pool", addrs: {pool: F.pool.account.address, mint: F.pool.mint, base_vault: F.pool.base_vault.address, quote_vault: F.pool.quote_vault.address}};
    const out = Object.assign({at: Date.now() / 1000, accounts: {}}, pool);
    for (const a of Object.values(pool.addrs)) out.accounts[a] = acc[a];
    return out;
  };
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("request", r => { if (/geckoterminal/.test(r.url())) gecko.push(r.url()); if (/mainnet-beta|helius|quiknode/.test(r.url())) direct.push(r.url()); });
  page.on("websocket", ws => direct.push("ws " + ws.url()));
  await page.route("**/api/meme/terminal", route => route.fulfill({json: {coins: COINS, ticker: [], manual: [], allow_unsellable: false, sol_mint: SOL, strong: {}, now: 1}}));
  await page.route("**/api/meme/chart?*", route => route.fulfill({json: {bars, levels: [], trades: [], symbol: "X", url: "https://dexscreener.com/solana/x"}}));
  await page.route("**/api/meme/chain?*", route => { const key = new URL(route.request().url()).searchParams.get("key"); chainCalls.push({key, t: Date.now()});
    if (mode === "down") return route.fulfill({status: 500, json: {error: "Solana RPC did not answer (TimeoutError)"}});
    if (mode === "other") { const a = answer(key); a.addrs.mint = RAY_MINT; return route.fulfill({json: a}); }
    if (mode === "garbage") return route.fulfill({contentType: "application/json", body: "not json {"});
    route.fulfill({json: answer(key)}); });
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript", body: "window.Jupiter={init:function(o){document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){}};"}));
  await page.route("https://lite-api.jup.ag/**", route => route.fulfill({json: {[SOL]: {usdPrice: F.sol_usd_jupiter}}}));
  await page.route("https://api.dexscreener.com/**", route => { ds.push(route.request().url());
    route.fulfill({json: {pairs: [{chainId: "solana", pairAddress: RAY_POOL, baseToken: {address: RAY_MINT}, priceUsd: "1.5"}]}}); });
  await page.goto(BASE + "/"); await page.waitForSelector(".tabs button");
  await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(600);
  const live = () => page.evaluate(() => TERMINAL.inspect().live);
  const pick = async sym => { if (w < 900) await page.click('#tx-bar [data-tpane="list"]'); await page.click(`#t-list .wl-row:has(.wl-sym:text-is("${sym}"))`); await sleep(400); };

  // ---- a pump.fun bonding curve
  await pick("CURVE"); await sleep(1700);
  let L = await live();
  const label = await page.$eval("#t-livelbl", e => e.textContent);
  const expect = Number(F.curve.dexscreener.priceUsd);
  check(tag + "curve: read through the desk by coin key, status live, on-chain label", L.mode === "chain" && L.status === "live" && chainCalls.every(c => c.key === CK) &&
        chainCalls.length >= 2 && /^live · on-chain · live since \d\d:\d\d:\d\d$/.test(label), {calls: chainCalls.length, status: L.status, label});
  check(tag + "curve: the price at open matches the recorded curve (within 2% of DexScreener's USD price then)", Math.abs(L.last / expect - 1) < 0.02, {last: L.last, ds: expect});
  const gaps = chainCalls.map((c, i, a) => i ? c.t - a[i - 1].t : 0).slice(1);
  check(tag + "curve: one read a second, and never to Solana's RPC or a websocket from the page", gaps.every(g => g > 800 && g < 1600) && direct.length === 0, {gaps, direct});
  await page.click('[data-tivl="1s"]'); await sleep(200);
  const t0 = L.ticks;
  // a buy of 1 SOL: virtual quote +1e9, virtual token down
  acc[F.curve.account.address].data[0] = patch(F.curve.account.data, [[16, 1e9], [8, -1290000000000]]); await sleep(1300);
  L = await live();
  check(tag + "curve: a reserve change is one tick: price up, volume = 1 SOL in USD", L.ticks === t0 + 1 && L.last > expect && Math.abs(L.volume - F.sol_usd_jupiter) < 0.01, {ticks: [t0, L.ticks], volume: L.volume});
  await sleep(2200);
  check(tag + "curve: no reserve change, no tick; the 1s chart carries flat dojis", (await live()).ticks === t0 + 1 && (await page.evaluate(() => TERMINAL.inspect().bars)) >= 3);
  // bad answers: another coin, invalid JSON, a wrong owner, a layout that does not decode, a jump over 50x
  const d0 = L.dropped, k0 = L.ticks;
  mode = "other"; await sleep(1100); mode = "garbage"; await sleep(2200); mode = "ok"; await sleep(4500);
  const good = acc[F.curve.account.address].data[0];
  acc[F.curve.account.address].data[0] = patch(F.curve.account.data, [[16, 10000e9]]); await sleep(1300);
  L = await live();
  acc[F.curve.account.address].data[0] = good; await sleep(1300);
  check(tag + "curve: answers for another coin, invalid JSON and over-50x prices are dropped, no tick", L.ticks === k0 && L.dropped >= d0 + 3, {dropped: [d0, L.dropped], ticks: [k0, L.ticks]});
  acc[F.curve.account.address].owner = "11111111111111111111111111111111"; await sleep(1300);
  L = await live();
  check(tag + "curve: an account with the wrong owner switches to the DexScreener fallback, with the reason", L.mode === "poll" && /pump.fun bonding curve/.test(L.why) &&
        /price only · delayed/.test(await page.$eval("#t-livelbl", e => e.textContent)), {mode: L.mode, why: L.why});
  acc[F.curve.account.address].owner = PUMP;

  // ---- 5m: the last candle follows the live ticks (a fresh curve coin)
  await pick("POOL"); await sleep(300); await pick("CURVE"); await sleep(1500);
  await page.click('[data-tivl="5m"]'); await sleep(500);
  const before = await page.evaluate(() => TERMINAL.inspect().lastBar);
  acc[F.curve.account.address].data[0] = patch(F.curve.account.data, [[16, 3e9], [8, -3800000000000]]); await sleep(1300);
  const after = await page.evaluate(() => TERMINAL.inspect().lastBar); L = await live();
  check(tag + "5m: the last candle's close and volume follow the live tick between server refreshes", after.time === before.time && Math.abs(after.close - L.last) < 1e-15 && after.close !== before.close && after.volume > before.volume,
        {before: [before.close, before.volume], after: [after.close, after.volume]});
  acc[F.curve.account.address].data[0] = F.curve.account.data;

  // ---- another coin: the curve is no longer read; the PumpSwap pool is
  await pick("POOL"); const tSwitch = Date.now(); await sleep(2600);
  L = await live();
  const old = chainCalls.filter(c => c.key === CK && c.t > tSwitch + 50).length, now = chainCalls.filter(c => c.key === PK).length;
  check(tag + "coin change: the old coin is not read again, the new one is", old === 0 && now >= 2 && L.mode === "chain", {old, now});
  const pexp = Number(F.pool.jupiter_usd);
  check(tag + "pool: the price at open uses effective quote reserves (within 2% of Jupiter's price then)", Math.abs(L.last / pexp - 1) < 0.02, {last: L.last, jupiter: pexp});
  // one trade moves both vaults in the same read: one tick, volume = the quote change
  const k1 = L.ticks;
  acc[F.pool.quote_vault.address].data[0] = patch(F.pool.quote_vault.data, [[64, 2e9]]);
  acc[F.pool.base_vault.address].data[0] = patch(F.pool.base_vault.data, [[64, -2300000000000]]);
  await sleep(1300); L = await live();
  check(tag + "pool: a trade (both vaults) is one tick, volume = 2 SOL in USD", L.ticks === k1 + 1 && Math.abs(L.volume - 2 * F.sol_usd_jupiter) < 0.01, {ticks: [k1, L.ticks], volume: L.volume});
  // the quote vault answers with a base-mint account: not the pool's vault, so a fallback with the reason
  const savedQ = acc[F.pool.quote_vault.address]; acc[F.pool.quote_vault.address] = acc[F.pool.base_vault.address]; await sleep(1300);
  L = await live();
  check(tag + "pool: a vault with the wrong mint is refused (fallback, with the reason)", L.mode === "poll" && /vaults/.test(L.why), {mode: L.mode, why: L.why});
  acc[F.pool.quote_vault.address] = savedQ;

  // ---- the desk cannot reach the RPC: amber, backoff, then live again
  await pick("CURVE"); await sleep(1500);
  mode = "down"; const td = Date.now(); await sleep(7500);
  const tries = chainCalls.filter(c => c.t > td).map(c => c.t), gapsDown = tries.map((t, i, a) => i ? t - a[i - 1] : t - td);
  L = await live();
  check(tag + "RPC down: status reconnecting, retries back off", L.status === "reconnecting" && tries.length <= 4 && (gapsDown.length < 3 || gapsDown[2] > gapsDown[1]), {status: L.status, gaps: gapsDown});
  mode = "ok"; await sleep(17000); L = await live();
  check(tag + "after the backoff it is live again", L.status === "live" && L.mode === "chain");

  // ---- hidden page: nothing is read; visible again: reads resume
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "hidden"}); document.dispatchEvent(new Event("visibilitychange")); });
  const th = Date.now(); await sleep(3500);
  L = await live();
  check(tag + "page hidden: no reads, status stopped", chainCalls.filter(c => c.t > th + 50).length === 0 && L.status === "stopped" && !L.polling, {status: L.status});
  await page.evaluate(() => { Object.defineProperty(document, "visibilityState", {configurable: true, get: () => "visible"}); document.dispatchEvent(new Event("visibilitychange")); });
  await sleep(2200);
  check(tag + "visible again: reads resume for the same coin", chainCalls.filter(c => c.t > th + 3500).length >= 1 && (await live()).status === "live");

  // ---- another venue: DexScreener fallback, price only and delayed, the desk not asked
  const n0 = chainCalls.length; await pick("RAY"); await sleep(2600); L = await live();
  check(tag + "Raydium: DexScreener fallback, labelled price only · delayed, no on-chain reads", L.mode === "poll" && chainCalls.length === n0 && ds.length >= 1 &&
        /^live · price only · delayed · live since/.test(await page.$eval("#t-livelbl", e => e.textContent)), {mode: L.mode, label: await page.$eval("#t-livelbl", e => e.textContent)});
  check(tag + "nothing from GeckoTerminal, nothing to Solana from the page, no console errors", gecko.length === 0 && direct.length === 0 && errors.length === 0, {gecko, direct, errors});
  await page.unrouteAll({behavior: "ignoreErrors"}); await ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const s of [[1280, 720, 1], [390, 844, 2]]) await run(browser, s); } finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
