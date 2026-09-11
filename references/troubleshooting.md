# Troubleshooting

## Environment

**`UnicodeEncodeError: 'gbk' codec can't encode character`**
The Windows console is cp936. Set `$env:PYTHONIOENCODING="utf-8"` before every
`python` call. `pdf2epub.py` also reconfigures its own streams, but child
processes and tracebacks still need the variable.

**`ModuleNotFoundError: No module named 'p2e'`**
`pdf2epub.py` inserts its own directory into `sys.path`; importing `p2e`
directly only works when `...\scripts` is on `sys.path`. Always invoke the
script by absolute path, or set `sys.path` explicitly in your driver.

**`pip install` hangs and then times out**
The default index is unreachable. Always use
`-i https://pypi.tuna.tsinghua.edu.cn/simple`.

**`rapidocr-onnxruntime` missing**
Conversion still works for PDFs with a usable text layer. `analyze` reports the
mode, `ocr` warns and returns an empty cache, and `assemble` falls back to the
embedded text. Install the package only when the PDF has no text layer.

## OCR

**Dozens of identical `OCR N pages with M workers` lines, then `RuntimeError:
An attempt has been made to start a new process…`**
Spawn recursion from an unguarded `__main__`. Kill the stray python processes:

```powershell
Get-Process python -ErrorAction SilentlyContinue | Stop-Process -Force
```

then re-run through `pdf2epub.py`, or pass `--workers 1`.

**OCR takes far longer than expected**
Check whether another heavy job is running — the pipeline is CPU-bound and the
worker count is `min(4, cpu/2)` by default. Reduce `--ocr-height` (2200 → 1600)
if you only need body text and not the footnotes.

## Structure

**The EPUB is one enormous chapter**
No outline and no detected printed TOC. Look at `analysis.json:toc_pages`; if the
contents page is a garbled scan it may be missed. Supply `--chapters`:

```json
[
  {"title": "序", "page": 6, "level": 1},
  {"title": "第一章", "page": 12, "level": 1},
  {"title": "第一节", "page": 15, "level": 2}
]
```

`page` is the **physical PDF page**, not the printed folio. Convert with
`pdf_page = printed_page + offset`, where `offset` is the difference between the
PDF page an outline entry points at and its printed number.

**Chapters start one page late or early**
Outline bookmarks in scans are frequently off by one. Compare
`analysis.json:outline[].page` against the page whose text contains the heading;
the `locate_title_page()` fallback does exactly this and is used when there is no
outline. Fix by hand with `--chapters`.

**Front matter appears in the reading order**
Pass nothing (default) to exclude 封面/书名/版权/目录, or
`--include-front-matter` to keep it.

## Text

**Two fragments of one line are swapped**
`util.order_lines()` clusters fragments by vertical overlap before sorting by x.
If a page still scrambles, its fragments differ in height by more than 60 %; the
row tolerance in `order_lines` is the knob.

**A paragraph ends mid-sentence every page**
The page's first line is being read as indented. This happens when a page carries
only a few lines and `global_margins()` fell back to per-page margins. Lower
`min_lines` in `global_margins()`.

**Page numbers or running heads survive**
They repeat on fewer than three pages, so the recurrence test does not fire. Add
them to a drop list, or narrow `strip_running_heads` by raising the margin band
factor.

**Footnotes appear in the middle of the body**
`split_footnotes()` treats type smaller than 85 % of `body_font_size` in the lower
half of the page as notes. If a book sets notes at 90 %, raise that ratio. If a
book has no notes at all the block is empty and nothing changes.

**Quotation marks are wrong (`“` for `”`, `「` for `：`)**
Add a context-anchored rule to `REPAIR_RULES` and verify with
`repair_report()`. Do not add a blanket character substitution.

## EPUB

**`validate` reports `manifest href missing from archive`**
A build was interrupted. Delete the `.epub` and re-run `build`; the writer only
renames the temporary file after the archive is complete.

**The cover does not show in a reader**
EPUB 3 cover display needs the `properties="cover-image"` manifest item (written
automatically) and, for older readers, the `<meta name="cover">` entry (also
written automatically). If a specific reader still ignores it, confirm the
`cover.xhtml` page is first in the spine — it is, unless
`--include-front-matter` reordered sections.

**The reader shows no table of contents**
`nav.xhtml` carries the EPUB 3 nav and `toc.ncx` the EPUB 2 fallback. Check that
`OEBPS/nav.xhtml` exists and that `<spine toc="ncx">` is present in
`content.opf`; `validate` flags either omission.

**Text renders in a fallback font**
`DEFAULT_CSS` in `p2e/epub.py` prefers Noto Serif CJK / Source Han Serif / Songti
/ SimSun. Readers substitute if none are installed; the book still renders, but
you can ship a font with `css=` if the appearance matters.
