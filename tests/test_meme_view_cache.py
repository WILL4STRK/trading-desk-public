"""The /api/meme view cache (desk/meme.py view, stats): display only. Cached and fresh outputs are identical, the cache
rebuilds once per input change and once for concurrent callers, callers cannot corrupt it, and the trading files
(shadows.jsonl, model.json) are byte-identical over the same passes with and without the cache (docs/MEME.md section 17:
nothing D1 freezes may change). No network: the fake API from test_meme."""
import json
import os
import random
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from desk import meme
from desk import settings as S

from .base import DeskCase
from .test_meme import T0, FakeAPI


_recording = []                                             # non-empty while the audit hook below records


def _clear():
    meme._view_cache.clear()
    meme._stats_memo.clear()
    meme._fw_cache.clear()


def _passes(api, n=4, between=None):
    out = []
    for i in range(n):
        api.pairs[next(iter(api.pairs))]["priceUsd"] = str(1.0 + 0.05 * (i % 3) - 0.2 * (i == 2))
        out.append(meme.run(now=T0 + 150 * i, fetch=api))
        if between:
            between()
    return out


class StatsMemo(DeskCase):
    def rows(self, n=120, coins=12, seed=3):
        r = random.Random(seed)
        return [{"key": "solana:C%d" % (i % coins), "net_pct": round(r.uniform(-30, 40), 3), "ts": T0 + i * 60,
                 "policy": "all"} for i in range(n)]

    def test_memo_equals_fresh_and_returns_copies(self):
        _clear()
        rows = self.rows()
        fresh = meme._stats_fresh(rows)
        a = meme.stats(rows)
        self.assertEqual(a, fresh)
        self.assertIsNotNone(a["mean_lo"])                      # 12 coins: the bootstrap ran
        a["mean"] = 999
        a["folds"].append(1)
        self.assertEqual(meme.stats(rows), fresh)               # a caller's change never reaches the memo
        with mock.patch.object(meme, "coin_bootstrap", side_effect=AssertionError("recomputed")):
            self.assertEqual(meme.stats(list(rows)), fresh)     # same inputs: served from the memo

    def test_memo_key_is_every_input_stats_reads(self):
        _clear()
        rows = self.rows()
        base = meme.stats(rows)
        for field, value in (("net_pct", 77.0), ("key", "solana:NEW"), ("ts", T0 - 1)):
            changed = [dict(x) for x in rows]
            changed[5][field] = value
            self.assertEqual(meme.stats(changed), meme._stats_fresh(changed), field)
        reordered = rows[1:] + rows[:1]
        self.assertEqual(meme.stats(reordered), meme._stats_fresh(reordered))
        self.assertEqual(meme.stats(rows), base)

    def test_memo_is_bounded(self):
        _clear()
        with mock.patch.object(meme, "STATS_MEMO_MAX", 3):
            for s in range(6):
                meme.stats(self.rows(n=10, coins=2, seed=s))
            self.assertEqual(len(meme._stats_memo), 3)


class ViewCache(DeskCase):
    def setUp(self):
        super().setUp()
        S.save({"meme_policy": "calm_aged"})
        self.api = FakeAPI()
        _clear()

    def test_cached_equals_fresh_after_passes(self):
        _passes(self.api)
        cached = meme.view()
        _clear()
        fresh = meme._view_fresh()
        self.assertEqual(json.dumps(cached, sort_keys=True), json.dumps(fresh, sort_keys=True))
        self.assertEqual(json.dumps(meme.view(), sort_keys=True), json.dumps(fresh, sort_keys=True))

    def test_rebuilds_once_per_change(self):
        _passes(self.api, n=2)
        with mock.patch.object(meme, "_view_fresh", wraps=meme._view_fresh) as fresh:
            meme.view(), meme.view(), meme.view()
            self.assertEqual(fresh.call_count, 1)
            time.sleep(0.02)
            meme.run(now=T0 + 900, fetch=self.api)              # a pass rewrites model.json: the next call rebuilds
            v = meme.view()
            self.assertEqual(fresh.call_count, 2)
            meme.view()
            self.assertEqual(fresh.call_count, 2)
        _clear()
        self.assertEqual(json.dumps(v, sort_keys=True), json.dumps(meme._view_fresh(), sort_keys=True))

    def test_settings_change_rebuilds(self):
        _passes(self.api, n=1)
        before = meme.view()
        time.sleep(0.02)
        S.save({"meme_policy": "hype"})
        after = meme.view()
        self.assertNotEqual(before["policy"], after["policy"])

    def test_concurrent_callers_share_one_rebuild(self):
        _passes(self.api, n=2)
        _clear()
        real = meme._view_fresh

        def slow():
            time.sleep(0.3)
            return real()
        with mock.patch.object(meme, "_view_fresh", side_effect=slow) as fresh:
            out = []
            ts = [threading.Thread(target=lambda: out.append(meme.view())) for _ in range(4)]
            [t.start() for t in ts]
            [t.join() for t in ts]
        self.assertEqual(fresh.call_count, 1)
        self.assertEqual(len({json.dumps(v, sort_keys=True) for v in out}), 1)

    def test_every_path_read_is_a_view_input(self):
        """An audit hook records every file open, directory listing and sqlite connect made on this thread while
        _view_fresh() runs; each must be one of view_inputs(), or a change to it would not rebuild the cache."""
        _passes(self.api, n=3)
        meme._view_fresh()                                      # warm: lazy imports open their own files
        allowed = {os.path.normcase(os.path.abspath(str(p))) for p in meme.view_inputs()}
        seen, me = [], threading.get_ident()

        def hook(event, args):
            if _recording and threading.get_ident() == me and event in ("open", "os.listdir", "os.scandir",
                                                                         "sqlite3.connect", "glob.glob"):
                seen.append((event, args[0] if args else None))
        _recording.append(True)
        sys.addaudithook(hook)                                  # cannot be removed: _recording switches it off
        try:
            meme._view_fresh()
        finally:
            _recording.clear()
        paths = [(e, a) for e, a in seen if not isinstance(a, int)]
        self.assertTrue(paths)                                  # not vacuous: the view does read files
        outside = sorted({(e, str(a)) for e, a in paths
                          if os.path.normcase(os.path.abspath(os.fsdecode(a) if a is not None else "")) not in allowed})
        self.assertEqual(outside, [])
        self.assertEqual([a for e, a in seen if isinstance(a, int)], [])     # no reads through a bare descriptor

    def test_callers_get_copies(self):
        _passes(self.api, n=2)
        v = meme.view()
        v["account"]["cash"] = -1
        v["candidates"].clear()
        w = meme.view()
        self.assertNotEqual(w["account"]["cash"], -1)
        _clear()
        self.assertEqual(json.dumps(w, sort_keys=True), json.dumps(meme._view_fresh(), sort_keys=True))


class TradingFilesUnchanged(DeskCase):
    """D1 check: the same passes, once with view() (cached) called after every pass and once with the uncached
    _view_fresh() instead, leave shadows.jsonl and model.json byte-identical."""

    def run_in(self, folder, between):
        os.environ["DESK_DATA"] = folder
        S.save({"meme_policy": "calm_aged"})
        meme._last.clear(), meme._cool.clear()
        _clear()
        _passes(FakeAPI(), n=6, between=between)
        f = meme.folder()
        return {n: (f / n).read_bytes() for n in ("shadows.jsonl", "model.json") if (f / n).exists()}

    def test_shadows_and_model_byte_identical(self):
        a = tempfile.mkdtemp(prefix="desk-cache-a-")
        b = tempfile.mkdtemp(prefix="desk-cache-b-")
        self.addCleanup(shutil.rmtree, a, True)
        self.addCleanup(shutil.rmtree, b, True)
        prev = os.environ["DESK_DATA"]
        self.addCleanup(os.environ.__setitem__, "DESK_DATA", prev)
        with mock.patch.object(time, "time", return_value=T0 + 1000):
            with_cache = self.run_in(a, lambda: (meme.view(), meme.view()))       # the second call is a cache hit
            without = self.run_in(b, lambda: meme._view_fresh())
        self.assertEqual(set(with_cache), {"shadows.jsonl", "model.json"})
        for name in with_cache:
            self.assertEqual(with_cache[name], without[name], name)
