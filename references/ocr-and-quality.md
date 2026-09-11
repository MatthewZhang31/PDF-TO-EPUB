# OCR engines, quality gates, and the repair rules

## Choosing a text source

Run `analyze` first and read three numbers:

| Field | Meaning | Read it as |
|---|---|---|
| `text_ratio` | share of pages with ≥30 characters of embedded text | `< 0.2` → a pure scan, OCR everything |
| `garble_ratio` | share of those pages that look like noise | `> 0.35` → the layer is worthless, OCR everything |
| `mode` | the conclusion drawn from the two above | `text-layer` → try the fast path first |

`mode: text-layer` does **not** mean the text is correct. ABBYY-era layers are
character-accurate but punctuation-hostile. Compare a page before deciding:

```powershell
python "...\scripts\pdf2epub.py" ocr --pdf X.pdf --out OUT --pages 10 --force-ocr
python "...\scripts\pdf2epub.py" assemble --pdf X.pdf --out OUT --text-source embedded
# then look at book.json for page 10, and again with --text-source ocr
```

### What the difference looks like in practice

From a 2016 Henan University Press scan (ABBYY FineReader 14 layer):

| Embedded layer | RapidOCR |
|---|---|
| `黄段子（萨德②收集人在这个色` | `黄段子（萨德收集）。在这个色` |
| `之一.由拉康T 1945年提` | `之一。由拉康于 1945年提` |
| `产物*逻辑时间是相对穩定的共时结` | `产物。逻辑时间是相对稳定的共时结` |
| `MmqiiisdeSade* 1740—18⑷'` | `Marquis de Sade，174()一1814）` |
| keeps `①` `②` footnote markers | **loses** all footnote markers |
| keeps folio numbers | **loses** folio numbers |

Rule of thumb: **punctuation and mixed-script accuracy → OCR; structure
(footnote markers, folios, geometry) → embedded layer.** `auto` picks the
embedded layer and leans on the repair rules below.

## RapidOCR specifics

- Package: `rapidocr-onnxruntime` (PP-OCR models, ONNX Runtime, CPU, offline
  after install). No compiler, no CUDA, no model download at runtime.
- Rendering: pages are rasterised to **greyscale** with a target height of
  2200 px (`--ocr-height`). The scanned source is usually ~6300 px tall; 2200 is
  enough for 14 pt footnotes and roughly 3× faster than native resolution. Raise
  it to 3000 for very small type, and expect the runtime to grow accordingly.
- Throughput on a 16-core machine: ~4 s/page in one process, ~1 s/page with 4
  workers. A 300-page book is 5–10 minutes; always run it as a background job.
- Bounding boxes are scaled back into PDF points so OCR and embedded lines are
  interchangeable downstream.
- Footnote markers: RapidOCR drops standalone superscripts. If a book depends on
  numbered notes, prefer the embedded layer, or post-process `book.json`.

## Windows multiprocessing hazard

`multiprocessing` on Windows uses the **spawn** start method: every worker
re-imports the module that was launched as `__main__`. If that module has
top-level side effects (running the pool), each worker starts another pool and
the machine fills with processes. Symptoms: dozens of identical log lines, then
`RuntimeError: An attempt has been made to start a new process before the current
process has finished its bootstrapping phase`.

Always call the pool through `pdf2epub.py`, whose entry point is guarded by
`if __name__ == "__main__":`. `ocr_pages_parallel()` also catches the failure and
falls back to serial processing, but the fallback is slow — fix the caller.

## The repair rules

`clean.REPAIR_RULES` is a list of `(regex, replacement, description)`. They are
deliberately narrow: each fires only where the reading is unambiguous.

```python
(r"(?<=[\u4e00-\u9fff])J(?=[\u4e00-\u9fff])", "。", "latin J -> 。 between CJK")
(r"(?<=[\u4e00-\u9fff])\s*\.\s*(?=[\u4e00-\u9fff])", "。", ". -> 。 between CJK")
(r"(?<=[\u4e00-\u9fff])厂(?=[\u4e00-\u9fff])", "：", "厂 -> ： before CJK")
(r"(?<=[\u4e00-\u9fff])T(?=\d)", "于", "T -> 于 before a digit")
```

Design rules:

- **Anchor on context.** Requiring CJK on both sides protects `J. Calvin`,
  `St. Augustine`, `1,000`, and decimal points.
- **One direction only.** `J`→`。` is safe; `。`→`J` never is.
- **No dictionary substitution.** Never "fix" a character because it looks like a
  word; that is how a repair pass silently corrupts a book.
- Apply rules *after* joining lines into paragraphs, so context spans the wrap.

`clean.repair_report(text)` counts which rules fired on a sample, which is a fast
way to see whether a new rule is doing anything before committing to a full run.

### When to add a rule

Only when the same substitution recurs across many pages **and** the replacement
is forced by the surrounding characters. Report the rule and its evidence to the
user rather than fixing one sentence at a time.

## Known limits

- Character-level OCR errors inside words (`MmqiiisdeSade`, `爼`) are not
  repairable by rules; they need a different OCR pass or human proofing.
- Vertical text, two-column layouts with ragged columns, and tables are not
  handled. Two-column *pages* whose columns are cleanly separated will read in
  row order; genuinely mixed columns will scramble.
- Illustrations, plates, and inline figures are dropped from the text. Only the
  cover image is carried into the EPUB.
- A book with no outline **and** a TOC page the detector misses falls back to a
  single chapter; supply `--chapters`.
