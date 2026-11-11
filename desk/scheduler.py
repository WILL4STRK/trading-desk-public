"""The background loop: a scan after every 5-minute bar closes, a check on open
trades every minute, the pre-market scan once a day."""
import threading
import time
import traceback

from . import settings as S

PAPER_EVERY = 30         # seconds between looks at the practice account's open orders


class Scheduler(threading.Thread):
    def __init__(self, desk, log=print):
        super().__init__(daemon=True, name="desk-scheduler")
        self.desk, self.log = desk, log
        self.stop_flag = threading.Event()
        self.last_scan_slot = None
        self.last_monitor = 0.0
        self.premarket_day = None
        self.last_paper = 0.0
        self.wake = threading.Event()

    def stop(self):
        self.stop_flag.set()
        self.wake.set()

    def scan_now(self):
        self.last_scan_slot = None
        self.wake.set()

    def _safe(self, name, fn):
        try:
            return fn()
        except Exception:
            self.log("%s failed:\n%s" % (name, traceback.format_exc()))

    def tick(self, wall=None):
        """One pass of the loop. Split out so the tests can drive it."""
        wall = time.time() if wall is None else wall
        st, now = S.load(), self.desk.src.now()
        if self.premarket_day != now[:10] and (self.desk.src.demo or now[11:16] >= "08:00"):
            self.premarket_day = now[:10]
            self._safe("pre-market scan", self.desk.premarket)
            self._safe("congress", self.desk.congress)
        # scan once per slot; in live a slot starts 25 seconds after each bar closes
        every = st["scan_every_seconds"]
        slot = "demo-" + now if self.desk.src.demo else int((wall - 25) // every)
        if slot != self.last_scan_slot:
            self.last_scan_slot = slot
            s = self._safe("scan", self.desk.scan)
            if s and s.get("new_signals"):
                self.last_paper = 0.0                   # a new ready signal: auto paper looks at once
            if s:
                self.log("%s scan: %s" % (now, s.get("halt") or s.get("error") or
                                            "%d charts, %d breakouts, %d fresh, %d new signals"
                                            % (s["scanned"], s["hits"], s["fresh"], s["new_signals"])))
        if wall - self.last_monitor >= st["monitor_every_seconds"]:
            self.last_monitor = wall
            self._safe("monitor", self.desk.monitor)
        # the practice account: fill paper orders the price has reached (desk/paper.py), then auto paper
        # (desk/auto.py) follows its trades and takes new ready signals. Paper mode only.
        if st["mode"] == "paper" and wall - self.last_paper >= PAPER_EVERY:
            self.last_paper = wall
            if not self.desk.src.demo:
                self._safe("paper account", self.desk.paper.settle)
            got = self._safe("auto paper", self.desk.auto.tick)
            if got and any(got.values()):
                self.log("%s auto paper: %s" % (now, ", ".join("%d %s" % (v, k) for k, v in got.items() if v)))

    def run(self):
        while not self.stop_flag.is_set():
            self.tick()
            self.wake.wait(5)
            self.wake.clear()
