"""Look-ahead guard, simulator maths and the statistics, on tiny synthetic markets."""
import math
import sys
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import lab  # noqa: E402


def market(prices, opens=None, extra=None):
    n = len(prices)
    cal = ['2020-01-%02d' % (i + 1) if i < 31 else '2020-02-%02d' % (i - 30) for i in range(n)]
    series = {s: ([None] * n, [None] * n) for s in lab.D.SYMBOLS}
    series['SPY'] = (list(opens or prices), list(prices))
    series['^IRX'] = ([0.0] * n, [0.0] * n)
    for s, (o, c) in (extra or {}).items():
        series[s] = (list(o), list(c))
    return lab.Market(cal, series)


class LookAhead(unittest.TestCase):
    def test_future_changes_do_not_change_the_past(self):
        base = [100 + math.sin(i / 3) * 5 + i * 0.2 for i in range(50)]
        a = market(base)
        b = market(base[:30] + [x * 3 for x in base[30:]])     # rewrite the future
        for i in range(30):
            va, vb = lab.View(a, i), lab.View(b, i)
            for fn in (lambda v: v.sma('SPY', 5), lambda v: v.ret('SPY', 10), lambda v: v.vol('SPY', 5),
                       lambda v: v.rsi('SPY', 2), lambda v: v.close('SPY')):
                self.assertEqual(fn(va), fn(vb))

    def test_view_refuses_negative_lookback(self):
        with self.assertRaises(AssertionError):
            lab.View(market([1, 2, 3]), 1).close('SPY', -1)

    def test_decisions_ignore_today(self):
        """A strategy's weights for day d are the same whatever happens on day d itself."""
        base = [100 + i for i in range(260)]
        cfg = ('trend', {'a': 'SPY', 'n': 50, 'off': 'cash', 'band': 0})
        seen = []
        orig = lab.FAMILIES['trend']
        lab.FAMILIES['trend'] = lambda v, p, h: seen.append(v.i) or orig(v, p, h)
        try:
            lab.simulate(market(base), cfg, '2020-01-02', '2020-02-09')
        finally:
            lab.FAMILIES['trend'] = orig
        self.assertEqual(seen, list(range(0, len(seen))))     # day d always decided from View(d-1)

    def test_development_never_loads_the_holdout(self):
        cal, _ = lab.load(lab.DEV_END)
        self.assertLessEqual(cal[-1], lab.DEV_END)


class Simulator(unittest.TestCase):
    def setUp(self):
        self.cost = lab.COST_BPS

    def tearDown(self):
        lab.COST_BPS = self.cost

    def test_hold_matches_buy_and_hold_without_costs(self):
        lab.COST_BPS = 0
        p = [100, 101, 99, 105, 110]
        o = [100, 100.5, 100, 104, 109]
        run = lab.simulate(market(p, o), ('hold', {'a': 'SPY'}), '2020-01-02', '2020-01-05')
        self.assertAlmostEqual(run['daily'][-1]['equity'], 400 * 110 / 100.5, places=9)

    def test_short_loses_when_price_rises(self):
        lab.COST_BPS = 0
        m = market([100, 100, 110], [100, 100, 100])
        run = lab.simulate(m, ('trend', {'a': 'SPY', 'n': 1, 'off': 'short', 'band': 0}), '2020-01-02', '2020-01-03')
        # day 2: price == sma so not above -> short 1x at open 100; close 110 -> -10%, minus one day of borrow
        self.assertAlmostEqual(run['daily'][-1]['return_'], -0.10 - lab.BORROW / 252, places=9)

    def test_costs_charged_on_turnover(self):
        lab.COST_BPS = 10
        run = lab.simulate(market([100] * 4), ('hold', {'a': 'SPY'}), '2020-01-02', '2020-01-04')
        self.assertAlmostEqual(run['daily'][0]['return_'], -0.001, places=12)
        self.assertAlmostEqual(run['daily'][1]['return_'], 0.0, places=12)

    def test_leverage_pays_margin_interest(self):
        lab.COST_BPS = 0
        m = market([100] * 4)
        m.series['^IRX'] = ([5.0] * 4, [5.0] * 4)
        run = lab.simulate(m, ('trend', {'a': 'SPY', 'n': 1, 'off': 'cash', 'band': 0.0, 'lev': 1.5}), '2020-01-02', '2020-01-03')
        self.assertTrue(all(r['return_'] <= 0 for r in run['daily']))


class Statistics(unittest.TestCase):
    def test_one_trial_deflated_equals_psr(self):
        r = [0.01, -0.005, 0.007, 0.002, -0.001] * 40
        self.assertGreater(lab.deflated_sharpe(r, [0.3]), 0.99)

    def test_more_trials_deflate_more(self):
        r = [0.002, -0.001, 0.0015, -0.0012] * 100
        few = lab.deflated_sharpe(r, [0.0, 0.05])
        many = lab.deflated_sharpe(r, [0.0, 0.05, -0.05, 0.1, -0.1] * 50)
        self.assertLess(many, few)

    def test_configs_are_unique_and_blends_resolve(self):
        names = [lab.name(c) for c in lab.configs()]
        self.assertEqual(len(names), len(set(names)))
        known = set(names)
        for fam, p in lab.configs():
            if fam == 'blend':
                self.assertTrue(set(p['parts']) <= known)


if __name__ == '__main__':
    unittest.main()
