"""The operative part divided at its labels (operative in bvwg_parts.yaml).

Each paragraph of an operative part that starts with an outline label ("A)", "B)", "I.", "1.") opens a section; labels
of different kinds in one paragraph ("A) I. Die Beschwerde …") are nested levels at the same position. "A)" / "B)" are
operative_part_a / operative_part_b (also under an appellant's number, "1. A) …"), every other label an operative_point
with its `point`. With "A)", "B)" and "C)" the letter on the revision is operative_part_b, every other one
operative_part_a; a part or point whose text is the ruling on the revision is operative_part_b whatever its label; where
a stretch has no label at all, the sentence on the revision is its operative_part_b (detection "revision_sentence").
Labels of quoted new wording ("… zu lauten hat: „I. … II. …“") open nothing, nor does a label alone at the end.
"""

from __future__ import annotations

import re

from .labels import Tokens, label_text, parse_label
from .nesting import NewSection, build
from .parts_rules import PartsRules

REPEAT_KINDS = ("R",)     # a roman point printed again in a paragraph of its own is the next point


def label_tokens(text: str) -> tuple[Tokens, str]:
    return parse_label(text, roman_brackets=True, bare_roman=True)


def operative_heads(paragraphs: list[dict], rules: PartsRules) -> tuple[list[dict], dict[int, tuple[Tokens, str]]]:
    """The labelled paragraphs of an operative part. A paragraph with labels of different kinds appears once per kind
    in the returned stretch (one level each). The labels before a title naming a sentence or a case of a provision
    ("1. Satz, 2. Fall …") are no point, nor is a label whose title begins like the rest of a sentence after a line not
    ending like one (a sentence broken before an enumeration of the parties)."""
    op = rules.operative
    stretch, heads, prev = [], {}, None
    for p in paragraphs:
        text = p["text"]
        for rx, replace in op["normalize"]:
            text = rx.sub(replace, text)
        tokens, title = label_tokens(text)
        if tokens and prev is not None and op["broken_line"]["title"].match(title) \
                and not op["broken_line"]["previous"].search(prev.rstrip()):
            tokens = []
        prev = p["text"]
        if tokens and op["not_point"].match(title):
            kind = tokens[-1][0]
            while tokens and tokens[-1][0] == kind:
                tokens = tokens[:-1]
        groups: list[Tokens] = []
        for tok in tokens:
            if groups and groups[-1][-1][0] == tok[0]:
                groups[-1].append(tok)
            else:
                groups.append([tok])
        for g in groups:
            heads[len(stretch)] = (g, title)
            stretch.append(p)
        if not groups:
            stretch.append(p)
    return stretch, heads


def quoted_paragraphs(run: list[dict]) -> set[int]:
    """Ids of the paragraphs inside a quotation (the new wording of the authority's decision). A quote runs from a
    paragraph opening a quotation mark it does not close to the paragraph closing it, or to the next paragraph with a
    label of the kind of the one before the quote."""
    inside, depth, outer = set(), 0, set()
    for k, p in enumerate(run):
        t = p["text"]
        tokens = label_tokens(t)[0]
        if depth > 0 and tokens and (tokens[0][0] in outer or tokens[0][0] == "A"):
            depth = 0                                     # the outer numbering resumes: the closing mark was missed
        if depth > 0:
            inside.add(id(p))
        change = sum(t.count(c) for c in "„»") - sum(t.count(c) for c in "“”«")
        if depth == 0 and change > 0 and re.match(r"\s*[„»]", t):
            inside.add(id(p))
            back = next((parse_label(x["text"])[0] for x in reversed(run[:k]) if parse_label(x["text"])[0]), [])
            outer = {back[0][0]} if back else set()
        depth = max(0, depth + change)
    return inside


def operative_sections(run: list[dict], section: dict, rules: PartsRules, make) -> list[dict]:
    """The sections of one stretch of an operative part. `make(kind_of, source_label)` gives the section factory."""
    op = rules.operative
    stretch, heads = operative_heads(run, rules)
    if run and not label_tokens(run[-1]["text"])[1]:                      # a label alone at the end opens nothing
        heads = {k: v for k, v in heads.items() if stretch[k] is not run[-1]}
    inside = quoted_paragraphs(run)
    heads = {k: v for k, v in heads.items() if id(stretch[k]) not in inside}
    implicit = None
    if not heads:                                                          # no label: the revision sentence is B)
        implicit = next((k for k, p in enumerate(stretch) if op["revision_b"].match(p["text"])), None)
        if implicit is not None:
            heads[implicit] = ([("A", 2)], parse_label(stretch[implicit]["text"])[1])
    letters = {g[0][1] for g, _ in heads.values() if g[0][0] == "A" and len(g) == 1}
    many = {1, 2, 3} <= letters                                            # "A)", "B)" and "C)"
    revision = set()
    for k, (g, title) in heads.items() if many else ():
        if g[0][0] == "A" and len(g) == 1:
            nxt = next((x["text"] for x in stretch[k + 1:] if x is not stretch[k]), "")
            if op["letters_by_revision"].match(title or nxt):
                revision.add(g[0][1])

    def kind_of(tokens: Tokens, title: str) -> tuple[str, str | None, None, None]:
        if many and tokens[0][0] == "A" and len(tokens) == 1:
            return ("operative_part_b" if tokens[0][1] in revision else "operative_part_a"), None, None, None
        if tokens[0][0] == "A" and tokens[0][1] in (1, 2) and len(tokens) == 1:
            return ("operative_part_a" if tokens[0][1] == 1 else "operative_part_b"), None, None, None
        return "operative_point", label_text(tokens).rstrip(".)"), None, None

    once = {k for k in heads if sum(1 for x in stretch if x is stretch[k]) == 1}   # a paragraph with one label
    new_section: NewSection = make(kind_of, section["source_label"])
    made = build(stretch, heads, None, section["level"], section["id"], new_section, REPEAT_KINDS, operative=True,
                 repeat_at=once, top=frozenset({implicit} if implicit is not None else ()))
    for m in made:
        if implicit is not None and m["heading_paragraph_id"] == stretch[implicit]["id"] \
                and m["type"] == "operative_part_b":
            m.update({"label": None, "detection": "revision_sentence"})
    revision_rulings(made, run, op["revision_ruling"])
    return made


def revision_rulings(made: list[dict], run: list[dict], ruling: re.Pattern) -> None:
    """A part or point whose text (or next line, after a label alone) is the ruling on the revision is
    operative_part_b, whatever its label ("C) Die Revisionen sind … nicht zulässig"); not a label inside a B)."""
    at = {p["id"]: k for k, p in enumerate(run)}
    inner = {}
    for m in made:                        # the label right before the text
        if m["level"] >= inner.get(m["heading_paragraph_id"], m)["level"]:
            inner[m["heading_paragraph_id"]] = m
    types = {m["id"]: m["type"] for m in made}
    for m in inner.values():
        k = at[m["heading_paragraph_id"]]
        text = m["title"] or (run[k + 1]["text"] if k + 1 < len(run) else "")
        if m["type"] != "operative_part_b" and types.get(m["parent_id"]) != "operative_part_b" and ruling.match(text):
            m.update({"type": "operative_part_b", "point": None})
