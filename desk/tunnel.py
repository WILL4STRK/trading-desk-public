"""A free Cloudflare quick tunnel to this desk (python3 -m desk --tunnel).

cloudflared makes an outbound connection to Cloudflare and gets a public
https://<random>.trycloudflare.com address that reaches 127.0.0.1:<port>.
No account or domain is needed. The address changes whenever the tunnel
restarts, so each new one is reported (event log and phone alert). Every
visit through it must log in (desk/remote.py).
"""
import os
import re
import shutil
import subprocess
import threading
import time

URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
WINDOWS_PATHS = (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe")


def find():
    return shutil.which("cloudflared") or next((p for p in WINDOWS_PATHS if os.path.exists(p)), None)


def watch(port, on_url, exe=None, popen=subprocess.Popen, stop=None, wait=30):
    """Runs cloudflared until stop is set, restarting it after `wait` seconds when it ends.
    Calls on_url(url) each time a new address appears."""
    stop = stop or threading.Event()
    while not stop.is_set():
        p = popen([exe or find() or "cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:%d" % port],
                  stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stderr:                       # cloudflared logs to stderr, the address among them
            m = URL.search(line)
            if m:
                on_url(m.group(0))
        p.wait()
        stop.wait(wait)


def start(port, on_url):
    if not find():
        raise RuntimeError("cloudflared is not installed")
    t = threading.Thread(target=watch, args=(port, on_url), daemon=True, name="desk-tunnel")
    t.start()
    return t
