"""Locate, crop and describe the tables and figures printed on scanned pages.

A scanned page is one big image with an OCR text layer over it, so a table or a
diagram is not a separate object that can be pulled out of the PDF: it has to be
found from the page geometry and cropped out of the page render.

Two facts make that reliable:

* **captions are labelled.** ``表2.1`` / ``图3.1`` mark the graphic, and a line
  that merely *mentions* a figure is surrounded by prose while a real caption
  sits against a non-prose neighbour -- table rows below it, or artwork above it.
* **the graphic is not prose.** Prose lines fill the text column; table cells
  are short fragments and artwork carries only scattered axis labels. So the
  band between a caption and the next piece of prose bounds the graphic.

The crop is then trimmed to the ink actually present inside that band, and the
running head is excluded by cropping to the text column's horizontal extent.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from .util import Line, PageText, log, squeeze, warn

# Connectors that follow a label in a citation: '图6.6和图6.7', '图6.6、图6.7'.
_REFERENCE_JOINERS = set("和与及、,，")

# 表2.1 / 图 2. 3 / Table 4 / Figure 5 -- OCR inserts spaces unpredictably
CAPTION_RE = re.compile(
    r"(?P<label>(?:表|图|图表|Table|Figure|Fig\.?)\s*\d{1,3}\s*[.\-．·]\s*\d{1,3})",
    re.IGNORECASE)
CAPTION_AT_START_RE = re.compile(
    r"^\s*(?P<label>(?:表|图|图表|Table|Figure|Fig\.?)\s*\d{1,3}\s*[.\-．·]\s*\d{1,3})",
    re.IGNORECASE)
_CJK_NUM = "０１２３４５６７８９"
TABLE_LABEL_RE = re.compile(r"^(表|图表|Table)", re.IGNORECASE)


@dataclass
class Figure:
    """One table or diagram found on a page."""
    page: int
    kind: str                 # 'table' | 'figure'
    label: str                # normalised, e.g. '表2.1'
    caption: str              # full caption text, used as the HTML figcaption
    band: tuple[float, float]  # graphic region (y0, y1) in PDF points, caption excluded
    cap: tuple[float, float] = (0.0, 0.0)  # caption's own y span
    image: str = ""           # filename once rendered
    width: int = 0
    height: int = 0

    def to_dict(self) -> dict:
        return {
            "page": self.page, "kind": self.kind, "label": self.label,
            "caption": self.caption, "band": list(self.band),
            "cap": list(self.cap),
            "image": self.image, "width": self.width, "height": self.height,
        }

    @staticmethod
    def from_dict(d: dict) -> "Figure":
        return Figure(page=d["page"], kind=d["kind"], label=d["label"],
                      caption=d["caption"], band=tuple(d["band"]),
                      cap=tuple(d.get("cap", (0.0, 0.0))),
                      image=d.get("image", ""), width=d.get("width", 0),
                      height=d.get("height", 0))


def normalize_label(raw: str) -> str:
    """'图 2. 3' -> '图2.3' so a caption matches the references in the prose."""
    lab = re.sub(r"\s+", "", raw)
    lab = lab.replace("．", ".").replace("·", ".").replace("-", ".")
    lab = lab.replace("Fig.", "图").replace("Figure", "图")
    lab = lab.replace("Table", "表")
    m = re.match(r"^(图表|表|图)(\d{1,3})\.(\d{1,3})$", lab)
    if m:
        return f"{m.group(1) if m.group(1) != '图表' else '表'}{m.group(2)}.{m.group(3)}"
    return lab


def _is_prose(line: Line, text_width: float) -> bool:
    """A full-measure line of running text, as opposed to a table cell."""
    t = line.text.strip()
    return line.width > text_width * 0.55 and len(t) >= 12


def _caption_group(lines: list[Line], i: int) -> list[Line]:
    """The caption line plus everything that belongs to the same caption.

    A caption is frequently emitted as several fragments: the label, the
    description, a citation. They share a row (so they are absorbed by vertical
    overlap, in both directions) and the description may continue on the lines
    immediately below.
    """
    group = [lines[i]]
    y0 = lines[i].y0
    y1 = lines[i].y1
    height = max(y1 - y0, 1.0)

    # same-row fragments before the label
    j = i - 1
    while j >= 0 and len(group) < 8:
        l = lines[j]
        if CAPTION_AT_START_RE.match(l.text):
            break
        if l.y1 <= y0 + 1 or l.y0 >= y1 - 1:
            break
        if _looks_like_prose_block(l):
            break
        group.insert(0, l)
        y0 = min(y0, l.y0)
        j -= 1

    # continuation lines below
    k = i + 1
    while k < len(lines) and len(group) < 8:
        l = lines[k]
        if CAPTION_AT_START_RE.match(l.text):
            break
        gap = l.y0 - max(g.y1 for g in group)
        if gap > height * 0.6:
            break
        if gap < -height:
            break
        group.append(l)
        k += 1
    return group


def _looks_like_prose_block(line: Line) -> bool:
    """A same-row neighbour that is a genuine paragraph, not a caption fragment."""
    t = line.text.strip()
    return line.width > 0 and len(t) >= 25 and line.width > 200.0


def _adjacent_graphic_extent(lines: list[Line], group: list[Line],
                             kind: str, text_width: float,
                             page_height: float) -> float:
    """How much non-prose space sits on the graphic side of a caption block.

    This is the test that separates a caption from a cross-reference, and it is
    deliberately independent of line width. A caption is printed against its own
    artwork or table body; a sentence that merely cites a figure is embedded in
    running text, so the space on the graphic side of it is prose.

    It also survives the case where the scanner emitted *no* text at all for the
    artwork (common with Acrobat), because then the extent is measured to the
    edge of the page instead of to a neighbouring line.
    """
    cap_top = min(g.y0 for g in group)
    cap_bottom = max(g.y1 for g in group)

    if kind == "table":
        below = [l for l in lines if l.y0 >= cap_bottom - 1]
        if not below:
            return page_height - cap_bottom
        bottom = None
        for l in below:
            if _is_prose(l, text_width) or CAPTION_AT_START_RE.match(l.text):
                break
            bottom = l.y1
        start = bottom if bottom is not None else below[0].y0
        return start - cap_bottom

    above = [l for l in lines if l.y1 <= cap_top + 1]
    if not above:
        return cap_top
    top = None
    for l in reversed(above):
        if _is_prose(l, text_width) or CAPTION_AT_START_RE.match(l.text):
            break
        top = l.y0
    start = top if top is not None else above[-1].y1
    return cap_top - start


def find_captions(page_lines: list[Line], text_width: float,
                  page_height: float) -> list[tuple[list[Line], str, str]]:
    """Return [(caption lines, label, kind)] for genuine captions on a page."""
    out: list[tuple[list[Line], str, str]] = []
    seen: set[str] = set()
    for i, line in enumerate(page_lines):
        m = CAPTION_AT_START_RE.match(line.text)
        if not m or _looks_like_reference(line.text):
            continue
        label = normalize_label(m.group("label"))
        if label in seen:
            continue

        is_table = bool(TABLE_LABEL_RE.match(m.group("label")))
        kind = "table" if is_table else "figure"
        group = _caption_group(page_lines, i)
        extent = _adjacent_graphic_extent(page_lines, group, kind,
                                          text_width, page_height)
        if extent < 20.0:
            continue
        seen.add(label)
        out.append((group, label, kind))
    return out


def text_column(lines: list[Line], left: float, right: float) -> list[Line]:
    """Lines inside the text column, ordered by raw vertical position.

    Thin wrapper over :func:`p2e.clean.text_column` so figure planning and
    paragraph rebuilding agree on what counts as page text.
    """
    from .clean import text_column as _tc
    return _tc(lines, left, right)


# '图6.6和图6.7', '图6.6、图6.7' -- a label joined to another label is a citation
_JOINER_REF_RE = re.compile(
    r"^\s*(?:表|图|图表|Table|Figure|Fig\.?)\s*\d{1,3}\s*[.\-．·]\s*\d{1,3}"
    r"\s*[和与及、,，]\s*(?:\d|表|图|Table|Figure|Fig)",
    re.IGNORECASE)


def _looks_like_reference(line_text: str) -> bool:
    """True when a line starting with a label is prose citing figures.

    Deliberately narrow, and the only text-only rule left. Scanner OCR deletes
    the space between a label and its caption text (``图3.1比恩绘制的…``), and a
    real caption routinely names other figures (``图6.7旋转图6.6中…``), so
    judging by what follows the label -- or by how many labels a line contains --
    rejects genuine captions. Whether the line sits against a graphic is the
    reliable discriminator (:func:`_adjacent_graphic_extent`); this screens out
    only the unambiguous citation form, a label joined to another by 和/、.
    """
    return bool(_JOINER_REF_RE.match(line_text))


def label_is_clean(label: str) -> bool:
    """A figure number whose minor part is plausibly a number, not merged text.

    OCR glues the following year onto the label (``图2.11868年``), yielding
    ``图2.118``. Real minor numbers here are one or two digits.
    """
    m = re.match(r"^(?:图表|表|图)(\d{1,3})\.(\d{1,3})$", label)
    return bool(m) and len(m.group(2)) <= 2


def _overlap_ratio(a: Figure, b: Figure) -> float:
    lo = max(a.band[0], b.band[0])
    hi = min(a.band[1], b.band[1])
    inter = max(0.0, hi - lo)
    shorter = min(a.band[1] - a.band[0], b.band[1] - b.band[0])
    return inter / shorter if shorter > 0 else 0.0


def merge_figures(primary: dict[int, list[Figure]],
                  secondary: dict[int, list[Figure]],
                  threshold: float = 0.5) -> dict[int, list[Figure]]:
    """Combine detections from two text sources, one entry per real graphic.

    Each text source sees a different subset: a caption OCR mangled in one is
    clean in the other. Duplicates are matched by where the graphic is on the
    page rather than by label, because a label can be corrupted in one source;
    when both describe the same region the cleaner label wins.
    """
    out: dict[int, list[Figure]] = {p: list(v) for p, v in primary.items()}
    for page, figs in secondary.items():
        bucket = out.setdefault(page, [])
        for f in figs:
            for i, g in enumerate(bucket):
                if _overlap_ratio(f, g) >= threshold:
                    if label_is_clean(f.label) and not label_is_clean(g.label):
                        keep = f
                        keep.image = g.image or keep.image
                        bucket[i] = keep
                    break
            else:
                bucket.append(f)
        bucket.sort(key=lambda x: x.band[0])
    return {p: v for p, v in out.items() if v}


def plan_figures(pages: list[PageText], profiles: dict,
                 body_size: float) -> dict[int, list[Figure]]:
    """Find every table and figure and work out the band to crop for it."""
    planned: dict[int, list[Figure]] = {}
    for pt in pages:
        prof = profiles.get(pt.page)
        if prof is None or not pt.lines:
            continue
        lines = text_column(pt.lines, prof.left, prof.right)
        if not lines:
            continue
        caps = find_captions(lines, prof.text_width, pt.height)
        if not caps:
            continue

        cap_idx = set()
        for group, _label, _kind in caps:
            for g in group:
                cap_idx.add(id(g))
        # Bounds for a graphic: prose paragraphs *and* neighbouring captions.
        # Leaving captions out let a table's band run into the next table.
        bounds: list[tuple[float, float, str]] = []
        for l in lines:
            if id(l) in cap_idx:
                continue
            if _is_prose(l, prof.text_width):
                bounds.append((l.y0, l.y1, "prose"))
        for group, _label, _kind in caps:
            bounds.append((min(g.y0 for g in group), max(g.y1 for g in group),
                           "caption"))
        bounds.sort()

        figures: list[Figure] = []
        for group, label, kind in caps:
            cap_y0 = min(g.y0 for g in group)
            cap_y1 = max(g.y1 for g in group)
            if kind == "table":
                y0 = cap_y1
                nxt = [b for b in bounds if b[0] >= cap_y1 - 1]
                y1 = nxt[0][0] if nxt else pt.height
            else:
                y1 = cap_y0
                prv = [b for b in bounds if b[1] <= cap_y0 + 1]
                y0 = prv[-1][1] if prv else 0.0
            if y1 - y0 < 18:
                continue
            caption = squeeze(" ".join(g.text for g in group))
            figures.append(Figure(
                page=pt.page, kind=kind, label=label,
                caption=caption, band=(round(y0, 1), round(y1, 1)),
                cap=(round(cap_y0, 1), round(cap_y1, 1))))
        if figures:
            planned[pt.page] = figures
    return planned


# --------------------------------------------------------------------------
# cropping
# --------------------------------------------------------------------------

def _ink_bbox(gray, threshold: int = 210):
    """Rows/cols in the array that carry ink; None when the band is blank."""
    import numpy as np
    ink = gray < threshold
    rows = np.where(ink.any(axis=1))[0]
    cols = np.where(ink.any(axis=0))[0]
    if rows.size == 0 or cols.size == 0:
        return None
    return int(rows[0]), int(rows[-1]), int(cols[0]), int(cols[-1])


def crop_bands(pdf_path: str, figures: list[Figure], x_range: tuple[float, float],
               dpi: int = 300, pad: float = 0.0, max_width: int = 1500) -> None:
    """Render each figure's band from the page and trim it to the ink.

    Rendering uses a clip rectangle, so only the band is rasterised. The running
    head is excluded by restricting the horizontal extent to the text column.

    ``pad`` defaults to 0 deliberately: a band already ends exactly where the
    neighbouring caption begins, so padding it would print the top of the next
    table's caption along the bottom of this figure. Breathing room comes from
    the word margin added around the trimmed ink instead, and that stays inside
    the band.
    """
    import numpy as np
    import pymupdf
    from PIL import Image

    doc = pymupdf.open(pdf_path)
    zoom = dpi / 72.0
    x0p, x1p = x_range
    for fig in figures:
        page = doc[fig.page - 1]
        y0 = max(0.0, fig.band[0] - pad)
        y1 = min(page.rect.height, fig.band[1] + pad)
        x0 = max(0.0, x0p)
        x1 = min(page.rect.width, x1p)
        clip = pymupdf.Rect(x0, y0, x1, y1)
        pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom),
                             colorspace=pymupdf.csGRAY, clip=clip)
        arr = np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)
        box = _ink_bbox(arr)
        if box is None:
            warn(f"figure {fig.label} on page {fig.page} cropped to a blank band")
            continue
        r0, r1, c0, c1 = box
        # a small white margin inside the band; the clip keeps it from reaching
        # a neighbouring caption
        margin = max(4, int(round(2.0 * zoom)))
        r0 = max(0, r0 - margin)
        c0 = max(0, c0 - margin)
        r1 = min(arr.shape[0] - 1, r1 + margin)
        c1 = min(arr.shape[1] - 1, c1 + margin)
        sub = arr[r0:r1 + 1, c0:c1 + 1]
        img = Image.fromarray(sub, mode="L").convert("RGB")
        if img.width > max_width:
            h = round(img.height * max_width / img.width)
            img = img.resize((max_width, h), Image.LANCZOS)
        fig.width, fig.height = img.size
        fig._pil = img  # type: ignore[attr-defined]
    doc.close()


def save_figures(figures: dict[int, list[Figure]], out_dir: str,
                 quality: int = 88) -> list[str]:
    """Write rendered figures to disk and set fig.image to the filename."""
    os.makedirs(out_dir, exist_ok=True)
    written: list[str] = []
    for page in sorted(figures):
        for n, fig in enumerate(figures[page], 1):
            img = getattr(fig, "_pil", None)
            if img is None:
                continue
            name = f"fig_p{page:04d}_{n}.jpg"
            path = os.path.join(out_dir, name)
            img.save(path, "JPEG", quality=quality, optimize=True)
            fig.image = name
            written.append(path)
    return written


def in_band(line: Line, fig: Figure, tol: float = 1.0) -> bool:
    """Is this line inside the figure's graphic region (caption excluded)?"""
    return line.y1 > fig.band[0] + tol and line.y0 < fig.band[1] - tol


def caption_y(fig: Figure) -> float:
    """Where the caption sits, used to order the figure in the text flow."""
    return fig.band[0]
