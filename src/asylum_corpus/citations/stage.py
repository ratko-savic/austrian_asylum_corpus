"""Stage 06: the explicit legal references of the decisions, one citation record per occurrence
(schemas/citation.schema.json).

Pass 1 reads all documents for the citation index: the forms under which a corpus document can be cited (BVwG
document numbers, ECLI, VwGH and VfGH case numbers). The BVwG run also counts the provisions cited explicitly (with
their own "§" / "Art.") and writes them to attested_designations.json, the table with which a bare number in a chain is
read; the VwGH and VfGH runs read the BVwG table. For the VwGH and VfGH the index is built from the documents of all
three courts, so that their citations of a BVwG or VwGH decision get its document as target.

Pass 2 extracts the citations, in chunks of CHUNK documents run in parallel. Output (processed/citations/<court>/),
the same bytes whatever the number of workers:
    attested_designations.json   (BVwG) per act the designations cited explicitly, with their number of citations
    <year>.jsonl.gz              the citations, in document order and within a document in text order; one gzip
                                 member per chunk. A document whose citations fail a check contributes none.
    index.csv                    every document: citations per type, counts of the reading rules, problems
    citation_report.md / .json   citations per year, rule and type; most cited acts, provisions and decisions; inputs
Checks per citation: the schema; `text` equals the paragraph's text at the span; the span lies inside its paragraph;
citations do not overlap; IDs numbered 1..n in text order.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import re
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..common import (
    CONFIG, data_dir, file_sha256, read_jsonl, remove_other_year_files, require, schema_validator, year_files,
)
from .cases import build_index, index_keys, prefer_own_case
from .extract import document_citations, explicit_provisions
from .statutes import Acts, load_acts, with_attested


ACTS = CONFIG / "citations" / "acts.yaml"
CHUNK = 1000             # documents per task and per gzip member (it shapes the bytes of the output)
TYPES = ["national_legislation", "national_case", "eu_legislation", "cjeu_case", "echr_provision", "ecthr_case",
         "international_treaty", "other", "publication_reference"]
PUBLICATION = "publication_reference"
LEGISLATION = {"national_legislation", "eu_legislation", "echr_provision", "international_treaty", "other"}
DECISIONS = {"national_case", "cjeu_case", "ecthr_case"}
STATS = {"leg_cit resolved": "leg_cit_resolved", "leg_cit without a preceding act": "leg_cit_without_act",
         "vwgh series numbers": "vwgh_series_numbers", "own case numbers skipped": "own_case_numbers_skipped"}
INDEX = ["ris_id", "decision_date", "citations"] + TYPES + list(STATS.values()) + ["targets_in_corpus", "problems"]
PROVISION = re.compile(r" (?:§|Art\.) ")
COUNTERS = ("documents", "by_rule", "by_type", "acts", "acts_docs", "provisions", "provisions_docs", "decisions",
            "decisions_docs", "targets", "extract", "problems")
TOP = 30

_acts: Acts | None = None
_index: dict | None = None


def documents_dir(court: str) -> Path:
    return data_dir() / "processed" / "documents" / court


def attested_path() -> Path:
    return data_dir() / "processed" / "citations" / "bvwg" / "attested_designations.json"


def index_shard(path: str) -> dict:
    """Pass 1 for one year file: its documents' index keys, its number of documents and its SHA-256 (worker)."""
    keyed = []
    for doc in read_jsonl(Path(path)):
        keyed.append((doc["document_id"], sorted(index_keys(doc)), doc["case_number"]))
    return {"path": path, "documents": len(keyed), "sha256": file_sha256(Path(path)), "keyed": keyed}


def attested_shard(path: str) -> Counter:
    """Pass 1 (BVwG): the provisions cited explicitly in the documents of one year file (worker)."""
    acts, out = load_acts(ACTS), Counter()
    for doc in read_jsonl(Path(path)):
        out.update(explicit_provisions(doc, acts))
    return out


def _init(index: dict, attested: str) -> None:
    global _acts, _index
    _acts, _index = with_attested(load_acts(ACTS), Path(attested)), index


def check(doc: dict, cits: list[dict]) -> list[tuple[str, str]]:
    """(check, message) for each problem of a document's citations; empty: all checks passed."""
    v = schema_validator("citation")
    paragraphs = {p["id"]: p for p in doc["paragraphs"]}
    problems, last_end = [], -1
    for k, c in enumerate(cits, 1):
        cid = c["citation_id"].rsplit("#", 1)[-1]
        for e in v.iter_errors(c):
            problems.append(("schema", f"{cid} {'/'.join(map(str, e.path)) or '(citation)'}: {e.message[:120]}"))
        p = paragraphs.get(c["paragraph_id"])
        if c["citation_id"] != f"{doc['document_id']}#cit:{k:04d}":
            problems.append(("numbering", f"{cid} is not number {k}"))
        if p is None or not p["start"] <= c["start"] < c["end"] <= p["end"]:
            problems.append(("span", f"{cid} {c['start']}-{c['end']} outside its paragraph"))
        elif c["text"] != p["text"][c["start"] - p["start"]:c["end"] - p["start"]]:
            problems.append(("text", f"{cid} differs from the paragraph's text at the span"))
        if c["start"] < last_end:
            problems.append(("overlap", f"{cid} overlaps the citation before it"))
        last_end = max(last_end, c["end"])
    return problems


def count(st: dict, year: str, cits: list[dict], stats: Counter) -> None:
    """Add a document's citations to the statistics of the report."""
    st["documents"][year] += 1
    st["extract"].update(stats)
    st["by_rule"].update(c["rule"] for c in cits)
    st["by_type"].update((year, c["type"]) for c in cits)
    acts, provisions, decisions = set(), set(), set()
    for c in cits:
        if c["type"] in LEGISLATION and PROVISION.search(c["normalized"]):
            act = PROVISION.split(c["normalized"], 1)[0]
            st["acts"][act] += 1
            st["provisions"][c["normalized"]] += 1
            acts.add(act)
            provisions.add(c["normalized"])
        elif c["type"] in DECISIONS:
            st["decisions"][c["normalized"]] += 1
            decisions.add(c["normalized"])
            t = c["target_id"] or ""
            st["targets"][(c["type"], "corpus document" if t.startswith("ris:") else
                           "case reference" if t.startswith("case:") else "none")] += 1
    st["acts_docs"].update(acts)
    st["provisions_docs"].update(provisions)
    st["decisions_docs"].update(decisions)


def extract_chunk(path: str, start: int, stop: int) -> dict:
    """Pass 2: the citations of documents start..stop-1 of a year file, as one gzip member, with the rows of the index
    and the statistics of the report (worker)."""
    year = Path(path).name.split(".")[0]
    st = {k: Counter() for k in COUNTERS}
    rows, buf, seen = [], io.BytesIO(), 0
    with gzip.open(path, "rt", encoding="utf-8") as fh, \
            gzip.GzipFile(filename="", mode="wb", fileobj=buf, mtime=0) as gz:
        for i, line in enumerate(fh):
            if i < start:
                continue
            if i >= stop:
                break
            seen += 1
            doc = json.loads(line)
            cits, stats = document_citations(doc, _acts, _index)
            problems = check(doc, cits)
            types = Counter(c["type"] for c in cits)
            in_corpus = sum(1 for c in cits if (c["target_id"] or "").startswith("ris:"))
            rows.append({"ris_id": doc["ris_id"], "decision_date": doc["decision_date"], "citations": len(cits),
                         **{t: types[t] for t in TYPES}, **{col: stats[k] for k, col in STATS.items()},
                         "targets_in_corpus": in_corpus, "problems": " | ".join(f"{k}: {m}" for k, m in problems)})
            if problems:
                st["problems"].update(k for k, _ in problems)
                continue
            for c in cits:
                gz.write((json.dumps(c, ensure_ascii=False) + "\n").encode("utf-8"))
            count(st, year, cits, stats)
    if seen != stop - start:
        raise RuntimeError(f"{path}: expected documents {start}..{stop - 1}, read {seen}")
    return {"year": year, "start": start, "gz": buf.getvalue(), "rows": rows, **st}


def write_attested(shards: list[str], out: Path, workers: int) -> Path:
    """Pass 1 (BVwG): the table of attested designations."""
    with ProcessPoolExecutor(min(workers, len(shards))) as pool:
        counts = sum(pool.map(attested_shard, shards), Counter())
    by_act: dict[str, dict] = {}
    for (act, d), n in counts.items():
        by_act.setdefault(act, {})[d] = n
    table = {"note": "provisions cited explicitly (with their own § / Art.) in the BVwG decisions",
             "citations": sum(counts.values()),
             "designations": {a: dict(sorted(v.items())) for a, v in sorted(by_act.items())}}
    path = out / "attested_designations.json"
    path.write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return path


def citation_index(index_shards: list[str], workers: int) -> tuple[list[dict], dict, dict]:
    """Pass 1: the keys of every document; the index before and after preferring the copy of a joined decision's own
    case."""
    with ProcessPoolExecutor(min(workers, len(index_shards))) as pool:
        keyed = list(pool.map(index_shard, index_shards))
    built = build_index([(d, set(keys)) for r in keyed for d, keys, _ in r["keyed"]])
    return keyed, built, prefer_own_case(built, {d: case for r in keyed for d, _, case in r["keyed"]})


def extract_all(tasks: list[tuple], index: dict, attested: Path, out: Path, workers: int) -> tuple[list, dict]:
    """Pass 2: the citations of every chunk, written to the year files in order; the index rows and the counts."""
    total = {k: Counter() for k in COUNTERS}
    rows, files = [], {}
    with ProcessPoolExecutor(workers, initializer=_init, initargs=(index, str(attested))) as pool:
        for r in pool.map(extract_chunk, *zip(*tasks)):         # in task order: year files are written in order
            if r["year"] not in files:
                files[r["year"]] = (out / f"{r['year']}.jsonl.gz.tmp").open("wb")
            files[r["year"]].write(r["gz"])
            rows += r["rows"]
            for k in total:
                total[k].update(r[k])
    for year, fh in files.items():
        fh.close()
        (out / f"{year}.jsonl.gz.tmp").replace(out / f"{year}.jsonl.gz")
    remove_other_year_files(out, [f"{year}.jsonl.gz" for year in files])
    return rows, total


def run(court: str, workers: int) -> int:
    documents = documents_dir(court)
    out = data_dir() / "processed" / "citations" / court
    shards = year_files(documents, f"stage 03 --court {court}")
    acts = load_acts(ACTS)
    courts = [court] if court == "bvwg" else ["bvwg", "vwgh", "vfgh"]     # VwGH / VfGH: an index of all three courts
    index_files = [f for c in courts for f in year_files(documents_dir(c), f"stage 03 --court {c}")]
    pass1_all, built, index = citation_index(sorted(index_files), workers)
    pass1 = [r for r in pass1_all if r["path"] in set(shards)]
    ambiguous = {k: v for k, v in built.items() if len(v) > 1}
    joined = sum(1 for k in ambiguous if len(index[k]) == 1)
    still = {k: v for k, v in index.items() if len(v) > 1}
    print(f"pass 1: {sum(r['documents'] for r in pass1):,} documents, {len(index):,} index keys, {len(ambiguous):,} "
          f"of several documents, {joined:,} of them resolved to the copy of the own case", flush=True)

    out.mkdir(parents=True, exist_ok=True)
    if court == "bvwg":
        attested = write_attested(shards, out, workers)
    else:
        attested = require(attested_path(), "stage 06 for the BVwG")
    attested_info = {"file": attested.relative_to(data_dir()).as_posix(), "sha256": file_sha256(attested),
                     "designations": sum(len(v) for v in json.loads(attested.read_text(encoding="utf-8"))
                                         ["designations"].values())}
    print(f"attested designations: {attested_info['designations']:,} in {attested_info['file']}", flush=True)

    tasks = [(r["path"], s, min(s + CHUNK, r["documents"])) for r in pass1 for s in range(0, r["documents"], CHUNK)]
    rows, total = extract_all(tasks, index, attested, out, workers)
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["ris_id"]))
    parse_report = documents / "parse_report.json"
    parse = json.loads(parse_report.read_text(encoding="utf-8")) if parse_report.exists() else {}
    report = build_report(rows, total, pass1, pass1_all, index, ambiguous, joined, still, parse, acts, attested_info)
    (out / "citation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    (out / "citation_report.md").write_text(render(report), encoding="utf-8")
    t = report["totals"]
    print(f"{t['documents']:,} documents, {t['citations']:,} citations, {t['documents_with_problems']:,} documents "
          f"with problems; report: {out / 'citation_report.md'}")
    return 0 if not t["documents_with_problems"] else 1


def _top(counter: Counter) -> list[tuple[str, int]]:
    return sorted(counter.items(), key=lambda x: (-x[1], x[0]))[:TOP]


def build_report(rows, total, pass1, pass1_all, index, ambiguous, joined, still, parse, acts, attested) -> dict:
    years = sorted(total["documents"])
    return {
        "stage": "06 citations", "acts": len({a for a, _ in acts.by_alias.values()}), "attested": attested,
        "input": {"texts": parse.get("inputs", {}).get("texts"),
                  "year_files_sha256": {Path(r["path"]).name: r["sha256"] for r in pass1}},
        "index": {"documents": sum(r["documents"] for r in pass1_all), "keys": len(index),
                  "keys_of_several_documents": len(ambiguous), "resolved_to_own_case": joined,
                  "ambiguous_keys": len(still), "ambiguous_examples": sorted(still.items())[:10]},
        "totals": {"documents": len(rows), "documents_with_problems": sum(1 for x in rows if x["problems"]),
                   "citations": sum(total["by_rule"].values()),
                   "publication_references": sum(total["by_type"][(y, PUBLICATION)] for y in years),
                   "documents_without_citations": sum(1 for x in rows if not x["citations"] and not x["problems"])},
        "problems": dict(sorted(total["problems"].items())),
        "per_year": {y: {"documents": total["documents"][y],
                         "citations": sum(total["by_type"][(y, t)] for t in TYPES),
                         **{t: total["by_type"][(y, t)] for t in TYPES}} for y in years},
        "by_rule": dict(sorted(total["by_rule"].items(), key=lambda x: (-x[1], x[0]))),
        "extensions": {col: total["extract"][k] for k, col in STATS.items()},
        "targets": {f"{t} -> {how}": n for (t, how), n in sorted(total["targets"].items())},
        "top_acts": [[a, n, total["acts_docs"][a]] for a, n in _top(total["acts"])],
        "top_provisions": [[p, n, total["provisions_docs"][p]] for p, n in _top(total["provisions"])],
        "top_decisions": [[d, n, total["decisions_docs"][d]] for d, n in _top(total["decisions"])],
        "distinct": {"acts": len(total["acts"]), "provisions": len(total["provisions"]),
                     "decisions": len(total["decisions"])},
    }


def render(r: dict) -> str:
    texts = r["input"]["texts"] or {}
    t = r["totals"]

    def per(n: int, d: int) -> str:
        return f"{n / d:.1f}" if d else "–"

    norms = t["citations"] - t["publication_references"]
    lines = ["# Citations (stage 06)", "",
             f"Input: stage 03 documents, text fingerprint `{(texts.get('sha256') or '?')[:16]}…` "
             f"({texts.get('documents', 0):,} texts); SHA-256 of each year file in `citation_report.json`.",
             f"Acts: {r['acts']} (`config/citations/acts.yaml`); rules: `src/asylum_corpus/citations/` (statutes.py, "
             "cases.py).",
             "",
             f"Provisions cited explicitly: {r['attested']['designations']:,} designations, `{r['attested']['file']}` "
             f"(SHA-256 `{r['attested']['sha256'][:16]}…`).", "",
             f"Documents: {t['documents']:,}; citations {t['citations']:,} ({per(t['citations'], t['documents'])} per "
             f"decision); documents with problems {t['documents_with_problems']:,}"
             + (f" ({', '.join(f'{k} {v:,}' for k, v in r['problems'].items())})" if r["problems"] else "")
             + f"; without any citation {t['documents_without_citations']:,}.", "",
             f"Of the citations, {t['publication_references']:,} are publication references (law-gazette numbers): "
             f"kept with their spans, but not citations of a norm; the counts of acts and provisions below leave them "
             f"out. Citations of norms and decisions: {norms:,} ({per(norms, t['documents'])} per decision).", "",
             f"Citation index: {r['index']['keys']:,} keys of {r['index']['documents']:,} documents. "
             f"{r['index']['keys_of_several_documents']:,} keys belong to several documents (a joined decision is "
             f"published once per case); {r['index']['resolved_to_own_case']:,} of them name the copy published under "
             f"their own case number, {r['index']['ambiguous_keys']:,} stay ambiguous (they give a case reference).",
             "",
             "## Per year", "",
             "| Year | Documents | Citations | per decision | " + " | ".join(TYPES) + " |",
             "|---|---|---|---|" + "---|" * len(TYPES)]
    for y, c in r["per_year"].items():
        lines.append(f"| {y} | {c['documents']:,} | {c['citations']:,} | {per(c['citations'], c['documents'])} | "
                     + " | ".join(f"{c[x]:,}" for x in TYPES) + " |")
    lines += ["", "## By rule", "", "`_chain`: one provision of a chain; `leg_cit`: \"leg. cit.\"; "
              "`vwgh_case_number_series`: a further number of a series; `bgbl`: a law-gazette number; "
              "`eu_act_provision`: a provision of an EU act named by its number; `eu_act_number_name` / `_bare`: an "
              "EU act number after a compound name or abbreviation, or alone; `ecthr_application_number_rs`: an "
              "application number after \"Rs\". The rules: `statutes.py` and `cases.py` in "
              "`src/asylum_corpus/citations/`.", "",
              "| Rule | Citations |", "|---|---|"]
    lines += [f"| `{k}` | {v:,} |" for k, v in r["by_rule"].items()]
    e = r["extensions"]
    lines += ["", f"\"leg. cit.\": {e['leg_cit_resolved']:,} resolved to the act cited before it in the paragraph, "
                  f"{e['leg_cit_without_act']:,} without a preceding act (no citation). VwGH series: "
                  f"{e['vwgh_series_numbers']:,} further numbers. The decision's own case numbers skipped: "
                  f"{e['own_case_numbers_skipped']:,}.", "",
              "## target_id", "", "| Citation type -> target | Citations |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["targets"].items()]
    for title, key, col in (("Most cited acts", "top_acts", "Act"), ("Most cited provisions", "top_provisions",
                            "Provision"), ("Most cited decisions", "top_decisions", "Decision")):
        lines += ["", f"## {title}", "", f"Distinct: {r['distinct'][key.split('_')[1]]:,}.", "",
                  f"| {col} | Citations | Decisions citing |", "|---|---|---|"]
        lines += [f"| {a} | {n:,} | {d:,} |" for a, n, d in r[key]]
    return "\n".join(lines) + "\n"
