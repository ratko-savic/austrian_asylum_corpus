"""Stage 07: relations between BVwG, VwGH and VfGH decisions (court_links.py; rules config/links/court_links.yaml).

Inputs: the connected step's matches (interim/connected/connections.jsonl), the pool manifest
(raw/connected/manifest.jsonl), the BFA dates the connected step read in each BVwG decision
(interim/connected/bvwg_heads.jsonl.gz) and the documents of stage 03 of the three courts.

Output (processed/links/):
    relations.jsonl      the relations, sorted by relation ID; each checked against the schema (one that fails is not
                         written)
    unresolved.jsonl     review matches that gave no relation (ambiguous, no candidate in the corpus, dated after the
                         review, a failed date-fallback check, no evidence) and transfers whose VfGH number does not
                         fit, with their candidates and the reason; mentions that are no review, matches inferred
                         from a later BVwG citation and decisions outside the parsed corpus are only counted
    index.csv            per VwGH/VfGH decision: court, date, relations by type, unresolved matches
    link_report.md/.json counts by type and match type, why matches gave no relation, BVwG decisions reviewed per year
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..common import CONFIG, data_dir, file_sha256, pct, read_jsonl, require, schema_validator, year_files
from ..ris.connected import BVWG_HEADS
from . import court_links as cl


RULES = CONFIG / "links" / "court_links.yaml"


def read_bvwg_shard(path: str) -> list[dict]:
    cfg = cl.load_config(RULES)
    return [cl.bvwg_info(d, cfg) for d in read_jsonl(Path(path))]


def read_bvwg(docs: Path, heads_path: Path, workers: int) -> dict[str, dict]:
    """RIS ID -> what the links need of each BVwG document, with the BFA dates the connected step read in it."""
    bvwg = {}
    with ProcessPoolExecutor(workers) as pool:
        for infos in pool.map(read_bvwg_shard, year_files(docs / "bvwg", "stage 03")):
            bvwg.update({i["ris_id"]: i for i in infos})
    for h in read_jsonl(heads_path):
        if h["ris_id"] in bvwg:
            bvwg[h["ris_id"]]["bfa_dates"] = h["bfa_dates"]
    return bvwg


def vfgh_documents(manifest_path: Path) -> dict[str, str]:
    """VfGH case number (without spaces) -> the document of the pool that has it."""
    out = {}
    for m in read_jsonl(manifest_path):
        if m["court"] == "vfgh":
            for n in m["case_numbers"]:
                out.setdefault(n.replace(" ", ""), f"ris:vfgh:{m['ris_id']}")
    return out


def link_higher(docs: Path, connections: dict, bvwg: dict, vfgh_by_number: dict, cfg: dict) -> tuple:
    """The reviews and transferred_to relations of the connected VwGH / VfGH decisions: (relations, unresolved,
    counts, index rows, RIS IDs of the parsed decisions)."""
    relations, unresolved, counts, index_rows, parsed = [], [], Counter(), [], set()
    for court in ("vwgh", "vfgh"):
        for shard in year_files(docs / court, f"stage 03 --court {court}"):
            for doc in read_jsonl(shard):
                parsed.add(doc["ris_id"])
                row = connections.get(doc["ris_id"])
                if row is None:
                    continue
                d = cl.Doc(doc)
                rels, unres, c = cl.reviews_of(row, d, bvwg, cfg)
                counts.update(c)
                if court == "vwgh":
                    reviewed = [bvwg[r["target_id"].split(":")[-1]] for r in rels]
                    t_rels, t_unres, c = cl.transfers_of(row, d, vfgh_by_number, cfg, reviewed)
                    rels, unres = rels + t_rels, unres + t_unres
                    counts.update(c)
                relations += rels
                unresolved += unres
                by_type = Counter(r["relation_type"] for r in rels)
                index_rows.append({"ris_id": doc["ris_id"], "court": court, "decision_date": doc["decision_date"],
                                   "reviews": by_type["reviews"], "transferred_to": by_type["transferred_to"],
                                   "match_types": " ".join(sorted({r["match_type"] for r in rels})),
                                   "unresolved": len(unres)})
    return relations, unresolved, counts, index_rows, parsed


def run(workers: int) -> int:
    data = data_dir()
    docs = data / "processed" / "documents"
    connections_path = require(data / "interim" / "connected" / "connections.jsonl", "stage 01 connected")
    manifest_path = require(data / "raw" / "connected" / "manifest.jsonl", "stage 01 connected")
    heads_path = require(data / "interim" / "connected" / BVWG_HEADS, "stage 01 connected")
    out = data / "processed" / "links"
    cfg = cl.load_config(RULES)
    validator = schema_validator("relation")

    bvwg = read_bvwg(docs, heads_path, workers)
    print(f"Links: {len(bvwg):,} BVwG documents read", flush=True)
    connections = {r["ris_id"]: r for r in read_jsonl(connections_path)}
    relations, unresolved, counts, index_rows, parsed = link_higher(docs, connections, bvwg,
                                                                     vfgh_documents(manifest_path), cfg)
    not_parsed = sorted(set(connections) - parsed)
    relations += cl.same_case(bvwg)
    written, failed = {}, Counter()
    for r in relations:
        errors = list(validator.iter_errors(r))
        if errors:
            failed[errors[0].message[:80]] += 1
            continue
        written[r["relation_id"]] = r
    rel_sorted = [written[k] for k in sorted(written)]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "relations.jsonl").open("w", encoding="utf-8") as fh:
        for r in rel_sorted:
            fh.write(cl.dumps(r) + "\n")
    with (out / "unresolved.jsonl").open("w", encoding="utf-8") as fh:
        for u in sorted(unresolved, key=lambda u: (u["source_id"], u["bvwg_case_number"] or "", u["reason"],
                                                   cl.dumps(u))):
            fh.write(cl.dumps(u) + "\n")
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["ris_id", "court", "decision_date", "reviews", "transferred_to",
                                           "match_types", "unresolved"])
        w.writeheader()
        w.writerows(sorted(index_rows, key=lambda r: r["ris_id"]))

    inputs = {"connections_sha256": file_sha256(connections_path), "manifest_sha256": file_sha256(manifest_path),
              "bvwg_heads_sha256": file_sha256(heads_path), "bvwg_documents": len(bvwg),
              "connected_decisions": len(connections), "connected_not_parsed": len(not_parsed)}
    report = build_report(rel_sorted, bvwg, inputs, failed, counts, unresolved, not_parsed)
    (out / "link_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "link_report.md").write_text(render(report), encoding="utf-8")
    print(f"{len(rel_sorted):,} relations ({', '.join(f'{k} {v:,}' for k, v in report['relations'].items())}); "
          f"unresolved {len(unresolved):,}; failing schema {sum(failed.values())}; BVwG decisions reviewed "
          f"{report['bvwg_reviewed']['either']:,}; report: {out / 'link_report.md'}")
    return 1 if failed else 0


def build_report(rel_sorted, bvwg, inputs, failed, counts, unresolved, not_parsed) -> dict:
    by_type = Counter((r["relation_type"], r["match_type"]) for r in rel_sorted)
    reviewed = defaultdict(set)
    for r in rel_sorted:
        if r["relation_type"] == "reviews":
            reviewed[r["source_id"].split(":")[1]].add(r["target_id"])
    year_of = {i["document_id"]: i["decision_date"][:4] for i in bvwg.values()}
    per_year = defaultdict(Counter)
    for i in bvwg.values():
        per_year[i["decision_date"][:4]]["decisions"] += 1
    for court, targets in reviewed.items():
        for t in targets:
            per_year[year_of[t]][court] += 1
    either = set().union(*reviewed.values()) if reviewed else set()
    for t in either:
        per_year[year_of[t]]["either"] += 1
    return {"stage": "07 links", "inputs": inputs,
            "relations": {f"{t} {m}": n for (t, m), n in sorted(by_type.items())},
            "relations_total": len(rel_sorted), "failing_schema": dict(failed),
            "matches": dict(sorted(counts.items())), "unresolved": len(unresolved),
            "bvwg_reviewed": {c: len(s) for c, s in sorted(reviewed.items())} | {"either": len(either)},
            "per_year": {y: dict(c) for y, c in sorted(per_year.items())},
            "not_parsed_examples": not_parsed[:10]}


def render(r: dict) -> str:
    lines = ["# Links between BVwG, VwGH and VfGH decisions (stage 07)", "",
             f"Input: {r['inputs']['connected_decisions']:,} connected VwGH/VfGH decisions of the connected step "
             f"(`{r['inputs']['connections_sha256'][:12]}…`), "
             f"{r['inputs']['bvwg_documents']:,} BVwG documents; {r['inputs']['connected_not_parsed']} connected "
             "decisions not parsed (stage 03 errors).", "",
             f"- Relations: **{r['relations_total']:,}**; failing the schema (not written): "
             f"{sum(r['failing_schema'].values())}; unresolved matches: {r['unresolved']:,}",
             "- BVwG decisions under review: " + ", ".join(f"{k} {v:,}" for k, v in r["bvwg_reviewed"].items()), "",
             "| Relation | Match type | Relations |", "|---|---|---|"]
    lines += [f"| {k.split()[0]} | {k.split()[1]} | {v:,} |" for k, v in r["relations"].items()]
    lines += ["", "## Matches of the connected step", "", "| Outcome | Matches |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["matches"].items()]
    lines += ["", "## BVwG decisions under review, per decision year", "",
              "| Year | Decisions | by the VwGH | by the VfGH | either | share |", "|---|---|---|---|---|---|"]
    for y, c in r["per_year"].items():
        lines.append(f"| {y} | {c.get('decisions', 0):,} | {c.get('vwgh', 0):,} | {c.get('vfgh', 0):,} | "
                     f"{c.get('either', 0):,} | {pct(c.get('either', 0), c.get('decisions', 0))} |")
    return "\n".join(lines) + "\n"
