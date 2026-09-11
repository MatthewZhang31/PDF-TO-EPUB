"""OCR engine wrapper (RapidOCR / PP-OCR ONNX), with multiprocessing driver."""
from __future__ import annotations

import os
from typing import Optional

import numpy as np
import pymupdf

from .util import Line, PageText, log, median, order_lines, step, warn

_ENGINE = None


def engine_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


def render_gray(page: pymupdf.Page, target_height: int = 2200) -> np.ndarray:
    """Render a page to a greyscale numpy array, scaled to ~target_height px."""
    zoom = target_height / max(page.rect.height, 1.0)
    pm = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY)
    return np.frombuffer(pm.samples, dtype=np.uint8).reshape(pm.height, pm.width)


def ocr_page(page: pymupdf.Page, target_height: int = 2200) -> PageText:
    """OCR one page and return its lines with geometry in PDF point units."""
    engine = _get_engine()
    arr = render_gray(page, target_height)
    scale = page.rect.height / max(arr.shape[0], 1)
    result, _ = engine(arr)
    lines: list[Line] = []
    if result:
        for item in result:
            box, text = item[0], item[1]
            try:
                score = float(item[2]) if len(item) > 2 else 1.0
            except (TypeError, ValueError):
                score = 1.0
            text = (text or "").strip()
            if not text:
                continue
            xs = [p[0] for p in box]
            ys = [p[1] for p in box]
            h = (max(ys) - min(ys)) * scale
            lines.append(Line(
                text=text,
                x0=min(xs) * scale, y0=min(ys) * scale,
                x1=max(xs) * scale, y1=max(ys) * scale,
                size=round(h, 2), score=score, source="ocr",
            ))
    lines.sort(key=lambda l: (round(l.y0, 1), l.x0))
    return PageText(page=page.number + 1, width=page.rect.width,
                    height=page.rect.height, lines=order_lines(lines), source="ocr")


_WORKER: dict = {}


def _worker_init(pdf_path: str, target_height: int) -> None:
    import pymupdf as _fitz
    from . import ocr as _ocr  # noqa: F401  (ensures models load in child)
    _WORKER["doc"] = _fitz.open(pdf_path)
    _WORKER["h"] = target_height
    _ocr._get_engine()


def _worker_run(page_no: int) -> dict:
    doc = _WORKER["doc"]
    pt = ocr_page(doc[page_no - 1], _WORKER["h"])
    return pt.to_dict()


def ocr_pages_parallel(pdf_path: str, page_numbers: list[int],
                       target_height: int = 2200, workers: Optional[int] = None) -> dict[int, PageText]:
    """OCR many pages using a process pool; returns {page_no: PageText}."""
    import multiprocessing as mp

    if workers is None:
        workers = max(1, min(4, (os.cpu_count() or 2) // 2))
    todo = sorted(set(page_numbers))
    out: dict[int, PageText] = {}
    if not todo:
        return out

    if workers <= 1:
        doc = pymupdf.open(pdf_path)
        for n in todo:
            out[n] = ocr_page(doc[n - 1], target_height)
            log(f"    ocr page {n}")
        return out

    step(f"OCR {len(todo)} pages with {workers} workers")
    try:
        with mp.Pool(workers, initializer=_worker_init,
                     initargs=(pdf_path, target_height)) as pool:
            for i, d in enumerate(pool.imap_unordered(_worker_run, todo, chunksize=1), 1):
                pt = PageText.from_dict(d)
                out[pt.page] = pt
                if i % 10 == 0 or i == len(todo):
                    log(f"    ocr {i}/{len(todo)}")
    except (RuntimeError, OSError) as exc:
        # Windows spawn re-imports the entry module; an unguarded __main__
        # makes the pool recurse. Fall back to serial rather than crash.
        warn(f"multiprocessing OCR failed ({exc}); falling back to a single process")
        doc = pymupdf.open(pdf_path)
        for n in todo:
            out[n] = ocr_page(doc[n - 1], target_height)
            log(f"    ocr page {n}")
    return out
