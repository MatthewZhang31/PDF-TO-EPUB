"""Extract embedded text layer, page images, and the cover from a PDF."""
from __future__ import annotations

import os
from typing import Optional

import pymupdf

from .util import (Line, PageText, log, median, order_lines, step, warn)

# Lines shorter than this many characters are not considered body text
# when measuring the body font size.
_MIN_BODY_SAMPLE = 8


def open_doc(pdf_path: str) -> pymupdf.Document:
    doc = pymupdf.open(pdf_path)
    if doc.needs_pass:
        raise RuntimeError(
            "PDF is password protected; supply an unlocked copy before converting."
        )
    return doc


def page_lines(page: pymupdf.Page) -> list[Line]:
    """Return visual lines with geometry and the dominant font size."""
    out: list[Line] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for ln in block.get("lines", []):
            spans = [s for s in ln.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            text = "".join(s["text"] for s in spans)
            if not text.strip():
                continue
            sizes = [s["size"] for s in spans]
            weight = [len(s["text"]) for s in spans]
            total = sum(weight) or 1
            size = sum(s * w for s, w in zip(sizes, weight)) / total
            box = ln["bbox"]
            out.append(Line(
                text=text.rstrip(),
                x0=float(box[0]), y0=float(box[1]),
                x1=float(box[2]), y1=float(box[3]),
                size=float(size), score=1.0, source="embedded",
            ))
    out.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return order_lines(out)


def body_font_size(pages: list[PageText]) -> float:
    """Median line size over long lines: a robust estimate of body text size."""
    sizes: list[float] = []
    for pt in pages:
        for l in pt.lines:
            if len(l.text) >= _MIN_BODY_SAMPLE and l.size > 0:
                sizes.append(round(l.size, 1))
    return median(sizes) or 0.0


def extract_text_layer(doc: pymupdf.Document) -> list[PageText]:
    """Extract every page's embedded text layer."""
    pages: list[PageText] = []
    for i, page in enumerate(doc):
        r = page.rect
        pages.append(PageText(
            page=i + 1, width=r.width, height=r.height,
            lines=page_lines(page), source="embedded",
        ))
    return pages


# --------------------------------------------------------------------------
# images / cover
# --------------------------------------------------------------------------

def page_image_coverage(page: pymupdf.Page) -> float:
    """Fraction of the page area covered by the largest embedded raster image."""
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return 0.0
    best = 0.0
    for info in page.get_image_info():
        bb = info.get("bbox")
        if not bb:
            continue
        area = max(0.0, bb[2] - bb[0]) * max(0.0, bb[3] - bb[1])
        best = max(best, area / page_area)
    return best


def guess_cover_page(doc: pymupdf.Document, max_scan: int = 8) -> Optional[int]:
    """Find the most likely cover page.

    A book's front cover is almost always the first page; later pages that are
    also full-page images (title page, frontispiece) must not win by a hair.
    So page 1 wins whenever it carries a full-page image, and otherwise the
    earliest page with a coverage close to the best is chosen.
    """
    if doc.page_count and page_image_coverage(doc[0]) >= 0.6:
        return 1
    covers = [(i + 1, page_image_coverage(doc[i]))
              for i in range(min(max_scan, doc.page_count))]
    if not covers:
        return None
    best_page, best_cov = max(covers, key=lambda t: t[1])
    if best_cov < 0.5:
        return 1 if doc.page_count and page_image_coverage(doc[0]) > 0.2 else None
    # prefer the earliest page that is nearly as good as the best
    for page_no, cov in covers:
        if cov >= best_cov * 0.92:
            return page_no
    return best_page


def render_page_image(doc: pymupdf.Document, page_no: int, dpi: int = 200):
    """Render a page to a PIL image."""
    from PIL import Image
    page = doc[page_no - 1]
    zoom = dpi / 72.0
    pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pm.width, pm.height), pm.samples)


def save_cover(doc: pymupdf.Document, page_no: int, out_path: str,
               max_width: int = 1600, quality: int = 88) -> str:
    """Render the cover page and save it as a JPEG sized for e-readers."""
    from PIL import Image
    img = render_page_image(doc, page_no, dpi=200)
    if img.width > max_width:
        h = round(img.height * max_width / img.width)
        img = img.resize((max_width, h), Image.LANCZOS)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    img.convert("RGB").save(out_path, "JPEG", quality=quality, optimize=True)
    log(f"  cover -> {os.path.basename(out_path)}  {img.width}x{img.height}")
    return out_path


def export_page_images(doc: pymupdf.Document, out_dir: str, page_numbers: list[int],
                       dpi: int = 150) -> list[str]:
    """Render selected pages to PNG (debug / inspection aid)."""
    os.makedirs(out_dir, exist_ok=True)
    paths = []
    for p in page_numbers:
        if not (1 <= p <= doc.page_count):
            continue
        img = render_page_image(doc, p, dpi=dpi)
        path = os.path.join(out_dir, f"page_{p:04d}.png")
        img.save(path)
        paths.append(path)
    return paths
