#!/usr/bin/env python3
"""pdf2epub - convert a scanned PDF book into an EPUB 3 with cover and TOC.

Typical use
-----------
    python pdf2epub.py all --pdf book.pdf --out out/book

Stages can also be run one at a time; every stage reads and writes JSON
artefacts inside --out so a failed run can be resumed cheaply.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p2e.util import (log, read_json, setup_console, slugify, step, warn,
                      write_json)  # noqa: E402

setup_console()


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _paths(out: str) -> dict:
    return {
        "analysis": os.path.join(out, "analysis.json"),
        "embedded": os.path.join(out, "pages_embedded.json"),
        "ocr": os.path.join(out, "ocr.json"),
        "book": os.path.join(out, "book.json"),
        "toc_json": os.path.join(out, "toc.json"),
        "toc_md": os.path.join(out, "toc.md"),
        "cover": os.path.join(out, "cover.jpg"),
        "epub": None,  # filled later
    }


def _parse_pages(spec: str, page_count: int) -> list[int]:
    """Parse '1-5,8,20-' into a list of page numbers."""
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            start = int(a) if a.strip() else 1
            end = int(b) if b.strip() else page_count
            out.extend(range(start, end + 1))
        else:
            out.append(int(chunk))
    return sorted({p for p in out if 1 <= p <= page_count})


def _load_embedded(path: str, pdf: str) -> dict:
    from p2e.extract import extract_text_layer, open_doc
    from p2e.util import PageText
    if os.path.isfile(path):
        raw = read_json(path)
        return {int(k): PageText.from_dict(v) for k, v in raw.items()}
    step("extracting embedded text layer")
    doc = open_doc(pdf)
    pages = extract_text_layer(doc)
    write_json(path, {str(p.page): p.to_dict() for p in pages})
    return {p.page: p for p in pages}


def _load_ocr(path: str) -> dict:
    from p2e.util import PageText
    if not os.path.isfile(path):
        return {}
    raw = read_json(path)
    return {int(k): PageText.from_dict(v) for k, v in raw.items()}


# --------------------------------------------------------------------------
# stages
# --------------------------------------------------------------------------

def stage_analyze(args) -> dict:
    from p2e.analyze import analyze, format_report
    from p2e.extract import extract_text_layer, open_doc
    out = args.out
    os.makedirs(out, exist_ok=True)
    rep = analyze(args.pdf)
    write_json(_paths(out)["analysis"], rep)
    log(format_report(rep))

    # always cache the embedded layer; the clean stages need geometry
    emb_path = _paths(out)["embedded"]
    if not os.path.isfile(emb_path):
        step("caching embedded text layer")
        doc = open_doc(args.pdf)
        pages = extract_text_layer(doc)
        write_json(emb_path, {str(p.page): p.to_dict() for p in pages})
    return rep


def stage_cover(args, rep: dict | None = None) -> str:
    from p2e.extract import open_doc, save_cover
    out = args.out
    p = _paths(out)
    if rep is None:
        rep = read_json(p["analysis"]) if os.path.isfile(p["analysis"]) else {}
    page = getattr(args, "cover_page", 0) or rep.get("cover_page") or 1
    doc = open_doc(args.pdf)
    step(f"extracting cover from page {page}")
    save_cover(doc, int(page), p["cover"])
    write_json(os.path.join(out, "cover.json"),
               {"page": int(page), "pdf": os.path.abspath(args.pdf)})
    return p["cover"]


def ensure_cover(args) -> str | None:
    """Refresh the cover when it is missing or was taken from another page.

    Re-running `build` alone must not ship a cover left over from an earlier
    analysis pass, which is how a title page ends up as the book cover.
    """
    out = args.out
    p = _paths(out)
    meta_path = os.path.join(out, "cover.json")
    rep = read_json(p["analysis"]) if os.path.isfile(p["analysis"]) else {}
    want = getattr(args, "cover_page", 0) or rep.get("cover_page") or 1
    if os.path.isfile(p["cover"]) and os.path.isfile(meta_path):
        try:
            meta = read_json(meta_path)
        except Exception:
            meta = {}
        if meta.get("page") == want and \
                os.path.abspath(meta.get("pdf", "")) == os.path.abspath(args.pdf):
            return p["cover"]
        step(f"cover is stale (page {meta.get('page')} -> {want}); re-extracting")
    return stage_cover(args, rep)


def stage_ocr(args, rep: dict | None = None) -> dict:
    from p2e.ocr import engine_available, ocr_pages_parallel
    import pymupdf
    out = args.out
    p = _paths(out)
    if rep is None:
        rep = read_json(p["analysis"]) if os.path.isfile(p["analysis"]) else {}
    if not engine_available():
        warn("rapidocr-onnxruntime is not installed; skipping OCR. "
             "Install it with: pip install rapidocr-onnxruntime onnxruntime")
        return {}

    doc = pymupdf.open(args.pdf)
    n = doc.page_count
    mode = rep.get("mode", "text-layer")
    detail = {d["page"]: d for d in rep.get("pages_detail", [])}
    if args.pages:
        todo = _parse_pages(args.pages, n)
    elif mode in ("scanned", "garbled-layer") or args.force_ocr:
        # nothing usable to start from: every page must be read
        todo = list(range(1, n + 1))
    else:
        # A usable but old text layer is still worth re-reading: modern OCR gets
        # punctuation and mixed Latin/CJK right where the old layer mangles it.
        # Pages that carry no text at all (plates, blanks) are skipped.
        todo = [i for i in range(1, n + 1)
                if detail.get(i, {}).get("chars", 0) >= 30
                or detail.get(i, {}).get("garbled")]
        if args.embedded_only:
            todo = [i for i in todo if detail.get(i, {}).get("garbled")]
            if not todo:
                step("--embedded-only: keeping the embedded text layer as-is")
                return _load_ocr(p["ocr"])

    existing = _load_ocr(p["ocr"])
    todo = [t for t in todo if t not in existing or args.redo]
    if not todo:
        step(f"OCR cache already covers all {len(existing)} pages")
        return existing

    step(f"OCR: {len(todo)} pages (mode={mode})")
    fresh = ocr_pages_parallel(args.pdf, todo, target_height=args.ocr_height,
                               workers=args.workers)
    merged = dict(existing)
    merged.update(fresh)
    write_json(p["ocr"], {str(k): v.to_dict() for k, v in sorted(merged.items())})
    step(f"OCR cache now holds {len(merged)} pages")
    return merged


def stage_assemble(args, rep: dict | None = None) -> dict:
    from p2e.analyze import parse_toc_lines
    from p2e.extract import body_font_size
    from p2e.structure import (apply_manual_chapters, assemble,
                               resolve_chapters_from_outline,
                               resolve_chapters_from_toc_entries, toc_json,
                               toc_markdown)
    out = args.out
    p = _paths(out)
    if rep is None:
        rep = read_json(p["analysis"]) if os.path.isfile(p["analysis"]) else {}

    embedded = _load_embedded(p["embedded"], args.pdf)
    ocr = _load_ocr(p["ocr"])
    page_count = rep.get("pages") or len(embedded)

    # choose per page
    from p2e.structure import pick_page_text
    pages = []
    for i in range(1, page_count + 1):
        pt = pick_page_text(i, embedded, ocr, args.text_source)
        if pt is not None:
            pages.append(pt)
    body = body_font_size(pages)
    step(f"assembling {len(pages)} pages (text-source={args.text_source}, body {body}pt)")

    # chapter skeleton
    if args.chapters:
        manual = read_json(args.chapters)
        chapters = apply_manual_chapters(manual, page_count)
        step(f"chapters: {len(chapters)} (from {args.chapters})")
    elif rep.get("outline"):
        chapters = resolve_chapters_from_outline(rep["outline"], page_count)
        step(f"chapters: {len(chapters)} (from PDF outline)")
    else:
        entries = parse_toc_lines(pages, rep.get("toc_pages") or [])
        chapters = resolve_chapters_from_toc_entries(pages, entries, page_count)
        step(f"chapters: {len(chapters)} (from printed TOC pages)")

    if not chapters:
        chapters = apply_manual_chapters(
            [{"title": rep.get("title") or "正文", "page": 1}], page_count)
        warn("no outline or TOC found - the whole book became one chapter; "
             "pass --chapters headings.json to define sections")

    book = assemble(pages, chapters, body, repair=not args.no_repair)

    write_json(p["book"], {
        "meta": {
            "pdf": os.path.abspath(args.pdf),
            "title": args.title or rep.get("title") or "",
            "author": args.author or rep.get("author") or "",
            "language": args.lang,
            "pages": page_count,
            "body_font_size": body,
            "text_source": args.text_source,
            "mode": rep.get("mode"),
        },
        "chapters": [c.to_dict() for c in book.chapters],
        "stats": book.stats,
    })
    write_json(p["toc_json"], toc_json(book.chapters))
    with open(p["toc_md"], "w", encoding="utf-8") as fh:
        fh.write(toc_markdown(book.chapters))
    write_json(os.path.join(out, "heading_candidates.json"), book.heading_candidates)

    log(f"  paragraphs: {book.stats['paragraphs']}  "
        f"footnote blocks: {book.stats['footnote_blocks']}  "
        f"heading candidates: {len(book.heading_candidates)}")

    # Flag sections built from stylised pages (covers, blurbs, plates): those
    # pages defeat OCR, so their text deserves a proofread. Front matter is
    # excluded because it is normally dropped from the spine anyway.
    garbled = set(rep.get("garbled_pages") or [])
    flagged = []
    if garbled:
        for c in book.chapters:
            if c.front_matter:
                continue
            hit = [p for p in range(c.start_page, c.end_page + 1) if p in garbled]
            if hit:
                flagged.append(f"{c.id} {c.title!r} (p{hit[0]}–{hit[-1]})")
    if flagged:
        step("note: these sections draw on pages the text layer read poorly, "
             "so proofread them or drop them via --chapters: " + "; ".join(flagged))
    return read_json(p["book"])


def stage_build(args, book: dict | None = None) -> str:
    from p2e.epub import build_epub, validate_epub
    from p2e.structure import Chapter
    from p2e.clean import Block
    out = args.out
    p = _paths(out)
    if book is None:
        book = read_json(p["book"])

    chapters = []
    for c in book["chapters"]:
        chapters.append(Chapter(
            id=c["id"], title=c["title"], level=c["level"],
            start_page=c["start_page"], end_page=c["end_page"],
            front_matter=c.get("front_matter", False),
            blocks=[Block(kind=b["kind"], text=b["text"], pages=b.get("pages", []))
                    for b in c["blocks"]],
        ))

    meta = book["meta"]
    title = args.title or meta.get("title") or "Untitled"
    author = args.author or meta.get("author") or ""
    slug = slugify(title)
    epub_path = os.path.join(out, f"{slug}.epub")

    ensure_cover(args)
    cover = p["cover"] if os.path.isfile(p["cover"]) else None
    step(f"building EPUB: {title!r} by {author!r}, {len(chapters)} sections, "
         f"cover={'yes' if cover else 'no'}")
    build_epub(epub_path, chapters, title=title, author=author, lang=args.lang,
               publisher=args.publisher, date=args.date,
               source=meta.get("pdf", ""), cover_path=cover,
               include_front_matter=args.include_front_matter)

    info = validate_epub(epub_path)
    write_json(os.path.join(out, "epub_check.json"), info)
    log(f"  check: {info['entries']} entries, {info.get('text_chars', 0)} chars, "
        f"{'cover ok' if info.get('cover') else 'no cover'}")
    if info["issues"]:
        for i in info["issues"]:
            warn(f"EPUB issue: {i}")
    else:
        log("  check: no structural issues found")
    return epub_path


# --------------------------------------------------------------------------
# command wiring
# --------------------------------------------------------------------------

def cmd_analyze(args):
    stage_analyze(args)
    if not args.no_cover:
        stage_cover(args)
    return 0


def cmd_extract(args):
    from p2e.extract import export_page_images, open_doc
    doc = open_doc(args.pdf)
    if args.pages:
        pages = _parse_pages(args.pages, doc.page_count)
    else:
        pages = [1]
    paths = export_page_images(doc, os.path.join(args.out, "pages"), pages, dpi=args.dpi)
    step(f"wrote {len(paths)} page images to {os.path.join(args.out, 'pages')}")
    return 0


def cmd_ocr(args):
    stage_ocr(args)
    return 0


def cmd_assemble(args):
    stage_assemble(args)
    return 0


def cmd_build(args):
    stage_build(args)
    return 0


def cmd_all(args):
    t0 = time.time()
    rep = stage_analyze(args)
    stage_cover(args, rep)
    if args.skip_ocr:
        step("skipping OCR (--skip-ocr)")
    else:
        stage_ocr(args, rep)
    stage_assemble(args, rep)
    path = stage_build(args)
    log(f"[p2e] done in {time.time() - t0:.0f}s -> {path}")
    return 0


def cmd_validate(args):
    from p2e.epub import validate_epub
    info = validate_epub(args.epub)
    import json
    log(json.dumps(info, ensure_ascii=False, indent=2))
    return 1 if info["issues"] else 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="pdf2epub",
        description="Convert a scanned PDF book into an EPUB 3 (cover + TOC preserved).")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--pdf", required=True, help="input PDF path")
        p.add_argument("--out", required=True, help="output working directory")
        p.add_argument("--title", default="", help="override book title")
        p.add_argument("--author", default="", help="override book author")
        p.add_argument("--lang", default="zh", help="BCP-47 language tag (default zh)")
        p.add_argument("--publisher", default="", help="dc:publisher")
        p.add_argument("--date", default="", help="dc:date")
        p.add_argument("--chapters", default="",
                       help="JSON file: [{\"title\":..,\"page\":..,\"level\":1}]")
        p.add_argument("--text-source", default="auto",
                       choices=["auto", "embedded", "ocr"],
                       help="where page text comes from (default auto)")
        p.add_argument("--no-repair", action="store_true",
                       help="disable OCR punctuation repair")
        p.add_argument("--include-front-matter", action="store_true",
                       help="keep cover/title/copyright/TOC pages in the spine")

    parsers: dict[str, argparse.ArgumentParser] = {}
    for name, fn, helptext in (
        ("analyze", cmd_analyze, "probe the PDF and extract the cover"),
        ("extract", cmd_extract, "render selected pages to PNG"),
        ("ocr", cmd_ocr, "run/caches OCR for pages that need it"),
        ("assemble", cmd_assemble, "clean text and build chapter structure"),
        ("build", cmd_build, "write the EPUB from book.json"),
        ("all", cmd_all, "analyze + cover + ocr + assemble + build"),
    ):
        sp = sub.add_parser(name, help=helptext)
        common(sp)
        sp.set_defaults(func=fn)
        parsers[name] = sp

    # extra options that only make sense for the stages that use them
    for name in ("analyze", "all"):
        parsers[name].add_argument("--cover-page", type=int, default=0,
                                   help="page number to use as the cover (1-based)")
    parsers["analyze"].add_argument("--no-cover", action="store_true")

    parsers["extract"].add_argument("--pages", default="1",
                                    help="page range, e.g. '1-5,10,20-'")
    parsers["extract"].add_argument("--dpi", type=int, default=150)

    for name in ("ocr", "all"):
        parsers[name].add_argument("--pages", default="",
                                   help="limit OCR to this page range")
        parsers[name].add_argument("--force-ocr", action="store_true",
                                   help="OCR every page, including image-only ones")
        parsers[name].add_argument("--embedded-only", action="store_true",
                                   help="never re-OCR pages that already have a "
                                        "usable text layer")
        parsers[name].add_argument("--redo", action="store_true",
                                   help="ignore the OCR cache and redo the pages")
        parsers[name].add_argument("--workers", type=int, default=0,
                                   help="OCR worker processes (default: auto)")
        parsers[name].add_argument("--ocr-height", type=int, default=2200,
                                   help="render height in px for OCR (default 2200)")

    parsers["all"].add_argument("--skip-ocr", action="store_true",
                                help="never run OCR; use the embedded text layer only")

    vp = sub.add_parser("validate", help="check an EPUB's structure")
    vp.add_argument("--epub", required=True)
    vp.set_defaults(func=cmd_validate)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if getattr(args, "workers", 0) == 0:
        args.workers = None
    if not hasattr(args, "force_ocr"):
        args.force_ocr = False
    if not hasattr(args, "embedded_only"):
        args.embedded_only = False
    if not hasattr(args, "redo"):
        args.redo = False
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
