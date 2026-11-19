import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import backtest as bt


def bar(day='2026-10-19', m=570, o=10, h=10.2, l=9.8, c=10, v=2000000):
    return dict(date=day, minute=m, start=bt.outcome._hhmm(m), o=o, h=h, l=l, c=c, v=v)


def history():
    return [bar((dt.date(2026, 9, 1)+dt.timedelta(days=i)).isoformat()) for i in range(48)]


class Blindness(unittest.TestCase):
    def test_future_daily_and_intraday_requests_fail(self):
        store = bt.Store({'X': [bar('2026-10-16'), bar()]}, lambda s: [bar(), bar(m=575)])
        clock = bt.Clock()
        clock.advance('2026-10-19 09:35')
        view = store.view(clock)
        self.assertEqual(len(view.session('X')), 1)
        self.assertEqual([b['date'] for b in view.daily('X')], ['2026-10-16'])
        with self.assertRaisesRegex(AssertionError, 'future'):
            view.session('X', through=580)
        with self.assertRaisesRegex(AssertionError, 'future'):
            view.daily('X', '2026-10-20')
        with self.assertRaises(AssertionError):
            clock.advance('2026-10-18 09:35')

    def test_poison_future_ohlcv_is_never_read(self):
        clock = bt.Clock()
        class Poison(dict):
            def __getitem__(self, key):
                if key in ('o','h','l','c','v') and clock.now < '2026-10-19 09:40':
                    raise AssertionError('FUTURE CANDLE READ')
                return super().__getitem__(key)
            def keys(self):
                if clock.now < '2026-10-19 09:40':
                    raise AssertionError('FUTURE CANDLE COPIED')
                return super().keys()
        future = Poison(bar(m=575, c=999))
        store = bt.Store({}, lambda s: [bar(), future])
        clock.advance('2026-10-19 09:35')
        self.assertEqual(store.view(clock).session('X')[0]['c'], 10)
        with self.assertRaisesRegex(AssertionError, 'FUTURE'):
            future['c']
        clock.advance('2026-10-19 09:40')
        self.assertEqual(len(store.view(clock).session('X')), 2)

    def test_view_returns_detached_bars(self):
        original = bar()
        store = bt.Store({}, lambda s: [original])
        clock = bt.Clock()
        clock.advance('2026-10-19 09:35')
        store.view(clock).session('X')[0]['c'] = 1000
        self.assertEqual(original['c'], 10)

    def test_movers_ignore_today(self):
        prior = history()
        store = bt.Store({'X': prior + [bar(c=100000, v=10**15)], 'SPY': prior}, lambda s: [])
        clock = bt.Clock()
        clock.advance('2026-10-19 09:00')
        view = store.view(clock)
        view._symbols = ('X',)
        screen = bt.screens(view, '2026-10-18')
        self.assertEqual(screen['most_actives'][0]['regularMarketPrice'], 10)
        self.assertEqual(screen['day_gainers'][0]['change'], 0)


class Executions(unittest.TestCase):
    def setUp(self):
        self.order = dict(trigger=590, until=600, limit=10, stop=9, target=12, qty=10, reserve=100.15)

    def test_never_fills_trigger_candle(self):
        self.assertIsNone(bt.fill_price(self.order, bar(m=585, l=1)))
        self.assertIsNone(bt.fill_price(self.order, bar(m=590, o=11, l=10.01, h=12)))
        self.assertEqual(bt.fill_price(self.order, bar(m=590, o=11, l=9.9, h=12)), 10)
        self.assertEqual(bt.fill_price(self.order, bar(m=590, o=9.5, l=9)), 9.5)
        self.assertIsNone(bt.fill_price(self.order, bar(m=600)))

    def test_stop_first_and_gap(self):
        p = dict(self.order, fill_minute=590)
        self.assertEqual(bt.exit_price(p, bar(m=595, h=13, l=8), 955), (9, 'stop'))
        self.assertEqual(bt.exit_price(p, bar(m=595, o=8, h=13, l=7), 955), (8, 'stop'))
        self.assertEqual(bt.exit_price(p, bar(m=590, o=8, h=13, l=7), 955), (8, 'stop'))
        self.assertIsNone(bt.exit_price(p, bar(m=590, h=13, l=9.5), 955))
        self.assertEqual(bt.exit_price(p, bar(m=955, o=10.5, h=13, l=8), 955), (10.5, 'flat'))

    def test_cost_math_and_settlement(self):
        a = bt.Account(400)
        fee = bt.per_side_cost(bt.S.DEFAULTS, 1)
        self.assertAlmostEqual(fee, .015)
        self.assertEqual(bt.per_side_cost(bt.S.DEFAULTS, 0), 0)
        self.assertEqual(bt.per_side_cost(bt.S.DEFAULTS, 2), .03)
        a.buy('X', self.order, 10, fee, bar(m=590))
        trade = a.sell('X', 11, fee, '2026-10-16', 600, 'target')
        self.assertAlmostEqual(trade['pnl'], 9.70)
        self.assertAlmostEqual(trade['r'], .97)
        self.assertAlmostEqual(a.equity(), 409.70)
        self.assertAlmostEqual(a.available(), 299.85)
        a.settle('2026-10-17')
        self.assertAlmostEqual(a.available(), 299.85)
        a.settle('2026-10-19')
        self.assertAlmostEqual(a.available(), 409.70)

    def test_reservation_and_no_leverage(self):
        a = bt.Account(100)
        a.orders['X'] = self.order
        self.assertEqual(a.available(), 0)
        with self.assertRaises(AssertionError):
            a.buy('X', self.order, 10, .015, bar(m=590))

    def test_paper_buying_power_is_rechecked(self):
        a = bt.Account(100, paper=True)
        order = dict(self.order, qty=40, reserve=400)
        a.buy('X', order, 10, 0, bar(m=590))
        with self.assertRaises(AssertionError):
            a.buy('Y', dict(self.order,qty=1), 10, 0, bar(m=595))

    def test_paper_profile_and_early_close(self):
        a = bt.Account(400, paper=True)
        self.assertEqual(a.available(), 1600)
        a.buy('X', self.order, 10, 0, bar(m=590))
        a.sell('X', 11, 0, '2026-10-16', 600, 'target')
        self.assertEqual(a.unsettled, [])
        rules = bt.S.for_day(bt.S.DEFAULTS, '13:00')
        self.assertEqual(rules['flat_by'], '12:55')
        self.assertEqual(bt.close_minute('2026-11-27'), 780)


class Statistics(unittest.TestCase):
    def test_spy_includes_first_close_to_close_return(self):
        store = bt.Store({'SPY':[bar('2026-10-16', c=100), bar('2026-10-19', c=110), bar('2026-10-20', c=99)]}, lambda s: [])
        run = bt.benchmark(store, ['2026-10-19', '2026-10-20'], 400)
        self.assertAlmostEqual(run['daily'][0]['equity'], 440)
        self.assertAlmostEqual(run['daily'][-1]['equity'], 396)
        m = bt.metrics(run)
        self.assertAlmostEqual(m['return_pct'], -1)
        self.assertAlmostEqual(m['max_drawdown_pct'], -10)
        self.assertIsNone(m['cagr_pct'])
        inf = bt.bootstrap(run, run, samples=10000)
        self.assertEqual(inf['p_two_sided'], 1)
        self.assertEqual(inf['ci_low_pct'], 0)

    def test_initial_loss_in_drawdown(self):
        dd, duration = bt.drawdowns([400, 360, 380, 400])
        self.assertAlmostEqual(min(dd), -.1)
        self.assertEqual(duration, 2)

    def test_variants_are_fixed_and_unique(self):
        self.assertEqual(len(bt.variants()), 30)
        self.assertEqual(len({v['name'] for v in bt.variants()}), 30)


class Integration(unittest.TestCase):
    def test_replay_prefix_unchanged_by_future_prices(self):
        day = '2026-10-19'
        bars = [bar(day, m) for m in range(570,960,5)]
        for b in bars[3:]:
            b.update(o=10.5, h=10.8, l=10.3, c=10.5)
        bars[10].update(h=12, c=11.5)
        rules = {'name':'tiny', 'settings':{'core_watchlist':['X'], 'auto_watchlist_movers':0}}
        daily = {'X':history(), 'SPY':history()}
        prepared = {day: {k:[] for k in bt.watchlist.SCREENS}}
        with patch.object(bt, 'odds', return_value=10):
            a = bt.simulate(bt.Store(daily, lambda s: bars), [day], rules, prepared=prepared)
            b = bt.simulate(bt.Store(daily, lambda s: bars+[bar('2026-10-20', c=100000)]), [day], rules, prepared=prepared)
        self.assertEqual(a, b)
        self.assertEqual(len(a['trades']), 1)
        trade = a['trades'][0]
        self.assertLess(trade['signal_at'], trade['exit_at'])
        self.assertEqual(trade['reason'], 'target')
        self.assertGreater(a['daily'][0]['unsettled'], 0)
        self.assertLessEqual(a['daily'][0]['settled'], 400)



class RiskAndMissingData(unittest.TestCase):
    def test_breakers_use_desk_rules(self):
        st = dict(bt.S.DEFAULTS, mode='live')
        trades = [dict(status='closed', pnl=-40, exit_at='2026-10-19 10:00')]
        status = bt.breaker.status(st, trades, 360, '2026-10-19')
        self.assertEqual(status['tripped']['key'], 'weekly')
        trades.append(dict(status='closed', pnl=-30, exit_at='2026-10-20 10:00'))
        status = bt.breaker.status(st, trades, 330, '2026-10-20')
        self.assertEqual(status['tripped']['key'], 'drawdown')
        self.assertTrue(status['needs_reset'])

    def test_missing_pending_candle_is_not_silently_a_no_fill(self):
        day = '2026-10-19'
        bars = [bar(day,m) for m in range(570,960,5) if m != 590]
        for b in bars:
            if b['minute'] >= 585:
                b.update(o=10.5,h=10.8,l=10.3,c=10.5)
        st = {'name':'missing', 'settings':{'core_watchlist':['X'],'auto_watchlist_movers':0}}
        prepared = {day:{k:[] for k in bt.watchlist.SCREENS}}
        with patch.object(bt,'odds',return_value=10):
            with self.assertRaisesRegex(ValueError,'pending order'):
                bt.simulate(bt.Store({'SPY':history(),'X':history()},lambda s:bars),[day],st,prepared=prepared)

    def test_sizing_equity_excludes_unrealized_gain(self):
        a = bt.Account(400)
        p = dict(qty=10,limit=10,stop=9,target=12,trigger=590,until=600,reserve=100)
        a.buy('X',p,10,0,bar(m=590,c=20))
        self.assertEqual(a.equity(),500)
        self.assertEqual(a.risk_equity(),400)


class FrozenDates(unittest.TestCase):
    def test_partial_session_never_enters_evaluation(self):
        bars = [bar('2026-10-19',m) for m in range(570,960,5)] + [bar('2026-10-20')]
        self.assertEqual(bt.completed_days(bars,dt.datetime(2026,10,20,10)),['2026-10-19'])
        self.assertEqual(bt.completed_days(bars,dt.datetime(2026,11,20,10)),['2026-10-19'])
        bars += [bar('2026-10-20',m) for m in range(575,960,5)]
        self.assertEqual(bt.completed_days(bars,dt.datetime(2026,10,20,15,59)),['2026-10-19'])
        self.assertEqual(bt.completed_days(bars,dt.datetime(2026,10,20,16)),['2026-10-19','2026-10-20'])

    def test_snapshot_clock_is_persistent(self):
        import os,tempfile
        with tempfile.TemporaryDirectory(dir=bt.HOME/'tmp') as folder:
            source,manifest = Path(folder)/'data.json',Path(folder)/'snapshot.json'
            source.write_text('{}')
            os.utime(source,(1792418400,1792418400))
            first = bt.snapshot_cutoff(source,manifest)
            os.utime(source,(1795010400,1795010400))
            self.assertEqual(bt.snapshot_cutoff(source,manifest),first)


class Insolvency(unittest.TestCase):
    def test_zero_equity_stays_flat_without_invalid_daily_return(self):
        day = '2026-10-19'
        bars = [bar(day,m) for m in range(570,960,5)]
        rules = {'name':'zero', 'settings':{'core_watchlist':['X'],'auto_watchlist_movers':0}}
        prepared = {day:{k:[] for k in bt.watchlist.SCREENS}}
        result = bt.simulate(bt.Store({'SPY':history(),'X':history()},lambda s:bars),[day],rules,
                             initial=0,paper=True,prepared=prepared)
        self.assertEqual(result['daily'][0]['equity'],0)
        self.assertEqual(result['daily'][0]['return_'],0)
        self.assertEqual(result['trades'],[])


class ReportCharts(unittest.TestCase):
    def test_offline_svg_is_well_formed_and_labels_escaped(self):
        import xml.etree.ElementTree as ET
        import report
        output = report.chart('A < B', [('Strategy',[400,390,420]),('SPY',[400,410,415])],
                              ['Start','10-19','10-20'],'Account value ($)')
        svg = output[output.index('<svg'):output.index('</svg>')+6]
        root = ET.fromstring(svg)
        self.assertEqual(root.attrib['role'],'img')
        self.assertEqual(len(root.findall('polyline')),2)
        self.assertIn('A &lt; B',output)
        self.assertIn('10-20',output)
        self.assertNotIn('<script',output)
        self.assertNotIn('http',output)


if __name__ == '__main__':
    unittest.main()
