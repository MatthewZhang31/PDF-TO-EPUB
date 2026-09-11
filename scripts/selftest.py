#!/usr/bin/env python3
"""Self-test for the pdf2epub toolkit — no PDF or network required.

    python selftest.py

Covers the parts that are easy to break silently: line ordering, paragraph
joining, junk filtering, punctuation repair, marker transfer, nested navigation
markup, and a full EPUB round-trip that parses every document as XML.
"""
from __future__ import annotations

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from p2e.clean import (Block, PageProfile, group_footnotes, is_centred,  # noqa: E402
                       join_lines, repair_text, transfer_markers)
from p2e.epub import build_epub, nav_xhtml, validate_epub  # noqa: E402
from p2e.structure import Chapter  # noqa: E402
from p2e.util import Line, is_junk, order_lines  # noqa: E402

FAILURES: list[str] = []


def check(name: str, got, want) -> None:
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got  {got!r}\n         want {want!r}")
        FAILURES.append(name)


def check_true(name: str, cond: bool, detail: str = "") -> None:
    check(name + (f" ({detail})" if detail and not cond else ""), bool(cond), True)


def L(text, x0, y0, x1=None, y1=None, size=19.0, score=1.0, source="embedded"):
    return Line(text=text, x0=x0, y0=y0, x1=x1 if x1 is not None else x0 + 10 * len(text),
                y1=y1 if y1 is not None else y0 + size, size=size, score=score,
                source=source)


# --------------------------------------------------------------------------
print("\n[1] order_lines: fragments sharing a row keep left-to-right order")
# a dialogue dash splits one visual line into two overlapping boxes
row = [L("她也没法立即起身离去。第二种", 211, 461.2, 489, 480),
       L("里有三个白人—", 56, 461.0, 189, 480)]
check("fragments ordered by x within the row",
      [l.text for l in order_lines(row)],
      ["里有三个白人—", "她也没法立即起身离去。第二种"])
check("distinct rows stay in vertical order",
      [l.text for l in order_lines([L("B", 50, 300), L("A", 50, 100)])],
      ["A", "B"])

# --------------------------------------------------------------------------
print("\n[2] is_junk: drops decoration, keeps real short content")
check("brace dropped", is_junk("}"), True)
check("ocr noise dropped", is_junk("I爼0"), True)
check("zero padding dropped", is_junk("00"), True)
check("bibliography year kept", is_junk("2012"), False)
check("short CJK kept", is_junk("变奏"), False)
check("english title kept", is_junk("Living in the End Times. London: Verso."), False)

# --------------------------------------------------------------------------
print("\n[3] join_lines: paragraph reconstruction")
prof = PageProfile(page=1, left=58.0, right=490.0, top=100.0, bottom=900.0,
                   body_size=19.0, text_width=432.0, indent=35.0)
lines = [
    L("我们应该重读拉康关于逻辑时间①的文本，", 96, 249),   # indented opener
    L("其间他对三个囚徒的逻辑难题做了精彩诠释。原", 58, 289),
    L("始版本不太为人所知。", 58, 326),
    L("◎第一种情况，两个黑人和一个白人在搞，而且这里面还牵扯到更多复杂的情形。", 96, 360),
]
paras = join_lines(lines, prof)
check("indented opener + wrapped continuation + bullet -> 2 paragraphs", len(paras), 2)
check_true("wrapped CJK lines joined without spaces", "诠释。原始版本" in paras[0], paras[0])
check_true("bullet starts its own paragraph", paras[1].startswith("◎第一种情况"), paras[1])
check_true("a short indented opener is not mistaken for a centred line",
           not is_centred(lines[0], prof))

# a sentence-final line that stops well short of the right margin ends a paragraph
short_tail = [L("这是第一段的结尾。", 58, 500),
              L("这是第二段的开头，后面还有很多很多字来占满这一行以便观察行为。", 58, 540)]
check("sentence-final short line starts a new paragraph", len(join_lines(short_tail, prof)), 2)

centred = [L("喜剧是一场合法化危机", 177, 242, 372, 261),
           L("以突如其来的丰饶结束", 178, 279, 371, 298)]
check("centred display lines stand alone", len(join_lines(centred, prof)), 2)
check_true("centred detection", is_centred(centred[0], prof))

# --------------------------------------------------------------------------
print("\n[4] repair_text: context-anchored OCR punctuation fixes")
cases = [
    ("那搞我的一定是白人J但是既然", "那搞我的一定是白人。但是既然"),
    ("我也没有厂", "我也没有！"),
    ("产物*逻辑时间", "产物。逻辑时间"),
    ("重要代表人物°他的宿命论", "重要代表人物。他的宿命论"),
    ("由拉康T1945年提出", "由拉康于1945年提出"),
    ("著作年诜", "著作年谱"),
    ("所导致, 但任何因素", "所导致，但任何因素"),
    ("白人。，但是既然", "白人。但是既然"),
    ("陷 入困境", "陷入困境"),
]
for src, want in cases:
    check(f"{src!r}", repair_text(src), want)
check("J. Calvin untouched (not CJK-adjacent)", repair_text("尤其是J.加尔文"), "尤其是J.加尔文")
check("1,000 untouched (not sentence-final)", repair_text("约有1,000人"), "约有1,000人")

# --------------------------------------------------------------------------
print("\n[5] transfer_markers: OCR text + embedded structure")
emb = [L("我们应该重读拉康关于逻辑时间①的文本，", 96, 249),
       L("◎第三种情况，每个女人都在被白人搞", 96, 752)]
ocr = [L("我们应该重读拉康关于逻辑时间的文本，", 96, 250, source="ocr"),
       L("③第三种情况，每个女人都在被白人搞", 96, 754, source="ocr")]
merged = transfer_markers(emb, ocr)
check("footnote marker re-inserted", merged[0].text, "我们应该重读拉康关于逻辑时间①的文本，")
check("OCR's wrong bullet replaced by the embedded one", merged[1].text,
      "◎ 第三种情况，每个女人都在被白人搞")

emb_year = [L("②萨德（MmqiiisdeSade*1740—18⑷’法国情色作家。", 58, 909)]
ocr_year = [L("萨德（Marquis de Sade，174()一1814），法国情色作家。", 58, 910, source="ocr")]
got_year = transfer_markers(emb_year, ocr_year)[0].text
check_true("digit-lookalike ⑷ is never injected into a year", "⑷" not in got_year, got_year)
check_true("regular footnote reference ② is still restored",
           got_year.startswith("②萨德"), got_year)

# --------------------------------------------------------------------------
print("\n[6] group_footnotes: one note per marker")
notes = group_footnotes([L("①拉康精神分析方法中最重要的概念之一。", 58, 784),
                         L("出。逻辑时间是一个辩证的三方主体结构。", 87, 813),
                         L("②萨德（Marquis de Sade，1740—1814），法国情色作家。", 58, 909)])
check("two notes split", len(notes), 2)
check_true("continuation lines stay with their note", "三方主体结构" in notes[0])
check_true("second note starts at its marker", notes[1].startswith("②萨德"))

# --------------------------------------------------------------------------
print("\n[7] nav_xhtml: nested lists must stay well-formed XML")
CASES = {
    "flat": [1, 1, 1],
    "nested": [1, 2, 2, 1, 2, 3],
    "skipped level": [1, 3, 1],
    "deep then shallow": [1, 2, 3, 2, 1],
    "starts deep": [2, 2, 3],
    "repeat after deeper": [1, 2, 1, 2, 2],
    "single": [1],
    "empty": [],
}
NS = "{http://www.w3.org/1999/xhtml}"
for name, levels in CASES.items():
    chs = [Chapter(id=f"ch{i:03d}", title=f"T{i}", level=lv, start_page=i + 1,
                   end_page=i + 1) for i, lv in enumerate(levels)]
    try:
        root = ET.fromstring(nav_xhtml(chs, "zh", "目录"))
    except ET.ParseError as exc:
        check(name, f"ParseError: {exc}", "well-formed")
        continue
    toc = root.find(f"{NS}body/{NS}nav[@id='toc']")
    check(f"{name}: {len(levels)} chapters -> nav links",
          len(toc.findall(f".//{NS}a")), len(levels))

# --------------------------------------------------------------------------
print("\n[8] EPUB round-trip: build, then validate every document as XML")
tmp = tempfile.mkdtemp(prefix="p2e-selftest-")
epub_path = os.path.join(tmp, "book.epub")
chs = [
    Chapter(id="ch001", title="代序：笑话在猴子变人过程中的作用", level=1,
            start_page=1, end_page=2,
            blocks=[Block(kind="para", text="在东欧共产党执政晚期，有一个流传甚广的谣言。", pages=[1]),
                    Block(kind="footnote", text="① 拉康精神分析方法中最重要的概念之一。", pages=[1])]),
    Chapter(id="ch002", title="齐泽克的笑话", level=1, start_page=3, end_page=4,
            blocks=[Block(kind="para", text="我们应该重读拉康关于逻辑时间①的文本。", pages=[3]),
                    Block(kind="heading", text="变奏", pages=[4]),
                    Block(kind="para", text="◎第一种情况，两个黑人和一个白人在搞。", pages=[4])]),
    Chapter(id="ch003", title="注释", level=1, start_page=5, end_page=5,
            blocks=[Block(kind="para", text="除非特别注明，所有的笑话均出自未发表的手稿。", pages=[5])]),
]

# a real cover, because a wrong relative path inside cover.xhtml yields a
# perfectly valid package that renders as a broken image
from PIL import Image  # noqa: E402
cover_path = os.path.join(tmp, "cover.jpg")
Image.new("RGB", (600, 900), (30, 60, 120)).save(cover_path, "JPEG")

build_epub(epub_path, chs, title="齐泽克的笑话", author="斯拉沃热·齐泽克",
           lang="zh", source="selftest", cover_path=cover_path)
info = validate_epub(epub_path)
check("no validation issues", info["issues"], [])
check_true("cover is in the package", info["cover"])
check_true("internal references were resolved", info["internal_refs"] >= 5,
           str(info.get("internal_refs")))
check("mimetype first entry", __import__("zipfile").ZipFile(epub_path).namelist()[0], "mimetype")
check_true("text survived", info["text_chars"] > 200, str(info["text_chars"]))

# the cover page must point at the image relative to OEBPS/, not OEBPS/text/
import zipfile as _zip  # noqa: E402
with _zip.ZipFile(epub_path) as _z:
    _cover = _z.read("OEBPS/cover.xhtml").decode("utf-8")
    _opf = _z.read("OEBPS/content.opf").decode("utf-8")
check_true("cover.xhtml uses the OEBPS-relative image path",
           "images/cover.jpg" in _cover and "../images/cover.jpg" not in _cover,
           _cover)
check_true("cover.xhtml wraps the image in an explicit-viewBox SVG",
           'viewBox="0 0 600 900"' in _cover, _cover)
check_true("cover page is first in the spine",
           _opf.index('idref="cover"') < _opf.index('idref="ch001"'), _opf)

# a package with no cover at all must still validate cleanly
_bad = os.path.join(tmp, "nocover.epub")
build_epub(_bad, chs, title="broken", author="", lang="zh")
_badinfo = validate_epub(_bad)
check_true("cover-less build still validates", _badinfo["issues"] == [],
           str(_badinfo["issues"]))

try:
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
except OSError:
    pass

# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
print("\n[9] printed folios: offset estimation and TOC page mapping")
from p2e.clean import (detect_folios, estimate_page_offset, global_margins,  # noqa: E402
                       profile_page)
from p2e.util import PageText  # noqa: E402

# a body page whose printed folio (73) sits in the bottom margin, offset +10
def body_page(phys, printed, extra="正文内容在这里，用来撑出版面结构。"):
    pt = PageText(page=phys, width=400, height=600)
    for k in range(6):
        pt.lines.append(L(f"这是第{k}行正文内容" + extra, 50, 100 + k * 20, 350,
                          118 + k * 20, size=14.0))
    pt.lines.append(L(str(printed), 190, 560, 210, 574, size=9.0))
    return pt

sample = [body_page(80 + i, 70 + i) for i in range(6)]
gl, gr = global_margins(sample, 14.0)
profs = {p.page: profile_page(p, 14.0, gl, gr) for p in sample}
folios = detect_folios(sample, profs)
check("folios detected on every body page", folios, {80: 70, 81: 71, 82: 72,
                                                     83: 73, 84: 74, 85: 75})
check("offset is physical - printed", estimate_page_offset(folios), 10)
check("a single page is not enough to trust an offset",
      estimate_page_offset({5: 1}), 0)
check("double-printed folios with the same value are accepted",
      detect_folios([body_page(83, 73)], {p.page: profile_page(p, 14.0, 50.0, 350.0)
                                          for p in [body_page(83, 73)]}),
      {83: 73})

# a printed TOC row maps through the offset to the physical page
from p2e.analyze import assign_toc_levels  # noqa: E402
from p2e.structure import resolve_chapters_from_toc_entries  # noqa: E402

pages = [body_page(80 + i, 70 + i) for i in range(6)]
pages[2].lines.insert(0, L("第三章 头部测量", 50, 60, 200, 80, size=18.0))
n_pages = max(p.page for p in pages)
rows = [{"title": "第三章 头部测量", "printed_page": 72, "x0": 47.0}]
chs = resolve_chapters_from_toc_entries(pages, rows, n_pages, offset=10)
check("TOC row resolved through the offset", [c.start_page for c in chs], [82])
check("title verified against the page text", chs[0].title, "第三章 头部测量")

# rows sharing a physical page become nav-only and must not duplicate text
rows2 = [{"title": "第三章 头部测量", "printed_page": 72, "x0": 47.0},
         {"title": "数字的诱惑", "printed_page": 72, "x0": 90.0}]
levels = assign_toc_levels(rows2)
for e, lv in zip(rows2, levels):
    e["level"] = lv
chs2 = resolve_chapters_from_toc_entries(pages, rows2, n_pages, offset=10)
check("shared page yields one spine chapter + one nav-only entry",
      (sum(1 for c in chs2 if not c.nav_only), sum(1 for c in chs2 if c.nav_only)),
      (1, 1))
check_true("nav-only entry points at its parent's file",
           chs2[1].nav_only and chs2[1].target() == chs2[0].target(),
           f"{chs2[1].nav_only} {chs2[1].target()} vs {chs2[0].target()}")
check_true("a deeper indent is inferred as a deeper level",
           levels[1] >= levels[0], str(levels))

# nav-only entries must stay out of the spine but appear in the nav
import zipfile as _zip2  # noqa: E402
_chk = os.path.join(tmp, "navonly.epub")
build_epub(_chk, chs2, title="nav-only", author="", lang="zh")
with _zip2.ZipFile(_chk) as _z2:
    _names = _z2.namelist()
    _nav = _z2.read("OEBPS/nav.xhtml").decode("utf-8")
    _opf2 = _z2.read("OEBPS/content.opf").decode("utf-8")
check("only the spine chapter gets a file",
      sum(1 for n in _names if n.startswith("OEBPS/text/")), 1)
check_true("nav lists both titles", "第三章 头部测量" in _nav and "数字的诱惑" in _nav)
check_true("nav-only title is absent from the OPF manifest",
           _opf2.count("application/xhtml+xml") == 2, _opf2)
check("the nav-only package validates", validate_epub(_chk)["issues"], [])

# --------------------------------------------------------------------------
print("\n[10] book.json round-trip: nav_only/href must survive assemble -> build")
# stage_build reconstructs Chapter objects from book.json; a field that is
# serialised but not read back silently turns every nav-only row into its own
# chapter with a file of its own.
from p2e.structure import Chapter as _Chapter  # noqa: E402

_round = [_Chapter(id="ch001", title="第三章 头部测量", level=1, start_page=10,
                   end_page=20,
                   blocks=[Block(kind="para", text="正文。", pages=[10])]),
          _Chapter(id="nav002", title="数字的诱惑", level=2, start_page=10,
                   end_page=10, nav_only=True, href="text/ch001.xhtml")]
_d = [_round[1].to_dict()]
_rebuilt = _Chapter(id=_d[0]["id"], title=_d[0]["title"], level=_d[0]["level"],
                    start_page=_d[0]["start_page"], end_page=_d[0]["end_page"],
                    front_matter=_d[0].get("front_matter", False),
                    nav_only=_d[0].get("nav_only", False),
                    href=_d[0].get("href", ""))
check_true("nav_only survives to_dict/from_dict",
           _rebuilt.nav_only and _rebuilt.target() == "text/ch001.xhtml",
           f"{_rebuilt.nav_only} {_rebuilt.target()}")

_chk2 = os.path.join(tmp, "roundtrip.epub")
build_epub(_chk2, _round, title="roundtrip", author="", lang="zh")
with _zip2.ZipFile(_chk2) as _z3:
    _nav2 = _z3.read("OEBPS/nav.xhtml").decode("utf-8")
    _files2 = [n for n in _z3.namelist() if n.startswith("OEBPS/text/")]
check("nav-only row creates no file of its own", len(_files2), 1)
check_true("nav-only row links to its parent's file",
           'href="text/ch001.xhtml"' in _nav2 and "nav002.xhtml" not in _nav2, _nav2)

print()
if FAILURES:
    print(f"{len(FAILURES)} check(s) FAILED:")
    for f in FAILURES:
        print("   -", f)
    raise SystemExit(1)
print("all checks passed")
