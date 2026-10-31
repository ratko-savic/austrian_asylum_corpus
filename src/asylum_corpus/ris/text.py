"""Plain text of a RIS decision XML for matching, and the case numbers and dates found in decision texts.

The RIS XML marks its blocks with a `ct` attribute (gz, betreff, spruch, begruendung, text, ...); the plain text
keeps those labels, so that a match can tell where it was found.

Case numbers (Geschäftszahlen). BVwG: "W144 2265432-1" = chamber W144, case 2265432, proceeding 1; with the
Ordnungsnummer of one document "W144 2265432-1/6E" (the letter: E decision, Z interim). VfGH: "E 2858/2025",
"E988/2025", "E 988/2025-17". Dates: "18. August 2025", "18.08.2025", "1. Jänner 2014".
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

# No leading word boundary: joined case numbers are printed without separators ("…-2/9EW233 …").
BVWG_GZ_RE = re.compile(r"(?<!\d)([WGLI]\d{3}) ?(\d{7}) ?- ?(\d{1,2})(?!\d)(?: ?/ ?(\d{1,3}[A-Z]))?")
VFGH_GZ_RE = re.compile(r"(?<![\w/])([EUBGV]) ?(\d{1,5})/(\d{4})(?:-\d+)?(?![\d/])")

MONTHS = {
    "jänner": 1, "januar": 1, "februar": 2, "feber": 2, "märz": 3, "april": 4, "mai": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "dezember": 12,
}
DATE_RE = re.compile(r"(?<!\d)(\d{1,2})\. ?(?:(\d{1,2})\. ?|(" + "|".join(MONTHS) + r") )(\d{4})(?!\d)", re.I)

_NS = "{http://www.bka.gv.at}"
_INLINE = {"gdash": "-", "tab": " ", "br": " "}
_SKIP = {"kzinhalt", "fzinhalt", "layoutdaten", "metadaten"}
_DASHES = re.compile("[‐-―−]")


def bvwg_key(match: re.Match) -> str:
    """Case number without the chamber prefix ("2265432-1"): stays the same when a case changes chamber."""
    return f"{match[2]}-{match[3]}"


def bvwg_base(match: re.Match) -> str:
    return f"{match[1]} {match[2]}-{match[3]}"


def bvwg_full(match: re.Match) -> str | None:
    """Case number with the Ordnungsnummer of one decision ("W144 2265432-1/6E"), if given."""
    return f"{bvwg_base(match)}/{match[4]}" if match[4] else None


def case_persons(case_numbers: list[str] | None) -> set[str]:
    """The persons a decision's BVwG case numbers name: one per number without chamber and proceeding number
    ("W182 2143175-2" and "2143175-2" are one person)."""
    return {re.sub(r"-\s?\d+$", "", re.sub(r"^[WGLI]\s?\d{3}\s+", "", re.sub(r"\s+", " ", c).strip())).strip()
            for c in case_numbers or []}


def vfgh_gz(match: re.Match) -> str:
    """VfGH case number without space and running number ("E2858/2025")."""
    return f"{match[1]}{match[2]}/{match[3]}"


def vfgh_gzs_of(case_numbers: list[str]) -> list[str]:
    return list(dict.fromkeys(vfgh_gz(m) for c in case_numbers for m in VFGH_GZ_RE.finditer(c)))


def find_dates(text: str) -> list[date]:
    found = []
    for m in DATE_RE.finditer(text):
        month = int(m[2]) if m[2] else MONTHS[m[3].lower()]
        try:
            found.append(date(int(m[4]), month, int(m[1])))
        except ValueError:
            continue
    return found


def normalize_space(text: str) -> str:
    text = _DASHES.sub("-", text.replace("\xad", "").replace("\xa0", " "))
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class DocumentText:
    """Plain text of a RIS decision, split into blocks labelled with the XML `ct` attribute."""

    blocks: list[tuple[str, str]]

    def __post_init__(self):
        self.spans, parts, pos = [], [], 0
        for label, text in self.blocks:
            self.spans.append((pos, pos + len(text), label))
            parts.append(text)
            pos += len(text) + 1
        self.full = "\n".join(parts)

    @classmethod
    def from_xml(cls, data: bytes) -> DocumentText:
        blocks: list[tuple[str, str]] = []

        def inline(el) -> str:
            out = [_INLINE.get(el.tag.replace(_NS, ""), ""), el.text or ""]
            for child in el:
                out.append(inline(child))
                out.append(child.tail or "")
            return "".join(out)

        def walk(el) -> None:
            tag = el.tag.replace(_NS, "")
            if tag in _SKIP:
                return
            if tag in ("absatz", "ueberschrift"):
                text = normalize_space(inline(el))
                if text:
                    blocks.append((el.get("ct") or ("heading" if tag == "ueberschrift" else "text"), text))
                return
            for child in el:
                walk(child)

        walk(ET.fromstring(data))
        return cls(blocks)

    def section(self, label: str) -> str:
        return " ".join(text for lab, text in self.blocks if lab == label)

    def label_at(self, pos: int) -> str:
        for start, end, label in self.spans:
            if start <= pos <= end:
                return label
        return "text"

    def window(self, start: int, end: int, before: int = 250, after: int = 250) -> str:
        return self.full[max(0, start - before):end + after]

    def snippet(self, start: int, end: int, before: int = 200, after: int = 200) -> str:
        s, e = max(0, start - before), min(len(self.full), end + after)
        return ("…" if s else "") + normalize_space(self.full[s:e]) + ("…" if e < len(self.full) else "")
