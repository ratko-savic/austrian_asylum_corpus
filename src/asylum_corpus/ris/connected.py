"""VwGH and VfGH decisions connected to the BVwG candidates.

A BVwG decision cannot name its own later review, so connected decisions are found from the other side: the VwGH and
VfGH name the BVwG decision they decide on. Instead of one RIS search per BVwG case number (about 300,000
requests), the pool of all VwGH and VfGH decisions whose text mentions the BVwG is retrieved once and matched
locally against the case numbers of the inventory. The pool is wider than asylum; the matching selects the
decisions connected to a candidate.

Phases (the network phases follow the RIS usage rules through RisClient):

    pool      network  metadata of the pool per court, sliced by decision date like the inventory
                       -> interim/connected/pool/ (inventory.jsonl, records.jsonl.gz, queries.jsonl,
                          pool_report.json)
    download  network  XML text and RIS metadata record of every pool decision
                       -> raw/{vwgh,vfgh}/<year>/<RIS_ID>.{json,xml}; log, manifest and report in raw/connected/
    match     local    BVwG case numbers in the pool texts against the inventory -> interim/connected/
    lookup    network  VfGH decisions named by a connected VwGH decision (transfers) but missing from the pool are
                       looked up once by case number and recorded as unresolved, with the result of the lookup

How a pool decision is matched to a BVwG candidate:
    1. a BVwG case number in its text (any chamber prefix; "2159588-2" identifies the case). In the Betreff (where the
       text has one; else next to "angefochten", "Beschwerde gegen", "Revision gegen" or "erhobene Beschwerde") it marks
       the decision under review; elsewhere it is a mention. Among the candidates with that case number, the decision is
       identified by the Ordnungsnummer ("/23E", high), the cited decision date (medium) or as the only one decided
       before (low); otherwise all are listed (ambiguous);
    2. VfGH texts rarely quote the BVwG case number. Without it: the BVwG decision date next to
       "Bundesverwaltungsgericht", accepted only if the date of that decision's BFA Bescheid also appears (medium);
       or a later BVwG decision citing the VfGH case number (after an annulment the BVwG decides again under the same
       case number);
    3. a VfGH decision that transferred the complaint to the VwGH (Abtretung) is connected through the VwGH decision
       that names it.

Outputs of `match` in interim/connected/:
    connections.jsonl          pool decisions connected to a BVwG candidate, each match with method and evidence
    unresolved.jsonl           VfGH decisions referenced but not in the pool (case number, referring decisions, result
                               of the RIS lookup)
    coverage_gaps.jsonl        asylum-related pool decisions reviewing a BVwG case number not in the inventory
    bvwg_heads.jsonl.gz        what the BVwG texts contribute (joined case numbers, dates, cited VfGH cases), read
                               by stage 07; each text is read once (delete the file after changing read_bvwg_head)
    connected_report.md / .json
"""

from __future__ import annotations

import gzip
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import date
from pathlib import Path

from ..common import utc_now, write_json, write_jsonl, write_jsonl_gz
from .client import RisClient, record_id, summarize
from .download import Downloader
from .inventory import INCLUDED_ASYLG_NORM_RE, InventoryBuilder
from .queries import Indicator, QueryConfig
from .text import (
    BVWG_GZ_RE, VFGH_GZ_RE, DocumentText, bvwg_base, bvwg_full, bvwg_key, find_dates, vfgh_gz, vfgh_gzs_of,
)

PHASES = ["pool", "download", "match", "lookup"]
POOL_COURTS = {"Vwgh": "vwgh", "Vfgh": "vfgh"}
BVWG_HEADS = "bvwg_heads.jsonl.gz"

CHALLENGED_RE = re.compile(r"angefochten|Beschwerde gegen|Revision gegen|erhobene Beschwerde")
DECLARED_RE = re.compile(r"mündlich verkündet")
BFA_BESCHEID_RE = re.compile(
    r"Bescheid\w*\s+(?:des\s+)?(?:Bundesamt\w*\s+für\s+Fremdenwesen\s+und\s+Asyl|BFA)\b[^.]{0,80}?\bvom\s+")
HEAD_END_RE = re.compile(r"IM NAMEN DER REPUBLIK|Das Bundesverwaltungsgericht|BESCHLUSS|Schriftliche Ausfertigung")
CASE_NUMBER_ONLY_RE = re.compile(r"(?<!\d)(\d{7}) ?- ?(\d{1,2})(?!\d)")
# Transfer wording in VwGH texts ("... ablehnte und sie dem Verwaltungsgerichtshof zur Entscheidung abtrat")
TRANSFER_RE = re.compile(r"abgetreten|abtrat|Abtretung|\btrat\b[^.]{0,150}?\bab\b")
# Transfer in a VfGH Spruch ("Die Beschwerde wird dem Verwaltungsgerichtshof abgetreten.")
VFGH_SPRUCH_TRANSFER_RE = re.compile(r"Verwaltungsgerichtshof[^.]{0,60}abgetreten|\btritt\b[^.]{0,150}?\bab\b")
VFGH_NAME_RE = re.compile(r"Verfassungsgerichtshof|VfGH")
BVWG_NAME_RE = re.compile(r"Bundesverwaltungsgericht")
BFA_CONTEXT_RE = re.compile(r"Bundesamt\w*\s+für\s+Fremdenwesen\s+und\s+Asyl|\bBFA\b|Bescheid")
DATE_FALLBACK_YEARS = 3  # a VfGH decision on a BVwG decision follows it within months, rarely years


def read_rows(path: Path) -> list[dict]:
    """The lines of a JSON Lines file; none where the file is missing."""
    if not path.exists():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


# --------------------------------------------------------------------------- pool


def collect_pool(client: RisClient, pool_dir: Path, params: dict, start: date, end: date, max_hits: int,
                 log=print) -> dict:
    collected = {}
    for application, court in POOL_COURTS.items():
        indicator = Indicator(f"{court}_mentions_bvwg", "connected_decisions", params=tuple(params.items()))
        config = QueryConfig(application, start, end, max_hits, [indicator], source="connected_pool")
        builder = InventoryBuilder(client, config, pool_dir / court, log=log)
        log(f"Pool {court.upper()} {start} .. {end}: {params}")
        leaves, splits = builder.collect()
        collected[court] = (builder, leaves, splits)
    return assemble_pool(pool_dir, collected, params, start, end)


def assemble_pool(pool_dir: Path, collected: dict, params: dict, start: date, end: date) -> dict:
    rows, seen, skipped, queries, incomplete = [], set(), Counter(), [], []
    hits: Counter = Counter()

    def new_records():
        for court, (builder, leaves, splits) in collected.items():
            for s in leaves:
                data = builder.read_leaf(s)
                queries.append({k: v for k, v in data.items() if k != "records"})
                hits[court] += data["hits"]
                if not data["complete"]:
                    incomplete.append({"slice_id": s.id, "hits": data["hits"], "unique": data["unique"]})
                for record in data["records"]:
                    rid = record_id(record)
                    if rid in seen:
                        continue
                    seen.add(rid)
                    summary = summarize(record)
                    if summary["document_type"] != "Text":
                        skipped[summary["document_type"]] += 1
                        continue
                    rows.append({**summary, "partition": court, "queries": [s.query.id],
                                 "first_retrieved_at": data["retrieved_at"]})
                    yield {"ris_id": rid, "record": record}
            queries.extend(json.loads(builder.split_file(s).read_text(encoding="utf-8")) for s in splits)

    write_jsonl_gz(pool_dir / "records.jsonl.gz", new_records())
    rows.sort(key=lambda r: (r["decision_date"] or "", r["ris_id"]))
    write_jsonl(pool_dir / "inventory.jsonl", rows)
    write_jsonl(pool_dir / "queries.jsonl", sorted(queries, key=lambda q: q["slice_id"]))
    report = {
        "generated_at": utc_now(), "date_range": [start.isoformat(), end.isoformat()], "params": params,
        "hits": dict(hits), "documents": dict(Counter(r["partition"] for r in rows)),
        "per_year": {c: dict(sorted(Counter(r["decision_date"][:4] for r in rows if r["partition"] == c).items()))
                     for c in POOL_COURTS.values()},
        "records_skipped_not_decision_text": dict(skipped), "incomplete_slices": incomplete,
    }
    write_json(pool_dir / "pool_report.json", report)
    return report


# --------------------------------------------------------------------------- match: what the texts say
# These run in worker processes: plain values in and out.


def bvwg_case_keys(case_numbers: list[str]) -> dict[str, str]:
    found = {}
    for c in case_numbers:
        if m := BVWG_GZ_RE.search(c):
            found.setdefault(bvwg_key(m), bvwg_base(m))
        elif m := CASE_NUMBER_ONLY_RE.search(c):
            found.setdefault(f"{m[1]}-{m[2]}", c.strip())
    return found


def read_bvwg_head(item: tuple[str, str, str, list[str]]) -> dict:
    """What a BVwG text contributes to matching: joined case numbers with Ordnungsnummer (head of the Spruch), the
    date an oral decision was announced, the dates of the BFA Bescheide, and the VfGH case numbers it cites."""
    ris_id, path, decision_date, case_numbers = item
    head = {"ris_id": ris_id, "text": False, "case_numbers": bvwg_case_keys(case_numbers),
            "decision_dates": [decision_date] if decision_date else [], "bfa_dates": [], "vfgh_cited": []}
    try:
        text = DocumentText.from_xml(Path(path).read_bytes())
    except (OSError, ET.ParseError):
        return head
    head["text"] = True
    spruch = text.section("spruch")
    top = spruch[:800] or text.full[:1500]
    if cut := HEAD_END_RE.search(top):
        top = top[:cut.start()]
    for m in BVWG_GZ_RE.finditer(top):
        key, full = bvwg_key(m), bvwg_full(m)
        current = head["case_numbers"].get(key)
        if current is None or (full and "/" not in current):
            head["case_numbers"][key] = full or bvwg_base(m)
    dates = set(head["decision_dates"])
    for m in DECLARED_RE.finditer(text.full[:5000]):
        dates.update(d.isoformat() for d in find_dates(text.window(m.start(), m.end(), 120, 120)))
    head["decision_dates"] = sorted(dates)
    source = spruch or text.full[:3000]
    head["bfa_dates"] = sorted({d.isoformat() for m in BFA_BESCHEID_RE.finditer(source)
                                for d in find_dates(source[m.end():m.end() + 25])[:1]})
    head["vfgh_cited"] = sorted({vfgh_gz(m) for m in VFGH_GZ_RE.finditer(text.full)
                                 if VFGH_NAME_RE.search(text.window(m.start(), m.end(), 200, 50))})
    return head


def read_pool_decision(item: tuple[str, str, str]) -> dict:
    """BVwG case numbers a VwGH/VfGH text mentions (and whether as the decision under review), the dates next to
    "Bundesverwaltungsgericht" and next to the BFA or a Bescheid (VfGH), and transfers between VfGH and VwGH."""
    ris_id, application, path = item
    out = {"ris_id": ris_id, "text": False, "mentions": [], "bvwg_dates": {}, "bfa_dates": [], "transfer_out": None,
           "transfer_refs": [], "international_protection": False}
    try:
        text = DocumentText.from_xml(Path(path).read_bytes())
    except (OSError, ET.ParseError):
        return out
    out["text"] = True
    has_betreff = any(label == "betreff" for label, _ in text.blocks)
    # One mention per case number and role: a VwGH text names the decision under review in its Betreff and may name
    # an earlier decision under the same case number elsewhere; the Ordnungsnummer of one must not go to the other.
    by_role: dict[tuple[str, bool], dict] = {}
    for m in BVWG_GZ_RE.finditer(text.full):
        label = text.label_at(m.start())
        challenged = label == "betreff" if has_betreff else bool(
            CHALLENGED_RE.search(text.window(m.start(), m.end(), 300, 100)))
        key, full = bvwg_key(m), bvwg_full(m)
        old = by_role.get((key, challenged))
        if old and (old["full"] or not full):
            continue
        by_role[(key, challenged)] = {
            "key": key, "base": bvwg_base(m), "full": full, "challenged": challenged, "section": label,
            "cited_dates": sorted({d.isoformat() for d in find_dates(text.window(m.start(), m.end(), 250, 40))}),
            "evidence": text.snippet(m.start(), m.end())}
    out["mentions"] = list(by_role.values())
    out["international_protection"] = "internationalen Schutz" in text.full
    if application == "Vfgh":
        for m in BVWG_NAME_RE.finditer(text.full):
            for d in find_dates(text.window(m.start(), m.end(), 300, 300)):
                out["bvwg_dates"].setdefault(d.isoformat(), text.snippet(m.start(), m.end()))
        out["bfa_dates"] = sorted({d.isoformat() for m in BFA_CONTEXT_RE.finditer(text.full)
                                   for d in find_dates(text.window(m.start(), m.end(), 150, 150))})
        spruch = text.section("spruch")
        if m := VFGH_SPRUCH_TRANSFER_RE.search(spruch):
            out["transfer_out"] = spruch[max(0, m.start() - 150):m.end() + 50]
    else:
        refs: dict[str, str] = {}
        for m in TRANSFER_RE.finditer(text.full):
            lo = max(0, m.start() - 250)
            window = text.full[lo:m.end() + 250]
            gzs = list(VFGH_GZ_RE.finditer(window))
            if gzs and VFGH_NAME_RE.search(window):
                closest = min(gzs, key=lambda g: abs(lo + g.start() - m.start()))
                refs.setdefault(vfgh_gz(closest), text.snippet(m.start(), m.end()))
        out["transfer_refs"] = [{"vfgh_case_number": gz, "evidence": ev} for gz, ev in refs.items()]
    return out


def parallel_map(func, items: list, workers: int = 4, log=print, label: str = "") -> list:
    if len(items) < 200 or workers <= 1:
        return [func(i) for i in items]
    log(f"  reading {len(items):,} {label} texts with {workers} processes")
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(func, items, chunksize=64))


# --------------------------------------------------------------------------- match: resolution


class BvwgIndex:
    """The BVwG candidates by case number, decision date and cited VfGH case number."""

    def __init__(self, rows: list[dict], heads: dict[str, dict]):
        self.rows = {r["ris_id"]: r for r in rows}
        self.heads = heads
        self.by_key: dict[str, list[str]] = defaultdict(list)
        self.by_date: dict[str, list[str]] = defaultdict(list)
        self.cites_vfgh: dict[str, list[str]] = defaultdict(list)
        for rid, h in heads.items():
            for key in h["case_numbers"]:
                self.by_key[key].append(rid)
            for d in h["decision_dates"]:
                self.by_date[d].append(rid)
            for gz in h["vfgh_cited"]:
                self.cites_vfgh[gz].append(rid)

    def date(self, rid: str) -> str:
        return self.rows[rid]["decision_date"]

    def resolve(self, key: str, full: str | None, cited_dates: set[str], before: str) -> dict:
        """Which candidate with case number `key` a VwGH/VfGH text refers to."""
        candidates = sorted(rid for rid in self.by_key.get(key, []) if self.date(rid) <= before)
        result = {"bvwg_ris_id": None, "confidence": None, "bvwg_candidates": candidates}
        if not candidates:
            return result | {"how": f"no candidate with case number {key} decided before {before}"}
        dated = [rid for rid in candidates if cited_dates & set(self.heads[rid]["decision_dates"])]
        if full:
            number = full.rsplit("/", 1)[1]
            exact = [rid for rid in candidates
                     if (v := self.heads[rid]["case_numbers"].get(key)) and v.rsplit("/", 1)[-1] == number and "/" in v]
            if exact:
                pick = next((rid for rid in exact if rid in dated), exact[0])
                return result | {"bvwg_ris_id": pick, "confidence": "high",
                                 "how": f"case number with Ordnungsnummer {full}"}
        if len(dated) == 1:
            shown = ", ".join(sorted(cited_dates & set(self.heads[dated[0]]["decision_dates"])))
            return result | {"bvwg_ris_id": dated[0], "confidence": "medium",
                             "how": f"case number {key} and decision date {shown}"}
        if len(candidates) == 1:
            return result | {"bvwg_ris_id": candidates[0], "confidence": "low",
                             "how": f"only candidate with case number {key} decided before {before}"}
        return result | {"how": f"{len(candidates)} candidates with case number {key}; none identified "
                                f"by Ordnungsnummer or cited date"}


def load_heads(rows: list[dict], bvwg: Downloader, out: Path, workers: int, log=print) -> dict[str, dict]:
    """Heads of all candidates: read from the BVwG texts (cached once read), from the metadata where no text is
    stored."""
    cache_file = out / BVWG_HEADS
    cached = {h["ris_id"]: h for h in read_rows(cache_file)}
    todo, heads = [], {}
    for r in rows:
        if r["ris_id"] in cached:
            heads[r["ris_id"]] = cached[r["ris_id"]]
            continue
        xml = bvwg.base(r).with_suffix(".xml")
        if xml.exists():
            todo.append((r["ris_id"], str(xml), r["decision_date"], r["case_numbers"]))
        else:
            heads[r["ris_id"]] = {"ris_id": r["ris_id"], "text": False,
                                  "case_numbers": bvwg_case_keys(r["case_numbers"]),
                                  "decision_dates": [r["decision_date"]], "bfa_dates": [], "vfgh_cited": []}
    if todo:
        for h in parallel_map(read_bvwg_head, todo, workers, log, "BVwG"):
            heads[h["ris_id"]] = h
            if h["text"]:
                cached[h["ris_id"]] = h
        write_jsonl_gz(cache_file, sorted(cached.values(), key=lambda h: h["ris_id"]))
    log(f"  BVwG candidates: {len(rows):,}, read from text: {sum(1 for h in heads.values() if h['text']):,}")
    return heads


def match(pool_rows: list[dict], pool: Downloader, bvwg_rows: list[dict], bvwg: Downloader, out: Path,
          workers: int, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    heads = load_heads(bvwg_rows, bvwg, out, workers, log)
    index = BvwgIndex(bvwg_rows, heads)
    items = [(r["ris_id"], r["application"], str(pool.base(r).with_suffix(".xml"))) for r in pool_rows]
    features = {f["ris_id"]: f for f in parallel_map(read_pool_decision, items, workers, log, "VwGH/VfGH")}
    by_id = {r["ris_id"]: r for r in pool_rows}
    vfgh_by_gz: dict[str, list[str]] = defaultdict(list)
    for r in pool_rows:
        if r["application"] == "Vfgh":
            for gz in vfgh_gzs_of(r["case_numbers"]):
                vfgh_by_gz[gz].append(r["ris_id"])

    matches: dict[str, list[dict]] = defaultdict(list)
    gaps = []
    for r in pool_rows:
        f, rid, when = features[r["ris_id"]], r["ris_id"], r["decision_date"]
        asylum_norm = any(INCLUDED_ASYLG_NORM_RE.match(n) for n in r["norms"])
        for mention in f["mentions"]:
            if mention["key"] not in index.by_key:
                if mention["challenged"] and (asylum_norm or f["international_protection"]):
                    gaps.append({"ris_id": rid, "court": r["partition"], "decision_date": when,
                                 "bvwg_case_number": mention["full"] or mention["base"],
                                 "asylum_signal": "norm" if asylum_norm else "text 'internationalen Schutz'",
                                 "evidence": mention["evidence"], "document_url": r["document_url"]})
                continue
            res = index.resolve(mention["key"], mention["full"], set(mention["cited_dates"]), when)
            matches[rid].append({"method": "bvwg_case_number",
                                 "mention": "challenged" if mention["challenged"] else "cited",
                                 "section": mention["section"], "bvwg_case_number": mention["full"] or mention["base"],
                                 **res, "evidence": mention["evidence"]})
        if r["application"] != "Vfgh" or any(m["mention"] == "challenged" for m in matches[rid]):
            continue
        matches[rid] += vfgh_by_date(f, index, when) or vfgh_via_later_bvwg(r, f, index)

    # Transfers: a VwGH decision naming the VfGH decision that transferred the complaint to it.
    transfers, unresolved = [], {}
    for r in pool_rows:
        f, rid = features[r["ris_id"]], r["ris_id"]
        if r["application"] != "Vwgh" or not matches.get(rid):
            continue
        reviewed = [m for m in matches[rid] if m["mention"] == "challenged"]
        for ref in f["transfer_refs"]:
            gz = ref["vfgh_case_number"]
            vfgh_ids = vfgh_by_gz.get(gz, [])
            transfers.append({"vfgh_case_number": gz, "vfgh_ris_ids": vfgh_ids, "vwgh_ris_id": rid,
                              "evidence": ref["evidence"]})
            for vid in vfgh_ids:
                if any(m["mention"] == "challenged" for m in matches[vid]):
                    continue
                for m in reviewed:
                    matches[vid].append({"method": "transfer_to_vwgh", "mention": "challenged", "section": None,
                                         "bvwg_case_number": m["bvwg_case_number"], "bvwg_ris_id": m["bvwg_ris_id"],
                                         "confidence": "medium", "bvwg_candidates": m["bvwg_candidates"], "via": rid,
                                         "how": f"VfGH {gz} transferred the complaint to VwGH {rid}, which reviews "
                                                f"this decision", "evidence": ref["evidence"]})
            if not vfgh_ids:
                entry = unresolved.setdefault(gz, {"reference": f"case:vfgh:{gz}", "court": "vfgh", "case_number": gz,
                                                   "context": "VfGH decision that transferred a complaint to the VwGH",
                                                   "referenced_by": [], "evidence": ref["evidence"]})
                entry["referenced_by"].append(rid)

    connections = []
    for rid, found in matches.items():
        if not found:
            continue
        r, f = by_id[rid], features[rid]
        connections.append({
            "ris_id": rid, "court": r["partition"], "decision_date": r["decision_date"],
            "case_numbers": r["case_numbers"], "decision_type": r["decision_type"], "norms": r["norms"],
            "document_url": r["document_url"],
            "xml_file": pool.rel(pool.base(r).with_suffix(".xml")),
            "matches": found, "vfgh_transfer_in_spruch": f["transfer_out"],
            "transfers_named": [t["vfgh_case_number"] for t in transfers if t["vwgh_ris_id"] == rid],
        })
    connections.sort(key=lambda c: (c["court"], c["decision_date"], c["ris_id"]))
    return {"connections": connections, "transfers": transfers, "unresolved": unresolved, "gaps": gaps,
            "features_read": sum(1 for f in features.values() if f["text"]), "pool_size": len(pool_rows),
            "bvwg_heads": heads, "bvwg_rows": len(bvwg_rows)}


def vfgh_by_date(f: dict, index: BvwgIndex, when: str) -> list[dict]:
    """BVwG decision date next to "Bundesverwaltungsgericht" plus the date of that decision's BFA Bescheid next to
    the BFA or a Bescheid. The BVwG decides about 100 decisions a day, so a tie is listed, not resolved."""
    found, text_dates, seen = [], set(f["bfa_dates"]), set()
    earliest = f"{int(when[:4]) - DATE_FALLBACK_YEARS}{when[4:]}"
    for d, evidence in sorted(f["bvwg_dates"].items()):
        if not earliest <= d < when:
            continue
        hits = sorted(rid for rid in set(index.by_date.get(d, [])) if set(index.heads[rid]["bfa_dates"]) & text_dates)
        if not hits or tuple(hits) in seen:
            continue
        seen.add(tuple(hits))
        one = len(hits) == 1
        bfa = sorted(set(index.heads[hits[0]]["bfa_dates"]) & text_dates)
        found.append({"method": "date_fallback", "mention": "challenged", "section": None,
                      "bvwg_case_number": None, "bvwg_ris_id": hits[0] if one else None,
                      "confidence": "medium" if one else None, "bvwg_candidates": sorted(hits),
                      "how": (f"BVwG decision date {d} next to 'Bundesverwaltungsgericht' and BFA Bescheid date "
                              f"{bfa[0]} in the text; verify manually") if one else
                             f"{len(hits)} candidates decided on {d} share a BFA Bescheid date with the text",
                      "evidence": evidence})
    return found


def vfgh_via_later_bvwg(r: dict, f: dict, index: BvwgIndex) -> list[dict]:
    """After an annulment the BVwG decides again under the same case number and cites the VfGH decision."""
    found, when = [], r["decision_date"]
    for gz in vfgh_gzs_of(r["case_numbers"]):
        for later in sorted(index.cites_vfgh.get(gz, [])):
            if index.date(later) < when:
                continue
            for key in index.heads[later]["case_numbers"]:
                res = index.resolve(key, None, set(f["bvwg_dates"]), when)
                found.append({"method": "later_bvwg_cites_vfgh", "mention": "challenged", "section": None,
                              "bvwg_case_number": key, **res, "via": later,
                              "how": f"later BVwG decision {later} cites VfGH {gz}; {res['how']}",
                              "evidence": f"VfGH {gz} cited in {later}"})
    return found


# --------------------------------------------------------------------------- lookup


def lookup_unresolved(client: RisClient, case_numbers: list[str], out: Path, log=print) -> dict[str, dict]:
    """Look up referenced VfGH case numbers missing from the pool, once each (results kept in lookups.jsonl)."""
    cache_file = out / "lookups.jsonl"
    cache = {e["case_number"]: e for e in read_rows(cache_file)}
    todo = [gz for gz in sorted(set(case_numbers)) if gz not in cache]
    if todo:
        log(f"Lookup of {len(todo):,} referenced VfGH case numbers missing from the pool")
    with cache_file.open("a", encoding="utf-8") as fh:
        for gz in todo:
            page = client.search_page("Vfgh", {"Geschaeftszahl": gz}, page_size=10)
            ids = [record_id(rec) for rec in page.records
                   if summarize(rec)["document_type"] == "Text" and gz in vfgh_gzs_of(summarize(rec)["case_numbers"])]
            entry = {"case_number": gz, "looked_up_at": utc_now(), "hits": page.hits, "ris_ids": ids,
                     "status": "in_ris_not_in_pool" if ids else "no_decision_text_in_ris"}
            cache[gz] = entry
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
    return cache


# --------------------------------------------------------------------------- outputs and report


def write_outputs(result: dict, lookups: dict[str, dict], out: Path, pool_report: dict | None,
                  download_report: dict) -> dict:
    connections = result["connections"]
    write_jsonl(out / "connections.jsonl", connections)
    unresolved = []
    for gz, entry in sorted(result["unresolved"].items()):
        look = lookups.get(gz)
        unresolved.append({**entry, "referenced_by": sorted(set(entry["referenced_by"])),
                           "ris_lookup": look["status"] if look else "not_checked",
                           "ris_ids": look["ris_ids"] if look else [], "looked_up_at": look and look["looked_up_at"]})
    write_jsonl(out / "unresolved.jsonl", unresolved)
    write_jsonl(out / "coverage_gaps.jsonl", result["gaps"])

    all_matches = [m for c in connections for m in c["matches"]]
    report = {
        "generated_at": utc_now(),
        "pool": pool_report,
        "pool_download": {k: download_report[k] for k in ("total", "by_court", "xml_bytes")},
        "pool_texts_read": result["features_read"],
        "bvwg_candidates": result["bvwg_rows"],
        "bvwg_texts_read": sum(1 for h in result["bvwg_heads"].values() if h["text"]),
        "connected_documents": dict(Counter(c["court"] for c in connections)),
        "matches_by_method": dict(Counter(m["method"] for m in all_matches)),
        "matches_by_mention": dict(Counter(m["mention"] for m in all_matches)),
        "matches_by_confidence": dict(Counter(m["confidence"] or "ambiguous" for m in all_matches)),
        "bvwg_candidates_reviewed": len({m["bvwg_ris_id"] for m in all_matches
                                         if m["mention"] == "challenged" and m["bvwg_ris_id"]}),
        "bvwg_candidates_mentioned": len({m["bvwg_ris_id"] for m in all_matches if m["bvwg_ris_id"]}),
        "transfers": {"named_by_connected_vwgh": len(result["transfers"]),
                      "vfgh_decision_in_pool": sum(1 for t in result["transfers"] if t["vfgh_ris_ids"])},
        "unresolved": {"total": len(unresolved), "by_lookup": dict(Counter(u["ris_lookup"] for u in unresolved))},
        "coverage_gaps": {"documents": len({g["ris_id"] for g in result["gaps"]}), "case_numbers": len(result["gaps"])},
    }
    write_json(out / "connected_report.json", report)
    (out / "connected_report.md").write_text(render_report(report), encoding="utf-8")
    return report


def render_report(r: dict) -> str:
    p, d = r["pool"] or {}, r["pool_download"]

    def counts(c: dict) -> str:
        return ", ".join(f"{k} {v:,}" for k, v in sorted(c.items())) or "–"

    lines = ["# Connected VwGH/VfGH decisions", "",
             f"Generated {r['generated_at']}. Pool: VwGH and VfGH decisions whose text mentions the BVwG "
             f"(`{(p.get('params') or {}).get('Suchworte', '')}`), decision dates "
             f"{' to '.join(p.get('date_range') or ['?', '?'])}; matched locally against the BVwG candidate inventory.",
             "", "## Pool", "", "| Court | RIS hits | Decision texts | Downloaded | Pending | Failed |",
             "|---|---|---|---|---|---|"]
    for court in POOL_COURTS.values():
        n = d["by_court"].get(court, {})
        lines.append(f"| {court.upper()} | {(p.get('hits') or {}).get(court, 0):,} | "
                     f"{(p.get('documents') or {}).get(court, 0):,} | {n.get('ok', 0):,} | {n.get('pending', 0):,} | "
                     f"{n.get('failed', 0):,} |")
    if p.get("incomplete_slices"):
        lines.append(f"\n**Incomplete pool slices:** {len(p['incomplete_slices'])} (see pool_report.json)")
    lines += ["", f"Texts read: {r['pool_texts_read']:,} pool decisions; {r['bvwg_texts_read']:,} of "
                  f"{r['bvwg_candidates']:,} BVwG candidates (the others are matched by their metadata case number "
                  "only).",
              "", "## Connected decisions", "", "| Court | Connected decisions |", "|---|---|"]
    lines += [f"| {c.upper()} | {n:,} |" for c, n in sorted(r["connected_documents"].items())] or ["| – | 0 |"]
    lines += ["", "- BVwG candidates under review by a connected decision (identified): "
                  f"{r['bvwg_candidates_reviewed']:,}",
              f"- BVwG candidates mentioned at all (identified): {r['bvwg_candidates_mentioned']:,}",
              f"- Matches by method: {counts(r['matches_by_method'])}",
              f"- Matches by mention: {counts(r['matches_by_mention'])}",
              f"- Matches by confidence: {counts(r['matches_by_confidence'])}",
              f"- VfGH transfers named by connected VwGH decisions: {r['transfers']['named_by_connected_vwgh']:,} "
              f"(VfGH decision in the pool: {r['transfers']['vfgh_decision_in_pool']:,})", "",
              "Methods: `bvwg_case_number` (case number in the text), `date_fallback` (VfGH: decision date and BFA "
              "Bescheid date), `later_bvwg_cites_vfgh`, `transfer_to_vwgh`. Ambiguous: several candidates share the "
              "case number and none is identified; all are listed.",
              "", "## Unresolved references", "",
              f"{r['unresolved']['total']:,} referenced VfGH decisions are not in the pool; RIS lookup: "
              f"{counts(r['unresolved']['by_lookup'])}. List: `unresolved.jsonl`.", "",
              "## Coverage signal", "",
              f"{r['coverage_gaps']['documents']:,} asylum-related pool decisions (AsylG norm or 'internationalen "
              f"Schutz') review {r['coverage_gaps']['case_numbers']:,} BVwG case numbers that are not in the "
              "inventory: excluded matters or gaps in the inventory. List: `coverage_gaps.jsonl`."]
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- run


def run(client: RisClient | None, phases: list[str], candidates: Path, raw: Path, out: Path, pool_query: dict,
        max_hits: int, limit: int | None = None, match_partial: bool = False, workers: int = 4, log=print) -> int:
    """Run the given phases. `pool_query`: params, start and end of the pool (config/ris/queries.yaml)."""
    pool_dir = out / "pool"
    pool_report = None
    if "pool" in phases:
        pool_report = collect_pool(client, pool_dir, pool_query["params"], pool_query["start"], pool_query["end"],
                                   max_hits, log)
        log(f"pool: {pool_report['documents']} decision texts (RIS hits {pool_report['hits']}), "
            f"{len(pool_report['incomplete_slices'])} incomplete slices")
    if not (pool_dir / "inventory.jsonl").exists():
        log("No pool yet: run the pool phase first.")
        return 0
    if pool_report is None and (pool_dir / "pool_report.json").exists():
        pool_report = json.loads((pool_dir / "pool_report.json").read_text(encoding="utf-8"))
    pool = Downloader(client, pool_dir, raw, pool=True, log=log)
    pool_rows = pool.rows()
    log(f"{len(pool_rows):,} pool decisions; metadata records written now: {pool.write_metadata(pool_rows):,}")
    if "download" in phases:
        try:
            log(f"pool download this run: {dict(pool.download(pool_rows, limit))}")
        finally:
            download_report = pool.rebuild_manifest(pool_rows)
    else:
        download_report = pool.rebuild_manifest(pool_rows)
    pending = download_report["total"].get("pending", 0)
    if not {"match", "lookup"} & set(phases):     # lookup reads what match leaves unresolved
        return 0
    if pending and not match_partial:
        log(f"match waits: {pending:,} pool texts still pending")
        return 0
    bvwg = Downloader(None, candidates, raw)
    with (candidates / "inventory.jsonl").open(encoding="utf-8") as fh:
        bvwg_rows = [json.loads(line) for line in fh]
    log("match: reading texts and matching case numbers")
    result = match(pool_rows, pool, bvwg_rows, bvwg, out, workers, log)
    lookups = {e["case_number"]: e for e in read_rows(out / "lookups.jsonl")}
    report = write_outputs(result, lookups, out, pool_report, download_report)
    if "lookup" in phases and result["unresolved"]:
        lookups = lookup_unresolved(client, list(result["unresolved"]), out, log)
        report = write_outputs(result, lookups, out, pool_report, download_report)
    log(f"connected: {report['connected_documents']}, unresolved: {report['unresolved']['total']}; "
        f"report: {out / 'connected_report.md'}")
    return 0
