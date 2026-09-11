"""Stage 1 - analyse a scanned PDF and decide how to convert it."""
from __future__ import annotations

import os
import re
from typing import Any

import pymupdf

from .extract import (body_font_size, extract_text_layer, guess_cover_page,
                      page_image_coverage)
from .util import (cjk_ratio, looks_garbled, log, median, step, warn, write_json)

# headings that mark a table-of-contents page in the scanned image
TOC_TITLES = ("目录", "目 录", "contents", "table of contents", "目次", "總目", "总目")

# "章节标题 ....... 123"  /  "章节标题    123"
TOC_LINE_RE = re.compile(
    r"^(?P<title>[^\d]{2,60}?)[\s.·…⋯\-—]{1,}(?P<page>\d{1,4})\s*$"
)


def analyze(pdf_path: str) -> dict[str, Any]:
    doc = pymupdf.open(pdf_path)
    meta = {k: v for k, v in (doc.metadata or {}).items() if v}
    pages = extract_text_layer(doc)
    body = body_font_size(pages)

    detail: list[dict] = []
    text_pages = 0
    garbled_pages: list[int] = []
    for i, page in enumerate(doc):
        pt = pages[i]
        chars = sum(len(l.text.strip()) for l in pt.lines)
        if chars >= 30:
            text_pages += 1
        blob = "\n".join(l.text for l in pt.lines)
        garbled = chars >= 30 and looks_garbled(blob)
        if garbled:
            garbled_pages.append(i + 1)
        detail.append({
            "page": i + 1,
            "chars": chars,
            "lines": len(pt.lines),
            "img_coverage": round(page_image_coverage(page), 3),
            "garbled": garbled,
            "cjk_ratio": round(cjk_ratio(blob), 3),
        })

    n = doc.page_count
    text_ratio = text_pages / n if n else 0.0
    garble_ratio = len(garbled_pages) / max(text_pages, 1)

    toc = doc.get_toc() or []
    outline = [{"level": lv, "title": t.strip(), "page": pg} for lv, t, pg in toc]

    cover = guess_cover_page(doc)
    toc_pages = detect_toc_pages(pages)

    if text_ratio < 0.2:
        mode = "scanned"          # almost no text layer: OCR everything
    elif garble_ratio > 0.35:
        mode = "garbled-layer"    # text layer exists but is unusable
    else:
        mode = "text-layer"       # usable embedded text; OCR only where it fails

    report = {
        "pdf": os.path.abspath(pdf_path),
        "pages": n,
        "metadata": meta,
        "title": meta.get("title") or "",
        "author": meta.get("author") or "",
        "body_font_size": round(body, 2),
        "text_pages": text_pages,
        "text_ratio": round(text_ratio, 3),
        "garbled_pages": garbled_pages,
        "garble_ratio": round(garble_ratio, 3),
        "mode": mode,
        "outline": outline,
        "cover_page": cover,
        "toc_pages": toc_pages,
        "page_size": [round(doc[0].rect.width, 1), round(doc[0].rect.height, 1)] if n else [],
        "pages_detail": detail,
    }
    return report


def _is_toc_heading(line: str) -> bool:
    """Recognise a '目录 / Contents' heading, tolerating OCR variants."""
    t = re.sub(r"[\s\u3000:：.．·]+", "", line or "").lower()
    if not t:
        return False
    if t in {"目录", "目次", "总目", "總目", "目绿", "冃录", "目求"}:
        return True
    return "contents" in t and len(t) <= 20


def detect_toc_pages(pages) -> list[int]:
    """Find pages that look like a printed table of contents."""
    found: list[int] = []
    for pt in pages:
        lines = [l.text.strip() for l in pt.lines if l.text.strip()]
        if not lines:
            continue
        if any(_is_toc_heading(l) for l in lines[:3]):
            found.append(pt.page)
            continue
        # pages that carry many "title <gap> page" rows, either on one line
        # or as a title line followed by a bare page number
        matched = 0
        for i, l in enumerate(lines):
            if TOC_LINE_RE.match(l):
                matched += 1
            elif i + 1 < len(lines) and re.fullmatch(r"\d{1,4}", lines[i + 1]) \
                    and 2 <= len(l) <= 60 and not re.fullmatch(r"\d+", l):
                matched += 1
        if matched >= 4:
            found.append(pt.page)
    return found


def parse_toc_lines(pages, toc_pages: list[int]) -> list[dict]:
    """Parse printed TOC pages into [{title, printed_page}] entries."""
    entries: list[dict] = []
    for pno in toc_pages:
        pt = next((p for p in pages if p.page == pno), None)
        if pt is None:
            continue
        lines = [l.text.strip() for l in pt.lines if l.text.strip()]
        for i, txt in enumerate(lines):
            if _is_toc_heading(txt):
                continue
            m = TOC_LINE_RE.match(txt)
            if m:
                entries.append({
                    "title": m.group("title").strip(" .·…⋯-—"),
                    "printed_page": int(m.group("page")),
                })
                continue
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            if re.fullmatch(r"\d{1,4}", nxt) and 2 <= len(txt) <= 60 \
                    and not re.fullmatch(r"\d+", txt):
                entries.append({"title": txt.strip(" .·…⋯-—"),
                                "printed_page": int(nxt)})
            elif entries and len(txt) <= 40 and not re.fullmatch(r"\d+", txt):
                # continuation of a wrapped title
                entries[-1]["title"] += txt
    # de-duplicate consecutive repeats
    seen, out = set(), []
    for e in entries:
        key = (e["title"], e["printed_page"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def format_report(rep: dict) -> str:
    lines = [
        f"PDF            : {rep['pdf']}",
        f"pages          : {rep['pages']}   size={rep['page_size']}",
        f"title/author   : {rep['title']!r} / {rep['author']!r}",
        f"embedded text  : {rep['text_pages']}/{rep['pages']} pages "
        f"({rep['text_ratio']:.0%}), garbled {len(rep['garbled_pages'])} "
        f"({rep['garble_ratio']:.0%})",
        f"body font size : {rep['body_font_size']}",
        f"convert mode   : {rep['mode']}",
        f"cover page     : {rep['cover_page']}",
        f"printed TOC    : pages {rep['toc_pages']}",
        f"PDF outline    : {len(rep['outline'])} entries",
    ]
    for o in rep["outline"]:
        lines.append(f"    {'  ' * (o['level'] - 1)}p{o['page']:<4} {o['title']}")
    return "\n".join(lines)
