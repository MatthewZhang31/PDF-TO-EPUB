"""Stage 4 - decide the book's structure and assemble chapters."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .clean import (Block, PageProfile, global_margins, group_footnotes,
                    is_heading_like, join_lines, profile_page, repair_text,
                    split_footnotes, strip_running_heads, transfer_markers)
from .util import (Line, PageText, is_junk, log, normalize_text, squeeze, step,
                   warn)

# outline entries that describe the physical book rather than its prose
FRONT_MATTER = {
    "封面", "封底", "书名", "书名页", "版权", "版权页", "目录", "扉页",
    "cover", "title", "title page", "copyright", "contents",
    "table of contents", "half title",
}
BACK_MATTER_HINT = {"附录", "后记", "译后记", "编后记", "注释", "参考文献"}


@dataclass
class Chapter:
    id: str
    title: str
    level: int
    start_page: int
    end_page: int = 0
    blocks: list[Block] = field(default_factory=list)
    front_matter: bool = False

    def to_dict(self) -> dict:
        return {
            "id": self.id, "title": self.title, "level": self.level,
            "start_page": self.start_page, "end_page": self.end_page,
            "front_matter": self.front_matter,
            "blocks": [b.to_dict() for b in self.blocks],
        }


def _norm_title(t: str) -> str:
    t = squeeze(normalize_text(t))
    return re.sub(r"[\s\u3000·．.、,:：;；\-—_—]+", "", t).lower()


def is_front_matter_title(title: str) -> bool:
    return _norm_title(title) in {_norm_title(x) for x in FRONT_MATTER}


# --------------------------------------------------------------------------
# chapter boundary resolution
# --------------------------------------------------------------------------

def resolve_chapters_from_outline(outline: list[dict], page_count: int) -> list[Chapter]:
    entries = [o for o in outline if o.get("title")]
    if not entries:
        return []
    entries.sort(key=lambda o: (o["page"], o["level"]))
    chapters: list[Chapter] = []
    for i, o in enumerate(entries):
        start = max(1, min(int(o["page"]), page_count))
        nxt = entries[i + 1]["page"] if i + 1 < len(entries) else page_count + 1
        end = max(start, min(int(nxt) - 1, page_count))
        title = squeeze(o["title"])
        chapters.append(Chapter(
            id=f"ch{i + 1:03d}", title=title, level=int(o.get("level", 1)),
            start_page=start, end_page=end,
            front_matter=is_front_matter_title(title),
        ))
    # An outline page number may point one past the real start; pull a chapter
    # back when its own title text is found on an earlier page.
    for c in chapters:
        c.start_page = max(1, c.start_page)
    return chapters


def locate_title_page(pages: list[PageText], title: str,
                      search_from: int, search_to: int) -> Optional[int]:
    """Find the first page in a range whose text contains the chapter title."""
    key = _norm_title(title)
    if len(key) < 2:
        return None
    for pt in pages:
        if not (search_from <= pt.page <= search_to):
            continue
        blob = _norm_title("\n".join(l.text for l in pt.lines[:6]))
        if key and key in blob:
            return pt.page
    return None


def resolve_chapters_from_toc_entries(pages: list[PageText], entries: list[dict],
                                      page_count: int) -> list[Chapter]:
    """Fallback: locate each printed-TOC entry by searching the body text."""
    chapters: list[Chapter] = []
    cursor = 1
    for i, e in enumerate(entries):
        found = locate_title_page(pages, e["title"], cursor, page_count)
        if found is None:
            continue
        chapters.append(Chapter(
            id=f"ch{i + 1:03d}", title=squeeze(e["title"]), level=1,
            start_page=found, end_page=page_count,
            front_matter=is_front_matter_title(e["title"]),
        ))
        cursor = found + 1
    for i, c in enumerate(chapters):
        c.end_page = (chapters[i + 1].start_page - 1) if i + 1 < len(chapters) else page_count
        c.end_page = max(c.start_page, c.end_page)
    return chapters


def apply_manual_chapters(manual: list[dict], page_count: int) -> list[Chapter]:
    chapters: list[Chapter] = []
    for i, m in enumerate(manual):
        chapters.append(Chapter(
            id=f"ch{i + 1:03d}", title=squeeze(str(m["title"])),
            level=int(m.get("level", 1)),
            start_page=int(m["page"]), end_page=page_count,
            front_matter=bool(m.get("front_matter", is_front_matter_title(str(m["title"])))),
        ))
    for i, c in enumerate(chapters):
        c.end_page = (chapters[i + 1].start_page - 1) if i + 1 < len(chapters) else page_count
        c.end_page = max(c.start_page, c.end_page)
    return chapters


# --------------------------------------------------------------------------
# assembly
# --------------------------------------------------------------------------

def pick_page_text(page_no: int, embedded: dict[int, PageText],
                   ocr: dict[int, PageText], source: str) -> PageText:
    """Choose which text source to use for a page.

    ``ocr`` reads punctuation and mixed Latin/CJK far better than the embedded
    layer that a 2000s-era scanner produced, so ``auto`` prefers it whenever it
    recovered a meaningful amount of text. The embedded layer is still used as
    the fallback and as the source of footnote markers and bullets, which a
    fresh OCR pass drops.
    """
    emb = embedded.get(page_no)
    oc = ocr.get(page_no)

    if source == "embedded":
        return emb or oc
    if source == "ocr":
        chosen = oc or emb
    else:
        n_ocr = sum(len(l.text.strip()) for l in oc.lines) if oc else 0
        chosen = oc if n_ocr >= 20 else (emb or oc)

    if chosen is not None and chosen.source == "ocr" and emb is not None:
        chosen.lines = transfer_markers(emb.lines, list(chosen.lines))
    return chosen


@dataclass
class AssembledBook:
    chapters: list[Chapter]
    heading_candidates: list[dict]
    stats: dict


def _drop_leading_title_line(lines: list[Line], titles: list[str]) -> list[Line]:
    """Remove a line at the top of a chapter page that merely restates the title.

    Chinese chapter openers frequently set the heading flush left at the same
    offset as the first body line, so nothing in the geometry separates them and
    the heading would otherwise be glued onto the first paragraph. The chapter
    title itself is the reliable signal.
    """
    if not titles or not lines:
        return lines
    for k in range(min(3, len(lines))):
        t = _norm_title(lines[k].text)
        if not t or len(t) > 60:
            continue
        for title in titles:
            if not title:
                continue
            if t == title or (len(t) <= len(title) * 1.5 and _ratio(t, title) >= 0.78):
                return lines[:k] + lines[k + 1:]
    return lines


def assemble(pages: list[PageText], chapters: list[Chapter],
             body_size: float, repair: bool = True) -> AssembledBook:
    """Clean every page and pour it into the chapter skeletons."""
    start_titles: dict[int, list[str]] = {}
    for c in chapters:
        key = _norm_title(c.title)
        if key:
            start_titles.setdefault(c.start_page, []).append(key)

    gleft, gright = global_margins(pages, body_size)
    profiles: dict[int, PageProfile] = {
        pt.page: profile_page(pt, body_size, gleft, gright) for pt in pages
    }
    stripped = strip_running_heads(pages, profiles, body_size)

    per_page_blocks: dict[int, list[Block]] = {}
    heading_candidates: list[dict] = []
    dropped = 0
    for pt in pages:
        prof = profiles[pt.page]
        lines = _drop_leading_title_line(stripped.get(pt.page, []),
                                         start_titles.get(pt.page, []))
        body, foot = split_footnotes(lines, prof)
        blocks: list[Block] = []
        for para in join_lines(body, prof):
            if is_junk(para):
                dropped += 1
                continue
            blocks.append(Block(kind="para", text=repair_text(para, repair),
                                pages=[pt.page]))
        for note in group_footnotes(foot):
            if is_junk(note):
                dropped += 1
                continue
            blocks.append(Block(kind="footnote", text=repair_text(note, repair),
                                pages=[pt.page]))
        for l in body:
            if is_heading_like(l.text, l, prof):
                heading_candidates.append({
                    "page": pt.page, "text": squeeze(l.text),
                    "size": round(l.size, 2),
                    "fill": round(l.width / prof.text_width, 2),
                })
        per_page_blocks[pt.page] = blocks

    stats = {"pages": len(pages), "paragraphs": 0, "footnote_blocks": 0,
             "dropped_junk": dropped}
    for c in chapters:
        for p in range(c.start_page, c.end_page + 1):
            c.blocks.extend(per_page_blocks.get(p, []))
        c.blocks = _drop_repeated_title(c)
        stats["paragraphs"] += sum(1 for b in c.blocks if b.kind == "para")
        stats["footnote_blocks"] += sum(1 for b in c.blocks if b.kind == "footnote")

    return AssembledBook(chapters=chapters, heading_candidates=heading_candidates,
                         stats=stats)


def _drop_repeated_title(c: Chapter) -> list[Block]:
    """Remove a leading block that merely repeats the chapter heading."""
    blocks = [b for b in c.blocks if b.text.strip()]
    if not blocks:
        return blocks
    head = blocks[0]
    if head.kind != "para":
        return blocks
    a = _norm_title(head.text)
    b = _norm_title(c.title)
    if not a or not b:
        return blocks
    if a == b or (len(a) <= len(b) * 1.4 and _ratio(a, b) >= 0.82):
        return blocks[1:]
    return blocks


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


def toc_markdown(chapters: list[Chapter]) -> str:
    lines = ["# 目录 / Table of Contents", ""]
    for c in chapters:
        if c.front_matter:
            continue
        indent = "  " * max(0, c.level - 1)
        lines.append(f"{indent}- {c.title}  (PDF p{c.start_page}"
                     + (f"–{c.end_page}" if c.end_page != c.start_page else "") + ")")
    return "\n".join(lines) + "\n"


def toc_json(chapters: list[Chapter]) -> list[dict]:
    return [{
        "title": c.title, "level": c.level,
        "start_page": c.start_page, "end_page": c.end_page,
        "front_matter": c.front_matter,
        "paragraphs": sum(1 for b in c.blocks if b.kind == "para"),
    } for c in chapters]
