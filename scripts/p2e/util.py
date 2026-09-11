"""Shared helpers: logging, JSON IO, paths, text utilities."""
from __future__ import annotations

import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any, Iterable


# --------------------------------------------------------------------------
# console / logging
# --------------------------------------------------------------------------

def setup_console() -> None:
    """Force UTF-8 on stdout/stderr so CJK never crashes on Windows cp936."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


_QUIET = os.environ.get("P2E_QUIET") == "1"


def log(msg: str = "") -> None:
    if not _QUIET:
        print(msg, flush=True)


def step(msg: str) -> None:
    log(f"[p2e] {msg}")


def warn(msg: str) -> None:
    print(f"[p2e][warn] {msg}", file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# json io
# --------------------------------------------------------------------------

def write_json(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def read_json(path: str) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def slugify(text: str, maxlen: int = 60) -> str:
    """Filesystem-safe ascii-ish slug, keeping CJK when present."""
    text = (text or "").strip()
    text = re.sub(r"[\x00-\x1f]", "", text)
    text = re.sub(r'[\\/:*?"<>|]+', "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    return (text[:maxlen] or "book").strip("._") or "book"


# --------------------------------------------------------------------------
# CJK text helpers
# --------------------------------------------------------------------------

CJK_RANGES = (
    (0x3400, 0x4DBF),   # CJK ext A
    (0x4E00, 0x9FFF),   # CJK unified
    (0xF900, 0xFAFF),   # compatibility ideographs
    (0x3040, 0x30FF),   # kana
    (0x20000, 0x2FA1F),  # ext B-F
)


def is_cjk(ch: str) -> bool:
    cp = ord(ch)
    return any(lo <= cp <= hi for lo, hi in CJK_RANGES)


def cjk_ratio(text: str) -> float:
    if not text:
        return 0.0
    return sum(1 for c in text if is_cjk(c)) / len(text)


_WS_RE = re.compile(r"[ \t\u00a0\u3000]+")


def squeeze(text: str) -> str:
    return _WS_RE.sub(" ", text).strip()


# Characters that appear in this style of ABBYY output as mis-read punctuation.
# Only applied where the context makes the intent unambiguous.
PUNCT_FIXES: tuple[tuple[str, str], ...] = (
    ("冃录", "目录"),
    ("目冃", "目录"),
    ("著作年诜", "著作年谱"),
)


def looks_garbled(text: str) -> bool:
    """Heuristic: does this text look like failed OCR of a stylised page?

    Only *CJK* noise counts. A page of English bibliography in a Chinese book is
    legitimately almost all Latin characters and must not be flagged, so the
    Latin/digit ratio is deliberately not a trigger here.
    """
    if not text.strip():
        return False
    cjk = [c for c in text if is_cjk(c)]
    if not cjk:
        return False
    # rare CJK that almost never occur in running prose, plus symbol soup
    rare = set("卄丿彳氵辶冂冋圡囗屮巛幺廾弌彐忄扌攴旡曰殳毌爿犭疋癶皿礻耒聿艮艸虍襾見訁豸頁黽")
    rare_hits = sum(1 for c in cjk if c in rare)
    symbols = sum(1 for c in text if unicodedata.category(c).startswith("S"))
    noise = rare_hits + symbols * 2
    return noise / max(len(text), 1) > 0.06


def normalize_text(text: str) -> str:
    """Light, lossless-ish normalisation applied to all text before output."""
    if not text:
        return ""
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\u00ad", "")          # soft hyphen
    text = text.replace("\ufeff", "")
    text = text.replace("\u200b", "")
    for a, b in PUNCT_FIXES:
        text = text.replace(a, b)
    return text


@dataclass
class Line:
    """One visual text line on a page."""
    text: str
    x0: float
    y0: float
    x1: float
    y1: float
    size: float = 0.0
    score: float = 1.0
    source: str = "embedded"

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(d: dict) -> "Line":
        return Line(**{k: d[k] for k in
                       ("text", "x0", "y0", "x1", "y1", "size", "score", "source")
                       if k in d})


@dataclass
class PageText:
    """All recovered text for a single physical PDF page."""
    page: int
    width: float
    height: float
    lines: list[Line] = field(default_factory=list)
    source: str = "embedded"

    def to_dict(self) -> dict:
        return {
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "source": self.source,
            "lines": [l.to_dict() for l in self.lines],
        }

    @staticmethod
    def from_dict(d: dict) -> "PageText":
        return PageText(
            page=d["page"], width=d["width"], height=d["height"],
            source=d.get("source", "embedded"),
            lines=[Line.from_dict(x) for x in d.get("lines", [])],
        )


def page_text_plain(pt: PageText) -> str:
    return "\n".join(l.text for l in pt.lines)


def order_lines(lines: list["Line"]) -> list["Line"]:
    """Order lines in reading order, clustering fragments that share a row.

    Scanned pages frequently emit one visual line as two fragments (a
    line-break dash, a tab stop, a footnote rule). Sorting by y alone
    interleaves them; clustering by vertical overlap and then sorting by x
    restores the intended reading order.
    """
    if not lines:
        return []
    rows: list[dict] = []
    for line in sorted(lines, key=lambda l: (l.y0 + l.y1) / 2.0):
        cy = (line.y0 + line.y1) / 2.0
        h = max(line.y1 - line.y0, 1.0)
        for row in rows:
            if abs(cy - row["cy"]) <= max(h, row["h"]) * 0.6:
                row["items"].append(line)
                n = len(row["items"])
                row["cy"] = (row["cy"] * (n - 1) + cy) / n
                row["h"] = max(row["h"], h)
                break
        else:
            rows.append({"cy": cy, "h": h, "items": [line]})
    rows.sort(key=lambda r: r["cy"])
    out: list[Line] = []
    for row in rows:
        out.extend(sorted(row["items"], key=lambda l: l.x0))
    return out


def is_junk(text: str) -> bool:
    """True for short runs of decorative noise left behind by OCR."""
    t = (text or "").strip()
    if len(t) < 2:
        return True
    cjk = sum(1 for c in t if is_cjk(c))
    alnum = sum(1 for c in t if c.isalnum())
    n = len(t)
    if n <= 6 and cjk <= 1:
        # short alphanumeric runs are noise, but keep plausible numbers:
        # bibliography years must survive, a stray folio or "00" must not.
        if t.isdigit():
            return not (1 <= int(t) <= 2100) or (len(t) > 1 and t[0] == "0")
        return True
    if cjk == 0 and alnum < max(6, n * 0.45):
        return True
    if n < 30 and cjk / n < 0.30 and alnum / n < 0.55:
        return True
    if cjk and cjk / n < 0.15 and alnum / n < 0.35:
        return True
    return False


def median(xs: Iterable[float]) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2.0
