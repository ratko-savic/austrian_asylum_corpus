"""The outcome annotations of a BVwG document: one `normalized_outcome` per outcome unit (schemas/outcome.schema.json).

An outcome unit is the operative parts that decide the same appellant or appellants (units.py). Its dispositions are
those of its parts, in order, each with the id of its operative part; its issue table, outcome and protection follow
from all of them (normalize.py). Each part keeps its own form, outcome, subtype, protection and revision in `parts`.
"""

from __future__ import annotations

from ..citations.statutes import Acts
from ..segmentation.forms import decision_forms
from .bvwg import lower, operative_part
from .normalize import issue_table, outcome, protection
from .rules import Rules
from .units import units


def unit_form(forms: list[str | None]) -> str | None:
    got = list(dict.fromkeys(f for f in forms if f))
    return None if not got else got[0] if len(got) == 1 else "judgment_and_order"


def unit_revision(revisions: list[str]) -> str:
    got = {r for r in revisions if r != "none"}
    if not got:
        return "none"
    return got.pop() if len(got) == 1 else "partly"


def document_outcomes(doc: dict, acts: Acts, rules: Rules, apart: dict | None = None) -> list[tuple[dict, list[dict]]]:
    """(annotation, the unit's operative part sections) for each outcome unit of the document, in order. `apart`, if
    given (a Counter), counts why consecutive operative parts were kept apart (units.py)."""
    ops = [s for s in doc["sections"] if s["type"] == "operative_part"]
    if not ops:
        return []
    forms = decision_forms(doc["sections"], doc["paragraphs"])
    rsec = reasoning_section(doc)
    reasoning = (lower(doc["full_text"][rsec["start"]:rsec["end"]]), rsec["start"]) if rsec else None
    groups = units(doc, forms, rules.units, apart)
    out = []
    for k, (sections, joined) in enumerate(groups, 1):
        dispositions, problems, parts = [], set(), []
        for s, how in zip(sections, joined):
            res = operative_part(doc, s, acts, rules, reasoning)
            out_, sub = outcome(res["dispositions"], rules)
            parts.append({"operative_part_id": s["id"], "decision_form": forms.get(s["id"]), "joined_by": how,
                          "outcome": out_, "outcome_subtype": sub,
                          "protection": protection(issue_table(res["dispositions"]), out_, rules),
                          "revision": res["revision"]})
            dispositions += res["dispositions"]
            problems |= set(res["problems"])
        if dispositions:
            problems.discard("no_disposition")      # a part without one is no problem when the unit has some
        table = issue_table(dispositions)
        out_, sub = outcome(dispositions, rules)
        evidence = [{"start": d["start"], "end": d["end"], "role": "disposition"} for d in dispositions]
        evidence += [{"start": p["start"], "end": p["end"], "role": "point"} for d in dispositions
                     for p in d["points"] if p["issue"]]
        out.append(({"annotation_id": f"{doc['document_id']}#out:{k:02d}", "document_id": doc["document_id"],
                     "type": "normalized_outcome",
                     "value": {"court": "BVwG", "operative_part_id": sections[0]["id"],
                               "operative_part_ids": [s["id"] for s in sections], "operative_part": k,
                               "operative_parts": len(groups),
                               "decision_form": unit_form([forms.get(s["id"]) for s in sections]), "outcome": out_,
                               "outcome_subtype": sub, "protection": protection(table, out_, rules), "issues": table,
                               "dispositions": dispositions, "parts": parts,
                               "revision": unit_revision([p["revision"] for p in parts]),
                               "problems": sorted(problems)},
                     "evidence": evidence, "method": "rule_based"}, sections))
    return out


def reasoning_section(doc: dict) -> dict | None:
    return next((s for s in doc["sections"] if s["type"] == "reasoning" and s.get("level", 1) == 1), None)
