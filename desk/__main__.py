"""python3 -m desk            the live desk at http://127.0.0.1:8787
python3 -m desk --demo     the same desk on saved data from 2026-10-19 (no network)
"""
import argparse
import os
import sys
import webbrowser

from .paths import ROOT


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m desk", description="Trading Desk: day-trading signals you place by hand at your broker. It never places a real order.")
    ap.add_argument("command", nargs="?", choices=("run", "review", "meme"), default="run",
                    help="run (default) starts the desk; review prints the record and exits; meme runs one memecoin pass and prints it")
    ap.add_argument("--demo", action="store_true", help="run on saved data from Monday 2026-10-19, no network")
    ap.add_argument("--demo-now", default="2026-10-19 10:15", help='the demo clock, New York time')
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--no-browser", action="store_true")
    a = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(line_buffering=True)     # data/desk.log gets each line as it happens
    except (AttributeError, ValueError):
        pass
    if a.demo and not os.environ.get("DESK_DATA"):
        os.environ["DESK_DATA"] = str(ROOT / "data-demo")   # demo trades never touch your real journal

    from .engine import Desk
    from .journal import Journal
    from .scheduler import Scheduler
    from .server import serve
    from .source import Demo, Live

    from . import mode as M
    from . import settings as S

    if a.command == "meme":                         # one memecoin pass (paper only), then its review section
        if a.demo:
            print("The memecoin model reads live public data; it has no demo. Run it without --demo.")
            return 2
        from . import meme
        print(meme.run())
        print("\n".join(meme.lines()))
        return 0
    if a.command == "review":
        return print_review(Demo(a.demo_now) if a.demo else Live())

    which = S.load()["mode"]
    desk = Desk(Journal(M.journal_path(which)), Demo(a.demo_now) if a.demo else Live())
    sched = Scheduler(desk)
    httpd = serve(desk, sched, a.port)
    sched.start()
    url = "http://127.0.0.1:%d/" % a.port
    print("Trading Desk %s at %s   (Ctrl+C to stop)" % ("DEMO (saved data)" if a.demo else "on today's market", url))
    print("Record: %s." % ("PAPER (practice)" if which == "paper" else "LIVE (real money)"))
    st = S.load()
    if st["auto_paper"] and which == "paper":
        print("Auto paper is ON: the desk trades ready signals itself in the practice account (no real money).")
    print("The desk never places real orders: you place each one at your broker yourself.")
    if not a.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        sched.stop()
        httpd.server_close()
    return 0


def print_review(source):
    """python3 -m desk review: both records as text, for "check the log and make changes". Starts nothing."""
    from . import mode as M
    from .engine import Desk
    from .journal import Journal

    shown = 0
    for which in M.MODES:
        path = M.journal_path(which)
        if not path.exists():
            continue
        j = Journal(path)
        try:
            desk = Desk.__new__(Desk)              # a reader only: no seeding, no scheduler, no server
            desk.j, desk.src = j, source
            print(desk.review(j, which)["text"])
            print("\n" + "=" * 72 + "\n")
            shown += 1
        finally:
            j.close()
    if not shown:
        print("No journal yet. Start the desk once (python3 -m desk) and record a trade first.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
