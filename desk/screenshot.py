"""Reads numbers off broker screenshots (labels like thinkorswim's), so you do not have to type them.

The image is saved under data/screenshots/ and read on your Mac with Apple's
Vision text recognition (tools/ocr/ocr.swift, built once into build/ocr).
Nothing is uploaded. The desk only suggests what it found: a person checks it
and presses Save.
"""
import base64
import binascii
import json
import re
import subprocess
from pathlib import Path

from .paths import ROOT

MAX_BYTES = 12 * 1024 * 1024
KINDS = ("balance", "trade")
READER = ROOT / "build" / "ocr"
READER_SOURCE = ROOT / "tools" / "ocr" / "ocr.swift"


class ScreenshotError(ValueError):
    """Plain words for the person; never a stack trace."""


# ---- the image -----------------------------------------------------------------
def _kind_of(data):
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[4:8] == b"ftyp" and data[8:12] in (b"heic", b"heix", b"mif1", b"heif", b"hevc"):
        return ".heic"
    return None


def save_image(raw, kind, root, now):
    """Checks a pasted or dropped image (data URL or base64) and saves it. Returns its path."""
    if kind not in KINDS:
        raise ScreenshotError("Unknown screenshot type.")
    if not isinstance(raw, str) or not raw.strip():
        raise ScreenshotError("No image came through. Drop or pick a screenshot.")
    text = raw.strip()
    m = re.match(r"^data:image/[a-z0-9.+-]+;base64,", text, re.I)
    if m:
        text = text[m.end():]
    text = re.sub(r"\s+", "", text)
    if len(text) > (MAX_BYTES * 4) // 3 + 8:
        raise ScreenshotError("That image is too big. A normal screenshot is well under 12 MB.")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        raise ScreenshotError("That file could not be read as an image.")
    if len(data) > MAX_BYTES:
        raise ScreenshotError("That image is too big. A normal screenshot is well under 12 MB.")
    ext = _kind_of(data)
    if not ext:
        raise ScreenshotError("That is not a PNG, JPEG or HEIC image. Take a screenshot (Cmd+Shift+4) and use that.")
    folder = Path(root) / "screenshots" / now[:10]
    folder.mkdir(parents=True, exist_ok=True)
    stem = "%s-%s" % (now[11:16].replace(":", ""), kind)
    path, n = folder / (stem + ext), 1
    while path.exists():
        n += 1
        path = folder / ("%s-%d%s" % (stem, n, ext))
    path.write_bytes(data)
    return path


# ---- the reader -----------------------------------------------------------------
def reader_available():
    return READER.is_file()


def build_reader():
    """Compiles the Swift reader once (about half a minute)."""
    READER.parent.mkdir(parents=True, exist_ok=True)
    try:
        p = subprocess.run(["/usr/bin/swiftc", "-O", str(READER_SOURCE), "-o", str(READER)],
                           capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired):
        raise ScreenshotError("The screenshot reader could not be set up. Type the numbers in instead.")
    if p.returncode != 0 or not READER.is_file():
        raise ScreenshotError("The screenshot reader could not be set up. Type the numbers in instead.")


def ocr(path):
    """The text in an image, as [{"text", "x", "y", "w", "h"}] (fractions, y from the top)."""
    if not reader_available():
        build_reader()
    try:
        p = subprocess.run([str(READER), str(path)], capture_output=True, text=True, timeout=60)
        out = json.loads(p.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ScreenshotError("The screenshot could not be read. Type the numbers in instead.")
    if "error" in out:
        raise ScreenshotError("The screenshot could not be read (%s). Type the numbers in instead." % out["error"])
    lines = out.get("lines")
    if not isinstance(lines, list):
        raise ScreenshotError("The screenshot could not be read. Type the numbers in instead.")
    return [l for l in lines if isinstance(l, dict) and isinstance(l.get("text"), str)]


# ---- understanding the text --------------------------------------------------------
_MONEY = re.compile(r"^\(?-?\$?\s*-?(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\)?$")

# label words -> field; first match in this order wins
LABELS = (
    ("net liquidating value", "net_liq"), ("net liq", "net_liq"), ("account value", "net_liq"),
    ("cash & sweep vehicle", "cash"), ("cash and sweep vehicle", "cash"), ("cash & sweep", "cash"),
    ("cash balance", "cash"), ("total cash", "cash"),
    ("stock buying power", "buying_power"), ("available funds for trading", "buying_power"),
    ("option buying power", "buying_power"), ("buying power", "buying_power"),
)


def _money(text):
    t = text.strip()
    m = _MONEY.match(t)
    if not m:
        return None
    value = float((m.group(1) + (m.group(2) or "")).replace(",", ""))
    return -value if t.startswith(("(", "-")) or t.startswith("$-") else value


def _label(text):
    low = " ".join(text.lower().replace(":", " ").split())
    if low in ("cash",):
        return "cash", low
    for words, field in LABELS:
        if low.startswith(words):
            return field, words
    return None, None


def _yc(l):
    return l["y"] + l["h"] / 2


def _value_for(label, lines, words):
    # 1. the number is in the same piece of text: "Net Liquidating Value $412.35"
    rest = label["text"].lower().replace(":", " ")
    tail = label["text"][rest.find(words) + len(words):] if words in rest else ""
    if tail.strip():
        v = _money(tail.strip().lstrip(":").strip())
        if v is not None:
            return v
    # 2. on the same row, to the right
    tol = max(label["h"], 0.01) * 0.6
    right = [l for l in lines if l is not label and abs(_yc(l) - _yc(label)) <= tol and l["x"] > label["x"]]
    for l in sorted(right, key=lambda l: l["x"]):
        v = _money(l["text"])
        if v is not None:
            return v
        if _label(l["text"])[0]:
            break
    # 3. just underneath
    below = [l for l in lines if l is not label and 0 < l["y"] - label["y"] <= max(label["h"], 0.01) * 3
             and label["x"] - 0.05 <= l["x"] <= label["x"] + max(label["w"], 0.05)]
    for l in sorted(below, key=lambda l: l["y"]):
        v = _money(l["text"])
        if v is not None:
            return v
    return None


def read_balance(lines):
    """[{"field": "cash"|"net_liq"|"buying_power", "label", "value"}], one per field."""
    found, seen = [], set()
    for l in sorted(lines, key=lambda l: (l["y"], l["x"])):
        field, words = _label(l["text"])
        if not field or field in seen:
            continue
        value = _value_for(l, lines, words)
        if value is None or value < 0:
            continue
        seen.add(field)
        found.append({"field": field, "label": l["text"].strip().rstrip(":"), "value": round(value, 2)})
    return found


_FILL = re.compile(r"\b(BOT|BOUGHT|BUY|SOLD|SLD|SELL)\b\s*([+-]?\s*\d[\d,]*)\s+([A-Za-z][A-Za-z0-9.]{0,6})\s*"
                   r"(?:@|\bat\b)\s*\$?\s*(\d[\d,]*(?:\.\d+)?)", re.I)


def _rows(lines):
    rows = []
    for l in sorted(lines, key=_yc):
        if rows and abs(_yc(l) - rows[-1]["yc"]) <= max(l["h"], rows[-1]["h"], 0.01) * 0.5:
            rows[-1]["parts"].append(l)
        else:
            rows.append({"yc": _yc(l), "h": l["h"], "parts": [l]})
    return [" ".join(p["text"] for p in sorted(r["parts"], key=lambda p: p["x"])) for r in rows]


def read_fills(lines):
    """[{"side": "buy"|"sell", "qty", "symbol", "price"}] in the order they appear."""
    fills = []
    for row in _rows(lines):
        for m in _FILL.finditer(row):
            symbol = m.group(3)
            if not symbol.isupper():
                continue
            qty = abs(int(re.sub(r"[\s,+]", "", m.group(2))))
            if not qty:
                continue
            fills.append({"side": "buy" if m.group(1).upper() in ("BOT", "BOUGHT", "BUY") else "sell",
                          "qty": qty, "symbol": symbol, "price": float(m.group(4).replace(",", ""))})
    return fills
