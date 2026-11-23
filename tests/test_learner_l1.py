"""L1 learner (docs/MEME.md section 18): no exit field reaches a prediction, void, missed, the hash chain, weights move only on
a close, restart without duplicates, the look's random control and bootstrap. Synthetic data in temp folders only."""
import json
import random
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from research.learner import learner as L

T0 = L.T18 + 1000.0


def shadow(key="solana:A", ts=T0, r1h=2.0, liq=50000.0, policy="all", screen="pass", **extra):
    return dict({"key": key, "symbol": "X", "chain": "solana", "policy": policy, "ts": ts, "at": "x", "px": 1.0, "screen": screen,
                 "checked": ts, "strict": "pass", "news": False, "veto": [],
                 "f": {"age_h": 10.0, "r5": 1.0, "r1h": r1h, "r6h": 3.0, "r24h": 4.0, "v1h": 9000.0, "vacc": 1.2, "rvol": 0.8,
                       "tx_m5": 40, "buy_ratio_m5": 0.6, "liq": liq, "mcap": 1e5, "v24": 1e5, "boosts_active": 0, "news": False}},
                **extra)


def closed(sh, exit_ts, net, why="time"):
    return dict(sh, exit_at="y", exit_ts=exit_ts, exit_px=1.1, why=why, net_pct=net)


class Desk:
    """A fake desk folder: model.json (open shadows, tracked) and shadows.jsonl (closed lines)."""

    def __init__(self, root):
        self.dir = Path(root) / "desk"
        self.dir.mkdir()
        self.open, self.tracked = [], {}
        self.write()

    def write(self):
        (self.dir / "model.json").write_text(json.dumps({"shadows": self.open, "tracked": self.tracked}, indent=2))

    def add_open(self, sh, dex="pumpswap"):
        self.open.append(sh)
        self.tracked[sh["key"]] = {"dex": dex}
        self.write()

    def close(self, sh, exit_ts, net, partial=False):
        self.open = [x for x in self.open if not (x["key"] == sh["key"] and x["ts"] == sh["ts"])]
        self.write()
        line = json.dumps(closed(sh, exit_ts, net), separators=(",", ":"))
        with open(self.dir / "shadows.jsonl", "a", encoding="utf8", newline="\n") as fh:
            fh.write(line if partial else line + "\n")

    def finish_partial(self):
        with open(self.dir / "shadows.jsonl", "a", encoding="utf8", newline="\n") as fh:
            fh.write("\n")


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.desk = Desk(self.tmp)
        self.home = Path(self.tmp) / "home"
        self.clock = Clock(T0 + 5)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def learner(self):
        return L.Learner(self.desk.dir, self.home, clock=self.clock)

    def preds(self):
        return L._rows(self.home / "out" / "predictions.jsonl")

    def closes(self):
        return L._rows(self.home / "out" / "closes.jsonl")


class NoExitField(Base):
    def test_exit_fields_on_an_open_shadow_never_reach_the_prediction(self):
        # even if model.json somehow carried exit fields, the prediction is the same as without them
        a = self.learner()
        self.desk.add_open(shadow(key="solana:A"))
        a.poll()
        b_home = Path(self.tmp) / "home2"
        tainted = shadow(key="solana:A", exit_ts=T0 + 60, net_pct=29.0, why="target", exit_px=9.9, exit_at="z", peak=9.9)
        self.desk.open = [tainted]
        self.desk.write()
        b = L.Learner(self.desk.dir, b_home, clock=self.clock)
        b.poll()
        p1, p2 = self.preds()[0], L._rows(b_home / "out" / "predictions.jsonl")[0]
        self.assertEqual(p1["features"], p2["features"])
        self.assertEqual(p1["pred"], p2["pred"])
        text = json.dumps(p2["features"])
        for f in L.EXIT_FIELDS:
            self.assertNotIn(f, text)

    def test_raw_features_reads_only_the_entry_fields(self):
        sh = closed(shadow(), T0 + 60, 25.0)
        raw = L.raw_features(sh, "raydium")
        self.assertEqual(set(raw), {"num", "chain", "dex"})
        self.assertEqual(set(raw["num"]), set(L.NUMERIC))

    def test_log_transform_and_missing_flag(self):
        sh = shadow(liq=None)
        sh["f"]["v1h"] = 999.0
        raw = L.raw_features(sh, None)
        self.assertIsNone(raw["num"]["liq"])
        self.assertAlmostEqual(raw["num"]["v1h"], 6.907755, places=5)
        x = L.vector(raw, L.new_model()["stats"])
        self.assertEqual(len(x), L.DIM)
        self.assertEqual(x[len(L.NUMERIC) + L.NUMERIC.index("liq")], 1.0)    # liq missing flag
        self.assertEqual(x[-1], 1.0)                                         # dex missing


class Void(Base):
    def test_prediction_at_or_after_exit_is_void(self):
        lr = self.learner()
        sh = shadow()
        self.desk.add_open(sh)
        self.clock.t = T0 + 100
        lr.poll()                                  # predicted at T0 + 100
        self.desk.close(sh, T0 + 100, 5.0)         # exit at the same second
        lr.poll()
        self.assertEqual(self.closes()[0]["status"], "void")
        self.assertEqual(lr.state["counts"]["void"], 1)

    def test_prediction_before_exit_is_not_void(self):
        lr = self.learner()
        sh = shadow()
        self.desk.add_open(sh)
        lr.poll()
        self.desk.close(sh, self.clock.t + 1, 5.0)
        lr.poll()
        self.assertEqual(self.closes()[0]["status"], "predicted")

    def test_void_is_never_scored(self):
        lr = self.learner()
        lr.state["counts"]["closes"] = L.WARMUP           # past warm-up
        sh = shadow()
        self.desk.add_open(sh)
        self.clock.t = T0 + 500
        lr.poll()
        self.desk.close(sh, T0 + 400, 5.0)
        lr.poll()
        sigs, _ = L.scored_signals(self.home)
        self.assertEqual(sigs, [])


class Missed(Base):
    def test_first_seen_closed_is_missed_and_not_predicted(self):
        lr = self.learner()
        sh = shadow()
        self.desk.close(sh, T0 + 60, 3.0)          # never seen open
        lr.poll()
        self.assertEqual(self.preds(), [])
        self.assertEqual(self.closes()[0]["status"], "missed")
        self.assertEqual(lr.state["counts"]["missed"], 1)
        self.assertEqual(lr.state["model"]["n"], 1)      # it still trains

    def test_population_filter(self):
        lr = self.learner()
        self.desk.add_open(shadow(key="a", policy="quick"))
        self.desk.add_open(shadow(key="b", screen="fail"))
        self.desk.add_open(shadow(key="c", ts=L.T18 - 1))
        self.desk.add_open(shadow(key="d", ts=L.T18))
        lr.poll()
        self.assertEqual(self.preds(), [])


class Chain(Base):
    def test_edited_line_is_caught(self):
        lr = self.learner()
        for i in range(3):
            self.desk.add_open(shadow(key="k%d" % i))
        lr.poll()
        p = self.home / "out" / "predictions.jsonl"
        self.assertEqual(L.verify(p), (3, None))
        lines = p.read_bytes().decode().split("\n")[:-1]
        row = json.loads(lines[1])
        row["pred"] = 99.0
        lines[1] = json.dumps(row, separators=(",", ":"), sort_keys=True)
        p.write_bytes(("\n".join(lines) + "\n").encode())
        n, why = L.verify(p)
        self.assertEqual(n, 3)                     # line 3's prev no longer matches the edited line 2
        self.assertIsNotNone(why)

    def test_first_line_chains_to_zeros(self):
        lr = self.learner()
        self.desk.add_open(shadow())
        lr.poll()
        self.assertEqual(self.preds()[0]["prev"], L.ZERO)


class WeightsOnlyOnClose(Base):
    def test_weights_change_only_after_a_close(self):
        lr = self.learner()
        h0 = L.weight_hash(lr.state["model"])
        for i in range(5):
            self.desk.add_open(shadow(key="k%d" % i, r1h=float(i)))
            lr.poll()
            self.assertEqual(L.weight_hash(lr.state["model"]), h0)
        self.assertEqual(lr.state["model"]["n"], 0)
        self.desk.close(self.desk.open[0], self.clock.t + 10, 12.0)
        lr.poll()
        self.assertNotEqual(L.weight_hash(lr.state["model"]), h0)
        self.assertEqual(lr.state["model"]["n"], 1)

    def test_target_is_clipped(self):
        a, b = L.new_model(), L.new_model()
        raw = L.raw_features(shadow(), "pumpswap")
        L.train(a, raw, 30.0)
        L.train(b, raw, 400.0)
        self.assertEqual(a["w"], b["w"])
        self.assertEqual(a["b"], b["b"])

    def test_warmup_flags(self):
        lr = self.learner()
        lr.state["counts"]["closes"] = L.WARMUP - 1
        self.desk.add_open(shadow(key="a"))
        lr.poll()
        lr.state["counts"]["closes"] = L.WARMUP
        self.desk.add_open(shadow(key="b"))
        lr.poll()
        self.assertEqual([p["warmup"] for p in self.preds()], [True, False])


class Restart(Base):
    def test_restart_resumes_without_duplicates(self):
        lr = self.learner()
        a, b = shadow(key="a"), shadow(key="b")
        self.desk.add_open(a)
        self.desk.add_open(b)
        lr.poll()
        self.desk.close(a, self.clock.t + 10, 4.0)
        lr.poll()
        del lr
        lr2 = self.learner()                       # a new process: same state, same logs
        lr2.poll()
        lr2.poll()
        self.assertEqual(len(self.preds()), 2)
        self.assertEqual(len(self.closes()), 1)
        self.desk.close(b, self.clock.t + 10, -4.0)
        lr2.poll()
        self.assertEqual(len(self.closes()), 2)
        self.assertEqual(L.verify(self.home / "out" / "predictions.jsonl")[1], None)
        self.assertEqual(L.verify(self.home / "out" / "closes.jsonl")[1], None)

    def test_crash_after_state_save_before_append(self):
        lr = self.learner()
        self.desk.add_open(shadow(key="a"))
        lr.flush = lambda: None                    # crash: state (with its outbox) saved, lines not appended
        lr.poll()
        self.assertEqual(self.preds(), [])
        lr2 = self.learner()                       # restart flushes the outbox once
        lr2.poll()
        self.assertEqual(len(self.preds()), 1)

    def test_crash_after_append_before_state_cleared(self):
        lr = self.learner()
        self.desk.add_open(shadow(key="a"))
        saved = lr.save
        calls = []

        def save_then_crash():
            calls.append(1)
            if len(calls) == 2:                    # crash before the save that clears the outbox after the append
                raise KeyboardInterrupt
            saved()

        lr.save = save_then_crash
        with self.assertRaises(KeyboardInterrupt):
            lr.poll()
        st = json.loads((self.home / "state" / "state.json").read_text())
        self.assertEqual(len(st["outbox"]), 1)     # the line is in the log and still in the outbox
        lr2 = self.learner()
        lr2.poll()
        self.assertEqual(len(self.preds()), 1)     # not written twice

    def test_partial_line_waits(self):
        lr = self.learner()
        sh = shadow()
        self.desk.add_open(sh)
        lr.poll()
        self.desk.close(sh, self.clock.t + 10, 4.0, partial=True)
        lr.poll()
        self.assertEqual(self.closes(), [])
        self.desk.finish_partial()
        lr.poll()
        self.assertEqual(len(self.closes()), 1)

    def test_unreadable_model_json_skips_the_poll(self):
        lr = self.learner()
        self.desk.close(shadow(key="z"), T0 + 9, 1.0)
        (self.desk.dir / "model.json").write_text('{"shadows": [')
        lr.poll()
        self.assertEqual(lr.state["offset"], 0)
        self.assertEqual(self.closes(), [])
        self.desk.write()
        lr.poll()
        self.assertEqual(len(self.closes()), 1)

    def test_shadows_file_is_never_written(self):
        lr = self.learner()
        self.desk.add_open(shadow(key="a"))
        self.desk.close(shadow(key="z"), T0 + 9, 1.0)
        before = {p.name: p.read_bytes() for p in self.desk.dir.iterdir()}
        lr.poll()
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.desk.dir.iterdir()})


class Look(unittest.TestCase):
    def sigs(self, n=400, seed=1):
        rng = random.Random(seed)
        out = []
        for i in range(n):
            take = i % 2 == 0
            out.append({"key": "k%d" % (i % 120), "ts": T0 + i * 600, "take": take, "net": rng.gauss(3 if take else -3, 4)})
        return out

    def test_expected_random_matches_many_draws(self):
        s = self.sigs()
        draws = L.random_draws(s, random.Random(5), 3000)
        self.assertAlmostEqual(sum(draws) / len(draws), L.expected_random(s), delta=0.1)

    def test_random_keeps_take_count_per_block(self):
        s = self.sigs()
        blocks = {}
        for x in s:
            blocks.setdefault(int(x["ts"] // L.BLOCK_S), []).append(x)
        rng = random.Random(2)
        for b in blocks.values():
            k = sum(x["take"] for x in b)
            self.assertEqual(len(rng.sample(b, k)), k)

    def test_a_clear_edge_passes_and_no_edge_fails(self):
        good = L.evaluate(self.sigs(), boot=500)
        self.assertTrue(all(good["conditions"].values()), good["conditions"])
        flat = [dict(x, net=x["net"] - 3 if x["take"] else x["net"] + 3) for x in self.sigs()]
        bad = L.evaluate(flat, boot=500)
        self.assertFalse(bad["conditions"]["gain_lower_bound_above_0"])

    def test_look_runs_once_and_only_at_fixed_counts(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                L.look(151, home=d)


class Frozen(unittest.TestCase):
    def test_constants(self):
        self.assertEqual((L.LR, L.L2, L.CLIP, L.WARMUP, L.SEED, L.DRAWS, L.LOOKS), (0.01, 1e-4, 30.0, 300, 18, 1000, (150, 300, 600, 1200)))
        self.assertEqual(L.DIM, 35)

    def test_t18_is_the_commit_that_added_section_18(self):
        repo = Path(L.__file__).resolve().parents[2]
        try:
            out = subprocess.run(["git", "log", "-S## 18. L1", "--format=%at", "--", "docs/MEME.md"], cwd=repo,
                                 capture_output=True, text=True, check=True).stdout.split()
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("no git")
        if not out:
            self.skipTest("shallow clone")
        self.assertEqual(int(out[-1]), L.T18)


if __name__ == "__main__":
    unittest.main()
