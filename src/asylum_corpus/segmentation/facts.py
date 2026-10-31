"""The facts part of a decision split into its three parts, as the court prints them (its coarse sections); used by
the agent corpus (stage 09).

Every paragraph of the reasoning before the legal assessment belongs to the innermost section of type procedural
history, findings or assessment of evidence that holds it. Two rules complete this:
- a section "procedural history and findings" ("I. Verfahrensgang und Sachverhalt") is the procedural history where
  the decision has findings of its own;
- a paragraph before the findings that is in none of the three parts is procedural history too (a history printed
  without a heading, the list of the evidence taken), except the frame lines: "Entscheidungsgründe" or "Begründung",
  and the heading of the court's considerations ("… hat erwogen:").
What is left is "other".
"""

from __future__ import annotations

import re

PARTS = ("procedural_history", "findings", "evidence_assessment")
AS_PART = {"procedural_history_and_findings": "procedural_history"}
FRAME = re.compile(r"^\W*(?:entscheidungsgründe|begründung)\W*$|\berwogen\W*$", re.I)


def facts_parts(doc: dict, legal_start: int | None) -> dict[str, list[int]]:
    """Paragraph indices by part: procedural_history, findings, evidence_assessment and other."""
    secs = doc["sections"]
    r_start = next((s["start"] for s in secs if s["type"] == "reasoning"), 0)
    has_findings = any(s["type"] == "findings" for s in secs)
    kinds = {t: t for t in PARTS} | (AS_PART if has_findings else {})
    findings_start = min((s["start"] for s in secs if s["type"] == "findings"), default=None)
    frame = {s.get("heading_paragraph_id") for s in secs if s["type"] in ("reasoning", "considerations")}
    parts = {p: [] for p in PARTS + ("other",)}
    for i, p in enumerate(doc["paragraphs"]):
        if p["start"] < r_start or (legal_start is not None and p["start"] >= legal_start):
            continue
        holding = [s for s in secs if s["type"] in kinds and s["start"] <= p["start"] < s["end"]]
        if holding:
            part = kinds[max(holding, key=lambda s: s["level"])["type"]]
        elif findings_start is not None and p["start"] < findings_start and not (
                p["id"] in frame or (len(p["text"]) <= 200 and FRAME.search(p["text"].strip()))):
            part = "procedural_history"
        else:
            part = "other"
        parts[part].append(i)
    return parts
