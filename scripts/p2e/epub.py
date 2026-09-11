"""Stage 5 - write an EPUB 3 package with cover, navigation and per-chapter XHTML."""
from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from datetime import datetime, timezone
from typing import Optional
from xml.sax.saxutils import escape, quoteattr

from .structure import Chapter
from .util import log, slugify, step, warn

DEFAULT_CSS = """\
@charset "utf-8";
html { font-size: 100%; }
body {
  font-family: "Noto Serif CJK SC", "Source Han Serif SC", "Songti SC",
               "SimSun", "Georgia", serif;
  line-height: 1.65;
  margin: 0 5%;
  text-align: justify;
  word-wrap: break-word;
}
h1, h2, h3 { font-family: inherit; line-height: 1.35; text-align: left; }
h1 { font-size: 1.45em; margin: 1.4em 0 1em; page-break-before: always; }
h2 { font-size: 1.2em;  margin: 1.2em 0 0.7em; }
h3 { font-size: 1.05em; margin: 1.1em 0 0.6em; }
p  { margin: 0; text-indent: 2em; }
p.noindent { text-indent: 0; }
blockquote { margin: 0.8em 1.6em; }
hr { border: 0; border-top: 1px solid #999; margin: 1.2em 0; }
.footnotes { font-size: 0.85em; line-height: 1.5; color: #333; }
.footnotes p { text-indent: 0; margin: 0.35em 0; }
.cover { margin: 0; padding: 0; text-align: center; }
.cover img { max-width: 100%; max-height: 100%; }
.center { text-align: center; text-indent: 0; }
figure.fig { margin: 1.2em 0; padding: 0; text-align: center; page-break-inside: avoid; }
figure.fig img { max-width: 100%; height: auto; }
figure.fig figcaption {
  font-size: 0.85em; line-height: 1.45; color: #333;
  text-align: left; text-indent: 0; margin-top: 0.45em;
}
"""


def _xhtml(title: str, body: str, lang: str = "zh", css_href: str = "../styles/style.css") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" '
        f'xml:lang="{lang}" lang="{lang}">\n'
        "<head>\n"
        '  <meta charset="utf-8"/>\n'
        f"  <title>{escape(title)}</title>\n"
        f'  <link rel="stylesheet" type="text/css" href="{css_href}"/>\n'
        "</head>\n"
        "<body>\n"
        f"{body}\n"
        "</body>\n"
        "</html>\n"
    )


def chapter_xhtml(ch: Chapter, lang: str, marker: str = "") -> str:
    """Render a chapter. Written to OEBPS/text/, so ../ links reach OEBPS/.

    Body text and figures are emitted in block order: a figure belongs where the
    page had it, not collected at the end of the chapter. Footnotes are the one
    exception -- they gather into a block at the bottom.
    """
    parts: list[str] = []
    tag = "h1" if ch.level <= 1 else ("h2" if ch.level == 2 else "h3")
    anchor = f' id="{escape(ch.id)}"' if marker else ""
    parts.append(f"<{tag}{anchor}>{escape(ch.title)}</{tag}>")
    foots: list[str] = []
    for b in ch.blocks:
        text = b.text.strip()
        if b.kind == "figure" and b.image:
            cap = f"\n  <figcaption>{escape(text)}</figcaption>" if text else ""
            parts.append(
                '<figure class="fig">\n'
                f'  <img src="../images/{escape(b.image)}" alt="{escape(b.label or "插图")}"/>{cap}\n'
                "</figure>")
            continue
        if not text:
            continue
        if b.kind == "footnote":
            foots.append(f"<p>{escape(text)}</p>")
        elif b.kind == "heading":
            parts.append(f"<h3>{escape(text)}</h3>")
        else:
            parts.append(f"<p>{escape(text)}</p>")
    if foots:
        parts.append('<div class="footnotes">')
        parts.append("<hr/>")
        parts.extend(foots)
        parts.append("</div>")
    return _xhtml(ch.title, "\n".join(parts), lang, css_href="../styles/style.css")


def cover_xhtml(lang: str, *, image_href: str = "images/cover.jpg",
                image_size: tuple[int, int] | None = None,
                css_href: str = "styles/style.css") -> str:
    """Render the cover page.

    Written to OEBPS/cover.xhtml, so its own links are relative to OEBPS/ —
    ``images/cover.jpg``, not ``../images/cover.jpg``. Getting this wrong
    produces a valid-looking package whose cover page renders as a broken
    image, which is why validate_epub now resolves every internal reference.

    A plain ``<img>`` sized only by CSS is rendered inconsistently by older
    readers (notably WPS Office); wrapping it in a full-page SVG with an
    explicit viewBox is the most portable cover markup there is.
    """
    if image_size:
        w, h = image_size
        body = (
            '<div class="cover">\n'
            '  <svg xmlns="http://www.w3.org/2000/svg" '
            'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" '
            f'width="100%" height="100%" viewBox="0 0 {w} {h}" '
            'preserveAspectRatio="xMidYMid meet">\n'
            f'    <image width="{w}" height="{h}" xlink:href="{image_href}"/>\n'
            "  </svg>\n"
            "</div>"
        )
    else:
        body = (
            '<div class="cover">\n'
            f'  <img src="{image_href}" alt="cover"/>\n'
            "</div>"
        )
    return _xhtml("Cover", body, lang, css_href=css_href)


def normalize_depths(chapters: list[Chapter]) -> list[int]:
    """Map arbitrary heading levels onto contiguous nesting depths.

    A book may jump from level 1 straight to level 3, or repeat a level after a
    deeper one. Normalising through a stack guarantees the nav list stays a
    well-formed, properly nested ``<ol>`` tree.
    """
    depths: list[int] = []
    stack: list[int] = []
    for ch in chapters:
        lvl = max(1, ch.level)
        while stack and stack[-1] >= lvl:
            stack.pop()
        stack.append(lvl)
        depths.append(len(stack))
    return depths


def _nav_ol(chapters: list[Chapter], depths: list[int],
            start: int, level: int) -> tuple[str, int]:
    """Render one nested <ol> and return (xhtml, index after the last item)."""
    out = ["<ol>"]
    i = start
    while i < len(chapters) and depths[i] == level:
        ch = chapters[i]
        out.append(f'<li><a href="{ch.target()}">{escape(ch.title)}</a>')
        i += 1
        if i < len(chapters) and depths[i] > level:
            sub, i = _nav_ol(chapters, depths, i, depths[i])
            out.append(sub)
        out.append("</li>")
    out.append("</ol>")
    return "\n".join(out), i


def nav_xhtml(chapters: list[Chapter], lang: str, title: str) -> str:
    """EPUB 3 navigation document: the toc plus a landmarks list.

    Written to OEBPS/nav.xhtml, so its stylesheet link is ``styles/style.css``
    while its links into the text are ``text/<id>.xhtml``. Nav-only entries from
    a printed contents page point at the file of the chapter that owns the page.
    """
    if chapters:
        depths = normalize_depths(chapters)
        toc, _ = _nav_ol(chapters, depths, 0, 1)
        first = chapters[0].target()
    else:
        toc = "<ol></ol>"
        first = ""

    landmarks = [
        '<li><a epub:type="toc" href="nav.xhtml">目录</a></li>',
    ]
    if first:
        landmarks.append(
            f'<li><a epub:type="bodymatter" href="{first}">正文</a></li>')

    body = (
        f'<nav epub:type="toc" id="toc">\n<h1>{escape(title)}</h1>\n{toc}\n</nav>\n'
        '<nav epub:type="landmarks" hidden="hidden">\n<ol>\n'
        + "\n".join(landmarks)
        + "\n</ol>\n</nav>"
    )
    return _xhtml(title, body, lang, css_href="styles/style.css")


def ncx_xml(chapters: list[Chapter], title: str, book_id: str) -> str:
    points = []
    for i, ch in enumerate(chapters, 1):
        points.append(
            f'    <navPoint id="np{i}" playOrder="{i}">\n'
            f"      <navLabel><text>{escape(ch.title)}</text></navLabel>\n"
            f'      <content src="text/{ch.id}.xhtml"/>\n'
            "    </navPoint>"
        )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">\n'
        "  <head>\n"
        f'    <meta name="dtb:uid" content="{escape(book_id)}"/>\n'
        '    <meta name="dtb:depth" content="1"/>\n'
        '    <meta name="dtb:totalPageCount" content="0"/>\n'
        '    <meta name="dtb:maxPageNumber" content="0"/>\n'
        "  </head>\n"
        f"  <docTitle><text>{escape(title)}</text></docTitle>\n"
        "  <navMap>\n" + "\n".join(points) + "\n  </navMap>\n</ncx>\n"
    )


def figure_images(chapters: list[Chapter]) -> list[str]:
    """Figure filenames referenced by the given chapters, in a stable order."""
    seen: list[str] = []
    for ch in chapters:
        for b in ch.blocks:
            if b.kind == "figure" and b.image and b.image not in seen:
                seen.append(b.image)
    return seen


def opf_xml(chapters: list[Chapter], *, title: str, author: str, lang: str,
            book_id: str, publisher: str = "", date: str = "",
            has_cover: bool = True, source: str = "",
            figures: list[str] | None = None) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = [
        f'    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>',
        f"    <dc:title>{escape(title)}</dc:title>",
        f"    <dc:language>{escape(lang)}</dc:language>",
    ]
    if author:
        meta.append(f'    <dc:creator id="creator">{escape(author)}</dc:creator>')
    if publisher:
        meta.append(f"    <dc:publisher>{escape(publisher)}</dc:publisher>")
    if date:
        meta.append(f"    <dc:date>{escape(date)}</dc:date>")
    if source:
        meta.append(f"    <dc:source>{escape(source)}</dc:source>")
    meta.append(f'    <meta property="dcterms:modified">{now}</meta>')
    if has_cover:
        meta.append('    <meta name="cover" content="cover-image"/>')

    manifest = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        '    <item id="css" href="styles/style.css" media-type="text/css"/>',
    ]
    if has_cover:
        manifest.append('    <item id="cover-image" href="images/cover.jpg" media-type="image/jpeg" properties="cover-image"/>')
        manifest.append('    <item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/>')
    for n, name in enumerate(figures or [], 1):
        manifest.append(f'    <item id="fig{n:03d}" href="images/{name}" media-type="image/jpeg"/>')
    for ch in chapters:
        manifest.append(f'    <item id="{ch.id}" href="text/{ch.id}.xhtml" media-type="application/xhtml+xml"/>')

    spine = []
    if has_cover:
        spine.append('    <itemref idref="cover"/>')
    spine.append('    <itemref idref="nav"/>')
    for ch in chapters:
        spine.append(f'    <itemref idref="{ch.id}"/>')

    guide = []
    if has_cover:
        guide.append('    <reference type="cover" title="Cover" href="cover.xhtml"/>')
    if chapters:
        guide.append(f'    <reference type="text" title="正文" href="{chapters[0].target()}"/>')

    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" '
        f'unique-identifier="bookid" xml:lang="{lang}">\n'
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">\n'
        + "\n".join(meta) + "\n"
        "  </metadata>\n"
        "  <manifest>\n" + "\n".join(manifest) + "\n  </manifest>\n"
        '  <spine toc="ncx">\n' + "\n".join(spine) + "\n  </spine>\n"
        "  <guide>\n" + "\n".join(guide) + "\n  </guide>\n"
        "</package>\n"
    )


CONTAINER_XML = """\
<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""


def build_epub(out_path: str, chapters: list[Chapter], *, title: str, author: str,
               lang: str = "zh", book_id: Optional[str] = None,
               publisher: str = "", date: str = "", source: str = "",
               cover_path: Optional[str] = None, css: str = DEFAULT_CSS,
               include_front_matter: bool = False,
               figure_dir: Optional[str] = None) -> str:
    """Write the EPUB file and return its path."""
    book_id = book_id or f"urn:uuid:{uuid.uuid4()}"

    def visible(ch: Chapter) -> bool:
        return include_front_matter or not ch.front_matter

    # nav-only rows from a printed contents page share their parent's file and
    # must not appear in the manifest or the spine
    nav_items = [c for c in chapters if visible(c)]
    packaged = [c for c in nav_items if not c.nav_only]
    if not packaged:
        packaged = nav_items or chapters
        nav_items = packaged
    has_cover = bool(cover_path and os.path.isfile(cover_path))

    # figures referenced by the packaged chapters, and actually present on disk
    fig_names = [n for n in figure_images(packaged)
                 if figure_dir and os.path.isfile(os.path.join(figure_dir, n))]
    missing = [n for n in figure_images(packaged) if n not in fig_names]
    if missing:
        warn(f"{len(missing)} figure image(s) missing from {figure_dir}: "
             f"{', '.join(missing[:3])}{' …' if len(missing) > 3 else ''}")

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    tmp = out_path + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)

    with zipfile.ZipFile(tmp, "w") as z:
        # mimetype must be first and stored uncompressed
        zi = zipfile.ZipInfo("mimetype", date_time=(1980, 1, 1, 0, 0, 0))
        zi.compress_type = zipfile.ZIP_STORED
        z.writestr(zi, "application/epub+zip")

        z.writestr("META-INF/container.xml", CONTAINER_XML, zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/content.opf", opf_xml(
            packaged, title=title, author=author, lang=lang, book_id=book_id,
            publisher=publisher, date=date, has_cover=has_cover, source=source,
            figures=fig_names),
            zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav_xhtml(nav_items, lang, "目录"),
                   zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx_xml(packaged, title, book_id),
                   zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/styles/style.css", css, zipfile.ZIP_DEFLATED)
        if has_cover:
            size = None
            try:
                from PIL import Image
                with Image.open(cover_path) as im:
                    size = im.size
            except Exception:
                size = None
            z.writestr("OEBPS/cover.xhtml",
                       cover_xhtml(lang, image_href="images/cover.jpg",
                                   image_size=size),
                       zipfile.ZIP_DEFLATED)
            z.write(cover_path, "OEBPS/images/cover.jpg", zipfile.ZIP_DEFLATED)
        for name in fig_names:
            z.write(os.path.join(figure_dir, name), f"OEBPS/images/{name}",
                    zipfile.ZIP_DEFLATED)
        for ch in packaged:
            z.writestr(f"OEBPS/text/{ch.id}.xhtml",
                       chapter_xhtml(ch, lang, marker="true"), zipfile.ZIP_DEFLATED)

    if os.path.exists(out_path):
        os.remove(out_path)
    os.rename(tmp, out_path)
    log(f"  epub -> {out_path}")
    return out_path


def validate_epub(path: str) -> dict:
    """Structural sanity check of the produced EPUB.

    Every XHTML/OPF/NCX document is parsed as XML: an e-reader will refuse or
    mis-render a malformed nav, and only parsing catches that.
    """
    import re as _re
    import xml.etree.ElementTree as ET

    issues: list[str] = []
    info: dict = {"path": path, "size": os.path.getsize(path), "issues": issues}
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        info["entries"] = len(names)
        if not names or names[0] != "mimetype":
            issues.append("mimetype is not the first entry")
        else:
            if z.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
                issues.append("mimetype is compressed")
            if z.read("mimetype") != b"application/epub+zip":
                issues.append("mimetype content wrong")
        for required in ("META-INF/container.xml", "OEBPS/content.opf",
                         "OEBPS/nav.xhtml"):
            if required not in names:
                issues.append(f"missing {required}")
        bad = z.testzip()
        if bad:
            issues.append(f"corrupt entry: {bad}")

        # XML well-formedness of every document in the package
        xml_docs = [n for n in names
                    if n.endswith((".xhtml", ".opf", ".ncx", ".xml"))]
        for n in xml_docs:
            try:
                ET.fromstring(z.read(n))
            except ET.ParseError as exc:
                issues.append(f"{n} is not well-formed XML: {exc}")
        info["xml_documents"] = len(xml_docs)

        opf = z.read("OEBPS/content.opf").decode("utf-8") if "OEBPS/content.opf" in names else ""
        info["chapters"] = opf.count('media-type="application/xhtml+xml"')
        manifest_hrefs = set()
        for m in _re.finditer(r'<item [^>]*href="([^"]+)"', opf):
            manifest_hrefs.add("OEBPS/" + m.group(1))
        for href in sorted(manifest_hrefs):
            if href not in names:
                issues.append(f"manifest href missing from archive: {href}")

        spine_ids = _re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
        if not spine_ids:
            issues.append("spine has no itemref entries")
        info["spine_items"] = len(spine_ids)

        # Resolve every internal reference *inside* each document. A manifest
        # entry can exist while the document that points at it uses the wrong
        # relative path, which renders as a broken image or an unstyled page
        # in a reader and is otherwise invisible to a package-level check.
        nameset = set(names)
        checked = 0
        link_attrs = (
            ("{http://www.w3.org/1999/xhtml}link", "href"),
            ("{http://www.w3.org/1999/xhtml}img", "src"),
            ("{http://www.w3.org/1999/xhtml}image", "{http://www.w3.org/1999/xlink}href"),
            ("{http://www.w3.org/1999/xhtml}a", "href"),
        )
        for n in xml_docs:
            try:
                root = ET.fromstring(z.read(n))
            except ET.ParseError:
                continue
            base = n.rsplit("/", 1)[0]
            for tag, attr in link_attrs:
                for el in root.iter(tag):
                    ref = el.get(attr)
                    if not ref or ref.startswith(("#", "http:", "https:", "mailto:", "data:")):
                        continue
                    path = ref.split("#", 1)[0]
                    if not path:
                        continue
                    target = os.path.normpath(os.path.join(base, path)).replace("\\", "/")
                    checked += 1
                    if target.endswith("/"):
                        continue
                    if target not in nameset:
                        issues.append(
                            f"{n} references missing resource {ref!r} (resolved to {target})")
        info["internal_refs"] = checked

        text_chars = 0
        for n in names:
            if n.startswith("OEBPS/text/") and n.endswith(".xhtml"):
                text_chars += len(z.read(n).decode("utf-8"))
        info["text_chars"] = text_chars
        info["cover"] = "OEBPS/images/cover.jpg" in names
        # a cover-less package is legitimate; only a declared-but-missing cover
        # image is an actual defect
        if 'name="cover"' in opf and not info["cover"]:
            issues.append("the package declares a cover but no cover image is present")
    return info
