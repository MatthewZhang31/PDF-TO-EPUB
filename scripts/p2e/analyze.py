"""Stage 1 - analyse a scanned PDF and decide how to convert it."""
from __future__ import annotations

import os
import re
from typing import Any

import pymupdf

from .extract import (body_font_size, extract_text_layer, guess_cover_page,
                      page_image_coverage)
from .util import (cjk_ratio, looks_garbled, log, median, squeeze, step, warn,
                   write_json)

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
    # a printed contents page sits near the front; searching the whole book
    # picks up body pages that merely contain numbered lists
    toc_pages = detect_toc_pages(pages, max_page=max(20, n // 8))

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


def _toc_row_score(lines: list[str]) -> int:
    """Count rows that look like 'title <gap> page' inside a candidate page."""
    matched = 0
    for i, l in enumerate(lines):
        if TOC_LINE_RE.match(l):
            matched += 1
        elif i + 1 < len(lines) and re.fullmatch(r"\d{1,4}", lines[i + 1]) \
                and 2 <= len(l) <= 60 and not re.fullmatch(r"\d+", l):
            matched += 1
    return matched


def detect_toc_pages(pages, max_page: int = 0) -> list[int]:
    """Find the printed table of contents.

    A contents page lives near the front of the book, so the search is limited
    to the front region: body pages full of numbered lists otherwise score just
    as highly and used to be picked up as contents pages, which then injected
    junk into the recovered TOC. Once the '目录 / Contents' heading is found,
    only the immediately following pages that keep looking like contents are
    included, instead of every page in the book that matches the pattern.
    """
    by_page = {pt.page: [l.text.strip() for l in pt.lines if l.text.strip()]
               for pt in pages}
    last = max_page or (max(by_page) if by_page else 0)

    # 1. the explicit heading, then its contiguous continuation
    heading = None
    for p in sorted(by_page):
        if p > last:
            break
        if any(_is_toc_heading(l) for l in by_page[p][:3]):
            heading = p
            break
    if heading is not None:
        out = [heading]
        for p in sorted(by_page):
            if p <= heading:
                continue
            if p > last:
                break
            if p not in out:
                pass
            if _toc_row_score(by_page[p]) >= 3:
                out.append(p)
            else:
                break
        # keep only a contiguous run starting at the heading
        out = [p for p in out if p >= heading]
        return out

    # 2. no heading: any front-region page dense with numbered rows
    found = []
    for p in sorted(by_page):
        if p > last:
            break
        if _toc_row_score(by_page[p]) >= 4:
            found.append(p)
    return found


# dot leaders and the page number on a contents row. Printed contents pages
# separate a title from its folio with a run of dots, middots or spaces, and
# often split one logical row across several text lines.
_LEAD = r"[\s.．。·…⋯・‧∙_\-—]"
_LEADER_RE = re.compile(rf"^(?P<title>.*?)(?P<lead>{_LEAD}{{3,}})(?P<page>\d{{1,4}})$")
_NUMBER_ONLY_RE = re.compile(rf"^{_LEAD}*(\d{{1,4}}){_LEAD}*$")
_LEAD_ONLY_RE = re.compile(rf"^{_LEAD}+$")
_LEAD_STRIP = ".．。·…⋯・‧∙_-— \t\u3000"


def _clean_toc_title(t: str) -> str:
    t = squeeze(t)
    t = t.strip(_LEAD_STRIP)
    t = re.sub(rf"{_LEAD}{{2,}}", "", t)
    return t.strip(_LEAD_STRIP)


def parse_toc_lines(pages, toc_pages: list[int]) -> list[dict]:
    """Parse printed TOC pages into [{title, printed_page, x0}] entries.

    Rows are assembled by *accumulating title fragments until a page number
    appears*, because a single logical row is frequently split across several
    text lines: the heading, a wrapped continuation, the dot leader and the
    folio can each be their own line. Treating any one of them as a complete
    entry is what produced merged titles and empty entries.

    ``x0`` of the first fragment is kept: indentation is the only reliable
    signal for how deeply a contents entry nests.
    """
    entries: list[dict] = []
    pending = ""
    pending_x0: float | None = None

    def emit(page: int) -> None:
        nonlocal pending, pending_x0
        title = _clean_toc_title(pending)
        if not _toc_title_is_noise(title):
            entries.append({"title": title, "printed_page": int(page),
                            "x0": round(pending_x0 if pending_x0 is not None else 0.0, 1)})
        pending, pending_x0 = "", None

    for pno in toc_pages:
        pt = next((p for p in pages if p.page == pno), None)
        if pt is None:
            continue
        for line in pt.lines:
            txt = line.text.strip()
            if not txt or _is_toc_heading(txt):
                continue
            if _LEAD_ONLY_RE.match(txt):
                continue

            m = _LEADER_RE.match(txt)
            if m:
                frag = m.group("title")
                if frag and not _LEAD_ONLY_RE.match(frag):
                    if pending_x0 is None:
                        pending_x0 = line.x0
                    pending += frag
                emit(int(m.group("page")))
                continue

            m = _NUMBER_ONLY_RE.match(txt)
            if m:
                if pending:
                    emit(int(m.group(1)))
                continue

            # a plain title fragment: start of a row, or its continuation
            if _CHAPTER_RE.match(txt) and len(pending) > 12:
                # the accumulated fragment never found a folio, so it is a
                # parsing artefact; do not let it swallow the chapter heading
                pending, pending_x0 = "", None
            if pending_x0 is None:
                pending_x0 = line.x0
            elif line.x0 - pending_x0 > 12 and len(pending) > 4:
                # clearly deeper indent: previous row had no folio, so drop the
                # accumulated fragment rather than gluing the rows together
                pending = ""
                pending_x0 = line.x0
            pending += txt

    # de-duplicate repeats of the same (title, page) pair
    seen, out = set(), []
    for e in entries:
        key = (e["title"], e["printed_page"])
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


CONTENTS_HEADING_ONLY = ("preface", "foreword", "introduction")


# A contents entry whose title looks like this is OCR noise rather than a
# heading: mostly digits/punctuation, or a stray running head.
_RUNNING_HEAD = {"目录", "目次", "contents", "人类的误测", "人类的误测·智商歧视的科学史"}
_CHAPTER_RE = re.compile(r"^第\s*[一二三四五六七八九十百零〇\d]+\s*[章篇部]")
_BACK_MATTER = {
    "致谢", "前言", "序", "序言", "导论", "导言", "引言", "后记", "附录",
    "跋", "结语", "尾声", "参考文献", "索引", "译后记", "编后记", "注释",
}


def _toc_title_is_noise(title: str) -> bool:
    t = squeeze(title)
    if len(t) < 2:
        return True
    if t.replace(" ", "") in {h.replace(" ", "") for h in _RUNNING_HEAD}:
        return True
    if len(t) > 70:
        return True
    cjk = sum(1 for c in t if "\u4e00" <= c <= "\u9fff")
    digits = sum(1 for c in t if c.isdigit())
    other = len(t) - cjk - digits
    if cjk < 2:
        return True
    if digits >= 3 or other > cjk * 0.6:
        return True
    return False


def assign_toc_levels(entries: list[dict], max_level: int = 4) -> list[int]:
    """Infer nesting depth for contents rows.

    Chapter numbers and front/back matter are authoritative level 1: they are
    recognisable from the text itself, whereas indentation on a scanned contents
    page is noisy and, used alone, pushes chapter titles to the deepest level.
    Everything else is ranked by clustering the remaining left offsets.
    """
    if not entries:
        return []

    levels: list[int | None] = []
    rest_x: list[float] = []
    for e in entries:
        t = squeeze(str(e.get("title", "")))
        forced = bool(_CHAPTER_RE.match(t)) or t in _BACK_MATTER
        levels.append(1 if forced else None)
        if not forced:
            rest_x.append(float(e.get("x0", 0.0)))

    if not rest_x:
        return [1] * len(entries)

    xs = sorted(set(round(x) for x in rest_x))
    clusters: list[list[int]] = [[xs[0]]]
    for x in xs[1:]:
        if x - clusters[-1][-1] <= 8:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    centres = [sum(c) / len(c) for c in clusters]

    def depth_for(x: float) -> int:
        best, bd = 0, 1e9
        for i, c in enumerate(centres):
            if abs(x - c) < bd:
                best, bd = i, abs(x - c)
        return min(2 + best, max_level)

    out: list[int] = []
    for lv, e in zip(levels, entries):
        out.append(lv if lv is not None else depth_for(float(e.get("x0", 0.0))))
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
