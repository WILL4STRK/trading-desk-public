#!/usr/bin/env python3
"""Blind formula replay. Run --fetch once; subsequent runs use only the cache."""
import argparse
from bisect import bisect_right
from collections import defaultdict
import csv
import concurrent.futures
import multiprocessing
import os
import tempfile
import datetime as dt
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from data import CACHE, HOME, ROOT, chart, vendor
from desk import breaker, guards, outcome, pipeline, settings as S, watchlist

entry = vendor('trader-entry', 'entry.py')
exit_planner = vendor('trader-exit', 'exit.py')
budget = vendor('trader-budget', 'size.py')
tm = vendor('trader-timemachine', 'timemachine.py')
OUT = HOME / 'out'


def minute(text):
    return outcome._minute(text)


def close_minute(day):
    session = entry.session(dt.date.fromisoformat(day))
    if not session:
        return None
    return session[1].hour * 60 + session[1].minute


def stamp(day, m):
    return day + ' ' + outcome._hhmm(m)


class Clock:
    def __init__(self):
        self.now = ''

    def advance(self, now):
        if now < self.now:
            raise AssertionError('simulation clock cannot go backwards')
        self.now = now


class View:
    """Only closed observations cross this boundary. No raw series is exposed."""
    def __init__(self, store, clock):
        self.__store, self.__clock = store, clock

    def daily(self, symbol, before=None):
        day = before or self.__clock.now[:10]
        if day > self.__clock.now[:10]:
            raise AssertionError('future daily candle requested')
        bars = self.__store.daily.get(symbol, ())
        dates = self.__store.dates.get(symbol, ())
        # A daily bar is available at the next morning, never during its own day.
        end = bisect_right(dates, day, hi=len(dates))
        if end and dates[end - 1] == day:
            end -= 1
        return tuple(dict(b) for b in bars[:end])

    def session(self, symbol, day=None, through=None):
        day = day or self.__clock.now[:10]
        through = through if through is not None else minute(self.__clock.now[11:])
        if stamp(day, through) > self.__clock.now:
            raise AssertionError('future intraday candle requested')
        bars = self.__store.sessions(symbol).get(day, ())
        # Timestamp filtering occurs at the feed boundary; future OHLCV is never read.
        return tuple(dict(b) for b in bars if b['minute'] + 5 <= through)

    def history(self, symbol):
        return tuple((d, self.session(symbol, d, close_minute(d)))
                     for d in self.__store.session_dates(symbol) if d < self.__clock.now[:10] and close_minute(d))


class Store:
    def __init__(self, daily, loader):
        self.daily = daily
        self.dates = {s: [b['date'] for b in bars] for s, bars in daily.items()}
        self.loader = loader

    @lru_cache(maxsize=96)
    def sessions(self, symbol):
        days = defaultdict(list)
        for b in self.loader(symbol):
            if 570 <= b['minute'] < 960 and b['minute'] % 5 == 0:
                days[b['date']].append(b)
        return dict(days)

    def session_dates(self, symbol):
        return sorted(self.sessions(symbol))

    def view(self, clock):
        return View(self, clock)


def screens(view, previous_day):
    quotes = []
    for sym in view._symbols:
        bars = view.daily(sym)
        if len(bars) < 15 or bars[-1]['date'] != previous_day or sym == 'SPY':
            continue
        last, prev = bars[-1], bars[-2]
        quotes.append({'symbol': sym, 'quoteType': 'EQUITY', 'regularMarketPrice': last['c'],
                       'averageDailyVolume3Month': statistics.mean(b['v'] for b in bars[-63:]),
                       'volume': last['v'], 'change': last['c'] / prev['c'] - 1})
    return {'most_actives': sorted(quotes, key=lambda q: (-q['volume'], q['symbol']))[:25],
            'day_gainers': sorted(quotes, key=lambda q: (-q['change'], q['symbol']))[:25],
            'day_losers': sorted(quotes, key=lambda q: (q['change'], q['symbol']))[:25]}


def morning(view, st, equity, screen_rows):
    maximum = S.limits(st, equity)['max_position']
    # Pure desk functions retain its liquidity, price, candidate budget and ATR rules.
    core = list(st['core_watchlist'])
    candidates = watchlist.candidates(screen_rows, set(core), set(st['blocked']), st['min_price'], maximum)
    ranked = sorted(candidates.items(), key=lambda kv: -kv[1]['avg_volume'])[:watchlist.MAX_CANDIDATES]
    picks = []
    for sym, candidate in ranked:
        atr = watchlist.atr_pct(view.daily(sym))
        if atr is not None:
            picks.append(dict(candidate, symbol=sym, atr_pct=atr))
    picks.sort(key=lambda p: -p['atr_pct'])
    picks = picks[:st['auto_watchlist_movers']]
    return core + [p['symbol'] for p in picks], picks


def odds_cached(key):
    path = CACHE / ('odds-' + hashlib.sha256(key.encode()).hexdigest() + '.json')
    if path.exists():
        return json.loads(path.read_text())
    return None


def odds(view, symbol, plan, day, trigger, flat, replays):
    sessions = [(d, list(bars)) for d, bars in view.history(symbol)
                if len(bars) == 78 and [b['minute'] for b in bars] == list(range(570, 960, 5))]
    if len(sessions) < tm.MIN_SESSIONS:
        return None
    sessions = sessions[-60:]
    payload = {'sessions': sessions, 'plan': plan, 'trigger': trigger, 'flat': flat,
               'replays': replays, 'code': hashlib.sha256(Path(tm.__file__).read_bytes()).hexdigest()}
    key = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    cached = odds_cached(key)
    if cached is not None:
        return cached['edge']
    result = tm.simulate(tm.build_pool(sessions), plan['entry'], plan['stop'], plan['target'],
                         (trigger - 570) // 5 - 1, (flat - 570) // 5 - 1,
                         replays=replays, seed=19)
    edge = pipeline.edge_points(result['p_target'], result['p_stop'], plan['target_r'])
    path = CACHE / ('odds-' + hashlib.sha256(key.encode()).hexdigest() + '.json')
    with tempfile.NamedTemporaryFile(mode='w', dir=CACHE, suffix='.tmp', delete=False) as f:
        json.dump({'edge': edge, 'p_target': result['p_target'], 'p_stop': result['p_stop'],
                   'sessions': len(sessions), 'latest': sessions[-1][0]}, f)
    os.replace(f.name, path)
    return edge


def plan_long(price, rng, st, bars):
    rule, parameter = exit_planner.parse_stop(st['stop_rule'])
    atr, n = exit_planner.atr_of(bars)
    p = exit_planner.compute(price, rule, parameter, 'r', exit_planner.D(st['target_r']),
                             rng['high'], rng['low'], atr, n)
    plan = {k: float(p[k]) for k in ('entry', 'stop', 'target', 'target_r')}
    plan['risk_per_share'] = float(p['risk'])
    plan['limit'] = pipeline._floor_cent(plan['entry'] + st['chase_r'] * plan['risk_per_share'])
    return plan


def fill_price(order, bar):
    if bar['minute'] < order['trigger'] or bar['minute'] >= order['until']:
        return None
    return min(order['limit'], bar['o']) if bar['l'] <= order['limit'] else None


def exit_price(position, bar, flat):
    if bar['minute'] >= flat:
        return bar['o'], 'flat'
    if bar['l'] <= position['stop']:
        return min(position['stop'], bar['o']), 'stop'
    # The fill candle's high might precede the fill; do not award its target.
    if bar['minute'] > position['fill_minute'] and bar['h'] >= position['target']:
        return max(position['target'], bar['o']), 'target'
    return None


def per_side_cost(st, multiplier):
    return multiplier * (st['est_cost_per_share'] / 2 + .01)


class Account:
    def __init__(self, initial, paper=False):
        self.initial = self.cash = initial
        self.unsettled = []
        self.positions = {}
        self.orders = {}
        self.trades = []
        self.paper = paper

    def settle(self, day):
        ready = [x for x in self.unsettled if x[0] <= day]
        self.cash += sum(x[1] for x in ready)
        self.unsettled = [x for x in self.unsettled if x[0] > day]

    def equity(self):
        return self.cash + sum(x[1] for x in self.unsettled) + sum(p['qty'] * p['mark'] for p in self.positions.values())

    def risk_equity(self):
        # The desk sizes and trips its breakers on realized equity (positions at cost).
        return self.cash + sum(x[1] for x in self.unsettled) + sum(p['qty'] * p['fill'] for p in self.positions.values())

    def available(self):
        reserve = sum(p['reserve'] for p in self.orders.values())
        if self.paper:
            return max(0, self.risk_equity() * S.PAPER_LEVERAGE - sum(p['qty'] * p['fill'] for p in self.positions.values()) - reserve)
        return max(0, self.cash - reserve)

    def buy(self, sym, order, price, fee, bar):
        qty = order['qty']
        cost = qty * (price + fee)
        if cost > self.available() + 1e-7:
            raise AssertionError('order exceeded available cash or paper buying power')
        self.cash -= cost
        self.positions[sym] = dict(order, fill=price, mark=bar['c'], fill_minute=bar['minute'], entry_fee=qty * fee)

    def sell(self, sym, price, fee, day, bar_minute, reason):
        p = self.positions.pop(sym)
        proceeds = p['qty'] * (price - fee)
        if self.paper:
            self.cash += proceeds
        else:
            next_day = dt.date.fromisoformat(day) + dt.timedelta(days=1)
            while not entry.trading_day(next_day)[0]:
                next_day += dt.timedelta(days=1)
            self.unsettled.append((next_day.isoformat(), proceeds))
        pnl = p['qty'] * (price - p['fill']) - p['entry_fee'] - p['qty'] * fee
        risk = p['qty'] * (p['limit'] - p['stop'])
        t = dict(symbol=sym, date=day, entry_at=stamp(day, p['fill_minute']), exit_at=stamp(day, bar_minute),
                 signal_at=stamp(day, p['trigger']), status='closed', qty=p['qty'], entry=p['fill'],
                 exit=price, stop=p['stop'], target=p['target'], pnl=pnl, r=pnl/risk if risk else 0,
                 costs=p['entry_fee'] + p['qty']*fee, reason=reason, minutes=max(5, bar_minute-p['fill_minute']))
        self.trades.append(t)
        return t


def simulate(store, days, variant, initial=400, cost=1, paper=False, replays=10000, prepared=None):
    clock = Clock()
    view = store.view(clock)
    view._symbols = tuple(store.daily)
    st = dict(S.DEFAULTS, mode='paper' if paper else 'live')
    st.update(variant.get('settings', {}))
    account = Account(initial, paper)
    daily, decisions = [], []
    persistent_halt = None
    fee = per_side_cost(st, cost)
    for day in days:
        clock.advance(stamp(day, 540))
        view._today = day
        account.settle(day)
        start = account.equity()
        rules = S.for_day(st, outcome._hhmm(close_minute(day)))
        flat = minute(rules['flat_by'])
        spy_history = view.daily('SPY')
        prior = spy_history[-1]
        screen_rows = prepared[day] if prepared is not None else screens(view, prior['date'])
        symbols, picks = morning(view, rules, start, screen_rows)
        regime = not variant.get('trend') or (len(spy_history) >= 20 and prior['c'] > statistics.mean(b['c'] for b in spy_history[-20:]))
        seen, held_minutes, filled_today = set(), 0, 0
        day_trades_start = len(account.trades)
        def log(sym, event, reason='', **extra):
            decisions.append(dict(date=day, time=clock.now, symbol=sym, event=event, reason=reason,
                                  equity=round(account.equity(), 6), settled_cash=round(account.cash, 6), **extra))
        log('', 'watchlist', 'prior-day formula only', symbols=' '.join(symbols), movers=' '.join(p['symbol'] for p in picks))
        if not regime:
            log('', 'filtered', 'SPY previous close not above previous 20-session average')
        for now in range(575, close_minute(day) + 1, 5):
            clock.advance(stamp(day, now))
            exposed_this_bar = bool(account.positions)
            if exposed_this_bar:
                held_minutes += 5
            latest = {}
            for sym in symbols:
                bars = view.session(sym)
                if bars and bars[-1]['minute'] == now-5:
                    latest[sym] = bars[-1]
            # All orders here existed before the current candle began.
            for sym in sorted(list(account.positions)):
                b = latest.get(sym)
                if b is None:
                    raise ValueError('Missing candle while holding %s at %s; refusing an invented exit' % (sym, clock.now))
                p = account.positions[sym]
                p['mark'] = b['c']
                result = exit_price(p, b, flat)
                if result:
                    t = account.sell(sym, result[0], fee, day, b['minute'], result[1])
                    log(sym, 'exit', result[1], pnl=t['pnl'], price=result[0])
            lim = S.limits(rules, account.risk_equity())
            status = breaker.status(rules, account.trades, account.risk_equity(), day)
            if status['needs_reset']:
                persistent_halt = status['tripped']['reason']
            day_pnl = sum(t['pnl'] for t in account.trades[day_trades_start:])
            halted = persistent_halt or (status['tripped'] or {}).get('reason')
            if day_pnl <= -lim['max_daily_loss'] < 0:
                halted = 'daily loss stop'
            if filled_today >= lim['trades_cap']:
                halted = 'daily trade cap'
            for sym in sorted(list(account.orders)):
                p = account.orders[sym]
                b = latest.get(sym)
                if halted or now-5 >= min(p['until'], flat, minute(rules['no_entries_after'])):
                    log(sym, 'no fill', halted or 'expired')
                    del account.orders[sym]
                    continue
                if b is None:
                    raise ValueError('Missing candle with pending order for %s at %s; fill is unknown' % (sym, clock.now))
                price = fill_price(p, b)
                if price is None:
                    continue
                del account.orders[sym]
                if p['qty'] * (price + fee) > account.available() + 1e-7:
                    log(sym, 'no fill', 'buying power fell after order submission')
                    continue
                account.buy(sym, p, price, fee, b)
                if not exposed_this_bar:
                    held_minutes += 5
                    exposed_this_bar = True
                filled_today += 1
                log(sym, 'fill', 'later candle', price=price, qty=p['qty'])
                result = exit_price(account.positions[sym], b, flat)
                if result:
                    t = account.sell(sym, result[0], fee, day, b['minute'], result[1])
                    log(sym, 'exit', result[1], price=result[0], pnl=t['pnl'])
                # A fill-candle loss may stop other pending fills in this same batch.
                lim = S.limits(rules, account.risk_equity())
                day_pnl = sum(t['pnl'] for t in account.trades[day_trades_start:])
                status = breaker.status(rules, account.trades, account.risk_equity(), day)
                if status['needs_reset']:
                    persistent_halt = status['tripped']['reason']
                halted = persistent_halt or (status['tripped'] or {}).get('reason')
                if day_pnl <= -lim['max_daily_loss'] < 0 or filled_today >= lim['trades_cap']:
                    halted = halted or 'daily limit reached'
            if now > minute(rules['no_entries_after']):
                continue
            for sym in symbols:
                if sym in seen:
                    continue
                bars = view.session(sym)
                if not bars or [b['minute'] for b in bars] != list(range(570, now, 5)):
                    continue
                rng, trigger, reason = outcome._breakout(bars, rules['or_minutes'], rules['no_entries_after'], rules['min_volume_x'], no_entries_before=rules['no_entries_before'])
                if not trigger:
                    continue
                seen.add(sym)
                log(sym, 'signal', 'first completed breakout', price=trigger['price'])
                if halted or not regime:
                    log(sym, 'rejected', halted or 'market trend filter')
                    continue
                if trigger['price'] < rules['min_price']:
                    log(sym, 'rejected', 'minimum price')
                    continue
                prior_bars = view.daily(sym)
                if variant.get('gap') and (not prior_bars or bars[0]['o'] / prior_bars[-1]['c'] - 1 < variant['gap']):
                    log(sym, 'rejected', 'opening gap filter')
                    continue
                atr = watchlist.atr_pct(prior_bars)
                if variant.get('atr_max') and (atr is None or atr > variant['atr_max']):
                    log(sym, 'rejected', 'prior-day ATR ceiling')
                    continue
                try:
                    p = plan_long(trigger['price'], rng, rules, bars)
                    refusal = guards.stop_too_close(p['entry'], p['risk_per_share'], rules['min_stop_pct'])
                    if refusal:
                        raise ValueError(refusal)
                    cfg = S.skills_config(rules, account.risk_equity())
                    sizing = budget.size_trade(dict(p, symbol=sym, date=day), cfg, limit=p['limit'], cash=account.available(), today=day)
                    if sizing['refusal']:
                        raise ValueError(sizing['refusal'])
                    # Include execution costs in the cash reservation; desk sizes price risk separately.
                    qty = min(sizing['qty'], max(0, int((account.available()-S.limits(rules, account.risk_equity())['cash_buffer'])/(p['limit']+fee))))
                    if qty < 1:
                        raise ValueError('insufficient cash including execution costs')
                    trigger_min = minute(trigger['close_time'])
                    edge = odds(view, sym, p, day, trigger_min, flat, replays)
                    if edge is None or edge < rules['min_edge_points']:
                        raise ValueError('Time Machine unavailable or edge below floor: %s' % edge)
                    p.update(symbol=sym, qty=qty, trigger=trigger_min,
                             until=trigger_min+rules['signal_valid_minutes'], reserve=qty*(p['limit']+fee))
                    account.orders[sym] = p
                    log(sym, 'order', 'risk and historical odds passed', qty=qty, price=p['limit'], edge=edge)
                except (ValueError, budget.SizeError, exit_planner.PlanError) as e:
                    log(sym, 'rejected', str(e))
        if account.positions:
            raise AssertionError('position survived flat-by')
        for sym in account.orders:
            log(sym, 'no fill', 'session ended')
        account.orders.clear()
        for sym in symbols:
            if sym not in seen:
                log(sym, 'no signal', 'no eligible breakout or incomplete candles')
        end = account.equity()
        if abs(end - (initial + sum(t['pnl'] for t in account.trades))) > 1e-6:
            raise AssertionError('cash, settlement and realized P&L do not reconcile')
        daily.append(dict(date=day, start=start, equity=end, pnl=end-start, return_=end/start-1 if start > 0 else 0,
                          trades=len(account.trades)-day_trades_start, exposure=held_minutes/(close_minute(day)-570),
                          settled=account.cash, unsettled=sum(x[1] for x in account.unsettled)))
    return {'daily': daily, 'trades': account.trades, 'decisions': decisions, 'initial': initial}


def variants():
    result = [{'name': 'base', 'settings': {}}]
    changes = [('or_minutes', v) for v in (5, 10, 30, 45, 60)]
    changes += [('stop_rule', v) for v in ('or-low', 'pct:0.5', 'pct:1', 'atr:1', 'atr:2')]
    changes += [('target_r', v) for v in (1.5, 2.5, 3)]
    changes += [('chase_r', v) for v in (0, .1, .4)]
    changes += [('min_volume_x', v) for v in (.5, 1, 1.5)]
    changes += [('no_entries_after', v) for v in ('10:30', '11:30', '13:30')]
    changes += [('auto_watchlist_movers', v) for v in (0, 5, 30)]
    changes += [('risk_per_trade_pct', 1)]
    result.extend({'name': '%s=%s' % (k, v), 'settings': {k: v}} for k, v in changes)
    result += [{'name': 'trend', 'settings': {}, 'trend': True},
               {'name': 'gap=1%', 'settings': {}, 'gap': .01},
               {'name': 'gap=2%', 'settings': {}, 'gap': .02}]
    assert len(result) == 30
    return result


def benchmark(store, days, initial):
    all_bars = {b['date']: b for b in store.daily['SPY']}
    preceding = max(d for d in all_bars if d < days[0])
    base = all_bars[preceding]['c']
    previous = base
    daily = []
    for day in days:
        price = all_bars[day]['c']
        daily.append(dict(date=day, start=initial*previous/base, equity=initial*price/base,
                          pnl=initial*(price-previous)/base, return_=price/previous-1, trades=0, exposure=1))
        previous = price
    return {'initial': initial, 'daily': daily, 'trades': [], 'decisions': []}


def drawdowns(values):
    peak, duration, longest = values[0], 0, 0
    ds = []
    for v in values:
        peak = max(peak, v)
        dd = v/peak-1 if peak else 0
        ds.append(dd)
        duration = duration+1 if dd < -1e-12 else 0
        longest = max(longest, duration)
    return ds, longest


def metrics(run, spy=None, is_spy=False):
    rows, trades = run['daily'], run['trades']
    r = [d['return_'] for d in rows]
    n = len(r)
    mean = statistics.mean(r) if n else 0
    sd = statistics.stdev(r) if n > 1 else 0
    downside = math.sqrt(sum(min(x, 0)**2 for x in r)/n) if n else 0
    dd, longest = drawdowns([run['initial']] + [d['equity'] for d in rows])
    total = rows[-1]['equity']/run['initial']-1 if rows else 0
    winners, losers = [t['pnl'] for t in trades if t['pnl'] > 0], [t['pnl'] for t in trades if t['pnl'] < 0]
    correlation = None
    if spy and n > 1:
        s = [d['return_'] for d in spy['daily']]
        sm, ss = statistics.mean(s), statistics.stdev(s)
        if sd > 1e-14 and ss > 1e-14:
            correlation = sum((a-mean)*(b-sm) for a, b in zip(r, s))/((n-1)*sd*ss)
    return dict(return_pct=100*total, cagr_pct=100*((1+total)**(252/n)-1) if n >= 252 and total > -1 else None,
                daily_vol_pct=100*sd, sharpe=mean/sd*math.sqrt(252) if sd > 1e-14 else None,
                sortino=mean/downside*math.sqrt(252) if downside > 1e-14 else None,
                max_drawdown_pct=100*min(dd), longest_drawdown_days=longest,
                win_rate_pct=100*len(winners)/len(trades) if trades else None,
                expectancy_r=statistics.mean(t['r'] for t in trades) if trades else None,
                profit_factor=sum(winners)/-sum(losers) if losers else None,
                trades=len(trades) if not is_spy else None,
                trades_per_day=len(trades)/n if n and not is_spy else None,
                time_in_market_pct=100*statistics.mean(d['exposure'] for d in rows) if rows else 0,
                correlation_spy=correlation, days=n)


def bootstrap(run, spy, samples=10000, seed=19, block=5):
    differences = [a['return_']-b['return_'] for a, b in zip(run['daily'], spy['daily'])]
    n = len(differences)
    if not n:
        raise ValueError('no paired returns')
    observed = statistics.mean(differences)
    rng = random.Random(seed)
    means = []
    for _ in range(samples):
        draws = []
        while len(draws) < n:
            start = rng.randrange(n)
            draws.extend(differences[(start+j) % n] for j in range(min(block, n)))
        means.append(sum(draws[:n])/n)
    means.sort()
    p = (1+sum(abs(x-observed) >= abs(observed)-1e-15 for x in means))/(samples+1)
    return {'mean_daily_excess_pct': observed*100, 'ci_low_pct': means[int(samples*.025)]*100,
            'ci_high_pct': means[int(samples*.975)]*100, 'p_two_sided': p,
            'samples': samples, 'seed': seed, 'block_days': min(block, n), 'days': n}


def write_csv(path, rows, empty_fields=('date',)):
    fields = list(dict.fromkeys(k for row in rows for k in row)) or list(empty_fields)
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_store():
    manifest = json.loads((CACHE/'universe.json').read_text())
    daily, errors = {}, dict(manifest['errors'])
    missing = [s for s in manifest['symbols'] if not (CACHE/(s+'-1d.json')).exists() and s not in errors]
    if missing:
        raise ValueError('Daily acquisition is incomplete: %d unattempted symbols; finish data.py first' % len(missing))
    for sym in manifest['symbols']:
        try:
            bars = chart(sym, '1d')
            if not bars:
                raise ValueError('empty daily history')
            daily[sym] = bars
        except (OSError, ValueError, KeyError, TypeError) as e:
            errors[sym] = str(e)[:150]
    if 'SPY' not in daily:
        raise ValueError('SPY daily history is required')
    return Store(daily, lambda s: chart(s, '5m')), manifest, errors


def prepare_screens(store, days):
    clock = Clock()
    view = store.view(clock)
    view._symbols = tuple(store.daily)
    result = {}
    for day in days:
        clock.advance(stamp(day, 540))
        result[day] = screens(view, view.daily('SPY')[-1]['date'])
    return result


def fingerprint(paths):
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


@lru_cache(maxsize=1)
def worker_store():
    return load_store()[0]


def score_variant(job):
    variant, train, validation, replays, prepared, identity = job
    path = CACHE / ('variant-' + hashlib.sha256((identity + variant['name']).encode()).hexdigest() + '.json')
    if path.exists():
        return json.loads(path.read_text())
    print('Tuning/validation:', variant['name'], flush=True)
    store = worker_store()
    result = [simulate(store, train, variant, replays=replays, prepared=prepared),
              simulate(store, validation, variant, replays=replays, prepared=prepared)]
    path.write_text(json.dumps(result, separators=(',', ':')))
    return result


def snapshot_cutoff(source, manifest):
    """Freeze the acquired snapshot's clock so tomorrow cannot close yesterday's partial bar."""
    if not manifest.exists():
        acquired = dt.datetime.fromtimestamp(source.stat().st_mtime, dt.timezone.utc)
        manifest.write_text(json.dumps({'as_of': acquired.isoformat()}))
    return dt.datetime.fromisoformat(json.loads(manifest.read_text())['as_of']).astimezone(
        __import__('zoneinfo').ZoneInfo('America/New_York'))


def completed_days(bars, as_of):
    slots = defaultdict(set)
    for b in bars:
        if 570 <= b['minute'] < 960 and b['minute'] % 5 == 0:
            slots[b['date']].add(b['minute'])
    result = []
    for day, minutes in sorted(slots.items()):
        close = close_minute(day)
        if not close or not set(range(570,close,5)) <= minutes:
            continue
        if day < as_of.date().isoformat() or (day == as_of.date().isoformat() and as_of.hour*60+as_of.minute >= close):
            result.append(day)
    return result


def run_experiment(args):
    OUT.mkdir(exist_ok=True)
    store, manifest, errors = load_store()
    spy_intraday = chart('SPY', '5m', args.fetch)
    as_of = snapshot_cutoff(CACHE/'SPY-5m.json', CACHE/'snapshot.json')
    # Twenty full prior sessions are required by the actual Time Machine gate.
    all_days = completed_days(spy_intraday, as_of)
    days = [d for d in all_days[tm.MIN_SESSIONS:] if (not args.start or d >= args.start) and (not args.end or d <= args.end)]
    if len(days) < 15:
        raise ValueError('Need at least 15 evaluation sessions after 20 warmup sessions; found %d' % len(days))
    if args.start and args.start < all_days[tm.MIN_SESSIONS]:
        raise ValueError('--start precedes the available 20-session odds warmup: ' + all_days[tm.MIN_SESSIONS])
    if args.end and args.end > all_days[-1]:
        raise ValueError('--end is beyond the last completed available session: ' + all_days[-1])
    n = len(days)
    train, validation, locked = days[:int(.6*n)], days[int(.6*n):int(.8*n)], days[int(.8*n):]
    prepared = prepare_screens(store, days)
    # Download a superset, without evaluating returns: each day's previous-close screen membership.
    symbols = set(S.DEFAULTS['core_watchlist']) | {'SPY'}
    for rows in prepared.values():
        symbols.update(watchlist.candidates(rows, set(S.DEFAULTS['core_watchlist']), set(S.DEFAULTS['blocked']),
                                            S.DEFAULTS['min_price'], float('inf')))
    failures = {}
    for i, sym in enumerate(sorted(symbols), 1):
        try:
            chart(sym, '5m', args.fetch)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as e:
            failures[sym] = str(e)[:180]
        if i % 50 == 0 or i == len(symbols):
            print('Intraday cache: %d/%d, failures %d' % (i, len(symbols), len(failures)), flush=True)
    if failures:
        (OUT/'data_failures.json').write_text(json.dumps(failures, indent=2))
        raise ValueError('%d required intraday downloads unavailable; see out/data_failures.json. No performance result fabricated.' % len(failures))
    files = list(HOME.glob('*.py')) + [ROOT/'desk'/x for x in ('settings.py','outcome.py','pipeline.py','breaker.py','watchlist.py','guards.py','shorts.py','charts.py')]
    files += [Path(m.__file__) for m in (entry, exit_planner, budget, tm)]
    hashes = {'code': fingerprint(files), 'universe': fingerprint([CACHE/'universe.json']), 'as_of': as_of.isoformat(), 'daily': fingerprint(CACHE.glob('*-1d.json')),
              'intraday': fingerprint(CACHE/(s+'-5m.json') for s in symbols), 'start': days[0], 'end': days[-1], 'replays': args.replays}
    saved = OUT/'experiment.json'
    seal_path = OUT/'selection.json'
    if saved.exists():
        result = json.loads(saved.read_text())
        if result['hashes'] != hashes:
            raise ValueError('This holdout has already been exposed. Existing results preserved; changed code/data cannot be presented as a fresh locked test.')
        print('Reusing sealed experiment; no holdout replay.', flush=True)
        return result
    if seal_path.exists():
        raise ValueError('Selection was sealed but the experiment did not finish. Resume needs inspection; do not silently reselect on exposed test data.')
    variants_log, validation_runs, training_runs = [], {}, {}
    spy_val = benchmark(store, validation, 400)
    spy_train = benchmark(store, train, 400)
    jobs = [(v, train, validation, args.replays, prepared, json.dumps(hashes,sort_keys=True)) for v in variants()]
    with concurrent.futures.ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context('spawn')) as pool:
        for v, (tr, va) in zip(variants(), pool.map(score_variant, jobs)):
            training_runs[v['name']] = tr
            validation_runs[v['name']] = va
            score = metrics(va, spy_val)['return_pct']-metrics(spy_val)['return_pct']
            mt = metrics(tr, spy_train)
            # Fixed-rule rolling five-session scores within tuning; no held-out re-selection.
            windows = [math.prod(1+x['return_'] for x in tr['daily'][i:i+5])-1 for i in range(0,len(train)-4,5)]
            variants_log.append(dict(name=v['name'], parameters=json.dumps(v, sort_keys=True),
                                 tuning_return_pct=mt['return_pct'], tuning_sharpe=mt['sharpe'],
                                 tuning_rolling_5d_mean_pct=100*statistics.mean(windows) if windows else None,
                                 validation_return_pct=metrics(va)['return_pct'], validation_excess_pct=score,
                                 validation_trades=len(va['trades'])))
            write_csv(OUT/'variants.csv', variants_log)
    # Explicit stable tie-break: higher validation excess, then lower turnover, then earlier declaration.
    winner_row = max(variants_log, key=lambda v: (v['validation_excess_pct'], -v['validation_trades']))
    winner = next(v for v in variants() if v['name'] == winner_row['name'])
    seal = dict(hashes=hashes, train=train, validation=validation, locked=locked, winner=winner,
                selection='highest validation net excess return at $400/base costs; tie: fewer trades then declaration order',
                predefined_test_controls=['base', 'trend'], trials=30)
    with seal_path.open('x') as f:
        json.dump(seal, f, indent=2)
    print('Selection sealed:', winner['name'], '; opening locked test once.', flush=True)
    runs = {}
    final_variants = {v['name']: v for v in [variants()[0], winner, {'name':'trend','settings':{},'trend':True}]}
    for name, v in final_variants.items():
        for capital in (400, 2000):
            for cost in (0, 1, 2):
                label = '%s | $%d | costs %dx | locked' % (name, capital, cost)
                print(label, flush=True)
                runs[label] = simulate(store, locked, v, capital, cost, replays=args.replays, prepared=prepared)
    paper_label = 'base | $400 | paper 4x immediate settlement | locked'
    runs[paper_label] = simulate(store, locked, variants()[0], paper=True, replays=args.replays, prepared=prepared)
    winner_label = '%s | $400 | costs 1x | locked' % winner['name']
    spy = {str(capital): benchmark(store, locked, capital) for capital in (400,2000)}
    inference = bootstrap(runs[winner_label], spy['400'])
    validation_inference = bootstrap(validation_runs[winner['name']], spy_val)
    validation_inference['bonferroni_p_30'] = min(1, validation_inference['p_two_sided']*30)
    result = dict(hashes=hashes, seal=seal, manifest=manifest, daily_errors=errors, universe_size=len(store.daily),
                  intraday_symbols=len(symbols), warmup=all_days[:20], winner_label=winner_label,
                  runs=runs, spy=spy, validation_spy=spy_val, training_spy=spy_train,
                  validation_runs=validation_runs, training_runs=training_runs, variants=variants_log,
                  inference=inference, validation_inference=validation_inference)
    saved.write_text(json.dumps(result, separators=(',',':')))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='fetch missing free data; cached files are never refreshed')
    parser.add_argument('--start', help='first evaluation date, after the 20-session warmup')
    parser.add_argument('--end', help='last evaluation date, inclusive')
    parser.add_argument('--variant', choices=['base'], default='base', help='include base and the fixed 30-variant experiment')
    parser.add_argument('--replays', type=int, default=10000, help='desk Time Machine paths per eligible plan (default 10000)')
    args = parser.parse_args()
    if not 1000 <= args.replays <= 100000:
        parser.error('--replays must be between 1000 and 100000')
    if args.start:
        dt.date.fromisoformat(args.start)
    if args.end:
        dt.date.fromisoformat(args.end)
    if args.start and args.end and args.start > args.end:
        parser.error('--start must not follow --end')
    result = run_experiment(args)
    from report import publish
    publish(result)
    print('Report:', OUT/'report.html')
    print('Headline:', result['winner_label'], metrics(result['runs'][result['winner_label']])['return_pct'])


if __name__ == '__main__':
    main()
