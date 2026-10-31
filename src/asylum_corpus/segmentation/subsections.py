"""The coarse sections of the reasoning from the printed headings: a document gets the subsections of its reasoning.

The level-1 sections (the RIS fields) keep their type and span; all sections are numbered anew in document order.
Inside each reasoning section, the headings that the heading
rules recognise (config/segmentation/bvwg_headings.yaml) become subsections by the hierarchy rules, in this order:

    zu_a_before_history  "Zu A)" before the first procedural history heading is no section.
    inside_history       From the start of the reasoning or a procedural history heading up to the next "erwogen"
                         heading, headings of other types are no sections when an "erwogen" heading follows: they
                         belong to the authority's decision or the appeal being reported.
    own_part_in_history  The exception: a heading with the label of the first procedural history heading
                         ("I. Verfahrensgang" ... "II. Sachverhalt") is the court's own part and stays a section;
                         without a procedural history heading, the label of the first "erwogen" heading is compared.
    erwogen_after_legal  An "erwogen" heading right after a legal assessment heading is that section's opening
                         sentence, no section.
    subtitle             A heading right after a heading of the same type with the same or a shallower label is its
                         subtitle ("Zu B)" "Unzulässigkeit der Revision:"), no section.
    fallback             A heading of a fallback rule counts only where the decision has no heading of its type from
                         the other rules.
    erwogen_as_legal     Where a decision has no legal assessment heading, an "erwogen" heading after the court's own
                         findings or assessment of evidence (with none after it) is its legal assessment.
    sub_point            A heading of the type of an open section with a deeper label is a sub-point, no section; with
                         the same or a shallower label it starts a new section. Two "Zu A)" headings naming different
                         points ("Zu A) I." ... "Zu A) II.") are siblings whatever their labels.
    point_of_evidence    A findings heading whose label continues the label of the open assessment of evidence
                         ("2. Beweiswürdigung" ... "2.1. Sachverhalt") is a point of that assessment, no section.
    parents              Procedural history and considerations go under the reasoning; findings, assessment of
                         evidence and legal assessment (and the combined types) under the open considerations, else
                         the reasoning; "Zu A)" and "Zu B)" under the open legal assessment, else the open
                         considerations, else the reasoning ("Zu B)" after an enclosing "Zu A)" goes beside it).
    zu_a_encloses        "Zu A)" before the findings / evidence / legal assessment encloses them up to "Zu B)".
    extent               A subsection runs from its heading to just before the next heading at the same or a higher
                         level, else to the end of its parent; text before the first heading stays in the parent.
    out_of_order         Findings, assessment of evidence and legal assessment out of order stay as found; the
                         template is merits_incomplete.

Every heading that becomes no section records the rule that dropped it. structure.hierarchy_rules_applied lists, in
alphabetical order, the rules that changed the document; parents and extent always apply, and fallback and
erwogen_as_legal are not listed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .forms import decision_forms
from .headings import LEADING_DASH, HeadingRule, HeadingRules
from .labels import parse_label
from .templates import COMBINED, FEL, fel_in_order, template

HISTORY = frozenset({"procedural_history", "procedural_history_and_findings"})
TOP = HISTORY | {"considerations"}
FEL_TYPES = frozenset(FEL) | frozenset(COMBINED)
LEGAL = frozenset({"legal_assessment", "legal_and_evidence"})
FACTS = frozenset({"findings", "evidence_assessment", "findings_and_evidence"})
LABEL_TOKEN_RE = re.compile(r"R\.|#\.|A\)|a\)|\(#\)")
ZU_A_POINT_RE = re.compile(r"zu\s+(?:spruchteil\s+|spruchpunkt\s+)?a\.?\)?\s*([IVX]{1,4})\.", re.I)
DROP_RULES = ("inside_history", "zu_a_before_history", "sub_point", "erwogen_after_legal", "subtitle",
              "point_of_evidence")


def printed_label(text: str) -> tuple:
    """The printed outline label as (kind, value) tokens ("II.1 Feststellungen" -> (("R", 2), ("#", 1))); a leading
    dash is left out."""
    return tuple(parse_label(LEADING_DASH.sub("", text, count=1))[0])


def zu_a_point(text: str) -> str | None:
    """The point a "Zu A)" heading names ("Zu A) II." -> "II")."""
    m = ZU_A_POINT_RE.search(text)
    return m.group(1).upper() if m else None


def family(section_type: str) -> str:
    """The type sub_point and subtitle compare: both procedural history types are one."""
    return "procedural_history" if section_type in HISTORY else section_type


def label_depth(signature: str) -> int:
    """Number of label tokens: '' 0, 'R.' 1, 'R.#.' 2, '#.#.#.' 3, 'A) #.' 2."""
    return len(LABEL_TOKEN_RE.findall(signature))


@dataclass
class Heading:
    index: int                    # position among the paragraphs of its reasoning section
    paragraph: dict
    rule: HeadingRule
    signature: str
    dropped: str | None = None    # the rule that made it no section (DROP_RULES or 'fallback')
    kept: str | None = None       # 'own_part_in_history': kept although inside_history would have dropped it
    as_type: str | None = None    # 'legal_assessment' for an 'erwogen' heading under erwogen_as_legal

    @property
    def type(self) -> str:
        return self.as_type or self.rule.type


@dataclass(eq=False)
class Node:                       # a section while the tree is built
    type: str
    level: int
    first: int                    # paragraph positions within the reasoning section (inclusive)
    last: int
    parent: Node | None = None
    heading: Heading | None = None
    container: bool = False       # a 'Zu A)' that encloses findings / evidence / legal assessment (zu_a_encloses)

    @property
    def signature(self) -> str:
        return self.heading.signature if self.heading else ""


def find_headings(paragraphs: list[dict], rules: HeadingRules) -> list[Heading]:
    """The section headings among the paragraphs (regular rules first, then fallback rules); title rules give none."""
    found = []
    for i, p in enumerate(paragraphs):
        if len(p["text"]) > rules.max_chars:
            continue
        hit = rules.matched(p["text"]) or rules.matched(p["text"], fallback=True)
        if hit and hit[0].type:
            found.append(Heading(i, p, hit[0], hit[1]))
    return found


def apply_history_rules(headings: list[Heading]) -> None:
    """zu_a_before_history, inside_history and its exception own_part_in_history."""
    first_history = next((h.index for h in headings if h.type in HISTORY), None)
    for h in headings:
        if h.type == "ruling_a" and first_history is not None and h.index < first_history:
            h.dropped = "zu_a_before_history"
    pending, pending_all, in_history, history_label = [], [], True, None
    for h in headings:
        if h.dropped:
            continue
        if h.type == "considerations":
            for x in pending:
                x.dropped = "inside_history"
            pending_all += pending
            pending, in_history = [], False
        elif h.type in HISTORY:
            if not in_history or history_label is None:    # the first heading of this procedural history
                history_label = h.signature                # ("I." in "I." > "I.1. Verfahrensgang")
            in_history = True
        elif in_history:
            if h.signature and h.signature == history_label:    # a sibling of the procedural history ends it
                h.kept, in_history = "own_part_in_history", False
            else:
                pending.append(h)
    if history_label is None:            # no procedural history heading: compare with the label of 'erwogen'
        erw = next((h for h in headings if h.type == "considerations" and not h.dropped), None)
        if erw is not None and erw.signature:
            for h in pending_all:
                if h.dropped == "inside_history" and h.index < erw.index and h.signature == erw.signature:
                    h.dropped, h.kept = None, "own_part_in_history"
    for h in headings:                   # the exception only counts where inside_history would have dropped it
        if h.kept == "own_part_in_history" and not any(
                x.type == "considerations" and x.index > h.index and not x.dropped for x in headings):
            h.kept = None


def apply_run_rules(headings: list[Heading]) -> None:
    """erwogen_after_legal and subtitle: a heading right after another one."""
    anchor, last_index = None, None      # the heading that opens the run, and the last heading paragraph of it
    for h in headings:
        if h.dropped or h.rule.fallback:
            continue
        if anchor is not None and h.index == last_index + 1:
            if h.type == "considerations" and anchor.type in LEGAL:
                h.dropped, last_index = "erwogen_after_legal", h.index
                continue
            if family(h.type) == family(anchor.type) and label_depth(h.signature) <= label_depth(anchor.signature):
                h.dropped, last_index = "subtitle", h.index
                continue
        anchor, last_index = h, h.index


def drop_unneeded_fallbacks(headings: list[Heading]) -> None:
    have = {part for h in headings if not h.dropped and not h.rule.fallback for part in COMBINED.get(h.type, (h.type,))}
    for h in headings:
        if h.rule.fallback and not h.dropped and h.type in have:
            h.dropped = "fallback"


def apply_erwogen_as_legal(headings: list[Heading]) -> None:
    """erwogen_as_legal: an 'erwogen' heading after the court's own facts parts, in a decision without a legal
    assessment."""
    live = [h for h in headings if not h.dropped]
    erwogen = next((h for h in live if h.type == "considerations"), None)
    if erwogen is None or not any(h.type in FACTS for h in live if h.index < erwogen.index):
        return
    if not any(h.type in FACTS for h in live if h.index > erwogen.index):
        erwogen.as_type = "legal_assessment"


def parent_for(h: Heading, root: Node, stack: list[Node]) -> Node:
    """parents: the open node a new section of this heading's type goes under."""
    if h.type in TOP:
        return root
    if h.type in FEL_TYPES:
        return next(n for n in reversed(stack) if n.type in ("considerations", "reasoning")
                    or (n.type == "ruling_a" and n.parent.type not in LEGAL))
    container = next((n for n in stack if n.container), None)
    if h.type == "revision_admissibility" and container is not None:
        return container.parent
    return next(n for n in reversed(stack) if n.type in LEGAL or n.type in ("considerations", "reasoning"))


def sub_point(h: Heading, stack: list[Node]) -> str | None:
    """sub_point or point_of_evidence where the heading is a point of an open section, not a section of its own."""
    same = next((n for n in reversed(stack) if family(n.type) == family(h.type)), None)
    if same is not None and label_depth(h.signature) > label_depth(same.signature):
        mine = zu_a_point(h.paragraph["text"]) if h.type == "ruling_a" else None
        theirs = zu_a_point(same.heading.paragraph["text"]) if mine and same.heading else None
        if not (mine and theirs and mine != theirs):       # "Zu A) I." … "Zu A) II." are siblings
            return "sub_point"
    if h.type == "findings":
        own = printed_label(h.paragraph["text"])
        other = next((n for n in reversed(stack) if n.type == "evidence_assessment" and n.heading), None)
        if other is not None and own:
            theirs = printed_label(other.heading.paragraph["text"])
            if theirs and len(own) > len(theirs) and own[:len(theirs)] == theirs:
                return "point_of_evidence"
    return None


def build_tree(n_paragraphs: int, headings: list[Heading], applied: set[str]) -> list[Node]:
    """The subsection nodes of one reasoning section in document order (parents, extent, sub_point, zu_a_encloses,
    point_of_evidence)."""
    root = Node("reasoning", 1, 0, n_paragraphs - 1)
    stack, nodes = [root], []
    for h in headings:
        if h.dropped:
            continue
        h.dropped = sub_point(h, stack)
        if h.dropped:
            continue
        parent = parent_for(h, root, stack)
        while stack[-1] is not parent:
            stack.pop().last = h.index - 1
        if h.type in FEL_TYPES and parent.type == "ruling_a":
            parent.container = True
            applied.add("zu_a_encloses")
        node = Node(h.type, parent.level + 1, h.index, n_paragraphs - 1, parent, h)
        stack.append(node)
        nodes.append(node)
    for n in stack[1:]:
        n.last = n_paragraphs - 1
    return nodes


def subsections(doc: dict, rules: HeadingRules) -> tuple[dict, list[Heading]]:
    """The document with its coarse sections, and its recognised headings with their outcome."""
    paragraphs = [dict(p) for p in doc["paragraphs"]]
    out = {**doc, "paragraphs": paragraphs}
    forms = decision_forms(doc["sections"], doc["paragraphs"])
    by_section: dict[str, list[int]] = {}
    for i, p in enumerate(paragraphs):
        by_section.setdefault(p["section_id"], []).append(i)

    entries = []   # (start, level, section dict, old id or None, node or None)
    for s in doc["sections"]:
        entries.append((s["start"], 1, {**s, "parent_id": None, "level": 1, "detection": "xml_field", "rule": None,
                                        "heading_paragraph_id": None, "decision_form": forms.get(s["id"])},
                        s["id"], None))

    applied: set[str] = set()
    trees, all_headings = [], []
    for s in doc["sections"]:
        if s["type"] != "reasoning":
            continue
        idx = by_section.get(s["id"], [])
        heads = find_headings([paragraphs[i] for i in idx], rules)
        apply_history_rules(heads)
        apply_run_rules(heads)
        trees.append((s, idx, heads))
        all_headings += heads
    drop_unneeded_fallbacks(all_headings)
    if not any(h.type in LEGAL for h in all_headings if not h.dropped):
        for _, _, heads in trees:
            apply_erwogen_as_legal(heads)

    node_entries = []
    for s, idx, heads in trees:
        for node in build_tree(len(idx), heads, applied):
            first, last = paragraphs[idx[node.first]], paragraphs[idx[node.last]]
            section = {"id": None, "type": node.type, "source_label": s["source_label"], "start": first["start"],
                       "end": last["end"], "parent_id": None, "level": node.level, "detection": "heading_rule",
                       "rule": node.heading.rule.id, "heading_paragraph_id": first["id"], "decision_form": None}
            node_entries.append((node, s["id"], section, idx))
            entries.append((section["start"], node.level, section, None, node))
    applied |= {h.dropped for h in all_headings if h.dropped in DROP_RULES}
    applied |= {h.kept for h in all_headings if h.kept}

    # number in document order (start, then level); map the old level-1 ids and the nodes to the new ids
    entries.sort(key=lambda e: (e[0], e[1]))
    new_id, node_id = {}, {}
    for k, (_, _, section, old, node) in enumerate(entries, 1):
        section["id"] = f"{doc['document_id']}#s:{k:02d}"
        if old:
            new_id[old] = section["id"]
        if node is not None:
            node_id[node] = section["id"]
    for p in paragraphs:
        p["section_id"] = new_id[p["section_id"]]
    for node, reasoning_id, section, idx in node_entries:
        section["parent_id"] = node_id[node.parent] if node.parent.level > 1 else new_id[reasoning_id]
        for i in idx[node.first:node.last + 1]:      # parents come first in document order; deeper ones overwrite
            paragraphs[i]["section_id"] = section["id"]

    types = [n.type for n, _, _, _ in node_entries]
    if any(t in FEL_TYPES for t in types) and not fel_in_order(types):
        applied.add("out_of_order")
    out["sections"] = [e[2] for e in entries]
    out["structure"] = {"status": template(doc, types), "hierarchy_rules_applied": sorted(applied)}
    return out, all_headings
