"""Stage 3 - turn raw page lines into clean paragraphs.

Responsibilities
----------------
* drop running heads, footers and bare page numbers
* separate the footnote block from the body
* rejoin hard-wrapped CJK lines into paragraphs
* apply a conservative set of OCR punctuation repairs
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from .util import (Line, PageText, is_cjk, looks_garbled, median, normalize_text,
                   squeeze)


# --------------------------------------------------------------------------
# paragraph reconstruction
# --------------------------------------------------------------------------

@dataclass
class Block:
    """A recovered logical block of text."""
    kind: str                 # 'heading' | 'para' | 'footnote' | 'plain'
    text: str
    pages: list[int] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "text": self.text, "pages": self.pages}


BULLETS = "◎●○◆◇■□▪▶►·•＊*※"

# Sentence enders used to decide whether a short line terminates a paragraph.
SENT_END = "。！？!?…」』】）)】”\"'"


def _is_bullet_start(text: str) -> bool:
    return bool(text) and text[0] in BULLETS


def _dedent_key(text: str) -> Optional[str]:
    """Normalise a line so repeated running heads collapse to one key."""
    t = squeeze(text)
    t = re.sub(r"\d+", "#", t)
    return t if 0 < len(t) <= 40 else None


@dataclass
class PageProfile:
    """Geometry-derived facts about one page, used for cleaning decisions."""
    page: int
    left: float
    right: float
    top: float
    bottom: float
    body_size: float
    text_width: float
    indent: float


def global_margins(pages: list[PageText], body_size: float,
                   min_lines: int = 5) -> tuple[float, float]:
    """Book-level left/right text margins.

    Sparse pages (section titles, part pages) cannot supply their own margins,
    so the profile is anchored on pages that actually carry a text block.
    """
    lefts: list[float] = []
    rights: list[float] = []
    for pt in pages:
        body = [l for l in pt.lines
                if l.size >= body_size * 0.9 and len(l.text.strip()) >= 6]
        if len(body) < min_lines:
            continue
        lefts.append(min(l.x0 for l in body))
        rights.append(max(l.x1 for l in body))
    if not lefts:
        return 0.0, 0.0
    return median(lefts), median(rights)


def profile_page(pt: PageText, body_size: float,
                 global_left: float = 0.0, global_right: float = 0.0) -> PageProfile:
    xs0 = [l.x0 for l in pt.lines]
    xs1 = [l.x1 for l in pt.lines]
    ys0 = [l.y0 for l in pt.lines]
    ys1 = [l.y1 for l in pt.lines]
    left = global_left or (median(xs0) if xs0 else 0.0)
    right = global_right or (max(xs1) if xs1 else 0.0)
    top = min(ys0) if ys0 else 0.0
    bottom = max(ys1) if ys1 else 0.0
    # Indent = most common "extra" left offset among non-first body lines.
    offsets = sorted(round(l.x0 - left, 1) for l in pt.lines)
    indent = 0.0
    if offsets:
        base = offsets[0]
        deltas = [o - base for o in offsets if o - base > 6]
        if deltas:
            indent = median(deltas)
    return PageProfile(page=pt.page, left=left, right=right, top=top,
                       bottom=bottom, body_size=body_size,
                       text_width=max(right - left, 1.0), indent=indent)


def strip_running_heads(pages: list[PageText], profiles: dict[int, PageProfile],
                        body_size: float) -> dict[int, list[Line]]:
    """Remove repeated header/footer lines and bare page numbers."""
    # find candidate margin lines and count how often each recurs
    counts: dict[str, int] = {}
    per_page_candidates: dict[int, list[Line]] = {}
    for pt in pages:
        prof = profiles[pt.page]
        cands: list[Line] = []
        for l in pt.lines:
            in_top = l.y1 < prof.top + max(prof.body_size * 1.2, 26)
            in_bottom = l.y0 > prof.bottom - max(prof.body_size * 1.2, 26)
            if not (in_top or in_bottom):
                continue
            cands.append(l)
        per_page_candidates[pt.page] = cands
        for l in cands:
            t = squeeze(l.text)
            if not t:
                continue
            key = _dedent_key(t)
            if key:
                counts[key] = counts.get(key, 0) + 1

    stripped: dict[int, list[Line]] = {}
    for pt in pages:
        drop_ids = set()
        for l in per_page_candidates[pt.page]:
            t = squeeze(l.text)
            if re.fullmatch(r"[-–—·•\s]*\d{1,4}[-–—·•\s]*", t):
                drop_ids.add(id(l))
                continue
            key = _dedent_key(t)
            if key and counts.get(key, 0) >= 3 and len(t) <= 40:
                drop_ids.add(id(l))
                continue
            # isolated superscript-like glyph runs in the margins
            if len(t) <= 3 and not is_cjk(t[:1] or " "):
                drop_ids.add(id(l))
        stripped[pt.page] = [l for l in pt.lines if id(l) not in drop_ids]
    return stripped


# --------------------------------------------------------------------------
# footnote split
# --------------------------------------------------------------------------

FOOT_MARK = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
FOOT_START_RE = re.compile(rf"^\s*[{FOOT_MARK}]")


def split_footnotes(lines: list[Line], prof: PageProfile) -> tuple[list[Line], list[Line]]:
    """Split a page's lines into (body, footnote) using font size + position."""
    if not lines:
        return [], []
    sizes = [l.size for l in lines if l.size > 0]
    small = [s for s in sizes if s < prof.body_size * 0.85]
    if not small:
        return lines, []
    # a footnote block is a run of small lines in the lower part of the page
    idx = None
    for i, l in enumerate(lines):
        if l.size and l.size < prof.body_size * 0.85 and l.y0 > prof.top + (prof.bottom - prof.top) * 0.55:
            if FOOT_START_RE.match(l.text) or idx is None:
                idx = i
                if FOOT_START_RE.match(l.text):
                    break
    if idx is None:
        return lines, []
    body, foot = lines[:idx], lines[idx:]
    # if the "footnotes" are mostly body-sized, treat as body
    if foot and median([l.size for l in foot]) > prof.body_size * 0.95:
        return lines, []
    return body, foot


# --------------------------------------------------------------------------
# line joining
# --------------------------------------------------------------------------

def _needs_space(prev: str, cur: str) -> bool:
    if not prev or not cur:
        return False
    a, b = prev[-1], cur[0]
    if is_cjk(a) or is_cjk(b):
        return False
    if a.isascii() and a.isalnum() and b.isascii() and b.isalnum():
        return True
    return False


def is_centred(line: Line, prof: PageProfile) -> bool:
    """A genuinely centred display line.

    Both margins must be wide *and comparable*: a short paragraph opener is
    inset only on the right for a left-aligned book, and treating that as
    centred would slice a paragraph in two.
    """
    gap_l = line.x0 - prof.left
    gap_r = prof.right - line.x1
    if gap_l <= prof.body_size * 1.5 or gap_r <= prof.body_size * 1.5:
        return False
    if line.width >= prof.text_width * 0.75:
        return False
    return abs(gap_l - gap_r) <= max(prof.body_size * 2.0, line.width * 0.35)


def join_lines(lines: list[Line], prof: PageProfile, keep_break: bool = False) -> list[str]:
    """Rejoin hard-wrapped lines into paragraphs.

    A new paragraph starts when a line is indented past the continuation
    offset, when the previous line ends a sentence and is short, when a line
    starts with a bullet, or when a centred display line appears.
    """
    paras: list[str] = []
    cur = ""
    prev: Optional[Line] = None
    indent_thresh = max(prof.indent * 0.5, prof.body_size * 0.7)

    for l in lines:
        text = normalize_text(squeeze(l.text))
        if not text:
            continue
        centred = is_centred(l, prof)
        indented = (l.x0 - prof.left) > indent_thresh
        new_para = False
        if cur:
            if centred or _is_bullet_start(text) or indented:
                new_para = True
            elif prev is not None and prev.text.rstrip()[-1:] in SENT_END \
                    and prev.width < prof.text_width * 0.82:
                new_para = True
            elif keep_break:
                new_para = True
        if new_para and cur:
            paras.append(cur)
            cur = text
        else:
            if cur and _needs_space(cur, text):
                cur += " " + text
            else:
                cur += text
        if centred and cur:
            # a centred line stands on its own
            paras.append(cur)
            cur = ""
        prev = l

    if cur:
        paras.append(cur)
    return [p.strip() for p in paras if p.strip()]


def group_footnotes(lines: list[Line]) -> list[str]:
    """Split a footnote block into one string per footnote marker."""
    groups: list[str] = []
    cur = ""
    for l in lines:
        t = normalize_text(squeeze(l.text))
        if not t:
            continue
        if FOOT_START_RE.match(t):
            if cur:
                groups.append(cur)
            cur = t
        else:
            cur += t
    if cur:
        groups.append(cur)
    return groups


# --------------------------------------------------------------------------
# inline running heads
# --------------------------------------------------------------------------

def frequent_margin_strings(pages: list[PageText], profiles: dict[int, PageProfile],
                            body_size: float, min_pages: int = 3) -> set[str]:
    """Recurring short strings that sit in a margin: running heads and folios.

    Needed separately from :func:`strip_running_heads` because a scanner often
    merges the running head onto the *same* text line as real content, where
    dropping whole lines cannot help and the string has to be cut out.
    """
    counts: dict[str, int] = {}
    for pt in pages:
        prof = profiles.get(pt.page)
        if prof is None:
            continue
        band = max(body_size * 1.2, 26)
        for l in pt.lines:
            if not (l.y1 < prof.top + band or l.y0 > prof.bottom - band):
                continue
            key = _dedent_key(l.text)
            if key and 2 <= len(key) <= 40:
                counts[key] = counts.get(key, 0) + 1
    return {k for k, v in counts.items() if v >= min_pages}


def remove_head_substrings(lines: list[Line], heads: set[str]) -> list[Line]:
    """Cut recurring running-head text out of the middle of a line."""
    if not heads:
        return lines
    out: list[Line] = []
    for l in lines:
        t = l.text
        changed = False
        for h in heads:
            if h and h in t:
                t = t.replace(h, "")
                changed = True
        if not changed:
            out.append(l)
            continue
        t = squeeze(t)
        if t:
            out.append(Line(text=t, x0=l.x0, y0=l.y0, x1=l.x1, y1=l.y1,
                            size=l.size, score=l.score, source=l.source))
    return out


# --------------------------------------------------------------------------
# printed folios and the printed->physical page offset
# --------------------------------------------------------------------------

_FOLIO_RE = re.compile(r"^[\[\]()（）【】·•\-–—\s]*(\d{1,4})[\[\]()（）【】·•\-–—\s]*$")


def detect_folios(pages: list[PageText],
                  profiles: dict[int, PageProfile]) -> dict[int, int]:
    """Map physical page number -> the folio printed on it.

    Scanned books set the folio as a lone number in the top or bottom margin.
    Knowing the printed number of each page is what makes a printed table of
    contents usable: its entries carry printed page numbers, and the difference
    between the two is a constant for the whole book.
    """
    out: dict[int, int] = {}
    for pt in pages:
        prof = profiles.get(pt.page)
        if prof is None or len(pt.lines) < 3:
            continue
        band = max(prof.body_size * 2.0, 18.0)
        cands: list[int] = []
        for l in pt.lines:
            m = _FOLIO_RE.match(squeeze(l.text))
            if not m:
                continue
            in_top = l.y1 <= prof.top + band
            in_bottom = l.y0 >= prof.bottom - band
            if in_top or in_bottom:
                cands.append(int(m.group(1)))
        if cands and len(set(cands)) == 1:
            out[pt.page] = cands[0]
    return out


def estimate_page_offset(folios: dict[int, int], min_support: int = 3) -> int:
    """The most common (physical - printed) difference across the book."""
    if not folios:
        return 0
    diffs: dict[int, int] = {}
    for phys, printed in folios.items():
        d = phys - printed
        diffs[d] = diffs.get(d, 0) + 1
    best, n = max(diffs.items(), key=lambda kv: kv[1])
    return best if n >= min_support else 0


def is_heading_like(text: str, line: Line, prof: PageProfile) -> bool:
    """Cheap heading test used to *suggest* candidates (never authoritative)."""
    t = squeeze(text)
    if not (1 < len(t) <= 24):
        return False
    if t[-1:] in "。！？，、；：":
        return False
    if t.startswith(("“", "（", "【")):
        return False
    if line.width > prof.text_width * 0.62:
        return False
    if line.size and line.size > prof.body_size * 1.02:
        return True
    return t in ("变奏", "尾声", "前言", "后记", "附录", "注释", "译后记", "编后记")


# --------------------------------------------------------------------------
# punctuation repair
# --------------------------------------------------------------------------

# Each rule is (regex, replacement, human description). They are deliberately
# narrow: they only fire in contexts where the intent cannot be read another
# way. They target the systematic confusions produced by ABBYY/older OCR.
REPAIR_RULES: list[tuple[str, str, str]] = [
    # sentence-final mis-reads
    (r"(?<=[\u4e00-\u9fff])\s*J\s*(?=$|[\u201c\u201d”\"'\u3002])", "。", "latin J -> 。 at sentence end"),
    (r"(?<=[\u4e00-\u9fff])J(?=[\u4e00-\u9fff])", "。", "latin J -> 。 between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*厂\s*$", "！", "厂 -> ！ at sentence end"),
    (r"(?<=[\u4e00-\u9fff])\s*1\s*(?=$|[\u201c\u201d”\"'])", "！", "digit 1 -> ！ at sentence end"),
    (r"(?<=[\u4e00-\u9fff])厂(?=[\u4e00-\u9fff])", "：", "厂 -> ： before CJK"),
    (r"(?<=[\u4e00-\u9fff])「(?=[\u4e00-\u9fff])", "：", "「 -> ： before CJK"),
    # western punctuation used where the source is CJK on both sides
    (r"(?<=[\u4e00-\u9fff])\s*\*(?=[\u4e00-\u9fff])", "。", "* -> 。 between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*°\s*(?=[\u4e00-\u9fff])", "。", "° -> 。 between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*\.\s*(?=[\u4e00-\u9fff])", "。", ". -> 。 between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*,\s*(?=[\u4e00-\u9fff])", "，", ", -> ， between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*;\s*(?=[\u4e00-\u9fff])", "；", "; -> ； between CJK"),
    (r"(?<=[\u4e00-\u9fff])\s*:\s*(?=[\u4e00-\u9fff])", "：", ": -> ： between CJK"),
    # single characters consistently mis-read in this class of scan
    (r"(?<=[\u4e00-\u9fff])T(?=\d)", "于", "T -> 于 before a digit"),
    (r"©(?=\s*[\u4e00-\u9fff])", "①", "© -> ①"),
    (r"岀", "出", "variant 岀 -> 出"),
    (r"穩", "稳", "variant 穩 -> 稳"),
    # spacing and quotes
    (r"[\u2018\u2019]{2}", "“", "double single quote -> open quote"),
    (r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", "drop space between CJK"),
    (r"(?<=[。，、；：！？》“”‘’）】])\s+(?=[\u4e00-\u9fff])", "", "drop space after CJK punctuation"),
    (r"(?<=[\u4e00-\u9fff])\s+(?=[。，、；：！？“”‘’（【])", "", "drop space before CJK punctuation"),
    (r"\s+(?=[。，、；：！？])", "", "drop space before sentence punctuation"),
    # doubled or contradictory punctuation left by two OCR passes
    (r"[。．]\s*[，,、]\s*(?=[\u4e00-\u9fff])", "。", "。， -> 。"),
    (r"[，,]\s*[。．](?=[\u4e00-\u9fff])", "。", "，。 -> 。"),
    (r"([。！？])\1+", r"\1", "collapse repeated sentence punctuation"),
]


def repair_text(text: str, enabled: bool = True) -> str:
    """Apply lossless normalisation plus the context-anchored OCR repairs."""
    if not text:
        return text
    text = normalize_text(text)
    if not enabled:
        return text
    for pat, rep, _why in REPAIR_RULES:
        text = re.sub(pat, rep, text)
    return text


def repair_report(text: str) -> list[tuple[str, int]]:
    out = []
    for pat, _rep, why in REPAIR_RULES:
        n = len(re.findall(pat, text))
        if n:
            out.append((why, n))
    return out


# --------------------------------------------------------------------------
# marker re-injection (OCR text + embedded structure)
# --------------------------------------------------------------------------

# Characters that carry structure but that a fresh OCR pass tends to drop.
# Only the circled-number footnote references and the decorative bullets are
# listed: parenthesised forms such as ⑷ are far more often a mis-read digit
# than a real reference, and transferring them corrupts years and numbers.
INLINE_MARKERS = set(
    "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"
    "◎●○◆◇■□▪▶►"
)

# leading decorative characters that may legitimately start a list item
LEAD_MARKERS = set(BULLETS) | set("①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳")


def _map_index(src: str, dst: str, i: int) -> int:
    """Map a character index in `src` to the aligned index in `dst`."""
    import difflib
    sm = difflib.SequenceMatcher(None, src, dst, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if i1 <= i < i2:
            if i2 > i1:
                return j1 + round((i - i1) * (j2 - j1) / (i2 - i1))
            return j1
    return len(dst)


def _leading_markers(text: str) -> tuple[str, str]:
    i = 0
    while i < len(text) and text[i] in LEAD_MARKERS:
        i += 1
    return text[:i], text[i:]


def _similar(a: str, b: str) -> float:
    import difflib
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def transfer_markers(emb_lines: list[Line], ocr_lines: list[Line],
                     y_tolerance: float = 12.0) -> list[Line]:
    """Reconcile OCR lines with the structure recorded in the embedded layer.

    A fresh OCR pass reads punctuation far better than an old embedded layer but
    drops superscript footnote references and small decorative bullets. Those
    are taken from the embedded line covering the same row and inserted where
    the two strings align. In the other direction, the embedded layer is the
    better authority on *which* leading symbol a list item uses: OCR regularly
    renders the bullet ``◎`` as ``③``.
    """
    if not emb_lines or not ocr_lines:
        return ocr_lines
    used: set[int] = set()
    for ocr_line in ocr_lines:
        oy = (ocr_line.y0 + ocr_line.y1) / 2.0
        best, best_d = -1, y_tolerance
        for k, e in enumerate(emb_lines):
            if k in used:
                continue
            d = abs((e.y0 + e.y1) / 2.0 - oy)
            if d < best_d:
                best, best_d = k, d
        if best < 0:
            continue
        used.add(best)
        src = emb_lines[best].text

        # 1. leading decoration: trust the embedded layer when the bodies match
        emb_head, emb_body = _leading_markers(src)
        ocr_head, ocr_body = _leading_markers(ocr_line.text)
        if emb_head and emb_head != ocr_head and _similar(emb_body, ocr_body) >= 0.7:
            ocr_line.text = emb_head + ocr_body
            if emb_head.startswith(("◎", "●", "○", "◆")):
                ocr_line.text = emb_head + " " + ocr_body if ocr_body else emb_head

        # 2. inline footnote references missing from the OCR text
        missing = []
        for i, c in enumerate(src):
            if c not in INLINE_MARKERS or c in ocr_line.text:
                continue
            before = src[i - 1] if i else ""
            after = src[i + 1] if i + 1 < len(src) else ""
            if before.isdigit() or after.isdigit() or before.isascii() and before.isalpha():
                continue
            missing.append((i, c))
        if not missing:
            continue
        inserts = [(_map_index(src, ocr_line.text, i), c) for i, c in missing]
        text = ocr_line.text
        for pos, ch in sorted(inserts, key=lambda t: -t[0]):
            pos = max(0, min(pos, len(text)))
            text = text[:pos] + ch + text[pos:]
        ocr_line.text = text
    return ocr_lines
