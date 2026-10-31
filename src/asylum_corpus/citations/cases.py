"""Citations of decisions and other identifiers, their normalized forms, and the link to a cited decision
(`target_id`). extract.find applies every pattern of PATTERNS together with the provision chains and keeps, of
overlapping matches, the one that starts first, then the longest. Some patterns need their court within 200
characters before or 80 after the match (CONTEXT); one must not follow certain words (EXCLUDE_BEFORE). The rules:

- A VwGH number series takes the prefix of its full number with a register ("Ra 2014/20/0017, 0018" -> two
  citations); a four-digit number starting with 19 or 20 is a year and ends it.
- Old VwGH numbers ("2008/19/0123"), VfGH numbers and ECtHR application numbers need their court nearby: without it
  the bare number is ambiguous.
- VfSlg: one citation per volume number ("VfSlg 13.836/1994, 14.650/1996"; extract.py).
- Law-gazette numbers get one form for all their printed forms ("BGBl I 33/2013", "BGBl. I 2013/33" -> "BGBl. I Nr.
  33/2013"), type publication_reference.
- EU act numbers after a name, an abbreviation or alone ("Aufnahmerichtlinie 2013/33/EU", "Status-RL 2011/95/EU",
  "(2011/95/EU)"): the form year/number/EU is unique to EU acts. Each number gets one spelling (eu_act), with
  "Verordnung" / "Richtlinie" where the text names the kind; a bare number stays without it.
- "Rs" before an ECtHR application number of at least four digits ("Rs 35989/03" -> EGMR 35989/03): old CJEU numbers
  ("Rs 283/81") have up to three. Not after a digit ("7 Rs 82/18" is an Austrian court's file), and not within 80
  characters after "EuGH".

Known limits: "Rs" before a number of up to three digits is not read; an EU act named by its number and by its short
name stays two strings ("Verordnung (EU) Nr. 604/2013 Art. 17", "Dublin III-VO Art. 17"): a number names one version
of an act, a short name may name several.
"""

from __future__ import annotations

import re
from collections import defaultdict

from ..ris.text import BVWG_GZ_RE, bvwg_base, bvwg_full

# Law-gazette numbers: the plain form first, then the other printed forms
GAZETTE = re.compile(r"BGBl\.?\s?(?:I{1,3}\s?)?Nr\.?\s?\d+/\d{4}"
                     r"|(?<![\w])BGB[lLI]\.?\s?(?:I{1,3}\.?\s?)?(?:Nr\.?\s?)?"
                     r"(?:\d+/(?:18|19|20)\d{2}|(?:19|20)\d{2}/\d+)"
                     r"(?![\d/])")
GAZETTE_PARTS = re.compile(r"BGB[lLI]\.?\s?(?:(?P<part>I{1,3})\.?\s?)?(?:Nr\.?\s?)?"
                           r"(?:(?P<n>\d+)/(?P<y>\d{4})(?![\d/])|(?P<y2>(?:19|20)\d{2})/(?P<n2>\d+))")
EU_NUMBER = (r"(?:Verordnung|VO|Richtlinie|RL)\s?\((?:EU|EG|EWG)\)\s?(?:Nr\.?\s?)?\d{2,4}/\d{1,4}"
             r"|Richtlinie\s?\d{2,4}/\d{1,4}/(?:EU|EG|EWG)|\b3\d{4}[RL]\d{4}\b|\bRL\s?\d{2,4}/\d{1,4}/(?:EU|EG|EWG)\b")
EU_NAME = re.compile(r"\b[\wäöüÄÖÜß-]*(?:richtlinie|verordnung)\s?(?:\((?:EU|EG|EWG)\)\s?(?:Nr\.?\s?)?\d{2,4}/\d{1,4}"
                     r"|\d{2,4}/\d{1,4}/(?:EU|EG|EWG)\b)"
                     r"|\b[\wäöüÄÖÜß-]*(?:RL|VO)\s?\d{2,4}/\d{1,4}/(?:EU|EG|EWG)\b")
EU_BARE = re.compile(r"(?<![\w/])\d{2,4}/\d{1,4}/(?:EU|EG|EWG)\b")
EU_PARTS = re.compile(r"\((?P<org>EU|EG|EWG)\)\s?(?:Nr\.?\s?)?(?P<a>\d{2,4})/(?P<b>\d{1,4})"
                      r"|(?P<y>\d{2,4})/(?P<n>\d{1,4})/(?P<org2>EU|EG|EWG)")
YEAR = re.compile(r"(?:19|20)\d{2}")

PATTERNS = [  # (rule, type, regex)
    ("eu_act_number", "eu_legislation",
     re.compile(r"(?:Verordnung|VO|Richtlinie|RL)\s?\((?:EU|EG|EWG)\)\s?(?:Nr\.?\s?)?\d{2,4}/\d{1,4}"
                r"|Richtlinie\s?\d{2,4}/\d{1,4}/(?:EU|EG|EWG)|\b3\d{4}[RL]\d{4}\b")),
    ("bgbl", "publication_reference", GAZETTE),
    ("vwgh_case_number", "national_case", re.compile(r"(?<![\w/])(?:Ra|Ro|Fr|Fe|Rv)\s?\d{4}/\d{2}/\d{4}(?![\d/])")),
    ("vwgh_case_number_old", "national_case", re.compile(r"(?<![\w/.])(?:\d{4}|\d{2})/\d{2}/\d{4}(?![\d/])")),
    ("vfgh_case_number", "national_case", re.compile(r"(?<![\w/])[EUBGV]\s?\d{1,5}/\d{4}(?:-\d+)?(?![\d/])")),
    ("vfslg", "national_case", re.compile(r"VfSlg\.?\s?\d{1,2}\.\d{3}/\d{4}(?:\s?(?:,|und)\s?\d{1,2}\.\d{3}/\d{4})*")),
    ("bvwg_case_number", "national_case", BVWG_GZ_RE),
    ("ecli", None, re.compile(r"ECLI:[A-Z]{2}:[A-Z]+:\d{4}:[\w.]+")),
    ("cjeu_case_number", "cjeu_case", re.compile(r"(?<![\w-])C[-‑]\s?\d{1,4}/\d{2}(?![\d/])")),
    ("ecthr_application_number", "ecthr_case",
     re.compile(r"(?:Bsw\.?|Beschwerde-?Nr\.?|Appl(?:ication)?\.?|no\.|Nr\.)\s?(?:Nr\.?\s?)?"
                r"\d{1,3}(?:\.?\d{3})?/\d{2}(?![\d/])")),
    ("eu_act_number_name", "eu_legislation", EU_NAME),
    ("eu_act_number_bare", "eu_legislation", EU_BARE),
    ("ecthr_application_number_rs", "ecthr_case",
     re.compile(r"(?<!\d\s)\bRs\.?\s?(?:\d{4,6}|\d{1,3}\.\d{3})/\d{2}(?![\d/])")),
]
CONTEXT = {  # rule -> regex that must occur within 200 characters before or 80 after the match
    "vwgh_case_number_old": re.compile(r"VwGH|Verwaltungsgerichtshof"),
    "vfgh_case_number": re.compile(r"VfGH|Verfassungsgerichtshof", re.I),
    "ecthr_application_number": re.compile(r"EGMR|Menschenrechte|ECHR|EKMR"),
}
EXCLUDE_BEFORE = {  # rule -> regex that must not occur within 80 characters before the match
    "ecthr_application_number_rs": re.compile(r"EuGH|Gerichtshof der Europäischen Union"),
}
SERIES = re.compile(r"\s?(?:,|und)\s?(\d{4})(?![\d/])")
EU_RULES = ("eu_act_number", "eu_act_number_name", "eu_act_number_bare")


def eu_act(text: str) -> str:
    """One spelling of an EU act number: "Nr." before number/year (the numbering before 2015), none before
    year/number; "Verordnung" / "Richtlinie" for "VO" / "RL"; a CELEX number as printed."""
    text = re.sub(r"\s+", " ", text).strip()
    m = EU_PARTS.search(text)
    if m is None or re.fullmatch(r"3\d{4}[RL]\d{4}", text):
        return text
    head = text[:m.start()]
    kind = "Verordnung" if re.search(r"(?i)verordnung|VO\s?$", head) else \
        "Richtlinie" if re.search(r"(?i)richtlinie|RL\s?$", head) else ""
    if m["org"]:
        a, b = m["a"], m["b"]
        number = f"{a}/{b}" if YEAR.fullmatch(a) and not YEAR.fullmatch(b) else f"Nr. {a}/{b}"
        return f"{kind} ({m['org']}) {number}".strip()
    return f"{kind} {m['y']}/{m['n']}/{m['org2']}".strip()


def gazette(text: str) -> str:
    """'BGBl. [I ]Nr. <number>/<year>' for every printed form of a law-gazette number."""
    g = GAZETTE_PARTS.match(text)
    number, year = (g["n"], g["y"]) if g["n"] else (g["n2"], g["y2"])
    return f"BGBl. {g['part'] + ' ' if g['part'] else ''}Nr. {number}/{year}"


def normalize(rule: str, m: re.Match) -> tuple[str, str]:
    """(citation type, normalized form) of an identifier match."""
    text = m.group(0)
    if rule == "vwgh_case_number":
        return "national_case", "VwGH " + re.sub(r"^(Ra|Ro|Fr|Fe|Rv)\s?", r"\1 ", text)
    if rule == "vwgh_case_number_old":
        return "national_case", f"VwGH {text}"
    if rule == "vfgh_case_number":
        g = re.match(r"([EUBGV])\s?(\d+)/(\d{4})", text)
        return "national_case", f"VfGH {g[1]} {g[2]}/{g[3]}"
    if rule == "bvwg_case_number":
        return "national_case", f"BVwG {bvwg_full(m) or bvwg_base(m)}"
    if rule == "ecli":
        return {"EU": "cjeu_case", "CE": "ecthr_case"}.get(text.split(":")[1], "national_case"), text
    if rule == "cjeu_case_number":
        return "cjeu_case", "EuGH " + re.sub(r"C[-‑]\s?", "C-", text)
    if rule in ("ecthr_application_number", "ecthr_application_number_rs"):
        g = re.search(r"(\d{1,3}\.?\d{0,3})/(\d{2})$", text)
        return "ecthr_case", f"EGMR {g[1].replace('.', '')}/{g[2]}"
    if rule == "bgbl":
        return "publication_reference", gazette(text)
    if rule in EU_RULES:
        return "eu_legislation", eu_act(text)
    raise ValueError(f"no normalization for rule {rule!r}")


def series(text: str, end: int, normalized: str) -> list[tuple[int, int, str]]:
    """Further numbers of a VwGH number series after a match ending at `end`: (start, end, normalized)."""
    prefix = normalized.rsplit("/", 1)[0]
    out, pos = [], end
    while (m := SERIES.match(text, pos)) and not m[1].startswith(("19", "20")):
        out.append((m.start(1), m.end(1), f"{prefix}/{m[1]}"))
        pos = m.end()
    return out


def case_reference(rule: str, normalized: str) -> str | None:
    """case:<court>:<case number as spelled in RIS> for a cited Austrian case number without a (unique) document in the
    corpus; a BVwG case number names the case, not one of its decisions."""
    if rule in ("vwgh_case_number", "vwgh_case_number_old", "vwgh_case_number_series"):
        return "case:vwgh:" + normalized.removeprefix("VwGH ")
    if rule == "vfgh_case_number":
        return "case:vfgh:" + normalized.removeprefix("VfGH ").replace(" ", "")
    if rule == "bvwg_case_number":
        return "case:bvwg:" + normalized.removeprefix("BVwG ").split("/")[0]
    return None


def index_keys(doc: dict) -> set[str]:
    """The normalized forms under which a corpus document can be cited: VwGH / VfGH case numbers, BVwG document
    numbers (a bare BVwG case number names the case), the ECLI."""
    keys = set()
    court = doc["court"]
    for gz in doc["case_numbers"]:
        if court == "VwGH":
            keys.add("VwGH " + gz)
        elif court == "VfGH":
            keys.update(f"VfGH {g[1]} {g[2]}/{g[3]}" for g in re.finditer(r"([EUBGV])\s?(\d+)/(\d{4})", gz))
    keys.update(f"BVwG {n}" for n in doc["document_numbers"])
    if doc.get("ecli"):
        keys.add(doc["ecli"])
    return keys


def build_index(keyed: list[tuple[str, set[str]]]) -> dict[str, list[str]]:
    """Index key -> the documents it names."""
    index: dict[str, list[str]] = defaultdict(list)
    for document_id, keys in keyed:
        for k in keys:
            index[k].append(document_id)
    return dict(index)


def prefer_own_case(index: dict[str, list[str]], case_number_of: dict[str, str]) -> dict[str, list[str]]:
    """Joined decisions: RIS publishes a decision on several cases once per case, each copy with the same text and all
    the document numbers, so a BVwG document number is a key of every copy. It names the copy published under its own
    case number ("BVwG G301 2238544-1/12E" -> the document whose case number is "G301 2238544-1") if exactly one copy
    is; otherwise the key stays ambiguous (and the citation gets a case reference)."""
    out = {}
    for key, docs in index.items():
        if len(docs) > 1 and key.startswith("BVwG "):
            own = [d for d in docs if case_number_of.get(d) == key.removeprefix("BVwG ").split("/")[0]]
            if len(own) == 1:
                out[key] = own
                continue
        out[key] = docs
    return out
