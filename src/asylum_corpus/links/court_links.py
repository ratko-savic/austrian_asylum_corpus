"""Relations between BVwG, VwGH and VfGH decisions (schemas/relation.schema.json; rules config/links/court_links.yaml).

The connected step of the download found, for each VwGH and VfGH decision of its pool, the BVwG decisions it names,
and identified the reviewed one where it could (connections.jsonl). These matches become relations:

    reviews         VwGH/VfGH decision -> the BVwG decision under review, with a match type from the connected step's
                    confidence; for a joined BVwG decision the copy whose own case number the higher court names
    transferred_to  VfGH decision (or case:vfgh:<number>) -> the VwGH decision that decided the transferred complaint
    same_case       BVwG decision -> the next later BVwG decision with the same own case number (chamber aside)

Every relation carries evidence: the passage of a document where the identifier (or, for the date fallback, the dates)
stands, with its paragraph ID and offsets. A match that fails one of the checks of the rules file stays unresolved,
with the reason.
"""

from __future__ import annotations

import bisect
import json
import re
from datetime import date
from pathlib import Path

import yaml

MONTHS = {1: ["Jänner", "Januar"], 2: ["Februar", "Feber"], 3: ["März"], 4: ["April"], 5: ["Mai"], 6: ["Juni"],
          7: ["Juli"], 8: ["August"], 9: ["September"], 10: ["Oktober"], 11: ["November"], 12: ["Dezember"]}
PREFIX = re.compile(r"^[A-Z]\s?\d{3}\s+")
STRENGTH = ["exact_identifier", "identifier_and_date", "identifier_only", "date_fallback"]   # strongest first
BFA_DATE = re.compile(r"BFA Bescheid date (\d{4}-\d{2}-\d{2})")
DATE_FALLBACK_PATTERNS = ("decision_before", "decision_after", "higher_after", "higher_before", "near",
                          "bescheid_before", "sentence_end", "granted", "refused")


def load_config(path: Path) -> dict:
    """The rules, with their compiled patterns."""
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    spec["_head_number"] = re.compile(spec["head_number"], re.M)
    spec["_df"] = {k: re.compile(spec["date_fallback"][k]) for k in DATE_FALLBACK_PATTERNS}
    return spec


def key(case_number: str) -> str:
    """A BVwG case number without the chamber prefix and the document suffix ("W150 2159588-2/7E" -> "2159588-2"):
    the case keeps its number when it changes chamber."""
    return PREFIX.sub("", case_number.strip()).split("/")[0].replace(" ", "")


def file_number(case_number: str) -> str:
    """The seven digits of a BVwG case number: one person's file across proceedings ("W192 2180308-2" -> "2180308")."""
    return key(case_number).split("-")[0]


def ordnungsnummer(number: str) -> str | None:
    """The number of one document of a case ("L508 1422401-1/9E" -> "9E"), or None."""
    return number.rsplit("/", 1)[1].replace(" ", "") if "/" in number else None


def own_ordnungsnummer(info: dict, case_key: str) -> str | None:
    """The candidate's own document number for this case number: its first document number with that case number,
    else the number printed at the head of its text with the same file number."""
    for n in info.get("document_numbers") or []:
        if key(n) == case_key and ordnungsnummer(n):
            return ordnungsnummer(n)
    head = info.get("head_number")
    if head and head[0] == case_key.split("-")[0]:
        return head[1]
    return None


def vfgh_years(case_numbers: list[str], registers: tuple[str, ...] | None) -> list[int]:
    """The years of the VfGH case numbers of these registers (None: any register; "E 3608/2021 ua" -> 2021)."""
    out = []
    for n in case_numbers or []:
        m = re.match(r"\s*([A-Z]+)\s?\d+(?:\s?-\s?\d+)?\s?/\s?(\d{4})", n)
        if m and (registers is None or m[1] in registers):
            out.append(int(m[2]))
    return out


def one_complaint(case_numbers: list[str]) -> bool:
    """A VfGH decision on one complaint: one case number, not a range ("E 1944-1948/2025") and not "ua" (and others)."""
    return len(case_numbers) == 1 and not re.search(r"\bua\b|u\.\s?a\.|^\s*[A-Z]+\s?\d+\s?-\s?\d+\s?/", case_numbers[0])


def years_of(info: dict) -> set[int]:
    return {int(d[:4]) for d in (info["decision_date"], info.get("pronouncement_date")) if d}


def fits_year(year: int, info: dict, after: int, before_too: bool = True) -> bool:
    """A VfGH case number of this year can concern this BVwG decision: registered in its year or up to `after` years
    later (with before_too False, only "not more than `after` years later")."""
    return any((y <= year or not before_too) and year <= y + after for y in years_of(info))


def identifier_pattern(identifier: str) -> re.Pattern:
    """The identifier as printed, spaces optional between its parts ("L508 1422401-1/9E", "E 76/2014")."""
    tokens = re.findall(r"[A-Za-z]+|\d+|[-/.]", identifier)
    return re.compile(r"(?<![\d/])" + r"\s?".join(re.escape(t) for t in tokens) + r"(?![\d])")


def date_pattern(iso: str) -> re.Pattern:
    d = date.fromisoformat(iso)
    names = "|".join(MONTHS[d.month])
    return re.compile(rf"(?<!\d)0?{d.day}\.\s?(?:(?:{names})\s?|0?{d.month}\.\s?){d.year}(?!\d)")


class Doc:
    """The parts of a document the evidence needs."""

    def __init__(self, doc: dict):
        self.document_id, self.text = doc["document_id"], doc["full_text"]
        self.paragraphs = doc["paragraphs"]
        self.starts = [p["start"] for p in self.paragraphs]
        self.section_of = [(s["start"], s["end"], s["type"]) for s in doc["sections"] if s.get("level", 1) == 1]

    def paragraph_at(self, pos: int) -> dict:
        return self.paragraphs[max(0, bisect.bisect_right(self.starts, pos) - 1)]

    def section_type(self, pos: int) -> str:
        for s, e, t in self.section_of:
            if s <= pos < e:
                return t
        return ""

    def evidence(self, pattern: re.Pattern, before: int, after: int,
                 prefer: tuple[str, ...] = ("subject", "operative_part")) -> dict | None:
        """The first occurrence of the pattern, in a preferred section if there is one there, as evidence."""
        hits = list(pattern.finditer(self.text))
        if not hits:
            return None
        return self.span(next((h for h in hits if self.section_type(h.start()) in prefer), hits[0]), before, after)

    def span(self, m: re.Match, before: int, after: int) -> dict:
        """The evidence for one match: its paragraph, cut to `before` and `after` characters at word boundaries."""
        p = self.paragraph_at(m.start())
        s, e = max(p["start"], m.start() - before), min(p["end"], m.end() + after)
        while s > p["start"] and not self.text[s - 1].isspace() and m.start() - s < before + 30:
            s -= 1
        while e < p["end"] and not self.text[e].isspace() and e - m.end() < after + 30:
            e += 1
        return {"document_id": self.document_id, "paragraph_id": p["id"], "start": s, "end": e,
                "quote": self.text[s:e]}


def relation(rel_type: str, source: str, target: str, evidence: list[dict], rule: str, match_type: str) -> dict:
    return {"relation_id": f"rel:{source}--{rel_type}--{target}", "relation_type": rel_type, "source_id": source,
            "target_id": target, "evidence": evidence, "method": "rule_based", "rule": rule, "match_type": match_type}


def unresolved_of(doc: Doc, m: dict, reason: str, candidates: list[str], withheld: str | None = None) -> dict:
    """An unresolved match; for a failed check, `withheld` is the ID of the relation it would have been."""
    u = {"source_id": doc.document_id, "method": m["method"], "reason": reason,
         "bvwg_case_number": m["bvwg_case_number"], "candidates": candidates, "how": m["how"]}
    return u | {"withheld_relation_id": withheld} if withheld else u


def reviews_id(doc: Doc, target: dict) -> str:
    return f"rel:{doc.document_id}--reviews--{target['document_id']}"


def dated_next_to(doc: Doc, case_number: str, isos: list[str]) -> bool:
    """One of these dates stands next to the cited case number (250 characters before, 40 after, as the connected
    step reads cited dates)."""
    patterns = [date_pattern(iso) for iso in isos if iso]
    return any(p.search(doc.text[max(0, m.start() - 250):m.end() + 40])
               for m in identifier_pattern(case_number).finditer(doc.text) for p in patterns)


def pronounced_next_to(doc: Doc, case_number: str, target: dict) -> bool:
    """The target's oral pronouncement date (other than its decision date) stands next to the cited case number: the
    higher court names the oral decision, often by the number of its minutes or short copy ("/8Z"), and the target is
    its written copy."""
    iso = target.get("pronouncement_date")
    if not iso or iso == target["decision_date"]:
        return False
    return dated_next_to(doc, case_number, [iso])


def copies_of_one_decision(cands: list[dict]) -> bool:
    """The candidates are the copies of one joined decision: one date and the same document numbers (RIS publishes a
    joined decision once per appellant)."""
    return len(cands) > 1 and bool(cands[0].get("document_numbers")) \
        and len({c["decision_date"] for c in cands}) == 1 \
        and len({tuple(sorted(c.get("document_numbers") or [])) for c in cands}) == 1


def joined_matches(row: dict, doc: Doc, m: dict, bvwg: dict) -> list[dict]:
    """An ambiguous match whose candidates are the copies of one joined decision, as matches of the copies to link;
    empty: it stays ambiguous."""
    cands = [bvwg[c] for c in m["bvwg_candidates"] if c in bvwg]
    if len(cands) < len(m["bvwg_candidates"]) or not copies_of_one_decision(cands):
        return []
    if m["method"] == "bvwg_case_number":       # the copy of the named case number, with its date next to it
        want = key(m["bvwg_case_number"])
        own = [c for c in cands if key(c["case_number"]) == want]
        dates = [own[0]["decision_date"], own[0].get("pronouncement_date")] if own else []
        if own and dated_next_to(doc, m["bvwg_case_number"], dates):
            return [m | {"bvwg_ris_id": own[0]["ris_id"], "confidence": "medium",
                         "rule": "connected:bvwg_case_number:ambiguous+own_copy_and_date"}]
    if m["method"] == "date_fallback" and not one_complaint(row.get("case_numbers") or []):   # every copy
        return [m | {"bvwg_ris_id": c["ris_id"], "confidence": "medium", "bfa_dates": c.get("bfa_dates") or [],
                     "rule": "connected:date_fallback:ambiguous+joined_copies"} for c in cands]
    return []


def decision_date_evidence(doc: Doc, target: dict, cfg: dict) -> dict | None:
    """Date fallback, check 2: the first passage where the target's decision or pronouncement date stands as the date
    of a decision (not of a VfGH or VwGH decision), near "Bundesverwaltungsgericht"."""
    df, rx = cfg["date_fallback"], cfg["_df"]
    for iso in [target["decision_date"], target.get("pronouncement_date")]:
        if not iso:
            continue
        for m in date_pattern(iso).finditer(doc.text):
            if not rx["near"].search(doc.text[max(0, m.start() - df["near_chars"]):m.end() + df["near_chars"]]):
                continue
            before, after = doc.text[max(0, m.start() - 100):m.start()], doc.text[m.end():m.end() + 80]
            if rx["higher_after"].search(after) or rx["higher_before"].search(before):
                continue
            if rx["decision_before"].search(before) or rx["decision_after"].search(after):
                return doc.span(m, cfg["evidence_before"], cfg["evidence_after"])
    return None


def bescheid_date_evidence(doc: Doc, iso: str, cfg: dict) -> dict | None:
    """Date fallback, check 3: the first passage where the BFA date stands as the date of a Bescheid, in one
    sentence."""
    rx = cfg["_df"]
    for m in date_pattern(iso).finditer(doc.text):
        before = rx["sentence_end"].split(doc.text[max(0, m.start() - 160):m.start()])[-1]
        if rx["bescheid_before"].search(before):
            return doc.span(m, cfg["evidence_before"], cfg["evidence_after"])
    return None


def date_fallback_check(row: dict, doc: Doc, target: dict, m: dict, cfg: dict) -> tuple[str | None, list[dict]]:
    """Checks 1-5 of a date_fallback match: the reason it fails, or None and its evidence."""
    df = cfg["date_fallback"]
    years = vfgh_years(row.get("case_numbers") or [], tuple(df["registers"]))
    if not years:
        return "date fallback: not a complaint against a decision (register not E)", []
    date_ev = decision_date_evidence(doc, target, cfg)
    if date_ev is None:
        return "date fallback: the date is not given as a BVwG decision's date", []
    bfa = BFA_DATE.search(m.get("how") or "")
    bfa_ev = None
    for iso in [bfa[1]] if bfa else m.get("bfa_dates") or []:     # a joined decision's copy: its BFA dates
        bfa_ev = bfa_ev or bescheid_date_evidence(doc, iso, cfg)
    if bfa_ev is None:
        return "date fallback: the BFA date is not given as a Bescheid date", []
    if not any(fits_year(y, target, df["years_after"], df["check_years_before"]) for y in years):
        return "date fallback: the VfGH case number's year does not fit the decision", []
    if target.get("granted_in_full"):
        return "date fallback: the decision granted asylum in full", []
    return None, [date_ev, bfa_ev] if bfa_ev != date_ev else [date_ev]


def reviews_of(row: dict, doc: Doc, bvwg: dict, cfg: dict) -> tuple[list[dict], list[dict], dict]:
    """The reviews relations of one connected VwGH/VfGH decision, its unresolved matches, and counts by outcome."""
    rels, unresolved, counts, pending = {}, [], {}, []

    def count(k):
        counts[k] = counts.get(k, 0) + 1

    def keep(r: dict, match_type: str):
        old = rels.get(r["relation_id"])
        if old is None or STRENGTH.index(match_type) < STRENGTH.index(old["match_type"]):
            rels[r["relation_id"]] = r
        count(f"relation {match_type}")

    todo = list(row["matches"])
    for m in todo:
        method, conf = m["method"], m["confidence"]
        if m["mention"] != "challenged":
            count("cited, not a review")
            continue
        if method == "later_bvwg_cites_vfgh":
            count("inferred from a later BVwG citation (not used)")
            continue
        if not m["bvwg_ris_id"]:
            copies = joined_matches(row, doc, m, bvwg) if m["bvwg_candidates"] else []
            if copies:                     # the copies of one joined decision: checked below like any match
                count(f"ambiguous, copies of one joined decision ({method})")
                todo += copies
                continue
            reason = "ambiguous" if m["bvwg_candidates"] else "no candidate in the corpus"
            count(reason)
            unresolved.append(unresolved_of(doc, m, reason, m["bvwg_candidates"]))
            continue
        match_type = (cfg["reviews"].get(method) or {}).get(conf)
        if not match_type:
            count(f"confidence {conf} of {method} not mapped")
            continue
        target = bvwg.get(m["bvwg_ris_id"])
        if target is None:
            count("identified decision not in the parsed corpus")
            continue
        if target["decision_date"] > row["decision_date"]:     # a review cannot precede the decision reviewed
            count("identified decision dated after the higher-court decision")
            unresolved.append(unresolved_of(doc, m, "identified decision dated after the review", [m["bvwg_ris_id"]]))
            continue
        if method == "bvwg_case_number":   # a joined decision: the copy of the case number named
            want = key(m["bvwg_case_number"])
            own = [bvwg[c] for c in m["bvwg_candidates"] if c in bvwg and bvwg[c]["decision_date"] ==
                   target["decision_date"] and key(bvwg[c]["case_number"]) == want]
            if own:
                target = own[0]
        rule = m.get("rule") or f"connected:{method}:{conf}"
        if match_type == "identifier_only":
            if pronounced_next_to(doc, m["bvwg_case_number"], target):
                # the number of the oral decision and its pronouncement date: the target is its written copy
                match_type, rule = "identifier_and_date", rule + "+pronouncement_date"
            else:
                cited = ordnungsnummer(m["bvwg_case_number"])
                own_no = own_ordnungsnummer(target, key(m["bvwg_case_number"]))
                if cited and own_no and cited.upper() != own_no.upper():
                    reason = "identifier only: the cited Ordnungsnummer is not the candidate's own"
                    count(reason)
                    unresolved.append(unresolved_of(doc, m, reason, [target["ris_id"]], reviews_id(doc, target)))
                    continue
        if method == "bvwg_case_number":
            ev = doc.evidence(identifier_pattern(m["bvwg_case_number"]), cfg["evidence_before"], cfg["evidence_after"])
            if ev is None:
                ev = doc.evidence(identifier_pattern(m["bvwg_case_number"].split("/")[0]), cfg["evidence_before"],
                                  cfg["evidence_after"])
            evidence = [ev] if ev else []
        else:                              # checks 1-5; the evidence: the date and the BFA date in their phrases
            reason, evidence = date_fallback_check(row, doc, target, m, cfg)
            if reason:
                count(reason)
                unresolved.append(unresolved_of(doc, m, reason, [target["ris_id"]], reviews_id(doc, target)))
                continue
        if not evidence:
            count("no evidence span in the text")
            unresolved.append(unresolved_of(doc, m, "no evidence span", [m["bvwg_ris_id"]]))
            continue
        r = relation("reviews", doc.document_id, target["document_id"], evidence, rule, match_type)
        if match_type == "date_fallback":
            pending.append((r, target, m))
        else:
            keep(r, match_type)
    # date fallback, check 6: of one person's file (of the whole source, when it decides one complaint), only the
    # latest decision the checks leave is the one under review
    latest: dict[str, str] = {}
    single = one_complaint(row.get("case_numbers") or [])

    def when(t: dict) -> str:
        return t.get("pronouncement_date") or t["decision_date"]

    def group(t: dict) -> str:
        return "" if single else file_number(t["case_number"])

    for _, target, _ in pending:
        latest[group(target)] = max(latest.get(group(target), ""), when(target))
    for r, target, m in pending:
        if when(target) < latest[group(target)]:
            reason = "date fallback: an earlier decision (same file, or one complaint) than the one under review"
            count(reason)
            unresolved.append(unresolved_of(doc, m, reason, [target["ris_id"]], r["relation_id"]))
            continue
        keep(r, "date_fallback")
    return list(rels.values()), unresolved, counts


def transfers_of(row: dict, doc: Doc, vfgh_by_number: dict, cfg: dict, reviewed: list[dict] | None = None
                 ) -> tuple[list[dict], list[dict], dict]:
    """The transferred_to relations named by one VwGH decision; a VfGH number whose year does not fit the BVwG
    decision(s) the VwGH decision reviews (`reviewed`) is unresolved."""
    out, unresolved, counts = [], [], {}
    after = cfg["transfer_years_after"]
    for number in row.get("transfers_named") or []:
        ev = doc.evidence(identifier_pattern(number), cfg["evidence_before"], cfg["evidence_after"], prefer=())
        if ev is None:
            continue
        source = vfgh_by_number.get(number.replace(" ", "")) or f"case:vfgh:{number}"
        years = vfgh_years([number], None)
        if reviewed and years and not any(fits_year(years[0], t, after) for t in reviewed):
            reason = "transfer: the VfGH case number's year does not fit the BVwG decision under review"
            counts[reason] = counts.get(reason, 0) + 1
            unresolved.append({"source_id": doc.document_id, "method": "transfers_named", "reason": reason,
                               "bvwg_case_number": None, "vfgh_case_number": number,
                               "candidates": sorted(t["ris_id"] for t in reviewed),
                               "how": f"VfGH {number}; reviewed BVwG decision(s) of "
                                      + ", ".join(sorted(t["decision_date"] for t in reviewed)),
                               "withheld_relation_id": f"rel:{source}--transferred_to--{doc.document_id}"})
            continue
        out.append(relation("transferred_to", source, doc.document_id, [ev], "connected:transfers_named",
                            "exact_identifier"))
        counts["transfer relation"] = counts.get("transfer relation", 0) + 1
    return out, unresolved, counts


def same_case(bvwg: dict) -> list[dict]:
    """Each BVwG decision -> the decisions of the next later date with the same own case number."""
    by_key: dict[str, dict[str, list]] = {}
    for info in bvwg.values():
        by_key.setdefault(key(info["case_number"]), {}).setdefault(info["decision_date"], []).append(info)
    out = []
    for by_date in by_key.values():
        dates = sorted(by_date)
        for a, b in zip(dates, dates[1:]):
            for src in sorted(by_date[a], key=lambda i: i["ris_id"]):
                for tgt in sorted(by_date[b], key=lambda i: i["ris_id"]):
                    ev = [e for e in (src["own_evidence"], tgt["own_evidence"]) if e]
                    if ev:
                        out.append(relation("same_case", src["document_id"], tgt["document_id"], ev,
                                            "same_own_case_number", "exact_identifier"))
    return out


def bvwg_info(doc: dict, cfg: dict) -> dict:
    """What the links need of a BVwG document: its identifiers and dates, the evidence of its own case number, its own
    document number at the head of the text, and whether it granted asylum in full (date fallback, check 5)."""
    d = Doc(doc)
    ev = d.evidence(identifier_pattern(doc["case_number"]), 0, 40) if doc.get("case_number") else None
    head = cfg["_head_number"].search(doc["full_text"][:300])
    rx = cfg["_df"]
    op = " ".join(doc["full_text"][s["start"]:s["end"]] for s in doc["sections"]
                  if s["type"] == "operative_part" and s.get("level", 1) == 1)
    return {"ris_id": doc["ris_id"], "document_id": doc["document_id"], "case_number": doc["case_number"] or "",
            "decision_date": doc["decision_date"], "pronouncement_date": doc["pronouncement_date"], "own_evidence": ev,
            "document_numbers": doc.get("document_numbers") or [],
            "head_number": [head[1], head[2].replace(" ", "")] if head else None,
            "granted_in_full": bool(rx["granted"].search(op)) and not rx["refused"].search(op)}


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
