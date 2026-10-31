"""The fine sections: a document's sections at any depth (schemas/fine_sections.schema.json, rules
config/segmentation/bvwg_parts.yaml).

The coarse sections stay; below them, within each stretch of a coarse section that no deeper coarse section covers:
    operative part   divided at its labels (operative.py)
    reasoning, other every heading of the court (heading_like.py, court_text.py) opens a section of type "part",
                     nested by its label (nesting.py); a part whose title names operative points is a point_assessment
                     (with `point` and `numbering`), and a part gets the topic of the groups court and any that its
                     title matches (`topic`)
Quoted material is not divided by its own headings (the court's numbers on its chapters are parts), and the
procedural history gets no parts. A main heading of the court that the
heading rules missed goes beside the coarse section whose number it continues (beside_sections).
"""

from __future__ import annotations

import re

from .court_text import CourtText
from .heading_like import reasoning_heads
from .labels import Tokens, kinds, label_text, parse_label, values
from .nesting import build
from .operative import operative_sections
from .parts_rules import PartsRules
from .titles import point_of, point_set, topic_of

FINE_FIELDS = ("label", "title", "point", "numbering", "topic", "nesting")


def fine_sections(doc: dict, rules: PartsRules) -> tuple[list[dict], dict[str, str]]:
    """The fine sections of a document with coarse sections (the coarse sections unchanged except where a missed main
    heading ends one earlier, then the new ones, in document order), and paragraph id -> section id where a paragraph
    moved."""
    doc_id = doc["document_id"]
    sections = [dict(s) for s in doc["sections"]]
    by_id = {s["id"]: s for s in sections}
    paragraphs = doc["paragraphs"]
    text_of = {p["id"]: p["text"] for p in paragraphs}
    heading_ids = {s["heading_paragraph_id"] for s in sections if s.get("heading_paragraph_id")}
    counter = [len(sections)]

    def make(kind_of, source_label: str):
        def new_section(p: dict, tokens: Tokens, title: str, level: int, parent_id: str, nesting: str) -> dict:
            counter[0] += 1
            typ, point, numbering, topic = kind_of(tokens, title)
            return {"id": f"{doc_id}#s:{counter[0]:02d}", "type": typ, "source_label": source_label,
                    "start": p["start"], "end": p["end"], "parent_id": parent_id, "level": level,
                    "detection": "label_structure" if typ.startswith("operative") else "heading_like", "rule": None,
                    "heading_paragraph_id": p["id"], "decision_form": None, "label": label_text(tokens) or None,
                    "title": title or None, "point": point, "numbering": numbering, "topic": topic,
                    "nesting": nesting, "_tokens": tokens}
        return new_section

    def stretches(sec: dict) -> list[list[dict]]:
        """Runs of the section's paragraphs not covered by a deeper coarse section."""
        kids = [s for s in sections if s.get("parent_id") == sec["id"]]
        runs, cur = [], []
        for p in paragraphs:
            if not sec["start"] <= p["start"] <= max(sec["end"], sec["start"]):
                continue
            if any(k["start"] <= p["start"] <= max(k["end"], k["start"]) for k in kids):
                if cur:
                    runs.append(cur)
                cur = []
                continue
            cur.append(p)
        if cur:
            runs.append(cur)
        return runs

    def own_label(sec: dict) -> Tokens | None:
        """The label the stretch starts from: that of the nearest labelled coarse section, the section itself first."""
        s = sec
        while s is not None:
            if s.get("heading_paragraph_id"):
                tokens = parse_label(text_of[s["heading_paragraph_id"]], letter_dot=True)[0]
                if tokens:
                    return tokens
            s = by_id.get(s.get("parent_id"))
        return None

    new = []
    for sec in sorted(sections, key=lambda s: (s["start"], s["level"])):
        if sec["type"] == "operative_part":
            for run in stretches(sec):
                new += operative_sections(run, sec, rules, make)
        elif sec["type"] != "subject":
            own = own_label(sec)
            for run in stretches(sec):
                new += reasoning_parts(run, sec, own, rules, heading_ids, text_of, make)
    moved = beside_sections(sections, new, paragraphs, rules, text_of)
    for s in new:
        del s["_tokens"]
    for s in sections:
        s.update({k: None for k in FINE_FIELDS})
    new.sort(key=lambda s: (s["start"], s["level"]))      # new ids consecutive in document order, after the coarse ones
    renumber = {s["id"]: f"{doc_id}#s:{len(sections) + k:02d}" for k, s in enumerate(new, 1)}
    for s in new:
        s["id"] = renumber[s["id"]]
        s["parent_id"] = renumber.get(s["parent_id"], s["parent_id"])
    out = sections + new
    out.sort(key=lambda s: (s["start"], s["level"]))
    return out, moved


def reasoning_parts(run: list[dict], sec: dict, own: Tokens | None, rules: PartsRules, heading_ids: set[str],
                    text_of: dict[str, str], make) -> list[dict]:
    """The court's parts in one stretch of a coarse section of the reasoning, with their topics."""
    heads, closers = reasoning_heads(run, sec, rules, heading_ids, text_of)
    if not heads:
        return []
    legal = sec["type"] in rules.quotes["provision_sections"]

    def kind_of(tokens: Tokens, title: str) -> tuple[str, str | None, str | None, None]:
        pt = point_of(title, rules, legal)
        return ("point_assessment", pt[0], pt[1], None) if pt else ("part", None, None, None)

    made = build(run, heads, own, sec["level"], sec["id"], make(kind_of, sec["source_label"]), rules.repeat_kinds,
                 closers=closers, tracker=CourtText(run, rules, sec["type"]))
    set_topics(made, rules)
    return made


def set_topics(made: list[dict], rules: PartsRules) -> None:
    """Topics of the groups court and any; a point part's topic is its issue (a court-group topic); no topic below a
    part on the country situation (its chapters are country information)."""
    group = rules.group
    for m in made:
        m["topic"] = topic_of(m["title"], rules, "court") if m["title"] else None
        if m["type"] == "point_assessment" and group.get(m["topic"]) != "court":
            m["topic"] = None
    by_id = {m["id"]: m for m in made}
    for m in made:
        up = by_id.get(m["parent_id"])
        while up is not None and up["topic"] not in rules.topic_none_below:
            up = by_id.get(up["parent_id"])
        if up is not None:
            m["topic"] = None


def beside_sections(sections: list[dict], new: list[dict], paragraphs: list[dict], rules: PartsRules,
                    text_of: dict[str, str]) -> dict[str, str]:
    """A main heading of the court that the heading rules missed - a part whose label continues the number of the
    coarse section it lies in by one, with a main heading's or the court's title ("4. Zum Unterbleiben einer
    mündlichen Verhandlung:" in "3. Rechtliche Beurteilung") - goes beside that coarse section, which then ends before
    it (its coarse end kept as `coarse_end`); the part's later siblings and their parts move with it (nesting
    "beside_section"). Where a further coarse section begins inside that section after the heading ("Zu B)"), the
    heading goes beside the coarse section it lies in ("Zu A)") if nothing begins there after it, else it stays. A
    coarse "Zu A)" keeps a part continuing its own number or right after it, unless that part is a ruling heading, on
    the revision or on other points than the "Zu A)" names. Returns paragraph id -> the coarse section that now holds
    it."""
    q = rules.quotes
    coarse = {s["id"]: s for s in sections}

    def label(s: dict) -> Tokens:
        hp = s.get("heading_paragraph_id")
        return parse_label(text_of[hp], letter_dot=True)[0] if hp else []

    pos = {p["id"]: k for k, p in enumerate(paragraphs)}
    remap: dict[str, str] = {}
    moved: set[str] = set()
    for part in sorted(new, key=lambda m: m["start"]):
        if part["nesting"] != "continues_section" or part["id"] in moved or part["type"] not in ("part",
                                                                                                 "point_assessment"):
            continue
        title = part["title"] or ""
        if not (q["main_title"].match(title) or q["court_title"].match(title) or part["type"] == "point_assessment"):
            continue
        r = coarse.get(part["parent_id"])
        tokens = part["_tokens"]
        chain, s = [], r
        while s is not None:                  # from the part's coarse section up to the one with the label it continues
            chain.append(s)
            own = label(s)
            if own:
                break
            s = coarse.get(s.get("parent_id"))
        if not chain or not own or kinds(own) != kinds(tokens) or values(own)[:-1] != values(tokens)[:-1] \
                or values(tokens)[-1] != values(own)[-1] + 1:
            continue
        k = pos[part["heading_paragraph_id"]]
        if r["type"] == "ruling_a" and (chain[-1] is r or r.get("heading_paragraph_id") == paragraphs[k - 1]["id"]):
            hp = r.get("heading_paragraph_id")
            own_point = point_of(parse_label(text_of[hp], letter_dot=True)[1], rules, True) if hp else None
            if not (rules.takes_in.match(title) or re.search(r"(?i)\brevision", title)
                    or own_point and part["point"] and not point_set(part["point"]) <= point_set(own_point[0])):
                continue                      # "Zu A)" keeps the parts after it
        cut = paragraphs[k - 1]["end"] if k else -1
        target = None
        for t in (chain[-1], chain[-2] if len(chain) >= 2 else None):
            if t is None or t.get("parent_id") is None or r["end"] != t["end"] or t["start"] > cut:
                continue
            if any(x not in chain and t["start"] <= x["start"] <= t["end"] and x["start"] > part["start"]
                   for x in sections):
                continue                      # a coarse section ("Zu B)") begins inside it after the heading
            target = t
            break
        if target is None or any(x["start"] > cut for x in chain[:chain.index(target) + 1]):
            continue
        heads = [m for m in new if m["parent_id"] == r["id"] and m["start"] >= part["start"]
                 and m["level"] == part["level"]]
        delta = part["level"] - target["level"]
        group = {m["id"] for m in heads}
        changed = True
        while changed:                        # the parts below the moved ones
            changed = False
            for m in new:
                if m["id"] not in group and m["parent_id"] in group:
                    group.add(m["id"])
                    changed = True
        for m in new:
            if m["id"] in group:
                m["level"] -= delta
                moved.add(m["id"])
        for m in heads:
            m["parent_id"] = target["parent_id"]
        part["nesting"] = "beside_section"
        for x in chain[:chain.index(target) + 1]:
            x.setdefault("coarse_end", x["end"])
            x["end"] = cut
        ids = {x["id"] for x in chain[:chain.index(target) + 1]}
        for p in paragraphs[k:]:
            if p["start"] > r["coarse_end"]:
                break
            if p["section_id"] in ids or remap.get(p["id"]) in ids:
                remap[p["id"]] = target["parent_id"]
    return remap
