"""Structure templates of the reasoning (`structure.status`): the structure the recognised headings form, tested in
this order:

    abridged           an abridged decision (gekürzte Ausfertigung): a reasoning of at most 3,000 characters with the
                       standard note of § 29 Abs. 5 VwGVG;
    no_reasoning       no reasoning, one shorter than 500 characters, or one that only repeats head and operative part
                       (it holds the last 60 letters and digits of the head and is at most 1.3 times as long as head
                       and operative part together, plus 200, letters and digits counted);
    none               a reasoning without any recognised heading;
    merits             findings, assessment of evidence and legal assessment, in this order (procedural history,
                       "erwogen", "Zu A)", "Zu B)" optional): the court decides on the facts itself;
    merits_incomplete  one or two of them, or all three out of order;
    short              procedural history and/or "erwogen" but none of the three, e.g. cassations;
    ab_only            only "Zu A)" and/or "Zu B)".

The structure alone decides, not the decision type. A combined heading counts as both its parts; a procedural history
heading that names the facts ("Verfahrensgang und Sachverhalt") counts as findings only where the decision has an
assessment of evidence or a legal assessment.
"""

from __future__ import annotations

import re

TEMPLATES = ("merits", "merits_incomplete", "short", "ab_only", "none", "abridged", "no_reasoning")
FEL = ("findings", "evidence_assessment", "legal_assessment")
COMBINED = {"findings_and_evidence": ("findings", "evidence_assessment"),
            "legal_and_evidence": ("evidence_assessment", "legal_assessment")}
HISTORY_AND_FINDINGS = "procedural_history_and_findings"
ABRIDGED_RE = re.compile(r"gekürzte[nr]? Ausfertigung|§ ?29 Abs\.? ?5 VwGVG", re.I)
ABRIDGED_MAX_CHARS = 3000   # an abridged decision has only a short standard note instead of a reasoning


def reasoning_text(doc: dict) -> str:
    return "\n".join(doc["full_text"][s["start"]:s["end"]] for s in doc["sections"] if s["type"] == "reasoning")


def expand(types: list[str]) -> list[str]:
    """Section types in document order, each combined type replaced by its parts; procedural_history_and_findings
    becomes procedural history and findings where the decision has an assessment of evidence or a legal assessment,
    else procedural history only."""
    base = [part for t in types for part in COMBINED.get(t, (t,))]
    merits_parts = bool({"evidence_assessment", "legal_assessment"} & set(base))
    out = []
    for t in base:
        if t == HISTORY_AND_FINDINGS:
            out += ["procedural_history", "findings"] if merits_parts else ["procedural_history"]
        else:
            out.append(t)
    return out


def fel_in_order(types: list[str]) -> bool:
    """Findings, assessment of evidence and legal assessment (after expand) appear in this order."""
    fel = [t for t in expand(types) if t in FEL]
    return fel == sorted(fel, key=FEL.index)


def repeats_head(doc: dict) -> bool:
    """RIS's text field only repeats head and operative part: the end of the head is in the reasoning, and the
    reasoning is at most 1.3 times as long as head and operative part together, plus 200 (letters and digits
    compared)."""
    def part(*types):
        text = "\n".join(doc["full_text"][s["start"]:s["end"]] for s in doc["sections"] if s["type"] in types)
        return "".join(ch for ch in text.lower() if ch.isalnum())
    subject, head, reasoning = part("subject"), part("subject", "operative_part"), part("reasoning")
    return len(subject) >= 60 and subject[-60:] in reasoning and len(reasoning) <= 1.3 * len(head) + 200


def template(doc: dict, types: list[str]) -> str:
    """The template of a document whose reasoning has subsections of `types` (in document order)."""
    text = reasoning_text(doc)
    if len(text) <= ABRIDGED_MAX_CHARS and ABRIDGED_RE.search(text):
        return "abridged"
    if len(text) < 500 or repeats_head(doc):
        return "no_reasoning"
    if not types:
        return "none"
    expanded = expand(types)
    fel = [t for t in expanded if t in FEL]
    if set(fel) == set(FEL) and fel_in_order(types):
        return "merits"
    if fel:
        return "merits_incomplete"
    if {"procedural_history", "considerations"} & set(expanded):
        return "short"
    return "ab_only"
