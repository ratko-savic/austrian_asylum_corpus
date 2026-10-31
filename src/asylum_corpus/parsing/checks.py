"""Checks every document must pass: its JSON schema, and the checks below (errors), plus warnings.

Errors (the document is not written):
    offsets     full_text[start:end] != text for a paragraph, or full_text is not the paragraphs joined by "\\n"
    partition   the level-1 sections do not cover full_text without gaps, or a paragraph lies outside its section
    ids         paragraph or section IDs not numbered consecutively in document order
    text_lost   the letters and digits of the decision text in the XML, read by a separate route, differ from those
                of the paragraphs (margin numbers included): text lost or added by the parser
Warnings:
    no_operative_part   no operative_part section (a Spruch is expected in every decision)
    no_reasoning        no reasoning section
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from ..common import schema_validator
from .xml import DROP, EDITORIAL_FIELDS, FOOTNOTES, HEADER_FIELDS, NS, SKIP


ERRORS = ("offsets", "partition", "ids", "text_lost")
WARNINGS = ("no_operative_part", "no_reasoning")


def schema_errors(doc: dict, limit: int = 5) -> list[str]:
    errors = sorted(schema_validator("document").iter_errors(doc), key=lambda e: list(map(str, e.path)))
    return [f"{'/'.join(map(str, e.path)) or '(document)'}: {e.message[:200]}" for e in errors[:limit]]


def source_alnum(xml: bytes) -> str:
    """Letters and digits of the decision text in the XML, without the parts the text rules leave out.

    Deliberately independent of the parser: every text node is collected (not only paragraph elements), so text
    the parser does not reach shows up as a difference. Footnote texts go to the end, as in the text rules."""
    parts: list[str] = []
    notes: list[str] = []

    def walk(el, target: list[str]) -> None:
        tag = el.tag.replace(NS, "")
        if tag in SKIP or tag in DROP or tag == "ueberschrift":
            return
        if tag == "absatz" and el.get("ct") in HEADER_FIELDS | EDITORIAL_FIELDS:
            return
        if tag == FOOTNOTES:
            target = notes
        target.append(el.text or "")
        for child in el:
            walk(child, target)
            target.append(child.tail or "")

    walk(ET.fromstring(xml), parts)
    return "".join(ch for ch in "".join(parts + notes) if ch.isalnum())


def first_difference(a: str, b: str) -> str:
    i = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f"at {i}: XML …{a[max(0, i - 30):i + 30]}… / parsed …{b[max(0, i - 30):i + 30]}…"


def check_document(doc: dict, xml: bytes) -> list[tuple[str, str]]:
    problems: list[tuple[str, str]] = []
    text, paras, sections = doc["full_text"], doc["paragraphs"], doc["sections"]
    bad = [p["id"] for p in paras if text[p["start"]:p["end"]] != p["text"]]
    if bad or text != "\n".join(p["text"] for p in paras):
        problems.append(("offsets", f"{len(bad)} paragraphs, e.g. {bad[:3]}"))
    level1 = [s for s in sections if s.get("level", 1) == 1]
    gaps = [s["id"] for prev, s in zip(level1, level1[1:]) if s["start"] != prev["end"] + 1]
    if not level1 or level1[0]["start"] != 0 or level1[-1]["end"] != len(text) or gaps:
        problems.append(("partition", f"sections do not cover full_text (gaps before {gaps[:3]})"))
    by_id = {s["id"]: s for s in sections}
    outside = [p["id"] for p in paras
               if not (s := by_id.get(p["section_id"])) or not s["start"] <= p["start"] <= p["end"] <= s["end"]]
    if outside:
        problems.append(("partition", f"{len(outside)} paragraphs outside their section, e.g. {outside[:3]}"))
    doc_id = doc["document_id"]
    if [p["id"] for p in paras] != [f"{doc_id}#p:{i:04d}" for i in range(1, len(paras) + 1)] or \
            [s["id"] for s in sections] != [f"{doc_id}#s:{i:02d}" for i in range(1, len(sections) + 1)]:
        problems.append(("ids", "IDs not consecutive in document order"))
    source = source_alnum(xml)
    parsed = "".join(ch for p in paras for ch in (p["marginal_number"] or "") + p["text"] if ch.isalnum())
    if source != parsed:
        problems.append(("text_lost", f"{len(source):,} vs {len(parsed):,} letters/digits; "
                                      + first_difference(source, parsed)))
    types = {s["type"] for s in sections}
    if "operative_part" not in types:
        problems.append(("no_operative_part", "no operative_part section"))
    if "reasoning" not in types:
        problems.append(("no_reasoning", "no reasoning section"))
    return problems
