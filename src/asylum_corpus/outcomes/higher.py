"""Outcomes of VwGH and VfGH decisions (config/outcomes/higher_courts.yaml; schemas/higher_court_outcome.schema.json).

The register of the decision (its RIS document number) fixes the outcome where the rules name one: a VwGH deadline
application (register Fr, letter F) is `deadline_application`, a VfGH review of a law or regulation (registers G and V)
is `norm_review`, whatever the operative part decides. The operative part is still read, for the dispositions.

The operative part is cut into sentences, not at the period of a date ("vom 28. Juli"), of a point label ("A. III.") or
of an abbreviation ("Zl."). A sentence about costs or fees is ancillary and not read. Every other sentence gets every
disposition type whose pattern matches. A sentence about a CJEU reference, legal aid, suspensive effect, a stay, a
restitution, a deadline application or a norm keeps only that type, the first in this order (`sentence_only`): its
"abgewiesen" or "aufgehoben" concerns that application or norm, not the remedy against the BVwG decision - unless it
annuls on the grounds of § 42 Abs. 2 VwGG or finds a violated right (its scope may name them; nor is a sentence that
annuls or finds a violated right ancillary). After a CJEU reference, the referred questions are not read (up to the
next roman point label).
A bare "Der Antrag wird abgewiesen" is about suspensive effect when the subject names it.

The outcome is the first of `outcome_order` found in any sentence. It is flagged `partial` when an annulment comes
with a rejection, a dismissal, a refusal to deal, a finding of no violation, or a partial marker ("insoweit", "im
Übrigen", a named point, "im angefochtenen Umfang"), and `transferred` when the operative part transfers a complaint
("abgetreten", the VfGH's to the VwGH). The grounds of an annulment (content, procedure, jurisdiction) and who brought
the revision (the authority or a party) are recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

SENTENCE = re.compile(r"(?<=[.;:])\s+(?=(?:[IVX]+\.\s*|\d{1,2}\.\)?\s*)?[A-ZÄÖÜ„])")   # also before "2.) Der …"
ROMAN_LABEL = re.compile(r"^(?:[IVX]+\.|[A-Z]\.\s*[IVX]+\.)\s")


@dataclass
class HigherRules:
    registers: dict
    register_rx: dict[str, re.Pattern]
    split_month: re.Pattern
    split_label: re.Pattern
    split_abbreviation: re.Pattern
    ancillary: re.Pattern
    dispositions: list[tuple[str, re.Pattern]]
    bare_application: re.Pattern
    subject_suspensive: re.Pattern
    grounds: dict[str, re.Pattern]
    partial: re.Pattern
    party: dict[str, re.Pattern]
    sentence_only: list[str]
    annulment_overrides: re.Pattern
    order: list[tuple[str, list[str]]]
    scope_of_annulment: list[str]
    negations: dict[str, list[str]]

    @classmethod
    def load(cls, path: Path) -> "HigherRules":
        spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        reg, sen = spec["registers"], spec["sentences"]
        return cls({"VwGH": reg["VwGH"], "VfGH": reg["VfGH"]},
                   {"VwGH": re.compile(reg["vwgh_register"]), "VfGH": re.compile(reg["vfgh_register"])},
                   re.compile(sen["date_month"]), re.compile(sen["point_label"]), re.compile(sen["abbreviation"]),
                   re.compile(spec["ancillary"]["pattern"]),
                   [(d["id"], re.compile(d["pattern"])) for d in spec["dispositions"]],
                   re.compile(spec["bare_application"]), re.compile(spec["subject_suspensive"]),
                   {k: re.compile(v) for k, v in spec["grounds"].items()}, re.compile(spec["partial"]),
                   {k: re.compile(v) for k, v in spec["revision_party"].items()}, spec["sentence_only"],
                   re.compile(spec["annulment_overrides"]),
                   [(o, types) for o, types in spec["outcome_order"]], spec["scope_of_annulment"], spec["negations"])


def norm(text: str) -> str:
    return " ".join(text.lower().split())


def keeps_together(text: str, at: int, end: int, rules: HigherRules) -> bool:
    """The period before position `at` does not end a sentence: a date ("vom 28. Juli"), a point label ("A. III."), an
    abbreviation ("Zl. W193")."""
    m = re.search(r"(\S+)$", text[max(0, at - 30):at])
    token, after = (m[1] if m else ""), text[end:end + 30]
    if re.fullmatch(r"\d{1,2}\.", token) and rules.split_month.match(after):
        return True
    if re.fullmatch(r"[A-Z]\.", token) and rules.split_label.match(after):
        return True
    return bool(rules.split_abbreviation.fullmatch(token))


def sentences(text: str, base: int, rules: HigherRules) -> list[tuple[int, int, str]]:
    out, pos = [], 0
    cuts = [m for m in SENTENCE.finditer(text) if not keeps_together(text, m.start(), m.end(), rules)]
    for m in cuts + [None]:
        end = m.start() if m else len(text)
        piece = text[pos:end]
        if piece.strip():
            lead = len(piece) - len(piece.lstrip())
            out.append((base + pos + lead, base + pos + len(piece.rstrip()), piece.strip()))
        pos = m.end() if m else end
    return out


def sentence_types(sentence: str, rules: HigherRules, subject: str = "") -> list[str]:
    s = norm(sentence)
    types = [t for t, rx in rules.dispositions if rx.search(s)]
    if rules.bare_application.search(s) and rules.subject_suspensive.search(subject) and not \
            set(types) & set(rules.sentence_only):
        return ["suspensive"]
    for neg, cancelled in rules.negations.items():
        if neg in types:
            types = [t for t in types if t not in cancelled]
    annuls = bool({"annulled", "rights_violated"} & set(types))
    if annuls and rules.annulment_overrides.search(s):
        types = [t for t in types if t not in rules.sentence_only]
    else:
        for only in rules.sentence_only:
            if only in types:
                return [only]
    if annuls:
        types = [t for t in types if t not in rules.scope_of_annulment]
    return types


def annuls(sentence: str, rules: HigherRules) -> bool:
    """The sentence annuls the BVwG decision or finds a violated right (its scope may name costs: not ancillary)."""
    return bool({"annulled", "rights_violated"} & set(sentence_types(sentence, rules)))


def register_of(doc: dict, rules: HigherRules) -> str | None:
    m = rules.register_rx[doc["court"]].match(doc.get("ris_id") or "")
    return m[1] if m else None


def outcome_of(doc: dict, rules: HigherRules) -> dict:
    """The outcome annotation of one VwGH or VfGH decision."""
    court = doc["court"]
    ops = [s for s in doc["sections"] if s["type"] == "operative_part" and s.get("level", 1) == 1]
    subject = norm(" ".join(doc["full_text"][s["start"]:s["end"]] for s in doc["sections"] if s["type"] == "subject"))
    problems, dispositions = [], []
    for sec in ops:
        questions = False                       # after a CJEU reference: the referred questions
        for start, end, text in sentences(doc["full_text"][sec["start"]:sec["end"]], sec["start"], rules):
            if questions and not ROMAN_LABEL.match(text):
                continue
            questions = False
            if rules.ancillary.search(norm(text)) and not annuls(text, rules):
                continue
            types = sentence_types(text, rules, subject)
            if types:
                dispositions.append({"start": start, "end": end, "text": doc["full_text"][start:end], "types": types})
            questions = "referral" in types
    if not ops:
        problems.append("no_operative_part")
    found = {t for d in dispositions for t in d["types"]}
    register = register_of(doc, rules)
    outcome = rules.registers[court].get(register)
    outcome = outcome or next((o for o, types in rules.order if found & set(types)), None)
    if outcome is None and "application" in found:
        outcome = "interim" if rules.subject_suspensive.search(subject) else "procedural"
    if outcome is None:
        outcome = "unrecognized"
        problems.append("no_disposition" if not dispositions else "unrecognized")
    op_text = norm(" ".join(d["text"] for d in dispositions))
    partial = outcome == "annulled" and bool(found & {"rejected", "dismissed", "declined", "no_violation"}
                                             or rules.partial.search(op_text))
    grounds = sorted(g for g, rx in rules.grounds.items() if outcome == "annulled" and rx.search(op_text))
    party = "party"
    if court == "VwGH":
        authority, other = bool(rules.party["authority"].search(subject)), bool(rules.party["party"].search(subject))
        hint = bool(rules.party["party_hint"].search(subject))
        party = ("both" if authority and other else "authority" if authority else
                 "party" if other or hint else "unknown")
        if party == "unknown":
            operative = norm(" ".join(doc["full_text"][s["start"]:s["end"]] for s in ops))
            party = "party" if rules.party["costs_to_party"].search(operative) else "unknown"
    value = {"court": court, "register": register, "outcome": outcome, "partial": partial,
             "transferred": "transferred" in found, "grounds": grounds, "revision_party": party,
             "dispositions": dispositions, "problems": problems}
    return {"annotation_id": f"{doc['document_id']}#out:01", "document_id": doc["document_id"],
            "type": "normalized_outcome", "method": "rule_based", "value": value,
            "evidence": [{"start": d["start"], "end": d["end"], "role": "disposition"} for d in dispositions]}
