"""Checks every document with coarse or fine sections must pass beyond its schema; one that fails is not written.

Coarse sections (they change nothing of the stage 03 document and form a proper tree):
    text_changed    full_text or a paragraph (id, text, offsets, margin number) differs from the stage 03 document
    level1_changed  the level-1 sections (type, source label, span, order) differ from the stage 03 sections
    nesting         a subsection without a parent one level up, outside its parent's span or not inside a reasoning
                    section; a level-1 section with a parent
    overlap         two sections with the same parent overlap
    heading         a subsection does not start with its heading paragraph
    deepest         a paragraph's section_id is not the deepest section containing it
    order           sections not listed by (start, level) or ids not consecutive in that order
Fine sections: each section inside its parent at level + 1, siblings in order without overlap, a new section starting
at its heading paragraph, topics from the rules.
"""

from __future__ import annotations

from ..common import schema_validator


PARAGRAPH_FIELDS = ("id", "text", "start", "end", "marginal_number")
LEVEL1_FIELDS = ("type", "source_label", "start", "end")


def sections_schema_errors(doc: dict, limit: int = 5) -> list[str]:
    errors = sorted(schema_validator("sections").iter_errors(doc), key=lambda e: list(map(str, e.path)))
    return [f"{'/'.join(map(str, e.path)) or '(document)'}: {e.message[:200]}" for e in errors[:limit]]


def check_subsections(doc: dict, original: dict) -> list[tuple[str, str]]:
    """(check, message) for a document with coarse sections against the stage 03 document it was made from."""
    problems: list[tuple[str, str]] = []
    paras, sections = doc["paragraphs"], doc["sections"]
    if doc["full_text"] != original["full_text"] or \
            [tuple(p[f] for f in PARAGRAPH_FIELDS) for p in paras] != \
            [tuple(p[f] for f in PARAGRAPH_FIELDS) for p in original["paragraphs"]]:
        problems.append(("text_changed", "full_text or paragraphs differ from the stage 03 document"))
    level1 = [s for s in sections if s["level"] == 1]
    if [tuple(s[f] for f in LEVEL1_FIELDS) for s in level1] != \
            [tuple(s[f] for f in LEVEL1_FIELDS) for s in original["sections"]]:
        problems.append(("level1_changed", "level-1 sections differ from the stage 03 document"))

    by_id = {s["id"]: s for s in sections}
    bad = []
    for s in sections:
        parent = by_id.get(s["parent_id"]) if s["parent_id"] else None
        if s["level"] == 1:
            if s["parent_id"] is not None:
                bad.append(s["id"])
            continue
        top = s
        for _ in range(len(sections)):        # bounded: a malformed parent chain could be a cycle
            if not top.get("parent_id"):
                break
            top = by_id.get(top["parent_id"], {})
        if parent is None or parent["level"] != s["level"] - 1 or not parent["start"] <= s["start"] <= s["end"] <= \
                parent["end"] or top.get("type") != "reasoning":
            bad.append(s["id"])
    if bad:
        problems.append(("nesting", f"{len(bad)} sections, e.g. {bad[:3]}"))

    children: dict[str | None, list[dict]] = {}
    for s in sections:
        children.setdefault(s["parent_id"], []).append(s)
    overlaps = [b["id"] for group in children.values() for a, b in zip(group, group[1:]) if b["start"] <= a["end"]]
    if overlaps:
        problems.append(("overlap", f"{len(overlaps)} sections, e.g. {overlaps[:3]}"))

    para_by_id = {p["id"]: p for p in paras}
    wrong = [s["id"] for s in sections if s["level"] > 1 and (
        not (h := para_by_id.get(s["heading_paragraph_id"])) or h["start"] != s["start"])]
    if wrong:
        problems.append(("heading", f"{len(wrong)} subsections, e.g. {wrong[:3]}"))

    deepest_bad = []
    for p in paras:
        containing = [s for s in sections if s["start"] <= p["start"] and p["end"] <= s["end"]]
        deepest = max(containing, key=lambda s: s["level"], default=None)
        if deepest is None or deepest["id"] != p["section_id"]:
            deepest_bad.append(p["id"])
    if deepest_bad:
        problems.append(("deepest", f"{len(deepest_bad)} paragraphs, e.g. {deepest_bad[:3]}"))

    doc_id = doc["document_id"]
    if [(s["start"], s["level"]) for s in sections] != sorted((s["start"], s["level"]) for s in sections) or \
            [s["id"] for s in sections] != [f"{doc_id}#s:{i:02d}" for i in range(1, len(sections) + 1)]:
        problems.append(("order", "sections not in (start, level) order or IDs not consecutive"))
    return problems


def check_fine(doc: dict, topics: set[str]) -> list[tuple[str, str]]:
    """(check, message) for a document with fine sections: its schema and its tree."""
    problems = [("schema", f"{'/'.join(map(str, e.path))}: {e.message[:120]}")
                for e in list(schema_validator("fine_sections").iter_errors(doc))[:5]]
    by_id = {s["id"]: s for s in doc["sections"]}
    paras = {p["id"]: p for p in doc["paragraphs"]}
    last_of_parent: dict = {}
    for s in doc["sections"]:
        if s["parent_id"]:
            parent = by_id.get(s["parent_id"])
            if parent is None or not (parent["start"] <= s["start"] <= s["end"] <= parent["end"]) \
                    or s["level"] != parent["level"] + 1:
                problems.append(("tree", f"{s['id']} not inside its parent at level + 1"))
            prev = last_of_parent.get(s["parent_id"])
            if prev is not None and prev["end"] >= s["start"]:
                problems.append(("tree", f"{s['id']} overlaps {prev['id']}"))
            last_of_parent[s["parent_id"]] = s
        if s.get("detection") in ("heading_like", "label_structure"):
            hp = paras.get(s["heading_paragraph_id"])
            if hp is None or hp["start"] != s["start"]:
                problems.append(("heading", f"{s['id']} does not start at its heading paragraph"))
        if s.get("topic") and s["topic"] not in topics:
            problems.append(("topic", f"{s['id']} topic {s['topic']} not in the rules"))
    return problems
