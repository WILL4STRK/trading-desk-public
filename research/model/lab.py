#!/usr/bin/env python3
"""Model lab: long/short ETF strategies against SPY, blind day by day, with a sealed holdout.

Why daily ETFs: the intraday backtest (research/backtest) only had 39 free sessions and lost
to SPY. Daily total-return bars go back to 1999, cover the 2000-02, 2008 and 2020 crashes
(where shorting matters) and ETFs cannot vanish from the sample the way failed stocks do.

Blind: each morning the strategy sees a View of closes up to yesterday only and sets target
weights (negative = short); orders fill at today's open. The development run never loads a
bar after DEV_END, so nothing tuned here has seen the holdout.

  python3 research/model/lab.py                 # development: every config, 2000-2020
  python3 research/model/lab.py --seal A,B,C    # freeze the finalists (after reading dev results)
  python3 research/model/lab.py --final         # opens 2021..yesterday once, writes report.html
"""
import argparse
import datetime as dt
import hashlib
import html
import json
import math
import statistics
import sys
from pathlib import Path

sys.dont_write_bytecode = True
HOME = Path(__file__).resolve().parent
sys.path.insert(0, str(HOME))
sys.path.insert(0, str(HOME.parent / 'backtest'))
import history as D                                 # noqa: E402
from backtest import bootstrap, drawdowns, metrics  # noqa: E402  (same maths as the intraday study)
from report import chart, table, markdown_table, fmt  # noqa: E402

OUT = HOME / 'out'
DEV_START, DEV_END = '2000-01-03', '2020-12-31'
TRADE = [s for s in D.SYMBOLS if not s.startswith('^')]
SECTORS = ['XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLI', 'XLB', 'XLU']
COST_BPS = 3.0           # spread + slippage per side, per unit of turnover (liquid ETFs: 1-3 bps)
BORROW = 0.01            # yearly fee on short value (liquid ETFs usually 0.25-1%)
MARGIN_SPREAD = 0.05     # margin loan rate = T-bill + 5%/yr (retail brokers charge roughly this or more)
TOL = 0.03               # skip a rebalance whose weights are all within 3 points of what is held
INITIAL = 400.0


# ---------- data and the point-in-time view ----------

def load(last_date):
    """Calendar (SPY trading days) up to last_date inclusive, and {sym: (opens, closes)} aligned to it."""
    cal = [b['date'] for b in D.bars('SPY') if b['date'] <= last_date]
    idx = {d: i for i, d in enumerate(cal)}
    series = {}
    for s in D.SYMBOLS:
        o, c = [None] * len(cal), [None] * len(cal)
        for b in D.bars(s):
            i = idx.get(b['date'])
            if i is not None:
                o[i], c[i] = b['o'], b['c']
        last = None
        for i in range(len(cal)):           # a missing day after listing: flat, no return
            if c[i] is None and last is not None:
                o[i] = c[i] = last
            last = c[i] if c[i] is not None else last
        series[s] = (o, c)
    return cal, series


class Market:
    """Trailing indicators, computed once per (symbol, length). Value at i uses closes 0..i only."""

    def __init__(self, cal, series):
        self.cal, self.series, self._memo = cal, series, {}

    def _memo_get(self, key, build):
        if key not in self._memo:
            self._memo[key] = build()
        return self._memo[key]

    def closes(self, s):
        return self.series[s][1]

    def sma(self, s, n):
        def build():
            c, out, run, cnt = self.closes(s), [None] * len(self.cal), 0.0, 0
            for i, x in enumerate(c):
                if x is None:
                    run, cnt = 0.0, 0
                    continue
                run, cnt = run + x, cnt + 1
                if cnt > n:
                    run -= c[i - n]
                    cnt = n
                if cnt == n:
                    out[i] = run / n
            return out
        return self._memo_get(('sma', s, n), build)

    def ret(self, s, n):
        def build():
            c = self.closes(s)
            return [c[i] / c[i - n] - 1 if i >= n and c[i] and c[i - n] else None for i in range(len(c))]
        return self._memo_get(('ret', s, n), build)

    def vol(self, s, n):
        """Annualised stdev of the last n daily log returns."""
        def build():
            c, out = self.closes(s), [None] * len(self.cal)
            lr = [math.log(c[i] / c[i - 1]) if i and c[i] and c[i - 1] else None for i in range(len(c))]
            for i in range(n, len(c)):
                w = lr[i - n + 1:i + 1]
                if None not in w:
                    out[i] = statistics.pstdev(w) * math.sqrt(252)
            return out
        return self._memo_get(('vol', s, n), build)

    def rsi(self, s, n):
        """Wilder RSI over n days."""
        def build():
            c, out = self.closes(s), [None] * len(self.cal)
            up = dn = None
            for i in range(1, len(c)):
                if c[i] is None or c[i - 1] is None:
                    continue
                g, l = max(c[i] - c[i - 1], 0), max(c[i - 1] - c[i], 0)
                up = g if up is None else (up * (n - 1) + g) / n
                dn = l if dn is None else (dn * (n - 1) + l) / n
                out[i] = 100.0 if dn == 0 else 100 - 100 / (1 + up / dn)
            return out
        return self._memo_get(('rsi', s, n), build)


class View:
    """What a strategy may know on the morning of day i+1: everything up to the close of day i."""

    def __init__(self, m, i):
        self._m, self.i = m, i
        self.date = m.cal[i]

    def _at(self, arr, back=0):
        assert back >= 0, 'look-ahead: a View cannot read a later day'
        j = self.i - back
        return arr[j] if j >= 0 else None

    def close(self, s, back=0):
        return self._at(self._m.closes(s), back)

    def sma(self, s, n):
        return self._at(self._m.sma(s, n))

    def ret(self, s, n):
        return self._at(self._m.ret(s, n))

    def vol(self, s, n):
        return self._at(self._m.vol(s, n))

    def rsi(self, s, n):
        return self._at(self._m.rsi(s, n))

    def listed(self, s, n=1):
        return self.close(s, n - 1) is not None

    def new_month(self):
        """True on the first trading day of a month (known from yesterday's and today's dates only)."""
        return self.i == 0 or self._m.cal[self.i][:7] != self._m.cal[self.i - 1][:7]


# ---------- strategies: f(view, params, held) -> {sym: weight} or None (keep what is held) ----------

def bull(v, s='SPY', n=200):
    m = v.sma(s, n)
    return m is not None and v.close(s) > m


def f_hold(v, p, held):
    return None if held else {p['a']: 1.0}


def f_trend(v, p, held):
    a, m = p['a'], v.sma(p['a'], p['n'])
    if m is None:
        return {}
    c, band = v.close(a), p.get('band', 0)
    if c > m * (1 + band) or (held.get(a, 0) > 0 and c > m * (1 - band)):
        return {a: p.get('lev', 1.0)}
    return off(v, p, a)


def safe(v, L=126):
    """Best of IEF/TLT/GLD when it beat short Treasuries (SHY) over L days, else SHY: in 2022 bonds fell with stocks."""
    hurdle = v.ret('SHY', L)
    if hurdle is None:
        return {}
    c = [s for s in ('IEF', 'TLT', 'GLD') if v.ret(s, L) is not None and v.ret(s, L) > hurdle]
    return {max(c, key=lambda s: v.ret(s, L)): 1.0} if c else {'SHY': 1.0}


def off(v, p, a):
    o = p.get('off', 'cash')
    if o == 'safe':
        return safe(v)
    if o == 'short':
        return {a: -p.get('short_w', 1.0)}
    if o in TRADE:
        return {o: 1.0} if v.listed(o) else {}
    return {}


def f_dual(v, p, held):
    if held and not v.new_month():
        return None
    L = p['L']
    risk = [s for s in p['risk'] if v.ret(s, L) is not None]
    if not risk:
        return {}
    best = max(risk, key=lambda s: v.ret(s, L))
    hurdle = v.ret('SHY', L) if v.ret('SHY', L) is not None else 0.0
    if v.ret(best, L) > hurdle:
        return {best: p.get('lev', 1.0)}
    if p['off'] == 'short':
        return {'SPY': -1.0}
    if p['off'] == 'safe':
        return safe(v, L)
    if p['off'] == 'bond':
        cands = [s for s in ('IEF', 'TLT', 'GLD') if v.ret(s, L) is not None]
        return {max(cands, key=lambda s: v.ret(s, L)): 1.0} if cands else {}
    return {}


def f_sector(v, p, held):
    if held and not v.new_month():
        return None
    L, k = p['L'], p['k']
    ranked = sorted((s for s in SECTORS if v.ret(s, L) is not None), key=lambda s: -v.ret(s, L))
    if len(ranked) < 2 * k:
        return {}
    w = {}
    if p['regime'] is None or bull(v, 'SPY', p['regime']):
        w = {s: p.get('lev', 1.0) / k for s in ranked[:k]}
        for s in ranked[-k:] if p.get('hedge') else []:
            w[s] = -p['hedge'] / k
        return w
    o = p['off']
    if o == 'short_bottom':
        return {s: -1.0 / k for s in ranked[-k:]}
    if o == 'short_spy':
        return {'SPY': -1.0}
    if o == 'safe':
        return safe(v, L)
    if o == 'bond':
        cands = [s for s in ('IEF', 'TLT') if v.ret(s, L) is not None]
        return {max(cands, key=lambda s: v.ret(s, L)): 1.0} if cands else {}
    return {}


def f_vol(v, p, held):
    a, rv = p['a'], v.vol(p['a'], p['vn'])
    if rv is None or rv <= 0:
        return {}
    w = min(p['cap'], p['tv'] / rv)
    if p['trend'] != 'none' and not bull(v, a, 200):
        if p['trend'] == 'safe':
            return safe(v) if v.new_month() or not held else None
        return {a: -round(min(1.0, w), 2)} if p['trend'] == 'short' else {}
    return {a: round(w, 2)}


def f_rsi(v, p, held):
    """Trend base plus short-term mean reversion: buy dips in bull markets, short rips in bear ones."""
    a = 'SPY'
    r, s5, c = v.rsi(a, 2), v.sma(a, 5), v.close(a)
    if r is None or s5 is None or v.sma(a, 200) is None:
        return {}
    w = _rsi_weights(v, p, held, a, r, s5, c)
    rv = v.vol(a, 20) if p.get('tv') else None
    if w and rv:                         # round 3: size by recent volatility, capped
        k = min(p['cap'], p['tv'] / rv)
        w = {s: round(x * k, 2) for s, x in w.items()}
    return w


def _rsi_weights(v, p, held, a, r, s5, c):
    h = held.get(a, 0)
    if bull(v, a, 200):
        # held weights drift with prices, so "in a dip trade" = above the midpoint of base and dip size
        in_dip = h > max((p['base'] + p['dip_w']) / 2, p['base'] + 1e-9)
        if p.get('tv'):                  # vol-scaled sizes hide the state: stay in while weak and under the 5-day average
            in_dip = h > 0 and r < 50
        if (in_dip and c <= s5) or r < p['lo']:
            return {a: p['dip_w']}
        return {a: p['base']} if p['base'] else {}
    in_short = h < -1e-9
    if p['short'] and ((in_short and c >= s5) or r > 100 - p['lo']):
        return {a: -1.0}
    return {}


FAMILIES = {'hold': f_hold, 'trend': f_trend, 'dual': f_dual, 'sector': f_sector, 'vol': f_vol, 'rsi': f_rsi}


def configs():
    """Every config tried, declared before any result is seen. The count feeds the multiple-testing haircut."""
    out = [('hold', {'a': 'SPY'}), ('hold', {'a': 'QQQ'})]
    for a in ('SPY', 'QQQ'):
        for n in (50, 100, 150, 200, 250):
            for o in ('cash', 'short', 'IEF', 'TLT', 'SH' if a == 'SPY' else 'PSQ'):
                for band in (0, 0.02):
                    out.append(('trend', {'a': a, 'n': n, 'off': o, 'band': band}))
            out.append(('trend', {'a': a, 'n': n, 'off': 'cash', 'band': 0.02, 'lev': 1.5}))
    for risk in (['SPY'], ['SPY', 'QQQ'], ['SPY', 'QQQ', 'IWM', 'MDY']):
        for L in (63, 126, 252):
            for o in ('bond', 'short', 'cash'):
                out.append(('dual', {'risk': risk, 'L': L, 'off': o}))
            out.append(('dual', {'risk': risk, 'L': L, 'off': 'bond', 'lev': 1.5}))
    for L in (63, 126, 252):
        for k in (1, 2, 3):
            out.append(('sector', {'L': L, 'k': k, 'regime': None, 'off': 'cash'}))
            for o in ('cash', 'short_bottom', 'short_spy', 'bond'):
                out.append(('sector', {'L': L, 'k': k, 'regime': 200, 'off': o}))
        for k in (2, 3):
            out.append(('sector', {'L': L, 'k': k, 'regime': 200, 'off': 'short_bottom', 'hedge': 0.5}))
    for a in ('SPY', 'QQQ'):
        for tv in (0.10, 0.15, 0.20):
            for cap in (1.0, 1.5, 2.0):
                for t in ('none', 'cash', 'short'):
                    out.append(('vol', {'a': a, 'vn': 20, 'tv': tv, 'cap': cap, 'trend': t}))
    for lo in (5, 10, 20):
        for dip in (1.0, 1.5):
            for base in (0.0, 1.0):
                for short in (False, True):
                    out.append(('rsi', {'lo': lo, 'dip_w': dip, 'base': base, 'short': short}))
    # round 2 (added after reading round-1 dev results; counted as trials all the same)
    for a in ('SPY', 'QQQ'):
        for n in (150, 200, 250):
            for lev in (1.0, 1.25, 1.5):
                out.append(('trend', {'a': a, 'n': n, 'off': 'safe', 'band': 0.02, 'lev': lev}))
    for risk in (['SPY', 'QQQ'], ['SPY', 'QQQ', 'IWM', 'MDY']):
        for L in (63, 126, 252):
            for lev in (1.0, 1.25):
                out.append(('dual', {'risk': risk, 'L': L, 'off': 'safe', 'lev': lev}))
    for L in (63, 126):
        for k in (2, 3):
            out.append(('sector', {'L': L, 'k': k, 'regime': 200, 'off': 'safe'}))
    for a in ('SPY', 'QQQ'):
        for tv in (0.15, 0.20):
            for cap in (1.0, 1.5):
                out.append(('vol', {'a': a, 'vn': 20, 'tv': tv, 'cap': cap, 'trend': 'safe'}))
    core = ['trend a=SPY band=0.02 lev=1.0 n=200 off=safe', 'dual L=126 lev=1.0 off=safe risk=SPY+QQQ+IWM+MDY',
            'sector L=126 k=2 off=safe regime=200']
    plus = core + ['vol a=QQQ cap=1.0 trend=safe tv=0.2 vn=20']
    for parts in (core, plus):
        for lev in (1.0, 1.25, 1.5):
            out.append(('blend', {'parts': parts, 'lev': lev}))
    out.append(('blend', {'parts': plus + ['rsi base=1.0 dip_w=1.0 lo=10 short=True'], 'lev': 1.0}))
    # round 3 (after reading the opened holdout; judged only on dev, then on fresh markets): lever the dip/rip model
    for base, dip in ((1.0, 2.0), (1.25, 1.5), (1.25, 2.0), (1.5, 2.0)):
        out.append(('rsi', {'lo': 10, 'dip_w': dip, 'base': base, 'short': True}))
    for lo in (5, 20):
        out.append(('rsi', {'lo': lo, 'dip_w': 2.0, 'base': 1.25, 'short': True}))
    for tv in (0.15, 0.20):
        for cap in (1.5, 2.0):
            out.append(('rsi', {'lo': 10, 'dip_w': 1.5, 'base': 1.0, 'short': True, 'tv': tv, 'cap': cap}))
    return out


def name(cfg):
    fam, p = cfg
    if fam == 'blend':
        return 'blend %d parts%s lev=%s' % (len(p['parts']), '+vol' if any(x.startswith('vol') for x in p['parts']) else '',
                                           p['lev']) + (' +rsi' if any(x.startswith('rsi') for x in p['parts']) else '')
    return fam + ' ' + ' '.join('%s=%s' % (k, '+'.join(x) if isinstance(x, list) else x) for k, x in sorted(p.items()))


def needs_margin(cfg):
    p = cfg[1]
    if cfg[0] == 'blend':
        by = {name(c): c for c in configs() if c[0] != 'blend'}
        return p['lev'] > 1 or any(needs_margin(by[n]) for n in p['parts'])
    return p.get('lev', 1) > 1 or p.get('cap', 1) > 1 or p.get('dip_w', 1) > 1 or p.get('off') in ('short', 'short_bottom', 'short_spy') \
        or p.get('trend') == 'short' or p.get('short') or p.get('hedge')


# ---------- the simulator ----------

def simulate(m, cfg, start, end, cost_mult=1.0, borrow=BORROW, initial=INITIAL, spread=MARGIN_SPREAD):
    """Day d: hold yesterday's weights overnight (close d-1 -> open d), rebalance at the open to the
    weights the strategy set from View(d-1), hold them to the close. Weights drift with prices."""
    if cfg[0] == 'blend':
        return blend(m, cfg[1], start, end, cost_mult, borrow, initial)  # shortcut: blends keep the default spread
    f, p = FAMILIES[cfg[0]], cfg[1]
    o_irx = m.closes('^IRX')
    s_i = next(i for i, d in enumerate(m.cal) if d >= start)
    e_i = max(i for i, d in enumerate(m.cal) if d <= end)
    assert s_i >= 1
    held, eq, rows, turn_total = {}, initial, [], 0.0
    for d in range(s_i, e_i + 1):
        # decided from yesterday's close: neither the View nor `held` reveals today's open
        target = f(View(m, d - 1), p, dict(held))
        on = 0.0
        for s, w in held.items():
            o_, c_ = m.series[s]
            on += w * (o_[d] / c_[d - 1] - 1)
        held = {s: w * (m.series[s][0][d] / m.series[s][1][d - 1]) / (1 + on) for s, w in held.items()}
        if target is not None:
            target = {s: w for s, w in target.items() if abs(w) > 1e-9}
            for s in target:
                assert s in TRADE, s
                assert m.series[s][1][d - 1] is not None and m.series[s][0][d] is not None, 'not listed: %s' % s
            if set(target) == set(held) and all(abs(target[s] - held[s]) < TOL for s in target):
                target = None
        new = held if target is None else target
        turnover = sum(abs(new.get(s, 0) - held.get(s, 0)) for s in set(new) | set(held))
        intr = sum(w * (m.series[s][1][d] / m.series[s][0][d] - 1) for s, w in new.items())
        longs, shorts = sum(w for w in new.values() if w > 0), -sum(w for w in new.values() if w < 0)
        rate = (o_irx[d - 1] or 0) / 100
        fees = turnover * COST_BPS * cost_mult / 1e4 + shorts * borrow / 252 + max(0, longs - 1) * (rate + spread) / 252
        r = (1 + on) * (1 + intr) - 1 - fees
        start_eq, eq = eq, eq * (1 + r)
        held = {s: w * (m.series[s][1][d] / m.series[s][0][d]) / (1 + intr) for s, w in new.items()}
        turn_total += turnover
        rows.append({'date': m.cal[d], 'start': start_eq, 'equity': eq, 'pnl': eq - start_eq, 'return_': r,
                     'trades': 0, 'exposure': longs + shorts, 'net': longs - shorts, 'turnover': turnover})
    return {'initial': initial, 'daily': rows, 'trades': [], 'decisions': [], 'turnover_per_year': turn_total / len(rows) * 252}


def blend(m, p, start, end, cost_mult, borrow, initial):
    by = {name(c): c for c in configs() if c[0] != 'blend'}
    parts = [simulate(m, by[n], start, end, cost_mult, borrow, initial) for n in p['parts']]
    irx, lev, k = m.closes('^IRX'), p['lev'], len(parts)
    idx = {d: i for i, d in enumerate(m.cal)}
    eq, rows = initial, []
    for j, base in enumerate(parts[0]['daily']):
        r = sum(x['daily'][j]['return_'] for x in parts) / k
        expo = sum(x['daily'][j]['exposure'] for x in parts) / k * lev
        r = lev * r - (lev - 1) * ((irx[idx[base['date']] - 1] or 0) / 100 + MARGIN_SPREAD) / 252
        s, eq = eq, eq * (1 + r)
        rows.append({'date': base['date'], 'start': s, 'equity': eq, 'pnl': eq - s, 'return_': r, 'trades': 0, 'exposure': expo})
    return {'initial': initial, 'daily': rows, 'trades': [], 'decisions': [],
            'turnover_per_year': lev * sum(x['turnover_per_year'] for x in parts) / k}


# ---------- statistics ----------

def moments(r):
    n, mu = len(r), statistics.mean(r)
    sd = statistics.pstdev(r)
    if sd == 0:
        return mu, 0.0, 0.0, 3.0
    skew = sum((x - mu) ** 3 for x in r) / n / sd ** 3
    kurt = sum((x - mu) ** 4 for x in r) / n / sd ** 4
    return mu, sd, skew, kurt


def deflated_sharpe(r, trial_sharpes):
    """Bailey & Lopez de Prado (2014): chance the true Sharpe beats the best you'd expect from luck
    after N trials. Daily units inside; trial_sharpes are daily Sharpes of every config tried."""
    N = len(trial_sharpes)
    mu, sd, sk, ku = moments(r)
    sr = mu / sd if sd else 0.0
    Z = statistics.NormalDist()
    g = 0.5772156649
    v = statistics.pvariance(trial_sharpes) if N > 1 else 0.0
    sr0 = math.sqrt(v) * ((1 - g) * Z.inv_cdf(1 - 1 / N) + g * Z.inv_cdf(1 - 1 / (N * math.e))) if N > 1 else 0.0
    denom = math.sqrt(max(1e-12, 1 - sk * sr + (ku - 1) / 4 * sr * sr))
    return Z.cdf((sr - sr0) * math.sqrt(len(r) - 1) / denom)


def summary(run, spy):
    mt = metrics(run, spy)
    sp = metrics(spy)
    years = len(run['daily']) / 252
    total, stotal = mt['return_pct'] / 100, sp['return_pct'] / 100
    cagr = (1 + total) ** (1 / years) - 1 if total > -1 else -1
    scagr = (1 + stotal) ** (1 / years) - 1
    return {'cagr_pct': 100 * cagr, 'spy_cagr_pct': 100 * scagr, 'excess_cagr_pct': 100 * (cagr - scagr),
            'total_pct': mt['return_pct'], 'sharpe': mt['sharpe'], 'sortino': mt['sortino'],
            'max_dd_pct': mt['max_drawdown_pct'], 'vol_pct': (mt['daily_vol_pct'] or 0) * math.sqrt(252),
            'corr_spy': mt['correlation_spy'], 'avg_exposure': mt['time_in_market_pct'] / 100,
            'turnover_yr': run.get('turnover_per_year'), 'years': years}


def walk_forward(runs, cal_rows, objective, window):
    """Each January pick the config with the best trailing `window` years (by objective) and trade it
    that year. Uses only finished years, so the stitched curve is out of sample."""
    names = list(runs)
    by_year = {}
    for i, row in enumerate(cal_rows):
        by_year.setdefault(row['date'][:4], []).append(i)
    years = sorted(by_year)
    out, picks = [], []
    for k, y in enumerate(years):
        past = years[max(0, k - window):k]
        if len(past) < window:
            continue
        idx = [i for py in past for i in by_year[py]]

        def score(n):
            r = [runs[n]['daily'][i]['return_'] for i in idx]
            mu, sd = statistics.mean(r), statistics.pstdev(r)
            return (mu / sd if sd else -9) if objective == 'sharpe' else math.prod(1 + x for x in r)
        best = max(names, key=score)
        picks.append((y, best))
        out.extend(runs[best]['daily'][i]['return_'] for i in by_year[y])
    return out, picks


def as_run(returns, dates, initial=INITIAL):
    eq, rows = initial, []
    for d, r in zip(dates, returns):
        s, eq = eq, eq * (1 + r)
        rows.append({'date': d, 'start': s, 'equity': eq, 'pnl': eq - s, 'return_': r, 'trades': 0, 'exposure': 1})
    return {'initial': initial, 'daily': rows, 'trades': [], 'decisions': []}


# ---------- commands ----------

def last_complete_day():
    """Yesterday or earlier: today's daily bar is still forming during the session."""
    return (dt.datetime.now(D.NY).date() - dt.timedelta(days=1)).isoformat()


def fingerprint():
    h = hashlib.sha256()
    for p in [HOME / 'lab.py', HOME / 'history.py'] + sorted(D.CACHE.glob('*.json')):
        h.update(p.name.encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def write_csv(path, rows):
    import csv
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def develop(args):
    cal, series = load(DEV_END)                      # the holdout is never loaded here
    m = Market(cal, series)
    cfgs = configs()
    runs, rows = {}, {}
    spy = simulate(m, ('hold', {'a': 'SPY'}), DEV_START, DEV_END)
    for k, cfg in enumerate(cfgs):
        n = name(cfg)
        runs[n] = simulate(m, cfg, DEV_START, DEV_END)
        rows[n] = dict(name=n, margin=needs_margin(cfg), **summary(runs[n], spy))
    daily_sr = []
    for n, run in runs.items():
        mu, sd, _, _ = moments([x['return_'] for x in run['daily']])
        daily_sr.append(mu / sd if sd else 0.0)
    for n, run in runs.items():
        rows[n]['deflated_sharpe_p'] = deflated_sharpe([x['return_'] for x in run['daily']], daily_sr)
    # sub-periods: does the edge hold in each regime, or only one?
    periods = [('2000-01-03', '2006-12-31'), ('2007-01-01', '2013-12-31'), ('2014-01-01', '2020-12-31')]
    for n in runs:
        for a, b in periods:
            idx = [i for i, x in enumerate(runs[n]['daily']) if a <= x['date'] <= b]
            sr = [runs[n]['daily'][i]['return_'] for i in idx]
            sp = [spy['daily'][i]['return_'] for i in idx]
            yrs = len(idx) / 252
            rows[n]['ex_%s' % a[:4]] = 100 * (math.prod(1 + x for x in sr) ** (1 / yrs) - math.prod(1 + x for x in sp) ** (1 / yrs))
    dates = [x['date'] for x in spy['daily']]
    wf = []
    for obj in ('sharpe', 'growth'):
        for win in (3, 5):
            r, picks = walk_forward(runs, spy['daily'], obj, win)
            first = len(dates) - len(r)
            s = summary(as_run(r, dates[first:]), as_run([x['return_'] for x in spy['daily'][first:]], dates[first:]))
            wf.append(dict(name='walk-forward %s %dy' % (obj, win), **s, picks=' '.join('%s:%s' % (y, p) for y, p in picks)))
    OUT.mkdir(exist_ok=True)
    table_rows = sorted(rows.values(), key=lambda r: -r['excess_cagr_pct'])
    write_csv(OUT / 'dev_configs.csv', table_rows)
    write_csv(OUT / 'dev_walkforward.csv', wf)
    print('Development %s..%s, %d configs, SPY CAGR %.2f%%' % (DEV_START, DEV_END, len(cfgs), rows[name(cfgs[0])]['cagr_pct']))
    keys = ('excess_cagr_pct', 'sharpe', 'max_dd_pct', 'deflated_sharpe_p', 'ex_2000', 'ex_2007', 'ex_2014', 'margin')
    for r in table_rows[:args.top]:
        print('%-62s ' % r['name'][:62] + ' '.join('%s=%s' % (k[:6], ('%.2f' % r[k]) if isinstance(r[k], float) else r[k]) for k in keys))
    for r in wf:
        print('%-26s cagr=%.2f excess=%.2f sharpe=%.2f maxdd=%.1f' % (r['name'], r['cagr_pct'], r['excess_cagr_pct'], r['sharpe'], r['max_dd_pct']))


def seal(args):
    names = {name(c): c for c in configs()}
    picks = [x.strip() for x in args.seal.split(',') if x.strip()]
    for p in picks:
        if p not in names and not p.startswith('walk-forward'):
            raise SystemExit('unknown config: %r' % p)
    path = OUT / 'selection.json'
    if path.exists():
        raise SystemExit('already sealed: %s' % path)
    with path.open('x') as f:
        json.dump({'finalists': picks, 'sealed_at': dt.datetime.now(D.NY).isoformat(), 'code': fingerprint(),
                   'holdout_start': '2021-01-01', 'configs_tried': len(names)}, f, indent=2)
    print('Sealed', len(picks), 'finalists')


def final(args):
    sel = json.loads((OUT / 'selection.json').read_text())
    if (OUT / 'final.json').exists():
        raise SystemExit('The holdout was already opened; out/final.json holds the one result. Not rerunning.')
    if sel['code'] != fingerprint():
        raise SystemExit('Code or data changed since sealing: the holdout would no longer be blind.')
    end = last_complete_day()
    cal, series = load(end)
    m = Market(cal, series)
    by = {name(c): c for c in configs()}
    hs, he = '2021-01-04', cal[-1]
    spy = simulate(m, ('hold', {'a': 'SPY'}), hs, he)
    res, runs = [], {}
    for n in sel['finalists']:
        if n.startswith('walk-forward'):
            _, obj, win = n.split()
            allr = {k: simulate(m, c, DEV_START, he) for k, c in by.items()}
            sp_all = simulate(m, ('hold', {'a': 'SPY'}), DEV_START, he)
            r, picks = walk_forward(allr, sp_all['daily'], obj, int(win[:-1]))
            dates = [x['date'] for x in sp_all['daily']][-len(r):]
            keep = [(d, x) for d, x in zip(dates, r) if d >= hs]
            runs[n] = as_run([x for _, x in keep], [d for d, _ in keep])
            extra = {'picks': ' '.join('%s:%s' % p for p in picks if p[0] >= '2021')}
        else:
            runs[n] = simulate(m, by[n], hs, he)
            extra = {'margin': needs_margin(by[n])}
            for cm in (0, 3):
                s = summary(simulate(m, by[n], hs, he, cost_mult=cm), spy)
                extra['cagr_costs_%dx' % cm] = s['cagr_pct']
        boot = bootstrap(runs[n], spy, block=21)
        res.append(dict(name=n, **summary(runs[n], spy), boot_mean_daily_excess_pct=boot['mean_daily_excess_pct'],
                        boot_ci_low=boot['ci_low_pct'], boot_ci_high=boot['ci_high_pct'], p=boot['p_two_sided'], **extra))
    out = {'holdout': [hs, he], 'results': res, 'selection': sel}
    with (OUT / 'final.json').open('x') as f:
        json.dump(out, f, indent=2)
    report(out, runs, spy)
    for r in res:
        print('%-50s cagr=%.2f spy=%.2f excess=%.2f sharpe=%s maxdd=%.1f p=%.3f' % (
            r['name'][:50], r['cagr_pct'], r['spy_cagr_pct'], r['excess_cagr_pct'], fmt(r['sharpe']), r['max_dd_pct'], r['p']))


def thin(rows, step=5):
    return rows[::step] + ([rows[-1]] if len(rows) % step != 1 else [])


def report(out, runs, spy):
    css = (':root{color-scheme:light dark;font-family:system-ui,sans-serif}body{max-width:1200px;margin:30px auto;'
           'padding:0 16px;line-height:1.5;background:Canvas;color:CanvasText}svg{width:100%;height:auto}'
           'svg text{font-size:12px;fill:currentColor}.grid{stroke:currentColor;opacity:.15}.scroll{overflow:auto}'
           'table{border-collapse:collapse;font-size:12px}th,td{border:1px solid #8886;padding:4px 6px}')
    parts = ['<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Model lab holdout</title><style>%s</style><h1>Model lab: sealed holdout %s to %s vs SPY</h1>' % (css, *out['holdout'])]
    sp_rows = thin(spy['daily'])
    for n, run in runs.items():
        rows = thin(run['daily'])
        labels = [r['date'] for r in rows]
        parts.append(chart(n + ' vs SPY ($400 start, after costs)',
                           [('Strategy', [r['equity'] for r in rows]), ('SPY', [r['equity'] for r in sp_rows])], labels, 'Account value ($)'))
        dd_s = drawdowns([run['initial']] + [r['equity'] for r in run['daily']])[0][1:]
        dd_b = drawdowns([spy['initial']] + [r['equity'] for r in spy['daily']])[0][1:]
        parts.append(chart(n + ': drawdown', [('Strategy', [100 * x for x in dd_s[::5]]), ('SPY', [100 * x for x in dd_b[::5]])],
                           [r['date'] for r in run['daily'][::5]], 'Drawdown (%)'))
    parts.append('<h2>Results</h2>' + table(out['results']))
    parts.append('<p>Blind: finalists were sealed from 2000-2020 results before any 2021+ bar was loaded. '
                 'Costs: %.0f bps per side, %.1f%%/yr borrow on shorts, T-bill+%.0f%% on margin. Total returns (dividends in) for SPY and the strategy.</p>'
                 % (COST_BPS, BORROW * 100, MARGIN_SPREAD * 100))
    (OUT / 'report.html').write_text(''.join(parts) + '</html>')
    (OUT / 'final.md').write_text(markdown_table(out['results']))


CROSS = [('^GSPC', '1950-01-03', '1999-12-31')] + [(s, None, None) for s in
         ('^GDAXI', '^N225', '^FTSE', '^HSI', '^GSPTSE', '^AXJO', '^FCHI', '^STOXX50E')]
SPY_ONLY = ('hold', 'trend', 'vol', 'rsi')


def load_index(sym, last_date):
    """A foreign or old index placed in the 'SPY' slot, so SPY-only strategies run on it unchanged."""
    b = [x for x in D.bars(sym, True) if x['date'] <= last_date]
    cal = [x['date'] for x in b]
    n = len(cal)
    series = {s: ([None] * n, [None] * n) for s in D.SYMBOLS}
    series['SPY'] = ([x['o'] for x in b], [x['c'] for x in b])
    rates = {x['date']: x['c'] for x in D.bars('^IRX', True)}
    irx, last = [], None
    for d in cal:                         # T-bill yield on or before each date (none before 1960: 0)
        last = rates.get(d, last)
        irx.append(last)
    series['^IRX'] = (irx, irx)
    return cal, series


def cross(args):
    sel_path = OUT / 'selection3.json'
    if not sel_path.exists():
        raise SystemExit('seal round 3 first: --seal3 NAMES')
    if (OUT / 'cross.json').exists():
        raise SystemExit('The cross-market test already ran once; out/cross.json holds it.')
    sel = json.loads(sel_path.read_text())
    by = {name(c): c for c in configs()}
    end = last_complete_day()
    res = []
    for sym, a, b in CROSS:
        cal, series = load_index(sym, end)
        m = Market(cal, series)
        a = a or cal[min(len(cal) - 1, 260)]          # a year of warm-up for the 200-day average
        b = b or cal[-1]
        bh = simulate(m, ('hold', {'a': 'SPY'}), a, b)
        for n in sel['finalists']:
            for sp in (MARGIN_SPREAD, 0.01):
                run = simulate(m, by[n], a, b, spread=sp)
                res.append(dict(market=sym, start=a, end=b, name=n, financing='T-bill+%g%%' % (sp * 100),
                                **summary(run, bh), bh_sharpe=metrics(bh)['sharpe'], bh_max_dd=metrics(bh)['max_drawdown_pct']))
    out = {'selection': sel, 'results': res}
    with (OUT / 'cross.json').open('x') as f:
        json.dump(out, f, indent=2)
    write_csv(OUT / 'cross.csv', res)
    Z = statistics.NormalDist()
    for n in sel['finalists']:
        for fin in sorted({r['financing'] for r in res}):
            rs = [r for r in res if r['name'] == n and r['financing'] == fin]
            wins = sum(r['excess_cagr_pct'] > 0 for r in rs)
            better = sum((r['sharpe'] or -9) > (r['bh_sharpe'] or -9) for r in rs)
            p = sum(math.comb(len(rs), k) for k in range(wins, len(rs) + 1)) / 2 ** len(rs)
            print('%-48s %-12s beat return %d/%d (sign p=%.3f) median excess %+.2f%%/yr, beat Sharpe %d/%d' % (
                n[:48], fin, wins, len(rs), p, statistics.median(r['excess_cagr_pct'] for r in rs), better, len(rs)))
    _ = Z


def seal3(args):
    names = {name(c): c for c in configs()}
    picks = [x.strip() for x in args.seal3.split(',') if x.strip()]
    for p in picks:
        if p not in names or names[p][0] not in SPY_ONLY or any(t in p for t in ('QQQ', 'IEF', 'TLT', 'SH', 'PSQ', 'safe')):
            raise SystemExit('not a SPY-only config: %r' % p)
    path = OUT / 'selection3.json'
    with path.open('x') as f:
        json.dump({'finalists': picks, 'sealed_at': dt.datetime.now(D.NY).isoformat(), 'code': fingerprint(),
                   'configs_tried': len(names)}, f, indent=2)
    print('Sealed round 3:', len(picks), 'finalists')


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--seal', help='comma-separated finalist names from out/dev_configs.csv')
    ap.add_argument('--final', action='store_true', help='open the holdout once')
    ap.add_argument('--top', type=int, default=25)
    ap.add_argument('--seal3', help='round 3: SPY-only finalists for the fresh cross-market test')
    ap.add_argument('--cross', action='store_true', help='run round 3 finalists once on 1950-1999 S&P and 8 foreign markets')
    a = ap.parse_args()
    if a.seal3:
        seal3(a)
    elif a.cross:
        cross(a)
    elif a.seal:
        seal(a)
    elif a.final:
        final(a)
    else:
        develop(a)


if __name__ == '__main__':
    main()
