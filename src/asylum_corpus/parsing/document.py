"""RIS XML + RIS metadata record -> document (schemas/document.schema.json).

The metadata fields come from the unchanged RIS record; text, paragraphs and sections from the XML (parsing/xml.py,
parsing/sections.py). BVwG: the head of the decision (before 'zu Recht erkannt:' / 'beschlossen:') lists all case
numbers of joined decisions, while the RIS metadata gives only one; it also prints the case numbers with the document
suffix ('L519 2309863-1/7E') and, for orally pronounced decisions, the date of the pronouncement. A decision whose
XML holds no text at all (RIS publishes only the header fields) raises EmptySource.
"""

from __future__ import annotations

import re
from datetime import date

from ..ris.client import summarize
from ..ris.text import BVWG_GZ_RE, bvwg_base, bvwg_full, find_dates
from .sections import INTRO_RE, layout, section_types, split_intro
from .xml import EDITORIAL_FIELDS, HEADER_FIELDS, RawParagraph, read_paragraphs

COURT_BY_APP = {"Bvwg": "BVwG", "Vwgh": "VwGH", "Vfgh": "VfGH"}
# "VS": a decision of a reinforced senate (verstärkter Senat) of the VwGH
DECISION_TYPES = {"Erkenntnis": "judgment", "Beschluss": "order", "ErkenntnisVS": "judgment", "BeschlussVS": "order"}
# "mündlich verkündet" in any case ("GEKÜRZTE AUSFERTIGUNG DES AM ... MÜNDLICH VERKÜNDETEN ERKENNTNISSES") and
# "des am ... verkündeten Erkenntnisses"
PRONOUNCED_RE = re.compile(r"(?i)mündlich\s+verkündet|\bam\s+[\d.\sA-Za-zäÄ]{6,20}?\d{4}\s+verkündete[nm]?\b")


class EmptySource(ValueError):
    """The RIS XML holds no decision text (only the header fields)."""


def document_id(court: str, ris_id: str) -> str:
    return f"ris:{court.lower()}:{ris_id}"


def document_metadata(record: dict) -> dict:
    """RIS ID, court, ECLI, case numbers, decision date and type (normalised), public URL, Data.Metadaten."""
    s = summarize(record)
    if s["decision_type"] not in DECISION_TYPES:
        raise ValueError(f"{s['ris_id']}: unknown Entscheidungsart {s['decision_type']!r}")
    court = COURT_BY_APP[s["application"]]
    return {
        "document_id": document_id(court, s["ris_id"]), "ris_id": s["ris_id"], "court": court, "ecli": s["ecli"],
        "case_numbers": s["case_numbers"], "decision_date": s["decision_date"],
        "decision_type": DECISION_TYPES[s["decision_type"]], "source_url": s["document_url"],
        "ris_metadata": record["Data"]["Metadaten"],
    }


def read_head(paragraphs: list[dict], sections: list[dict], ris_case_numbers: list[str]
               ) -> tuple[list[str], list[str], date | None]:
    """(case_numbers, document_numbers, pronouncement_date) from the first subject section."""
    head = [p for p in paragraphs if p["section_id"] == sections[0]["id"]] if sections[0]["type"] == "subject" else []
    before_intro = [p for p in head if not INTRO_RE.search(p["text"])]
    case_numbers, document_numbers = list(dict.fromkeys(ris_case_numbers)), []
    for p in before_intro:
        for m in BVWG_GZ_RE.finditer(p["text"]):
            case_numbers.append(bvwg_base(m))
            if full := bvwg_full(m):
                document_numbers.append(full)
    pronounced = next((find_dates(p["text"])[0] for p in before_intro
                       if PRONOUNCED_RE.search(p["text"]) and find_dates(p["text"])), None)
    return list(dict.fromkeys(case_numbers)), list(dict.fromkeys(document_numbers)), pronounced


def build_document(xml: bytes, record: dict) -> dict:
    """The document of one decision."""
    meta = document_metadata(record)
    raw = [p for p in read_paragraphs(xml) if p.field not in HEADER_FIELDS | EDITORIAL_FIELDS]
    if not raw:
        raise EmptySource(f"{meta['ris_id']}: no decision text in the RIS XML (only the header fields)")
    raw = split_intro(raw, meta["court"],
                      lambda p, text, first: RawParagraph(p.field, p.label, text, p.marginal_number if first else None))
    sections, paragraphs, full_text = layout(raw, section_types(raw, meta["court"]), meta["document_id"])
    case_numbers, document_numbers, pronounced = list(dict.fromkeys(meta["case_numbers"])), [], None
    if meta["court"] == "BVwG":
        case_numbers, document_numbers, pronounced = read_head(paragraphs, sections, meta["case_numbers"])
    return {
        "document_id": meta["document_id"],
        "ris_id": meta["ris_id"],
        "court": meta["court"],
        "ecli": meta["ecli"],
        "case_number": meta["case_numbers"][0],
        "case_numbers": case_numbers,
        "document_numbers": document_numbers,
        "decision_date": meta["decision_date"],
        "pronouncement_date": pronounced.isoformat() if pronounced else None,
        "decision_type": meta["decision_type"],
        "source_url": meta["source_url"],
        "ris_metadata": meta["ris_metadata"],
        "full_text": full_text,
        "sections": sections,
        "paragraphs": paragraphs,
    }
