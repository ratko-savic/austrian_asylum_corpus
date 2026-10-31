"""The citations of one document.

Per paragraph every pattern is applied: provision chains with a known act, chains followed by "leg. cit.", chains
before an EU act number, then the identifiers of cases.py. Overlapping matches are resolved by position, longest
first. "leg. cit." refers to the act cited last before it in the same paragraph. The decision's own BVwG case numbers
(in its head, or one of its document numbers) are not citations, and neither is an article of the StGB: the Austrian
StGB has paragraphs only, so "Artikel 94 des Strafgesetzbuches" is a foreign penal code in a country report.
`target_id` is the cited document where the citation index names exactly one (none for a citation of the document
itself), otherwise the case reference of a cited Austrian case number.
"""

from __future__ import annotations

import re
from collections import Counter

from . import cases
from .normalize import designation
from .statutes import CHAIN, OF, Acts, chain_items, explicit_items

FOREIGN_PENAL_CODE = "StGB Art."     # the normalized form of an article of a "Strafgesetzbuch"
# a provision chain before an EU act number: "Art. 15 lit. b der Verordnung (EU) 2024/1347"
EU_PROVISION = re.compile(rf"(?<![\w§]){CHAIN}{OF}(?P<eu>{cases.EU_NUMBER})")


def head_paragraph_ids(doc: dict) -> set[str]:
    """BVwG: the paragraphs of the first section if it is the subject (printed case and document numbers)."""
    first = doc["sections"][0] if doc["sections"] else None
    if doc["court"] != "BVwG" or not first or first["type"] != "subject":
        return set()
    return {p["id"] for p in doc["paragraphs"] if p["section_id"] == first["id"]}


def find(paragraph: dict, acts: Acts) -> list[tuple]:
    """Non-overlapping matches of one paragraph: (start, end, rule, payload), offsets into the paragraph text."""
    text, found = paragraph["text"], []
    patterns = [("legislation_provision", acts.legislation), ("leg_cit", acts.leg_cit),
                ("eu_act_provision", EU_PROVISION)] + [(rule, rx) for rule, _, rx in cases.PATTERNS]
    for rule, rx in patterns:
        for m in rx.finditer(text):
            if rule in cases.CONTEXT and not cases.CONTEXT[rule].search(text[max(0, m.start() - 200):m.end() + 80]):
                continue
            before = text[max(0, m.start() - 80):m.start()]
            if rule in cases.EXCLUDE_BEFORE and cases.EXCLUDE_BEFORE[rule].search(before):
                continue
            if rule == "vfslg":  # "VfSlg 13.836/1994, 14.650/1996": one citation per volume number
                for k, item in enumerate(re.finditer(r"\d{1,2}\.\d{3}/\d{4}", m.group(0))):
                    s = m.start() if k == 0 else m.start() + item.start()
                    found.append((s, m.start() + item.end(), rule, ("national_case", f"VfSlg {item.group(0)}")))
                continue
            found.append((m.start(), m.end(), rule, m))
    found.sort(key=lambda c: (c[0], -(c[1] - c[0]), c[2]))
    kept, last_end = [], -1
    for c in found:
        if c[0] >= last_end:
            kept.append(c)
            last_end = c[1]
    return kept


def chain_text(m: re.Match) -> str:
    """The chain of a provision match: the designations without the act, the EU act number or "leg. cit."."""
    group = "act" if "act" in m.groupdict() and m["act"] else "eu" if "eu" in m.groupdict() and m["eu"] else None
    chain = m.group(0)[:m.start(group) - m.start()] if group else re.sub(r"\s?leg\.?\s?cit\b\.?$", "", m.group(0))
    return re.sub(r"\s?(?:des|der|dieses|dieser)\s?$", "", chain)


def attested_for(acts: Acts, act: str) -> frozenset | None:
    """The designations of an act cited explicitly in the BVwG decisions, or None when the acts carry no table."""
    return None if acts.attested is None else acts.attested.get(act, frozenset())


def explicit_provisions(doc: dict, acts: Acts) -> Counter:
    """(act, designation) of every provision of a document printed with its own "§" / "Art." in a chain with a known
    act: what the table of attested designations counts."""
    out = Counter()
    for p in doc["paragraphs"]:
        for _, _, rule, m in find(p, acts):
            if rule == "legislation_provision":
                act = acts.by_alias[m["act"]][0]
                out.update((act, d) for d in explicit_items(chain_text(m)))
    return out


def provision_citations(m: re.Match, act: str, typ: str, rule: str, attested: frozenset | None = None) -> list[tuple]:
    """(start, end, rule, type, normalized) of each provision of a chain, offsets into the paragraph. Every provision
    but the last ends at its own text; the last ends with the act."""
    chain = chain_text(m)
    items = chain_items(chain, attested)
    if len(items) <= 1:
        return [(m.start(), m.end(), rule, typ, f"{act} {items[0][2] if items else designation(chain)}")]
    out = []
    for k, (s, e, desig) in enumerate(items):
        end = m.end() if k == len(items) - 1 else m.start() + e
        out.append((m.start() + s, end, f"{rule}_chain", typ, f"{act} {desig}"))
    return out


def document_citations(doc: dict, acts: Acts, index: dict) -> tuple[list[dict], Counter]:
    """The citations of a document, numbered in text order, and counts of what was resolved or skipped."""
    head = head_paragraph_ids(doc)
    stats, raw = Counter(), []
    for p in doc["paragraphs"]:
        last_act = None                                   # for "leg. cit.": the act cited last in the paragraph
        for start, end, rule, payload in find(p, acts):
            if rule == "legislation_provision":
                act, typ = acts.by_alias[payload["act"]]
                last_act = (act, typ)
                items = provision_citations(payload, act, typ, rule, attested_for(acts, act))
            elif rule == "eu_act_provision":              # the act is the EU act number, in one spelling
                last_act = (cases.eu_act(payload["eu"]), "eu_legislation")
                items = provision_citations(payload, *last_act, rule)
            elif rule == "leg_cit":
                if last_act is None:
                    stats["leg_cit without a preceding act"] += 1
                    continue
                items = provision_citations(payload, *last_act, rule, attested_for(acts, last_act[0]))
                stats["leg_cit resolved"] += 1
            elif rule == "vfslg":
                items = [(start, end, rule) + payload]
            else:
                typ, norm = cases.normalize(rule, payload)
                own = p["id"] in head or norm.removeprefix("BVwG ") in doc["document_numbers"]
                if rule == "bvwg_case_number" and own:
                    stats["own case numbers skipped"] += 1
                    continue
                items = [(start, end, rule, typ, norm)]
                if rule == "vwgh_case_number":
                    extra = cases.series(p["text"], end, norm)
                    items += [(s, e, "vwgh_case_number_series", typ, n) for s, e, n in extra]
                    stats["vwgh series numbers"] += len(extra)
            for s, e, r, typ, norm in items:
                if not norm.startswith(FOREIGN_PENAL_CODE):
                    raw.append((p["start"] + s, p["start"] + e, r, typ, norm, p["id"]))
    raw.sort(key=lambda c: (c[0], c[1]))
    out = []
    for start, end, rule, typ, norm, pid in raw:
        targets = index.get(norm, [])
        if len(targets) == 1:
            target = None if targets[0] == doc["document_id"] else targets[0]
        else:
            target = cases.case_reference(rule, norm)
        out.append({"citation_id": f"{doc['document_id']}#cit:{len(out) + 1:04d}",
                    "source_document_id": doc["document_id"], "paragraph_id": pid, "start": start, "end": end,
                    "text": doc["full_text"][start:end], "type": typ, "normalized": norm, "target_id": target,
                    "method": "rule_based", "rule": rule})
    return out, stats
