# The staged workflow in detail

## Why stages

A 300-page scan takes minutes of OCR and produces dozens of intermediate
decisions. Doing it in one shot means a failure at the end throws everything
away, and it gives the operator no place to intervene. Every stage here writes a
JSON artefact into `--out` and reads only the artefacts it needs, so a run is
resumable, inspectable, and diffable.

```
PDF ──analyze──► analysis.json ─┐
    └─extract──► pages_embedded.json
    └─ocr──────► ocr.json ──────┤
                                 ├─assemble──► book.json ─► toc.json/toc.md
                                 │                └─► heading_candidates.json
                                 └─cover─────► cover.jpg
                                                    │
                              book.json + cover ────┴─build──► <book>.epub ─validate─► epub_check.json
```

## Stage 1 - analyse

`analyze()` opens the PDF and reports:

- page count, page size, metadata (`title`, `author`, producer)
- per page: character count, line count, image coverage, `garbled` flag, CJK ratio
- `body_font_size` – median size over long lines; the anchor for everything else
- `mode`:
  - `text-layer` – ≥20 % of pages have text and <35 % of those look garbled
  - `garbled-layer` – text exists but most of it is noise
  - `scanned` – almost no text at all
- `outline` – the PDF bookmark tree, normalised to `{level, title, page}`
- `cover_page` – first page carrying a near-full-page raster image
- `toc_pages` – pages that look like a printed contents page

The `garbled` test (`util.looks_garbled`) counts rare CJK radicals, symbols and
Latin/digit runs. It is tuned for ABBYY output of stylised pages (covers, title
pages) whose glyph shapes defeat the OCR.

## Stage 2 - cover

The cover is *rendered* from the page rather than pulled from an embedded image
stream: scanned covers are frequently stored as several strips or as a
JBIG2/CCITT stream that is awkward to re-encode. Rendering at 200 dpi and
resizing to 1600 px wide yields a clean, small JPEG that every e-reader accepts.

`--cover-page N` overrides the automatic choice.

## Stage 3 - text

Two sources, selected per page:

| Source | Strength | Weakness |
|---|---|---|
| embedded text layer | exact line geometry, keeps footnote markers and page numbers, instant | punctuation is systematically mis-mapped by older OCR engines (`。`→`J`, `：`→`厂`, `！`→`1`) |
| RapidOCR (PP-OCR ONNX) | markedly better punctuation and mixed Latin/CJK accuracy | loses superscript footnote markers and page numbers; ~1 s/page |

`--text-source auto` keeps the embedded layer unless a page produced almost
nothing, and `--force-ocr` overrides it. When punctuation quality matters more
than speed, run `--text-source ocr` and accept the loss of footnote markers.

## Stage 4 - clean and assemble

For every page:

1. **Running heads and folios are removed.** A line is dropped when it sits in
   the top/bottom margin band and either is a bare number or repeats (as a
   digit-normalised key) on three or more pages.
2. **Footnotes are separated.** The block below the last body-sized line, set in
   type smaller than 85 % of `body_font_size`, becomes footnote content, then is
   split again on `①`–`⑳` so each note becomes its own paragraph.
3. **Lines are reordered.** Fragments sharing a row (a dialogue dash, a tab
   stop) are clustered by vertical overlap and then ordered by x.
4. **Lines are joined into paragraphs.** A new paragraph begins at a line
   indented past the continuation offset, after a short sentence-final line, at a
   bullet (`◎`, `●`, `•`…), or at a centred display line.
5. **OCR noise is dropped** (`util.is_junk`).
6. **Punctuation repair** runs (`clean.REPAIR_RULES`) – see `ocr-and-quality.md`.
7. **A leading block that repeats the chapter title is removed.**

`global_margins()` computes the book-wide left and right text edges. Sparse pages
(a part title, a single centred line) cannot supply their own margins, and
without the global anchor their centred lines are never recognised as centred.

## Stage 5 - structure

Chapter skeletons come from the first source that yields any:

1. `--chapters file.json` (explicit, always wins)
2. the PDF outline
3. the printed TOC pages, with each title located by searching the body text
4. a single chapter spanning the whole book (with a warning)

Outline titles matching `FRONT_MATTER` (封面/书名/版权/目录/cover/copyright/…) are
tagged `front_matter` and excluded from the spine unless
`--include-front-matter` is passed. Their images are still used for the cover.

## Stage 6 - build and validate

`build_epub()` writes a **stored** (uncompressed) `mimetype` first, then
`META-INF/container.xml`, `OEBPS/content.opf`, an EPUB 3 `nav.xhtml`, an EPUB 2
`toc.ncx` for older readers, the CSS, an optional `cover.xhtml`, `cover.jpg`, and
one XHTML file per section.

`validate_epub()` then checks: entry order, `mimetype` storage and content,
required files, zip integrity, every manifest href present in the archive, and
cover presence. Any problem is written to `epub_check.json` and echoed as a
warning; the process exits non-zero only for `validate` itself.
