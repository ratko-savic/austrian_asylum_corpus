"""Stage 08: the outcomes of the decisions.

BVwG (run_bvwg): one `normalized_outcome` annotation per outcome unit - the operative parts that decide the same
appellant or appellants (schemas/outcome.schema.json): the dispositions with their spans, issues and operative part, the
per-issue table, the outcome (with a subtype for "other"), the protection outcome, the admissibility of a revision, the
decision form, and each part's own reading (`parts`). Rules: config/outcomes/bvwg_*.yaml. Provisions are read with the
stage 06 extractor and its table of attested designations (stage 06 runs first). Output (processed/outcomes/bvwg/):
    <year>.jsonl.gz            the annotations, in document order and by unit
    index.csv                  every outcome unit; the document's outcome and protection (the common value of its
                               units, or "mixed")
    outcome_report.md / .json  outcomes per year, template, RIS decision type and stated form; protection per year and
                               template; dispositions and issues
Checks per annotation: the outcome and annotation schemas; each disposition's span inside the operative part it names,
which is one of the unit's, with text = full_text[start:end], in order and not overlapping; each resolved point's span
inside the reasoning. An annotation that fails a check is not written.

VwGH / VfGH (run_higher): one annotation per decision (schemas/higher_court_outcome.schema.json; rules
config/outcomes/higher_courts.yaml), written to processed/outcomes/<court>/: outcomes.jsonl, index.csv,
outcome_report.md / .json.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..citations.stage import attested_path
from ..citations.statutes import load_acts, with_attested
from ..common import (
    CONFIG, data_dir, file_sha256, pct, read_csv, read_jsonl, remove_other_year_files, require, schema_validator,
    write_lines_gz, year_files,
)
from .annotate import document_outcomes, reasoning_section
from .higher import HigherRules, outcome_of
from .rules import load_rules


ACTS = CONFIG / "citations" / "acts.yaml"
HIGHER_RULES = CONFIG / "outcomes" / "higher_courts.yaml"
OUTCOMES = ["upheld", "partially_upheld", "dismissed", "rejected", "remanded", "other"]
SUBTYPES = ["discontinued", "interim", "procedural", "unrecognized"]
PROTECTIONS = ["asylum", "subsidiary_protection", "residence_right", "remanded", "none", "not_decided"]
PROBLEMS = ["no_disposition", "unattributed_sentence", "unresolved_point", "ambiguous_asylg"]
INDEX = ["ris_id", "operative_part", "operative_parts", "unit_sections", "unit_joined_by", "decision_date",
         "ris_decision_type", "decision_form", "outcome", "outcome_subtype", "protection", "issues", "revision",
         "problems", "document_outcome", "document_protection"]
HIGHER_INDEX = ["ris_id", "decision_date", "group", "decision_type", "register", "outcome", "partial", "transferred",
                "grounds", "revision_party", "types", "problems"]
JOINS = ["continuation", "repeated_head"]
COUNTERS = ("dispositions", "issue_dispositions", "points", "problems", "parts_kept_apart")


def check(doc: dict, ann: dict, sections: list[dict], reasoning: dict | None) -> list[str]:
    problems = [f"schema: {'/'.join(map(str, e.path)) or '(annotation)'}: {e.message[:120]}"
                for e in schema_validator("outcome").iter_errors(ann)]
    problems += [f"annotation schema: {e.message[:120]}" for e in schema_validator("annotation").iter_errors(ann)]
    by_id = {s["id"]: s for s in sections}
    if ann["value"]["operative_part_ids"] != [s["id"] for s in sections]:
        problems.append("unit: operative_part_ids differ from the unit's sections")
    last = sections[0]["start"]
    for d in ann["value"]["dispositions"]:
        section = by_id.get(d["operative_part_id"])
        if section is None:
            problems.append(f"unit: disposition {d['start']}-{d['end']} names an operative part outside the unit")
        elif not (last <= d["start"] < d["end"] <= section["end"] and d["start"] >= section["start"]):
            problems.append(f"span: disposition {d['start']}-{d['end']} outside its operative part or overlapping")
        elif d["text"] != doc["full_text"][d["start"]:d["end"]]:
            problems.append(f"text: disposition {d['start']}-{d['end']}")
        last = max(last, d["end"])
        for p in d["points"]:
            if p["issue"] and not (reasoning and reasoning["start"] <= p["start"] < p["end"] <= reasoning["end"]):
                problems.append(f"span: point {p['point']} outside the reasoning")
    return problems


def index_row(doc: dict, v: dict) -> dict:
    return {"ris_id": doc["ris_id"], "operative_part": v["operative_part"], "operative_parts": v["operative_parts"],
            "unit_sections": " ".join(i.split("#s:")[1] for i in v["operative_part_ids"]),
            "unit_joined_by": " ".join(p["joined_by"] for p in v["parts"] if p["joined_by"]),
            "decision_date": doc["decision_date"], "ris_decision_type": doc["decision_type"],
            "decision_form": v["decision_form"] or "", "outcome": v["outcome"],
            "outcome_subtype": v["outcome_subtype"] or "", "protection": v["protection"],
            "issues": "; ".join(f"{r['issue']}={'+'.join(r['dispositions'])}" for r in v["issues"]),
            "revision": v["revision"], "problems": " ".join(v["problems"])}


def shard(path: str) -> dict:
    """The annotations of one year file and the statistics for the index and the report (worker)."""
    acts, rules = with_attested(load_acts(ACTS), attested_path()), load_rules()
    rows, errors, lines = [], [], []
    st = {k: Counter() for k in COUNTERS}
    for doc in read_jsonl(Path(path)):
        rsec = reasoning_section(doc)
        for ann, secs in document_outcomes(doc, acts, rules, st["parts_kept_apart"]):
            v = ann["value"]
            bad = check(doc, ann, secs, rsec)
            if bad:
                errors.append({"ris_id": doc["ris_id"], "operative_part": v["operative_part"], "problems": bad})
                continue
            lines.append(json.dumps(ann, ensure_ascii=False) + "\n")
            st["dispositions"].update({t for d in v["dispositions"] for t in d["types"]})
            st["issue_dispositions"].update((r["issue"] if not r["issue"].startswith("point:")
                                             else "point (unresolved)", t)
                                            for r in v["issues"] for t in r["dispositions"])
            st["points"].update("resolved" if p["issue"] else "unresolved" for d in v["dispositions"]
                                for p in d["points"])
            st["problems"].update(v["problems"])
            rows.append(index_row(doc, v))
    return {"year": Path(path).name.split(".")[0], "path": path, "sha256": file_sha256(Path(path)), "lines": lines,
            "rows": rows, "errors": errors, **st}


def templates_of(data: Path) -> dict[str, str]:
    """For the report: the structure template of each document (stage 05)."""
    path = data / "processed" / "sections" / "bvwg" / "index.csv"
    return {x["ris_id"]: x["template"] for x in read_csv(path)} if path.exists() else {}


def run_bvwg(workers: int) -> int:
    data = data_dir()
    documents = data / "processed" / "documents" / "bvwg"
    out = data / "processed" / "outcomes" / "bvwg"
    shards = year_files(documents, "stage 03")
    require(attested_path(), "stage 06 for the BVwG")
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(min(workers, len(shards))) as pool:
        results = list(pool.map(shard, shards))
    rows, errors, total = [], [], {k: Counter() for k in COUNTERS}
    for r in results:
        write_lines_gz(out / f"{r['year']}.jsonl.gz", r["lines"])
        rows += r["rows"]
        errors += r["errors"]
        for k in total:
            total[k].update(r[k])
    remove_other_year_files(out, [f"{r['year']}.jsonl.gz" for r in results])

    by_doc: dict[str, list[dict]] = {}       # document level: the common value of the units, or "mixed"
    for x in rows:
        by_doc.setdefault(x["ris_id"], []).append(x)
    for xs in by_doc.values():
        for key, col in (("outcome", "document_outcome"), ("protection", "document_protection")):
            values = {x[key] for x in xs}
            for x in xs:
                x[col] = values.pop() if len(values) == 1 else "mixed"
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=INDEX, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: (x["ris_id"], x["operative_part"])))

    parse_path = documents / "parse_report.json"
    parse = json.loads(parse_path.read_text(encoding="utf-8")) if parse_path.exists() else {}
    report = build_report(rows, by_doc, errors, total, templates_of(data), parse, results)
    (out / "outcome_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "outcome_report.md").write_text(render(report), encoding="utf-8")
    t = report["totals"]
    print(f"{t['documents']:,} documents, {t['units']:,} units of {t['operative_parts']:,} operative parts, "
          f"{t['failing_checks']:,} failing a check; report: {out / 'outcome_report.md'}")
    return 0 if not errors else 1


def crosstab(rows: list[dict], key, values: list[str], col: str) -> dict:
    out: dict[str, Counter] = {}
    for x in rows:
        out.setdefault(key(x), Counter())[x[col]] += 1
    return {k: {v: c.get(v, 0) for v in values} for k, c in sorted(out.items())}


def build_report(rows, by_doc, errors, total, templates, parse, results) -> dict:
    attested = attested_path()

    def template(x: dict) -> str:
        return templates.get(x["ris_id"], "?")

    return {
        "stage": "08 outcomes",
        "input": {"texts": parse.get("inputs", {}).get("texts"),
                  "attested_designations": {"file": attested.relative_to(data_dir()).as_posix(),
                                            "sha256": file_sha256(attested)},
                  "year_files_sha256": {Path(r["path"]).name: r["sha256"] for r in results}},
        "totals": {"documents": len(by_doc), "units": len(rows),
                   "operative_parts": sum(len(x["unit_sections"].split()) for x in rows), "failing_checks": len(errors),
                   "units_of_several_parts": sum(1 for x in rows if x["unit_joined_by"]),
                   "documents_with_several_units": sum(1 for xs in by_doc.values() if len(xs) > 1),
                   "documents_mixed_outcome": sum(1 for xs in by_doc.values() if xs[0]["document_outcome"] == "mixed")},
        "joined_by": {j: sum(x["unit_joined_by"].split().count(j) for x in rows) for j in JOINS},
        "parts_kept_apart": dict(sorted(total["parts_kept_apart"].items())),
        "errors": errors[:20],
        "problems": {p: total["problems"][p] for p in PROBLEMS},
        "points": dict(total["points"]),
        "outcome": {o: sum(1 for x in rows if x["outcome"] == o) for o in OUTCOMES},
        "outcome_subtype": {s: sum(1 for x in rows if x["outcome_subtype"] == s) for s in SUBTYPES},
        "protection": {p: sum(1 for x in rows if x["protection"] == p) for p in PROTECTIONS},
        "outcome_per_year": crosstab(rows, lambda x: x["decision_date"][:4], OUTCOMES, "outcome"),
        "protection_per_year": crosstab(rows, lambda x: x["decision_date"][:4], PROTECTIONS, "protection"),
        "outcome_per_template": crosstab(rows, template, OUTCOMES, "outcome"),
        "protection_per_template": crosstab(rows, template, PROTECTIONS, "protection"),
        "outcome_per_ris_type": crosstab(rows, lambda x: x["ris_decision_type"], OUTCOMES, "outcome"),
        "outcome_per_decision_form": crosstab(rows, lambda x: x["decision_form"] or "none", OUTCOMES, "outcome"),
        "revision": dict(Counter(x["revision"] for x in rows).most_common()),
        "dispositions": dict(total["dispositions"].most_common()),
        "issue_dispositions": {f"{i} / {d}": n for (i, d), n in sorted(total["issue_dispositions"].items(),
                                                                       key=lambda x: (-x[1], x[0]))},
    }


def render(r: dict) -> str:
    texts = r["input"]["texts"] or {}
    t = r["totals"]
    n = t["units"]
    lines = ["# Outcomes (stage 08)", "",
             f"Input: stage 03 documents, text fingerprint `{(texts.get('sha256') or '?')[:16]}…` "
             f"({texts.get('documents', 0):,} texts); SHA-256 of each year file in `outcome_report.json`. Rules "
             "`config/outcomes/`, acts `config/citations/acts.yaml`.", "",
             f"Documents: {t['documents']:,}; outcome units (the operative parts deciding the same appellants: one "
             f"appellant's judgment and order form one unit) {n:,}, from {t['operative_parts']:,} operative parts; "
             f"failing a check {t['failing_checks']:,}. Units of several operative parts "
             f"{t['units_of_several_parts']:,} (parts joined: "
             + ", ".join(f"{k} {v:,}" for k, v in r["joined_by"].items()) + "; config/outcomes/bvwg_units.yaml); "
             "consecutive parts kept apart: " + ", ".join(f"{k} {v:,}" for k, v in r["parts_kept_apart"].items())
             + f". Documents with several units {t['documents_with_several_units']:,}, of which with different "
             f"outcomes {t['documents_mixed_outcome']:,}.", "",
             "Problems (units): " + ", ".join(f"{k} {v:,}" for k, v in r["problems"].items())
             + f". Numbered points of the authority's decision: resolved {r['points'].get('resolved', 0):,}, "
             f"unresolved {r['points'].get('unresolved', 0):,}.", "",
             "## Outcome and protection", "", "| Outcome | Units | Share |", "|---|---|---|"]
    lines += [f"| {k} | {v:,} | {pct(v, n)} |" for k, v in r["outcome"].items()]
    lines += ["", "Subtypes of other: " + ", ".join(f"{k} {v:,}" for k, v in r["outcome_subtype"].items()) + ".", "",
              "| Protection | Units | Share |", "|---|---|---|"]
    lines += [f"| {k} | {v:,} | {pct(v, n)} |" for k, v in r["protection"].items()]
    for title, key, cols in (("Outcome per year", "outcome_per_year", OUTCOMES),
                             ("Protection per year", "protection_per_year", PROTECTIONS),
                             ("Outcome per template (stage 05)", "outcome_per_template", OUTCOMES),
                             ("Protection per template (stage 05)", "protection_per_template", PROTECTIONS),
                             ("Outcome per RIS decision type", "outcome_per_ris_type", OUTCOMES),
                             ("Outcome per decision form stated in the text", "outcome_per_decision_form", OUTCOMES)):
        lines += ["", f"## {title}", "", "| | Units | " + " | ".join(cols) + " |", "|---|---|" + "---|" * len(cols)]
        for k, c in r[key].items():
            m = sum(c.values())
            lines.append(f"| {k} | {m:,} | " + " | ".join(f"{c[x]:,} ({pct(c[x], m)})" for x in cols) + " |")
    lines += ["", "Revision: " + ", ".join(f"{k} {v:,}" for k, v in r["revision"].items()) + ".", "",
              "## Dispositions and issues", "", "Units containing each disposition: "
              + ", ".join(f"{k} {v:,}" for k, v in r["dispositions"].items()) + ".", "",
              "| Issue / disposition | Units |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in list(r["issue_dispositions"].items())[:40]]
    return "\n".join(lines) + "\n"


def run_higher(court: str) -> int:
    """VwGH / VfGH outcomes."""
    rules = HigherRules.load(HIGHER_RULES)
    check_schema = schema_validator("higher_court_outcome")
    docs = data_dir() / "processed" / "documents" / court
    out = data_dir() / "processed" / "outcomes" / court
    shards = year_files(docs, f"stage 03 --court {court}")
    groups = {r["ris_id"]: r["group"] for r in read_csv(require(docs / "index.csv", f"stage 03 --court {court}"))}
    out.mkdir(parents=True, exist_ok=True)
    rows, anns, failing = [], [], 0
    for path in shards:
        for doc in read_jsonl(path):
            ann = outcome_of(doc, rules)
            errors = list(check_schema.iter_errors(ann))
            spans_ok = all(doc["full_text"][d["start"]:d["end"]] == d["text"] for d in ann["value"]["dispositions"])
            if errors or not spans_ok:
                failing += 1
                continue
            anns.append(ann)
            v = ann["value"]
            rows.append({"ris_id": doc["ris_id"], "decision_date": doc["decision_date"],
                         "group": groups.get(doc["ris_id"], ""), "decision_type": doc["decision_type"],
                         "register": v["register"] or "", "outcome": v["outcome"], "partial": v["partial"],
                         "transferred": v["transferred"], "grounds": " ".join(v["grounds"]),
                         "revision_party": v["revision_party"],
                         "types": " ".join(sorted({t for d in v["dispositions"] for t in d["types"]})),
                         "problems": " ".join(v["problems"])})
    anns.sort(key=lambda a: a["document_id"])
    with (out / "outcomes.jsonl").open("w", encoding="utf-8") as fh:
        for a in anns:
            fh.write(json.dumps(a, ensure_ascii=False, sort_keys=True) + "\n")
    rows.sort(key=lambda r: r["ris_id"])
    with (out / "index.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=HIGHER_INDEX)
        w.writeheader()
        w.writerows(rows)

    def tally(key, subset=None):
        return dict(Counter(str(r[key]) for r in rows if subset is None or subset(r)).most_common())

    report = {"stage": "08 outcomes", "court": court,
              "inputs": {"documents": len(rows) + failing,
                         "parse_report_sha256": file_sha256(docs / "parse_report.json")},
              "totals": {"annotations": len(anns), "failing_checks": failing},
              "outcome": tally("outcome"), "outcome_connected": tally("outcome", lambda r: r["group"] == "connected"),
              "register": tally("register"),
              "partial": tally("partial", lambda r: r["outcome"] == "annulled"),
              "transferred": tally("transferred"), "grounds": tally("grounds", lambda r: r["outcome"] == "annulled"),
              "revision_party": tally("revision_party"), "problems": tally("problems", lambda r: r["problems"])}
    (out / "outcome_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lines = [f"# Outcomes of the {court.upper()} decisions (stage 08 --court {court})", "",
             f"Rules `config/outcomes/higher_courts.yaml`. {len(anns):,} decisions, {failing} failing a check.", ""]
    for key in ("outcome", "outcome_connected", "register", "partial", "transferred", "grounds", "revision_party",
                "problems"):
        lines += [f"## {key.replace('_', ' ')}", "", "| Value | Decisions |", "|---|---|"]
        lines += [f"| {k or '(none)'} | {v:,} |" for k, v in report[key].items()] + [""]
    (out / "outcome_report.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"{court}: {len(anns):,} outcomes, {failing} failing; outcome {report['outcome']}")
    return 1 if failing else 0
