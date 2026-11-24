// Wheel and "Swap ↓" checks for the Terminal tab, run by hand against a demo desk:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal-wheel.e2e.js [http://127.0.0.1:8799]
// Read-only: candles come from a fixture (page.route) and the checkbox is never ticked, only focused.
const { chromium } = require("playwright-core");
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const SIZES = [[960, 503, 1.75, "user's Chrome"], [1280, 720, 1]];
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok}); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const bars = Array.from({length: 120}, (_, i) => ({time: 1795000000 + i * 300, open: 1e-5 * (1 + i / 90), high: 1.05e-5 * (1 + i / 90), low: 0.95e-5 * (1 + i / 90), close: 1.01e-5 * (1 + i / 90), volume: 100 + i}));

async function run(browser, [w, h, dpr, note]) {
  const tag = `${w}x${h}@${dpr}${note ? " (" + note + ")" : ""} `;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, deviceScaleFactor: dpr});
  const page = await ctx.newPage(), errors = [];
  page.on("console", m => { if (m.type() === "error" && !/Failed to load resource.*(404|400)/.test(m.text())) errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  await page.route("**/api/meme/chart?*", route => route.fulfill({json: {bars, levels: [], trades: [], symbol: "X", url: "https://dexscreener.com/solana/x"}}));
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript", body: "window.Jupiter={init:function(o){document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){}};"}));
  await page.route("**/api/meme/chain?*", route => route.fulfill({status: 400, json: {error: "not in this test"}}));   // on-chain reads: none in this suite (the DexScreener stand-in instead)
  await page.route("https://lite-api.jup.ag/**", route => route.fulfill({json: {So11111111111111111111111111111111111111112: {usdPrice: 72.8}}}));
  await page.route("https://api.dexscreener.com/**", route => route.fulfill({json: {pairs: []}}));
  await page.goto(BASE + "/"); await page.waitForSelector(".tabs button");
  await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(1500);
  const toTerminal = () => page.evaluate(() => { const t = document.querySelector("#tx"), bh = document.querySelector(".real-banner").getBoundingClientRect().height; scrollTo(0, t.getBoundingClientRect().top + scrollY - bh - 8); });
  const range = () => page.evaluate(() => { const r = TERMINAL.inspect().range; return r && +(r.to - r.from).toFixed(2); });
  const center = sel => page.$eval(sel, e => { const b = e.getBoundingClientRect(); return [b.left + b.width / 2, b.top + b.height / 2]; });

  // a plain wheel over the chart scrolls the page and does not zoom
  await toTerminal(); await sleep(300);
  const [cx, cy] = await center("#t-chart");
  await page.mouse.move(cx, cy);
  const span0 = await range(), y0 = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, 240); await sleep(500);
  const span1 = await range(), y1 = await page.evaluate(() => scrollY);
  check(tag + "plain wheel over the chart scrolls the page and does not zoom", y1 > y0 + 50 && span1 === span0, {scrollY: [y0, y1], visibleBars: [span0, span1]});

  // Ctrl+wheel over the chart zooms and does not scroll the page (or zoom the browser)
  await toTerminal(); await sleep(300);
  const [cx2, cy2] = await center("#t-chart"); await page.mouse.move(cx2, cy2);
  const s0 = await range(), z0 = await page.evaluate(() => scrollY), pz0 = await page.evaluate(() => devicePixelRatio);
  await page.keyboard.down("Control"); await page.mouse.wheel(0, -240); await page.keyboard.up("Control"); await sleep(400);
  const s1 = await range(), z1 = await page.evaluate(() => scrollY), pz1 = await page.evaluate(() => devicePixelRatio);
  await page.keyboard.down("Control"); await page.mouse.wheel(0, 240); await page.keyboard.up("Control"); await sleep(400);
  const s2 = await range();
  check(tag + "Ctrl+wheel zooms the chart in and out, page and browser zoom unchanged", s1 < s0 && s2 > s1 && Math.abs(z1 - z0) < 1 && pz0 === pz1, {visibleBars: [s0, s1, s2], scrollY: [z0, z1], dpr: [pz0, pz1]});

  // drag still pans
  const r0 = await page.evaluate(() => TERMINAL.inspect().range.from);
  await page.mouse.move(cx2, cy2); await page.mouse.down(); await page.mouse.move(cx2 + 120, cy2, {steps: 6}); await page.mouse.up(); await sleep(300);
  const r1 = await page.evaluate(() => TERMINAL.inspect().range.from);
  check(tag + "drag still pans the chart", r1 < r0 - 1, {from: [r0, r1]});

  // the watchlist: the wheel scrolls the list first; at its bottom it moves the page; at its top, back up
  await toTerminal(); await sleep(300);
  const [lx, ly] = await center("#t-list"); await page.mouse.move(lx, ly);
  const lt0 = await page.$eval("#t-list", l => l.scrollTop), py0 = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, 200); await sleep(400);
  const lt1 = await page.$eval("#t-list", l => l.scrollTop), py1 = await page.evaluate(() => scrollY);
  check(tag + "wheel over the list scrolls the list first, not the page", lt1 > lt0 && Math.abs(py1 - py0) < 1, {listTop: [lt0, lt1], scrollY: [py0, py1]});
  await page.$eval("#t-list", l => { l.scrollTop = l.scrollHeight; }); await sleep(300);
  const pyB = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, 240); await sleep(600);
  const pyC = await page.evaluate(() => scrollY);
  check(tag + "at the bottom of the list the wheel scrolls the page", pyC > pyB + 50, {scrollY: [pyB, pyC]});
  await toTerminal(); await page.$eval("#t-list", l => { l.scrollTop = 0; }); await page.evaluate(() => scrollBy(0, 120)); await sleep(300);
  const [lx2, ly2] = await center("#t-list"); await page.mouse.move(lx2, ly2);
  const pyD = await page.evaluate(() => scrollY);
  await page.mouse.wheel(0, -200); await sleep(600);
  const pyE = await page.evaluate(() => scrollY);
  check(tag + "at the top of the list the wheel scrolls the page up", pyE < pyD - 50, {scrollY: [pyD, pyE]});

  // "Swap ↓": shown from 900 to 1199 px; brings the swap card's checkbox into view and focuses it
  await toTerminal(); await sleep(300);
  const shown = await page.$eval("#t-jump", b => getComputedStyle(b).display !== "none" && b.getClientRects().length > 0);
  if (w >= 900 && w < 1200) {
    const before = await page.evaluate(() => { const a = document.querySelector("#t-warnline summary"); return a && a.getBoundingClientRect().top < innerHeight; });
    await page.click("#t-jump"); await sleep(1200);
    const after = await page.evaluate(() => { const a = document.querySelector("#t-warnline summary"), bh = document.querySelector(".real-banner").getBoundingClientRect().height, b = a.getBoundingClientRect();
      return {top: Math.round(b.top), bottom: Math.round(b.bottom), inView: b.top >= bh && b.bottom <= innerHeight, focused: document.activeElement === a, checked: false}; });
    check(tag + "Swap ↓ is shown and brings the swap card's failed-checks line into view, focused", shown && before === false && after.inView && after.focused && !after.checked, {shown, before, after});
  } else {
    const inView = await page.evaluate(() => { const a = document.querySelector("#t-warnline summary"); return a && a.getBoundingClientRect().bottom <= innerHeight; });
    check(tag + "Swap ↓ is hidden here, and the swap card is already in view in the right column", !shown && inView, {shown, inView});
  }
  check(tag + "no console errors besides the favicon 404", errors.length === 0, errors);
  await page.unrouteAll({behavior: "ignoreErrors"}); await ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const s of SIZES) await run(browser, s); } finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
