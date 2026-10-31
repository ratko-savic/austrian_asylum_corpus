"""Outcome units of a BVwG decision: the operative parts that decide the same appellant(s). One appellant's judgment
and order form one unit. The rules that join consecutive parts, with their reasons: config/outcomes/bvwg_units.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..ris.text import case_persons

WS = re.compile(r"\s+")
MONTHS = {m: i for i, m in enumerate(["jänner", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                      "september", "oktober", "november", "dezember"], 1)}


@dataclass(frozen=True)
class UnitRules:
    head_inside: re.Pattern
    names_party: re.Pattern
    form_clause: re.Pattern
    challenged_date: re.Pattern
    listed_person: re.Pattern
    person_label: re.Pattern
    forms: frozenset


def load_unit_rules(path: Path) -> UnitRules:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    rx = {k: re.compile(spec[k]) for k in ("head_inside", "names_party", "form_clause", "challenged_date",
                                            "listed_person", "person_label")}
    return UnitRules(forms=frozenset(spec["forms"]), **rx)


def norm_date(printed: str) -> str:
    """'16.07.2015' / '16. Juli 2015' -> '2015-07-16' (other forms as printed, without spaces)."""
    s = WS.sub("", printed.lower()).replace("januar", "jänner")
    m = re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s)
    if m:
        year = m[3] if len(m[3]) == 4 else "20" + m[3]
        return f"{year}-{int(m[2]):02d}-{int(m[1]):02d}"
    m = re.fullmatch(r"(\d{1,2})\.(\D+)(\d{4})", s)
    if m and m[2] in MONTHS:
        return f"{m[3]}-{MONTHS[m[2]]:02d}-{int(m[1]):02d}"
    return s


def subject_info(text: str, rules: UnitRules) -> dict:
    t = WS.sub(" ", text).strip()
    intro = rules.head_inside.search(t)
    party_part = t[intro.start():] if intro else t
    return {"full": bool(rules.names_party.search(t)), "form_clause": bool(rules.form_clause.search(t)),
            "dates": {norm_date(m[1]) for m in rules.challenged_date.finditer(t)},
            "listed": len({m[1] for m in rules.listed_person.finditer(party_part)}),
            "person_label": bool(rules.person_label.search(t))}


def join_reason(earlier_text: str, earlier: dict | None, later: dict | None, form0: str | None, form1: str | None,
                n_persons: int, rules: UnitRules) -> tuple[str | None, str | None]:
    """(why the later operative part joins the earlier one: 'continuation' / 'repeated_head', or None; why it is kept
    apart, or None) - the reasons kept apart are counted in the stage report."""
    if later is None:
        return None, "no_subject"
    if rules.head_inside.search(earlier_text):
        return None, "head_inside_the_earlier_part"
    if not later["full"]:
        return ("continuation", None) if later["form_clause"] else (None, "subject_without_form")
    if earlier is None or not earlier["full"]:
        return None, "earlier_subject_not_full"
    if {form0, form1} != rules.forms:
        return None, "not_judgment_and_order"
    if earlier["dates"] and later["dates"] and earlier["dates"] != later["dates"]:
        return None, "other_challenged_decision"
    one = (n_persons == 1 and not earlier["person_label"] and not later["person_label"]
           and earlier["listed"] < 2 and later["listed"] < 2)
    listed = earlier["listed"] == later["listed"] >= max(2, n_persons)
    return ("repeated_head", None) if one or listed else (None, "persons_not_shown_the_same")


def units(doc: dict, forms: dict[str, str | None], rules: UnitRules, apart: dict | None = None) \
        -> list[tuple[list[dict], list[str | None]]]:
    """The document's operative parts grouped into units, in order: (parts, join reasons; None for the first part).
    `apart`, if given (a Counter), counts the reasons consecutive parts were kept apart."""
    level1 = [s for s in doc["sections"] if s.get("level", 1) == 1]
    full, out, previous = doc["full_text"], [], None
    subjects: dict[str, dict | None] = {}
    for s in level1:
        if s["type"] == "operative_part":
            subjects[s["id"]] = subject_info(full[previous["start"]:previous["end"]], rules) \
                if previous and previous["type"] == "subject" else None
        previous = s
    ops = [s for s in doc["sections"] if s["type"] == "operative_part"]
    n = len(case_persons(doc.get("case_numbers")))
    for k, s in enumerate(ops):
        reason = None
        if k:
            p = ops[k - 1]
            reason, why_apart = join_reason(full[p["start"]:p["end"]], subjects.get(p["id"]), subjects.get(s["id"]),
                                            forms.get(p["id"]), forms.get(s["id"]), n, rules)
            if why_apart and apart is not None:
                apart[why_apart] += 1
        if reason and out:
            out[-1][0].append(s)
            out[-1][1].append(reason)
        else:
            out.append(([s], [None]))
    return out
