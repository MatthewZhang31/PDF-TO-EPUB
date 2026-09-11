---
name: pdf-to-epub
description: "Convert a scanned or image-based PDF book into a valid EPUB 3 that keeps the original cover image and a real table of contents. Use when someone supplies a PDF book (scanned, photocopied, or a PDF whose text layer is missing or garbled) and wants an EPUB, MOBI-ready e-book, or clean structured text out of it. Covers text-layer extraction, RapidOCR fallback, headline/footnote/paragraph reconstruction, cover extraction, TOC recovery from the PDF outline or the printed contents page, and EPUB packaging with validation."
whenToUse: "Use for any 'make this PDF into an EPUB / e-book' request, especially Chinese or Japanese scanned books, and for extracting clean chapter-structured text plus cover and TOC from a PDF. Do not use for reflowing a born-digital PDF that already has a perfect text layer and outline if the user only wants light editing - but do use it when the text layer is noisy, which is the common case for ABBYY-scanned Chinese books."
---

# PDF (scanned book) → EPUB

A staged, resumable pipeline that turns a scanned PDF book into an EPUB 3 with the
**original cover** and a **real, navigable table of contents**.

## Where everything lives

| Item | Path |
|---|---|
| Toolkit entry point | `C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\pdf2epub.py` |
| Python package | `C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\p2e\` |
| Self-test (no PDF needed) | `C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\selftest.py` |
| Source-comparison diagnostic | `C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\compare_sources.py` |
| Requirements | `C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\requirements.txt` |
| Reference docs | `C:\Users\41489\.dsh\skills\pdf-to-epub\references\` |
| Default work area | `D:\deepseek workingspace\pdf2epub\out\<book-slug>\` |

Always call the script by its **absolute path**; do not assume the session cwd.

**After changing any code in `p2e\`, run the self-test before trusting a conversion:**

```powershell
python "C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\selftest.py"
```

It covers line ordering, paragraph joining, junk filtering, punctuation repair,
marker transfer, nested nav markup, and a full EPUB round-trip in which every
document is parsed as XML. It needs no PDF and no network.

## Check the plugin market before writing anything new

A scan of the 3,408-entry registry at `https://awesome-dsh-plugin.com/plugins.json`
(plus the awesome-dsh-plugin list) found **no plugin that produces an EPUB**. The
PDF-related plugins that exist are *readers* for the model, not converters:

| Plugin | What it does | Why it is not a substitute |
|---|---|---|
| [`dsh-pdf-mineru`](https://github.com/Yurzi/dsh-pdf-mineru), [`dsh-mineru`](https://github.com/Lee-Hilex/dsh-mineru) | MinerU layout analysis → Markdown; strong on two-column, tables, formulas; has an `ocr` mode for scans | needs a MinerU cloud API key (uploads the book) or a self-hosted GPU service; 200 MB / 200 page cloud cap; emits Markdown for the model, with no cover, no EPUB, no nav |
| `dsh-document`, `dsh-anydoc`, `dsh-plugin-anydoc` | read Word/PPT/Excel/PDF/EPUB as Markdown | input-side readers only |
| `dsh-pdf`, `dsh-pdf-reader`, `dsh-pdf-edit`, `dsh-pdf-translate` | inspect, split, edit, translate PDFs | no reflow into an e-book |
| `dsh-tesseract-ocr`, `dsh-windows-ocr`, `dsh-ocr-review` | OCR helpers | no structure recovery, no packaging |

`dsh-pdf-mineru` is worth reconsidering for a book with genuinely hard layout
(tables, formulas, multi-column) — its Markdown could be fed into
`p2e/epub.py` instead of our own text stage. For ordinary scanned prose books the
local pipeline here is faster, offline, and already produces the cover and nav.

Re-run the market check with:

```powershell
python -c "import json,urllib.request;d=json.load(urllib.request.urlopen('https://awesome-dsh-plugin.com/plugins.json',timeout=90));items=d if isinstance(d,list) else d.get('plugins',d);print(len(items));print([p.get('name') for p in items if 'epub' in json.dumps(p,ensure_ascii=False).lower()])"
```

## Environment check (do this first, once per machine)

```powershell
python -c "import pymupdf, PIL; print('ok')"
python -c "from rapidocr_onnxruntime import RapidOCR; print('ocr ok')"
```

If either fails:

```powershell
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pymupdf pillow rapidocr-onnxruntime onnxruntime
```

Never run `pip install` without a mirror on this machine - the default index times out.

## The one command that does everything

```powershell
$env:PYTHONIOENCODING="utf-8"
python "C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\pdf2epub.py" all `
  --pdf "<absolute path to the PDF>" `
  --out "D:\deepseek workingspace\pdf2epub\out\<slug>"
```

`all` = analyse → cover → OCR (only if needed) → assemble → build → validate.

It prints a summary and writes `epub_check.json`. Read that summary before
reporting success, and always inspect the EPUB artefacts listed below.

### Outputs you must show the user

| File | Meaning |
|---|---|
| `<book-slug>.epub` | the finished e-book |
| `cover.jpg` | the cover extracted from the scan |
| `toc.json`, `toc.md` | the recovered table of contents |
| `analysis.json` | what the tool found in the PDF (mode, outline, gaps) |
| `heading_candidates.json` | possible sub-headings the text layer could not confirm |
| `epub_check.json` | structural validation of the produced EPUB |

## Decision flow

1. **`analyze` decides the mode.** Never guess it by hand:
   - `text-layer` – the PDF already carries usable OCR text (very common for
     ABBYY-scanned Chinese books).
   - `garbled-layer` – a text layer exists but is mostly noise; every page is OCR'd.
   - `scanned` – essentially no text layer; every page is OCR'd.
2. **A fresh OCR pass still beats an old text layer.** ABBYY-era layers are
   character-accurate but punctuation-hostile (`。`→`J`, `：`→`厂`, `！`→`1`).
   So `all` OCRs every page that carries text, and `--text-source auto` prefers
   the OCR result, falling back to the embedded layer page by page. Footnote
   markers (`①②③`) and list bullets (`◎`) that the OCR pass drops are carried
   over from the embedded layer by aligning the two lines
   (`clean.transfer_markers`). Use `--embedded-only` for a fast, OCR-free run.
3. **Cover** comes from `analysis.json:cover_page` (page 1 whenever page 1 is a
   full-page image). Override with `--cover-page N` when the first page is a
   library stamp, a barcode sheet, or a blank.
4. **TOC** comes from the PDF's embedded outline when one exists, because it
   matches the printed contents page. When there is no outline, the printed
   contents page is OCR'd and each row's *printed page number* is mapped to a
   physical page through the folio offset — far more reliable than searching
   the body for each title. See "Books with no PDF outline" below.
5. **Chapter text** is the concatenation of the pages each section spans. Rows
   that share a page become **nav-only** entries pointing at the chapter that
   owns the page, so no page's text is ever emitted twice.

## Books with no PDF outline

Many scans (anything run through a plain scanner rather than a publishing
pipeline) have no bookmarks at all. The recovered TOC then rests on two derived
facts, both printed in `analysis.json` / the run log:

1. **The printed contents page** — `detect_toc_pages` searches only the front
   region of the book, because body pages full of numbered lists otherwise score
   just as highly. It then follows the heading through contiguous pages only.
2. **The folio offset** — `detect_folios` reads the lone number in each page's
   margin, and `estimate_page_offset` takes the most common
   `physical − printed` difference. A contents row's printed number plus that
   offset gives its physical page.

The offset is validated against the whole contents list before being trusted
(`offset_is_usable`): if too few rows land on pages that actually carry their
title, the offset is discarded and each title is searched for in the body
instead. The run log states which happened:

```
printed folios on 273 pages -> page offset +10; 89 TOC rows, 3 indent level(s)
```

### When to transcribe the contents by hand

Automatic extraction is good but not perfect: OCR mangles headings, and a
running head that the scanner merged onto the same line as a title will be glued
onto it. **Read `toc.json` and judge.** If chapter-level entries are wrong or
missing, render the contents pages, read them yourself, and supply
`--chapters`:

```powershell
python "...\scripts\pdf2epub.py" extract --pdf X.pdf --out OUT --pages 7-10 --dpi 220
# read OUT\pages\page_0007.png … page_0010.png
python "...\scripts\pdf2epub.py" all --pdf X.pdf --out OUT --chapters chapters.json
```

The supplementary script that produced `chapters.json` for the reference book
keeps the transcription in a compact `"<level> <printed page> <title>"` block
and applies the offset in code — reuse that shape and let the generator reject
non-monotonic input.

`--chapters` rows are **physical** pages, `level` 1–3 nests the nav, and rows
sharing a page are de-duplicated automatically.

### Working with the extracted contents

```powershell
python "...\scripts\pdf2epub.py" validate --epub OUT\book.epub
```

`epub_check.json` reports `internal_refs` (every link/image/CDATA reference
resolved), `xml_documents`, and any issue. Nav-only rows make the nav deeper
than the spine, which is expected: `chapters` (manifest count) is larger than
`spine_items`.

## Stage caches and stale artefacts

Every stage writes JSON into `--out` and reuses it on the next run:

| File | Written by | Refresh rule |
|---|---|---|
| `analysis.json` | `analyze` | rewritten on every `analyze` / `all` |
| `pages_embedded.json` | `analyze` | written once, then reused |
| `ocr.json` | `ocr` | per-page cache; `--redo` forces a re-read |
| `cover.json` | `cover` | records the page and PDF the cover came from |
| `book.json` | `assemble` | rewritten on every `assemble` |

**Re-running one stage reuses the others' cached output.** That is the point, but
it also means a fix to `analyze` will not take effect in an existing work
directory until `analyze` runs again. When something looks unchanged after a
code change, re-run `all` (or delete the relevant JSON) rather than a single
stage. `build` also re-extracts the cover whenever `cover.json` disagrees with
`analysis.json`, so a stale title-page cover cannot survive a rebuild.

## Quality review you are expected to perform

The pipeline is deterministic; the *judgement* calls are yours. After a run:

- Read `analysis.json`: if `garble_ratio` is high but `mode` is `text-layer`,
  re-run with `--force-ocr --text-source ocr`.
- Read `toc.json`: fix obviously wrong titles by passing `--chapters fixed.json`
  (`[{"title": "...", "page": <PDF page>, "level": 1}, ...]`) and rebuilding.
  This is also how you add sub-sections.
- Read `heading_candidates.json`: if the book has real sub-headings (joke titles,
  named sections), promote the right ones into `--chapters` at `level: 2`.
- Spot-check the first and last paragraph of the largest chapter in `book.json`.
  Watch for scrambled lines, leftover page numbers, and merged footnotes.

Report the residual OCR error rate honestly; do not claim a perfect transcription
of a scan.

## Stage-by-stage use (when one command is not enough)

```powershell
$S = "C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\pdf2epub.py"
python $S analyze  --pdf X.pdf --out OUT
python $S extract  --pdf X.pdf --out OUT --pages 1,5,10 --dpi 150   # PNG previews
python $S ocr      --pdf X.pdf --out OUT --force-ocr --workers 4
python $S assemble --pdf X.pdf --out OUT --text-source auto
python $S build    --pdf X.pdf --out OUT --title "..." --author "..."
python $S validate --epub OUT\book.epub
```

Stages communicate only through JSON in `--out`, so any stage can be re-run
alone. `ocr.json` is a cache: pages already done are skipped unless `--redo`.

## Important flags

| Flag | Effect |
|---|---|
| `--text-source auto\|embedded\|ocr` | per-page text source; `auto` prefers OCR where it exists (default) |
| `--force-ocr` | OCR every page, including image-only plates and blanks |
| `--embedded-only` | never re-OCR a page that already has a usable text layer (fast path) |
| `--skip-ocr` | `all` only: never run OCR at all |
| `--workers N` | OCR processes; default auto (min(4, cpu/2)) |
| `--ocr-height N` | render height for OCR, default 2200 px; raise to 3000 for very small type |
| `--chapters FILE` | supply/fix the TOC and section boundaries by hand |
| `--no-repair` | disable the OCR punctuation repair rules |
| `--include-front-matter` | keep cover/copyright/printed-TOC pages in the spine |
| `--title`, `--author`, `--lang`, `--publisher`, `--date` | override metadata |

## Choosing a text source with evidence, not guesswork

```powershell
python "C:\Users\41489\.dsh\skills\pdf-to-epub\scripts\compare_sources.py" `
  --pdf X.pdf --out OUT --pages 10,11,165-170
```

It prints the embedded layer and a fresh OCR pass side by side for the same
pages. On the reference book, page 154 is decisive:

```
embedded : "没有J      "我也没有厂
OCR      : "没有。"     "我也没有。"
```

Use `--fresh` to re-OCR at a different `--height` before committing to a
book-wide run.

## Rules that keep this reliable

- **Long-running OCR must be a background job.** A 200-page book takes ~1 s/page
  with 4 workers. Never block a turn on it; start it in the background and keep
  working.
- **Never put unguarded top-level code in a script that triggers the OCR pool.**
  On Windows `multiprocessing` uses *spawn* and re-imports the entry module; an
  unguarded `__main__` recurses and spawns processes until the machine chokes.
  Call the pool only through `pdf2epub.py` (which is guarded) or from inside
  `if __name__ == "__main__":`.
- **Keep `PYTHONIOENCODING=utf-8`** in every shell call; the console is cp936 and
  CJK output crashes otherwise.
- Diagnose before re-OCR'ing: `--skip-ocr` on a `text-layer` book finishes in
  about a minute and often already produces a good EPUB.

## Failure modes seen in practice

| Symptom | Cause | Fix |
|---|---|---|
| `ModuleNotFoundError: p2e` | script invoked with a wrong cwd/sys.path | use the absolute path to `pdf2epub.py` |
| cover is a title page or barcode | page-image heuristic picked a later full-page image | `--cover-page 1` |
| TOC empty | no outline and the printed TOC page was not detected | supply `--chapters` |
| body text stops at a page | that page was classified as garbled | `--force-ocr`, or `--pages a-b` for the gap |
| words run together | paragraph join used the wrong indentation base | re-run `assemble`; the global margins should fix it |
| footnote text glued to body | footnote block too close to the body font size | lower the footnote ratio threshold in `clean.py` (`split_footnotes`) |
| `RuntimeError: An attempt has been made to start a new process…` | unguarded `__main__` with the OCR pool | guard the entry point, or pass `--workers 1` |

## Verified reference run

《齐泽克的笑话》 (Henan University Press, 2016; 177-page ABBYY scan, Simplified
Chinese) was converted end to end as the acceptance test:

| Step | Command | Result |
|---|---|---|
| analyse + cover | `all --skip-ocr` | mode `text-layer`, 173/177 pages with text, garble 1.2 % (pages 2–3 only), cover = page 1 |
| OCR | `ocr --force-ocr --workers 4` | 177 pages in ~4 min (~1.3 s/page) |
| assemble + build | `all` (OCR cached, 41 s) | 8 spine sections, 533 paragraphs, 75 footnote blocks, 62 456 chars |
| validate | `build` | 16 zip entries, 13 XML documents all well-formed, mimetype stored first, all manifest hrefs present, cover present, **0 issues** |
| self-test | `selftest.py` | all checks passed |

Structure recovered from the PDF outline matched the printed TOC page exactly:
代序 / 齐泽克的笑话 / 编后记 / 著作年谱 / 关于作者及编者 / 注释 / 译后记 / 附录.
Cover, `toc.json` and `toc.md` are all separate deliverables, not just embedded
in the EPUB.

Residual defects (character-level OCR slips that no rule can fix safely):
`三个自人`→`三个白人`, `便于后人`→`便于后入`, `陷人`→`陷入`. Report this class
of error to the user; do not silently "correct" it with a dictionary.

## Extending

- OCR engine: `p2e/ocr.py` is the only place that imports RapidOCR. Swapping in
  PaddleOCR, Tesseract, or a cloud API means reimplementing `ocr_page()`.
- Punctuation repair: `REPAIR_RULES` in `p2e/clean.py`. Each entry is
  `(regex, replacement, description)`; keep rules context-anchored and re-run
  `assemble` to see the effect. `repair_report()` counts which rules fired.
- House style: `DEFAULT_CSS` in `p2e/epub.py`, or pass `css=` to `build_epub`.
