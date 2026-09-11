#!/usr/bin/env python3
"""Compare the embedded PDF text layer against a fresh OCR pass, page by page.

Use this before choosing ``--text-source``: it shows exactly which characters
each source gets right, so the decision rests on evidence rather than on the
age of the scan.

    python compare_sources.py --pdf book.pdf --out OUT --pages 10,11,165
    python compare_sources.py --pdf book.pdf --out OUT --pages 10 --height 2600
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p2e.util import PageText, read_json, setup_console  # noqa: E402

setup_console()


def load_embedded(out: str) -> dict[int, PageText]:
    path = os.path.join(out, "pages_embedded.json")
    if not os.path.isfile(path):
        return {}
    return {int(k): PageText.from_dict(v) for k, v in read_json(path).items()}


def load_ocr(out: str) -> dict[int, PageText]:
    path = os.path.join(out, "ocr.json")
    if not os.path.isfile(path):
        return {}
    return {int(k): PageText.from_dict(v) for k, v in read_json(path).items()}


def parse_pages(spec: str, n: int) -> list[int]:
    out: list[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, _, b = chunk.partition("-")
            out.extend(range(int(a) if a.strip() else 1, (int(b) if b.strip() else n) + 1))
        else:
            out.append(int(chunk))
    return sorted(set(out))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--out", required=True, help="working dir holding the caches")
    ap.add_argument("--pages", required=True, help="e.g. 10,11,165-170")
    ap.add_argument("--height", type=int, default=2200)
    ap.add_argument("--fresh", action="store_true",
                    help="re-OCR the pages even when ocr.json already has them")
    args = ap.parse_args()

    embedded = load_embedded(args.out)
    ocr = load_ocr(args.out)

    import pymupdf
    doc = pymupdf.open(args.pdf)
    pages = parse_pages(args.pages, doc.page_count)

    missing = [p for p in pages if args.fresh or p not in ocr]
    if missing:
        from p2e import ocr as ocr_mod
        print(f"OCR: {len(missing)} page(s) at height {args.height} ...")
        for p in missing:
            if p < 1 or p > doc.page_count:
                continue
            ocr[p] = ocr_mod.ocr_page(doc[p - 1], args.height)

    for p in pages:
        emb = embedded.get(p)
        oc = ocr.get(p)
        print(f"\n{'=' * 78}\n### page {p}\n{'=' * 78}")
        print("--- embedded text layer ---")
        if emb:
            for l in emb.lines:
                print(f"  y={l.y0:6.0f} | {l.text}")
        else:
            print("  (none)")
        print("--- fresh OCR ---")
        if oc:
            for l in oc.lines:
                print(f"  y={l.y0:6.0f} | {l.text}")
        else:
            print("  (none)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
