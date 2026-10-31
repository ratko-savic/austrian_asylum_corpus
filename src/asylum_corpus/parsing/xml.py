"""The paragraphs of a RIS decision XML, with the text rules of the document (schema `full_text`).

    1. one paragraph per RIS paragraph element (<absatz>), list item (<listelem>: its symbol, a space, the text) and
       list closing paragraph (<schluss>, <schlussteil>) with non-empty text, and one per table row;
    2. left out: page header/footer and layout (kzinhalt, fzinhalt, layoutdaten, metadaten), the RIS header fields
       (Gericht, Entscheidungsdatum, Geschäftszahl, ECLI), the RIS field labels (<ueberschrift>), the editorial VfGH
       Leitsatz (it stays in ris_metadata), images;
    3. margin numbers (a number followed by a tab at the start of a paragraph, VwGH) go to marginal_number; a
       paragraph that holds nothing but such a number keeps it as its text (outline numbering printed as empty
       paragraphs), so that no digit is lost;
    4. tables: one paragraph per row, cells joined by ' | ' in column order, empty cells kept, empty rows dropped;
       a table nested in a cell, and list items in a cell, are part of that cell's text;
    5. footnotes (<fnen>/<fntext>, a block RIS places before the text): one paragraph per footnote, after all other
       paragraphs, labelled 'Fußnoten'; the reference marks (<fnzeichen/>) carry no text.
Inline elements become characters, a few characters are mapped, whitespace runs become one space; every other
character stays unchanged (no dash or quote normalisation).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

NS = "{http://www.bka.gv.at}"
SKIP = {"kzinhalt", "fzinhalt", "layoutdaten", "metadaten"}   # page header/footer, layout
HEADER_FIELDS = {"gericht", "entscheidungsdatum", "gz", "ecli"}  # repeat the RIS metadata
EDITORIAL_FIELDS = {"leitsatz"}                                  # VfGH headnote, kept in ris_metadata
FOOTNOTES = "fnen"
FOOTNOTE_FIELD, FOOTNOTE_LABEL = "fussnoten", "Fußnoten"
PARAGRAPH_ELEMENTS = ("absatz", "listelem", "schluss", "schlussteil")

INLINE = {"gdash": "-", "tab": " ", "br": " ", "nbsp": " ", "em14": " ", "amp": "&", "lt": "<", "gt": ">"}
DROP = {"binary", "src"}  # images
# Word symbol-font bullets (private use area), soft hyphen, no-break space
CHAR_MAP = str.maketrans({"\uf0b7": "•", "\uf095": "•", "\uf02d": "-", "\xad": None, "\xa0": " "})


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(CHAR_MAP)).strip()


def local(tag: str) -> str:
    return tag.replace(NS, "")


def inline(el) -> str:
    tag = local(el.tag)
    if tag in DROP:
        return ""
    if tag == "symbol":  # list symbol ("-", "1.", "a)"), separated from the item text
        return (el.text or "") + " "
    parts = [INLINE.get(tag, ""), el.text or ""]
    for child in el:
        parts += [inline(child), child.tail or ""]
    return "".join(parts)


@dataclass
class RawParagraph:
    field: str | None      # RIS field (XML ct attribute)
    label: str | None      # RIS field label (the preceding <ueberschrift>)
    text: str
    marginal_number: str | None = None


def table_rows(table) -> list:
    """The rows of this table, not those of a table nested in one of its cells."""
    rows = []

    def visit(el) -> None:
        for child in el:
            tag = local(child.tag)
            if tag == "tr":
                rows.append(child)
            elif tag != "table":
                visit(child)

    visit(table)
    return rows


def cell_paragraphs(td) -> list[str]:
    """The texts of a cell's paragraphs, list items and closing paragraphs in document order, nested tables included."""
    out: list[str] = []

    def visit(el) -> None:
        for child in el:
            if local(child.tag) in PARAGRAPH_ELEMENTS:
                out.append(inline(child))
            elif local(child.tag) not in ("ueberschrift",) + tuple(SKIP):
                visit(child)

    visit(td)
    return out


def paragraph_text(el) -> tuple[str, str | None]:
    """(text, margin number) of a paragraph element."""
    lead = (el.text or "").strip()
    if re.fullmatch(r"\d+", lead) and len(el) and local(el[0].tag) == "tab":  # margin number: "<number><tab>text"
        text = clean((el[0].tail or "") + "".join(inline(c) + (c.tail or "") for c in el[1:]))
        return (text, lead) if text else (lead, None)  # only a number: it is the text
    return clean(inline(el)), None


def read_paragraphs(xml: bytes) -> list[RawParagraph]:
    """Paragraphs in document order (paragraph elements and table rows), footnotes at the end."""
    out: list[RawParagraph] = []
    footnotes: list[RawParagraph] = []
    label = None

    def add(target: list[RawParagraph], field: str | None, text: str, number: str | None = None) -> None:
        if target is footnotes:
            target.append(RawParagraph(FOOTNOTE_FIELD, FOOTNOTE_LABEL, text, number))
        else:
            target.append(RawParagraph(field, label, text, number))

    def walk(el, target: list[RawParagraph]) -> None:
        nonlocal label
        tag = local(el.tag)
        if tag in SKIP:
            return
        if tag == FOOTNOTES:
            for note in el:
                if any(local(d.tag) in PARAGRAPH_ELEMENTS + ("table",) for d in note.iter() if d is not note):
                    walk(note, footnotes)
                elif text := clean(inline(note)):
                    footnotes.append(RawParagraph(FOOTNOTE_FIELD, FOOTNOTE_LABEL, text))
            return
        if tag == "ueberschrift":
            label = clean(inline(el))
            return
        if tag == "table":
            for tr in table_rows(el):
                cells = [clean(" ".join(cell_paragraphs(td))) for td in tr if local(td.tag) == "td"]
                fields = [a.get("ct") for a in tr.iter(NS + "absatz") if a.get("ct")]
                text = clean(" | ".join(cells)) if any(cells) else ""  # empty cells kept: columns stay aligned
                if text:
                    add(target, fields[0] if fields else None, text)
            return
        if tag in PARAGRAPH_ELEMENTS:
            text, number = paragraph_text(el)
            if text:
                add(target, el.get("ct"), text, number)
            return
        for child in el:
            walk(child, target)

    walk(ET.fromstring(xml), out)
    return out + footnotes
