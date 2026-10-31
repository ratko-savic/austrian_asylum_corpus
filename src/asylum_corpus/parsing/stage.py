"""Stage 03: RIS XML and metadata -> documents (schemas/document.schema.json).

Parsing, text normalisation and the sections run together, so all offsets point into the normalised text. Every
document is validated against the schema and checked (checks.py: offsets, section partition, IDs, no text lost or
added); a document with an error is not written.

Input, BVwG: the included candidates (interim/filtered/filter_decisions.jsonl) with a stored text in
raw/bvwg/<partition>/<year>/<RIS ID>.{xml,json}. VwGH / VfGH: the pool of the connected step
(raw/connected/manifest.jsonl); the group is "connected" where the connected step found in it the case number of a
downloaded BVwG decision (interim/connected/connections.jsonl), else "pool".

Output, processed/documents/<court>/:
    <year>.jsonl.gz           the documents of that decision year, sorted by RIS ID
    index.csv                 every input decision: status (ok / error / empty_source / pending_text), counts, problems
    parse_report.md / .json   counts per year, errors and warnings, inputs
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..common import GzipLines, data_dir, file_sha256, read_jsonl, remove_other_year_files, require, text_fingerprint
from .checks import ERRORS, WARNINGS, check_document, schema_errors
from .document import EmptySource, build_document
from .xml import FOOTNOTE_LABEL

FAILED = set(ERRORS) | {"schema", "build"}
INDEX_COLUMNS = ["ris_id", "decision_date", "group", "interim_procedural", "status", "section_types", "sections",
                 "paragraphs", "characters", "case_numbers", "document_numbers", "pronouncement_date", "problems"]


def parse_one(task: tuple[str, str, str]) -> dict:
    """Build, validate and check one document (runs in a worker process)."""
    ris_id, xml_path, json_path = task
    try:
        xml = Path(xml_path).read_bytes()
        doc = build_document(xml, json.loads(Path(json_path).read_text(encoding="utf-8")))
    except EmptySource as exc:  # RIS published the decision without text: not a parser error
        return {"ris_id": ris_id, "problems": [("empty_source", str(exc))], "line": None, "empty": True}
    except Exception as exc:  # recorded as an error of this document
        return {"ris_id": ris_id, "problems": [("build", f"{type(exc).__name__}: {exc}")], "line": None}
    problems = [("schema", e) for e in schema_errors(doc)] + check_document(doc, xml)
    failed = any(code in FAILED for code, _ in problems)
    return {
        "ris_id": ris_id, "problems": problems,
        "line": None if failed else json.dumps(doc, ensure_ascii=False),
        "stats": {"section_types": " ".join(s["type"] for s in doc["sections"]), "sections": len(doc["sections"]),
                  "paragraphs": len(doc["paragraphs"]), "characters": len(doc["full_text"]),
                  "case_numbers": len(doc["case_numbers"]), "document_numbers": len(doc["document_numbers"]),
                  "pronouncement_date": doc["pronouncement_date"],
                  "footnotes": any(s["source_label"] == FOOTNOTE_LABEL for s in doc["sections"])},
    }


class ShardWriter:
    """One gzip JSONL file per decision year, written in input order."""

    def __init__(self, out: Path):
        self.out, self.year, self.file, self.written = out, None, None, []

    def write(self, year: str, line: str) -> None:
        if year != self.year:
            self.close()
            self.year, self.file = year, GzipLines(self.out / f"{year}.jsonl.gz")
        self.file.write(line + "\n")

    def close(self) -> None:
        if self.file:
            self.file.close()
            self.written.append(self.year)
            self.file = None


def bvwg_inputs(decisions: Path, root: Path) -> tuple[list[dict], list[tuple], set]:
    """The included BVwG candidates and the tasks of those with a stored text."""
    inventory = root / "interim" / "candidates" / "inventory.jsonl"
    partition = {row["ris_id"]: row["partition"] for row in read_jsonl(require(inventory, "stage 01 inventory"))}
    included = [d for d in read_jsonl(require(decisions, "stage 02")) if d["verdict"] == "include"]
    included.sort(key=lambda d: (d["decision_date"][:4], d["ris_id"]))
    tasks, pending = [], set()
    for d in included:
        base = root / "raw" / "bvwg" / partition[d["ris_id"]] / d["decision_date"][:4] / d["ris_id"]
        if base.with_suffix(".xml").exists():
            tasks.append((d["ris_id"], str(base.with_suffix(".xml")), str(base.with_suffix(".json"))))
        else:
            pending.add(d["ris_id"])
    return included, tasks, pending


def higher_court_inputs(court: str, decisions: Path, root: Path) -> tuple[list[dict], list[tuple], set]:
    """The VwGH or VfGH decisions of the connected step's pool."""
    raw = root / "raw"
    connections = root / "interim" / "connected" / "connections.jsonl"
    connected = {r["ris_id"] for r in read_jsonl(require(connections, "stage 01 connected"))}
    included, tasks, pending = [], [], set()
    for m in read_jsonl(require(decisions, "stage 01 connected")):
        if m["court"] != court:
            continue
        included.append({"ris_id": m["ris_id"], "decision_date": m["decision_date"], "flags": [],
                         "group": "connected" if m["ris_id"] in connected else "pool"})
        xml = raw / m["xml_file"] if m["xml_file"] else None
        if m["status"] == "ok" and xml and xml.exists():
            tasks.append((m["ris_id"], str(xml), str(raw / m["metadata_file"])))
        else:
            pending.add(m["ris_id"])
    included.sort(key=lambda d: (d["decision_date"][:4], d["ris_id"]))
    order = {d["ris_id"]: (d["decision_date"][:4], d["ris_id"]) for d in included}
    tasks.sort(key=lambda t: order[t[0]])
    return included, tasks, pending


def status_of(result: dict | None) -> str:
    if not result:
        return "pending_text"
    if result.get("empty"):
        return "empty_source"
    return "error" if any(code in FAILED for code, _ in result["problems"]) else "ok"


def run(court: str, workers: int) -> int:
    root = data_dir()
    out = root / "processed" / "documents" / court
    if court == "bvwg":
        decisions = root / "interim" / "filtered" / "filter_decisions.jsonl"
        included, tasks, pending = bvwg_inputs(decisions, root)
        log = root / "raw" / "retrieval_log.jsonl"
    else:
        decisions = root / "raw" / "connected" / "manifest.jsonl"
        included, tasks, pending = higher_court_inputs(court, decisions, root)
        log = root / "raw" / "connected" / "retrieval_log.jsonl"
    texts = text_fingerprint(log, [t[0] for t in tasks])
    print(f"Parser ({court}): {len(included):,} decisions, {len(tasks):,} with a stored text, "
          f"{len(pending):,} pending; {workers} processes")
    out.mkdir(parents=True, exist_ok=True)
    year_of = {d["ris_id"]: d["decision_date"][:4] for d in included}
    results: dict[str, dict] = {}
    writer = ShardWriter(out)
    with ProcessPoolExecutor(workers) as pool:
        for n, r in enumerate(pool.map(parse_one, tasks, chunksize=16), 1):
            if r["line"]:
                writer.write(year_of[r["ris_id"]], r["line"])
            r.pop("line")
            results[r["ris_id"]] = r
            if n % 2000 == 0:
                print(f"  {n:,}/{len(tasks):,}")
    writer.close()
    remove_other_year_files(out, [f"{y}.jsonl.gz" for y in writer.written])

    rows = []
    for d in included:
        r = results.get(d["ris_id"])
        codes = [c for c, _ in r["problems"]] if r else []
        rows.append({"ris_id": d["ris_id"], "decision_date": d["decision_date"], "group": d["group"],
                     "interim_procedural": "interim_procedural" in d["flags"], "status": status_of(r),
                     **(r.get("stats", {}) if r else {}), "problems": "; ".join(dict.fromkeys(codes))})
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX_COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["ris_id"]))
    inputs = {"decisions": decisions.name, "decisions_sha256": file_sha256(decisions), "texts": texts}
    report = build_report(rows, results, inputs)
    report["court"] = court
    (out / "parse_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "parse_report.md").write_text(render_report(report), encoding="utf-8")
    t = report["totals"]
    print(f"ok {t['ok']:,}, error {t['error']:,}, empty source {t['empty_source']:,}, pending {t['pending_text']:,}; "
          f"warnings {report['warnings']}; report: {out / 'parse_report.md'}")
    return 1 if t["error"] else 0


def build_report(rows: list[dict], results: dict, inputs: dict) -> dict:
    per_year: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        per_year[r["decision_date"][:4]][r["status"]] += 1
    ok = [r for r in rows if r["status"] == "ok"]
    examples: dict[str, list] = defaultdict(list)
    for rid, r in sorted(results.items()):
        for code, detail in r["problems"]:
            if len(examples[code]) < 10:
                examples[code].append({"ris_id": rid, "detail": detail})

    def count(codes: set[str]) -> dict:
        return dict(sorted(Counter(c for r in results.values() for c in dict.fromkeys(c for c, _ in r["problems"])
                                   if c in codes).items()))

    para = [r["paragraphs"] for r in ok]
    chars = [r["characters"] for r in ok]
    return {
        "inputs": inputs,
        "totals": {"included": len(rows), **{s: sum(1 for r in rows if r["status"] == s)
                                             for s in ("ok", "error", "empty_source", "pending_text")}},
        "errors": count(FAILED), "warnings": count(set(WARNINGS)),
        "per_year": {y: dict(sorted(c.items())) for y, c in sorted(per_year.items())},
        "section_type_sequences": dict(Counter(r["section_types"] for r in ok).most_common(12)),
        "joined_decisions": sum(1 for r in ok if r["case_numbers"] > 1),
        "with_document_numbers": sum(1 for r in ok if r["document_numbers"]),
        "orally_pronounced": sum(1 for r in ok if r["pronouncement_date"]),
        "with_footnotes": sum(1 for r in results.values() if r.get("stats", {}).get("footnotes")),
        "paragraphs": {"median": statistics.median(para), "max": max(para)} if para else {},
        "characters": {"median": statistics.median(chars), "max": max(chars), "total": sum(chars)} if chars else {},
        "examples": dict(sorted(examples.items())),
    }


def render_report(r: dict) -> str:
    t, inputs = r["totals"], r["inputs"]
    lines = ["# Parsing: documents", "",
             f"Court {r['court']}. Input: `{inputs['decisions']}`. Same inputs and code give identical files.", "",
             f"Input fingerprint: {inputs['texts']['documents']:,} decision texts, SHA-256 "
             f"`{inputs['texts']['sha256'][:16]}…` (RIS ID + XML hash from the retrieval log; "
             f"{inputs['texts']['without_hash']} without hash); decisions file `{inputs['decisions_sha256'][:16]}…`.",
             "",
             f"- Input decisions: {t['included']:,}",
             f"- Parsed and written: **{t['ok']:,}**; errors (not written): {t['error']:,}; published by RIS without "
             f"text (not written): {t['empty_source']:,}; text not yet stored: {t['pending_text']:,}",
             "- Errors: " + (", ".join(f"`{k}` {v:,}" for k, v in r["errors"].items()) or "none"),
             "- Warnings: " + (", ".join(f"`{k}` {v:,}" for k, v in r["warnings"].items()) or "none"),
             f"- Joined decisions (several case numbers): {r['joined_decisions']:,}; with document numbers: "
             f"{r['with_document_numbers']:,}; orally pronounced: {r['orally_pronounced']:,}; with footnotes: "
             f"{r['with_footnotes']:,}"]
    if r["paragraphs"]:
        lines.append(f"- Paragraphs per document: median {r['paragraphs']['median']:,}, "
                     f"max {r['paragraphs']['max']:,}; characters: median {r['characters']['median']:,}, "
                     f"max {r['characters']['max']:,}, total {r['characters']['total']:,}")
    lines += ["", "## Per year", "", "| Year | ok | error | empty_source | pending_text |", "|---|---|---|---|---|"]
    for y, c in r["per_year"].items():
        lines.append(f"| {y} | {c.get('ok', 0):,} | {c.get('error', 0):,} | {c.get('empty_source', 0):,} | "
                     f"{c.get('pending_text', 0):,} |")
    lines += ["", "## Section sequences (most common)", "", "| Sections | Documents |", "|---|---|"]
    lines += [f"| {seq} | {n:,} |" for seq, n in r["section_type_sequences"].items()]
    if r["examples"]:
        lines += ["", "## Examples of problems", ""]
        for code, items in r["examples"].items():
            lines.append(f"- `{code}`: " + "; ".join(f"{i['ris_id']} ({i['detail'][:160]})" for i in items[:5]))
    return "\n".join(lines) + "\n"
