// Fit checks for the Terminal tab at the user's real Chrome window and common sizes, run by hand:
//   DESK_DATA=<a COPY of data/> python -m desk --demo --port 8799 --no-browser
//   NODE_PATH=<folder with playwright-core> node tests/e2e/terminal-fit.e2e.js [http://127.0.0.1:8799]
// Read-only: the coin list comes from the desk (a copy of the live data), candles from a fixture (page.route), and
// the failed-checks line is never ticked. 960x503 at devicePixelRatio 1.75 is the user's Chrome as measured on 2026-11-23.
const { chromium } = require("playwright-core");
const BASE = process.argv[2] || "http://127.0.0.1:8799";
const SIZES = [[960, 503, 1.75, "user's Chrome"], [1527, 801, 1.75, "user's Chrome, wider"], [1280, 720, 1], [1920, 1080, 1], [390, 844, 2]];
const results = [];
const check = (name, ok, detail) => { results.push({name, ok: !!ok}); console.log((ok ? "PASS " : "FAIL ") + name + (detail !== undefined ? "  " + JSON.stringify(detail) : "")); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const bars = Array.from({length: 80}, (_, i) => ({time: 1795000000 + i * 300, open: 1e-5 * (1 + i / 100), high: 1.05e-5 * (1 + i / 100), low: 0.95e-5 * (1 + i / 100), close: 1.01e-5 * (1 + i / 100), volume: 100 + i}));

// what every layout must hold: no sideways scroll, nothing wider than its box, the chart filling its pane
const MEASURE = () => {
  const r = e => { const b = (typeof e === "string" ? document.querySelector(e) : e).getBoundingClientRect(); return {l: b.left, t: b.top, r: b.right, b: b.bottom, w: b.width, h: b.height}; };
  const de = document.documentElement, cbox = r(".tx-cbox"), canv = [...document.querySelectorAll("#t-chart canvas")].map(c => c.getBoundingClientRect());
  // text inside the failed-checks summary is clipped by its own ellipsis on purpose (the full list is one click away)
  const vis = [...document.querySelectorAll("#v-terminal *")].filter(e => e.getClientRects().length && !e.closest(".vh") && !e.classList.contains("vh") && !e.closest("#t-warnline summary"));
  return {
    inner: [innerWidth, innerHeight], bannerh: document.querySelector(".real-banner").getBoundingClientRect().height,
    sideways: de.scrollWidth - de.clientWidth,
    outside: vis.filter(e => { const b = e.getBoundingClientRect(); return b.width && (b.right > de.clientWidth + 0.5 || b.left < -0.5); }).length,
    // text cut with an ellipsis is shortened on purpose (with its full text in a title); anything else cut off is a spill
    spills: vis.filter(e => { const cs = getComputedStyle(e); return /hidden|clip/.test(cs.overflowX) && cs.textOverflow !== "ellipsis" && e.scrollWidth > e.clientWidth + 1 && !e.closest("#t-chart"); })
      .map(e => (e.id || e.className) + " " + e.clientWidth + "<" + e.scrollWidth).slice(0, 5),
    chartFill: canv.length ? {w: Math.round(Math.max(...canv.map(c => c.right)) - cbox.l), boxW: Math.round(cbox.w), h: Math.round(Math.max(...canv.map(c => c.bottom)) - cbox.t), boxH: Math.round(cbox.h)} : null,
    tx: r("#tx"), list: r(".tx-list"), chart: r(".tx-chart"), side: r(".tx-side"), swap: r(".tx-swap"), bar: document.querySelector(".tx-bar").getClientRects().length ? r(".tx-bar") : null,
    ack: document.querySelector("#t-warnline summary") ? r("#t-warnline summary") : document.querySelector("#t-gate .callout") ? r("#t-gate .callout") : null, cols: getComputedStyle(document.querySelector("#tx")).gridTemplateColumns};
};

async function run(browser, [w, h, dpr, note]) {
  const tag = `${w}x${h}@${dpr}${note ? " (" + note + ")" : ""} `;
  const ctx = await browser.newContext({viewport: {width: w, height: h}, deviceScaleFactor: dpr});
  const page = await ctx.newPage(), errors = [], bad = [];
  page.on("console", m => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", e => errors.push("pageerror: " + e.message));
  page.on("response", r => { if (r.status() >= 400) bad.push(r.status() + " " + new URL(r.url()).pathname); });
  await page.route("https://plugin.jup.ag/**", route => route.fulfill({contentType: "text/javascript", body: "window.Jupiter={init:function(o){document.getElementById(o.integratedTargetId).textContent='JUPITER STUB'},close:function(){}};"}));
  await page.route("https://api.mainnet-beta.solana.com/", route => { const b = JSON.parse(route.request().postData()); route.fulfill({json: {jsonrpc: "2.0", id: b.id, result: {context: {slot: 1}, value: b.params[0].map(() => null)}}}); });   // on-chain: unreadable, so the DexScreener stand-in
  await page.route("https://lite-api.jup.ag/**", route => route.fulfill({json: {So11111111111111111111111111111111111111112: {usdPrice: 72.8}}}));
  await page.route("https://api.dexscreener.com/**", route => route.fulfill({json: {pairs: []}}));
  await page.route("**/api/meme/chart?*", route => route.fulfill({json: {bars, levels: [], trades: [], symbol: "X", url: "https://dexscreener.com/solana/x"}}));
  await page.goto(BASE + "/"); await page.waitForSelector(".tabs button");
  await page.click('[data-v="terminal"]'); await page.waitForSelector("#t-list .wl-row"); await sleep(1500);
  // open a coin that shows the failed-checks line (never ticked)
  const key = await page.evaluate(() => { const r = [...document.querySelectorAll("#t-list .wl-row")]; return r.length ? r[0].dataset.tkey : null; });
  await page.evaluate(k => document.querySelector(`#t-list .wl-row[data-tkey="${CSS.escape(k)}"]`).click(), key); await sleep(1200);
  let phone = w < 900, width = w;
  const toTop = () => page.evaluate(() => { const t = document.querySelector("#tx"), bh = document.querySelector(".real-banner").getBoundingClientRect().height; scrollTo(0, t.getBoundingClientRect().top + scrollY - bh - 8); });

  const verify = async label => {
    width = await page.evaluate(() => innerWidth); phone = width < 900;            // the layout follows the window as it is now
    if (!phone) { await toTop(); await sleep(300); }
    const m = await page.evaluate(MEASURE);
    check(tag + label + "no sideways scroll, nothing past the window's edge", m.sideways <= 0 && m.outside === 0, {sideways: m.sideways, outside: m.outside});
    check(tag + label + "no text spills out of its box (rows, readout, panes)", m.spills.length === 0, m.spills);
    if (m.chartFill) check(tag + label + "the chart fills its pane", Math.abs(m.chartFill.w - m.chartFill.boxW) <= 2 && Math.abs(m.chartFill.h - m.chartFill.boxH) <= 2, m.chartFill);
    if (!phone) {
      const want = Math.max(420, m.inner[1] - m.bannerh - 16);
      // 1200 px and up: the three columns are the window's height (the chart shares its column with the trades tabs);
      // 900 to 1199 px: watchlist and chart side by side are the window's height
      const full = width >= 1200 ? [m.list, m.side] : [m.list, m.chart];
      check(tag + label + "the terminal fills the window under the banner, columns full height", full.every(p => Math.abs(p.h - want) <= 2 && p.t >= m.bannerh - 1 && p.b <= m.inner[1] + 1) &&
            (width >= 1200 ? Math.abs(m.tx.h - want) <= 2 && m.chart.b < m.list.b : Math.abs(m.list.h - m.chart.h) <= 1),
            {want: Math.round(want), list: [Math.round(m.list.t), Math.round(m.list.b)], chart: [Math.round(m.chart.t), Math.round(m.chart.b)], side: [Math.round(m.side.t), Math.round(m.side.b)], cols: m.cols});
      if (width >= 1200) {
        // the right column scrolls as a whole: token details at their natural height (no inner scroll), the swap card below
        const tok = await page.$eval("#t-token", e => ({h: e.clientHeight, sh: e.scrollHeight, cs: getComputedStyle(e).overflowY}));
        await page.$eval(".tx-side", e => { e.scrollTop = 0; });
        await page.$eval(".tx-side", e => { const sw = e.querySelector(".tx-swap"); e.scrollTop = sw.offsetTop - 8; }); await sleep(200);
        const s = await page.evaluate(MEASURE);
        check(tag + label + "right column: token details at full height, column scrolls, swap card and its line reachable in view",
              tok.sh <= tok.h + 1 && tok.h > 150 && s.ack && s.ack.t >= s.side.t - 1 && s.ack.b <= s.side.b + 1 && s.ack.b <= s.inner[1] + 1,
              {token: tok, side: [Math.round(s.side.t), Math.round(s.side.b)], line: s.ack && [Math.round(s.ack.t), Math.round(s.ack.b)]});
        await page.$eval(".tx-side", e => { e.scrollTop = 0; });
      }
      else { await page.$eval(".tx-swap", e => e.scrollIntoView({block: "center"})); await sleep(200); const s = await page.evaluate(MEASURE);
        check(tag + label + "swap card (below the chart here) is whole and its failed-checks line reachable", s.ack && s.ack.b <= s.inner[1] && s.ack.t >= s.bannerh && s.swap.w >= 260, {swap: [Math.round(s.swap.t), Math.round(s.swap.b), Math.round(s.swap.w)], ack: s.ack && Math.round(s.ack.t)}); }
    } else {
      for (const p of ["list", "chart", "swap"]) {
        await page.click(`#tx-bar [data-tpane="${p}"]`); await sleep(500);
        await page.$eval("#tx", e => e.scrollIntoView({block: "start"})); await page.evaluate(() => scrollBy(0, -document.querySelector(".real-banner").getBoundingClientRect().height - 8)); await sleep(300);
        const s = await page.evaluate(MEASURE), pane = p === "list" ? s.list : p === "chart" ? s.chart : s.side;
        const fits = p === "swap" ? s.ack && s.ack.b <= s.bar.t : pane.b <= s.bar.t + 1 && pane.t >= s.bannerh - 1;
        check(tag + label + `phone ${p} pane fits between the banner and the bottom bars`, fits && s.sideways <= 0, {pane: [Math.round(pane.t), Math.round(pane.b)], bar: Math.round(s.bar.t), ack: s.ack && Math.round(s.ack.b)});
        if (p === "chart" && s.chartFill) check(tag + label + "phone chart fills its pane", Math.abs(s.chartFill.w - s.chartFill.boxW) <= 2 && Math.abs(s.chartFill.h - s.chartFill.boxH) <= 2, s.chartFill);
      }
    }
  };
  await verify("");
  // the window changes size: the layout and the chart follow
  await page.setViewportSize({width: Math.max(360, w - 170), height: Math.max(480, h - 90)}); await sleep(700);
  await verify("resized smaller: ");
  await page.setViewportSize({width: w, height: h}); await sleep(700);
  await verify("resized back: ");
  check(tag + "no console errors besides the favicon 404", errors.filter(e => !/Failed to load resource.*404/.test(e)).length === 0 && bad.filter(x => !x.endsWith("/favicon.ico")).length === 0, {errors, bad});
  await page.screenshot({path: (process.env.SHOTS || ".") + `/fit-${w}x${h}.png`});
  await page.unrouteAll({behavior: "ignoreErrors"}); await ctx.close();
}

(async () => {
  const browser = await chromium.launch({channel: "chrome"});
  try { for (const s of SIZES) await run(browser, s); } finally { await browser.close(); }
  const fails = results.filter(r => !r.ok);
  console.log(`\n${results.length - fails.length} passed, ${fails.length} failed`);
  process.exit(fails.length ? 1 : 0);
})();
