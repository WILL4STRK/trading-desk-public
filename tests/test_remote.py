"""Remote sign-in (desk/remote.py) and the quick tunnel's address reader (desk/tunnel.py)."""
import io
import json
import threading

from desk import remote
from desk import tunnel
from tests.base import DeskCase


class Remote(DeskCase):
    def test_password_is_stored_as_a_hash_only(self):
        with self.assertRaises(ValueError):
            remote.set_password("short")
        remote.set_password("correct horse battery")
        self.assertNotIn("correct horse", remote.path().read_text())
        self.assertTrue(remote.check("correct horse battery"))
        self.assertFalse(remote.check("correct horse batterY"))

    def test_sessions_expire_and_a_new_password_signs_everyone_out(self):
        remote.set_password("correct horse battery")
        tok = remote.token(now=1000)
        self.assertTrue(remote.valid(tok, now=2000))
        self.assertFalse(remote.valid(tok, now=1000 + remote.SESSION_DAYS * 86400 + 1))
        self.assertFalse(remote.valid("bad", now=2000))
        remote.set_password("another long password")
        self.assertFalse(remote.valid(tok, now=2000))

    def test_no_password_means_no_session(self):
        self.assertFalse(remote.check("anything at all"))
        self.assertFalse(remote.valid("9999999999.abc"))


class Tunnel(DeskCase):
    def test_each_new_address_is_reported_and_it_restarts(self):
        seen, stop, runs = [], threading.Event(), []

        class Fake:
            def __init__(self, cmd, **kw):
                runs.append(cmd)
                n = len(runs)
                self.stderr = io.StringIO("INF Requesting new quick Tunnel\nINF |  https://word-%d.trycloudflare.com  |\n" % n)
                if n == 2:
                    stop.set()

            def wait(self):
                return 0
        tunnel.watch(8790, seen.append, exe="cloudflared", popen=Fake, stop=stop, wait=0)
        self.assertEqual(seen, ["https://word-1.trycloudflare.com", "https://word-2.trycloudflare.com"])
        self.assertIn("http://127.0.0.1:8790", runs[0])
