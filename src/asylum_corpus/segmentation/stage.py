"""Stage 05: the structure of the BVwG decisions, in two steps.

segment  reads the stage 03 documents (processed/documents/bvwg/) and writes them with their coarse sections, the
         subsections of the reasoning, and the structure template (processed/sections/bvwg/,
         schemas/sections.schema.json):
             <year>.jsonl.gz            the documents, in the order of the input year files
             index.csv                  every document: template, subsection types, hierarchy rules applied, headings
                                        dropped, problems
             segment_report.md / .json  templates per year, by RIS decision type and by the form stated in the text;
                                        hierarchy rules and heading outcomes per rule; inputs
fine     reads those documents and writes them with their fine sections (processed/fine_sections/bvwg/,
         schemas/fine_sections.schema.json): <year>.jsonl.gz, index.csv, fine_report.md / .json
A document that fails a check (checks.py) is not written; its problems are in index.csv. Same input and code give the
same bytes.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from functools import lru_cache
from pathlib import Path

from ..common import CONFIG, GzipLines, data_dir, file_sha256, pct, read_jsonl, remove_other_year_files, year_files
from .checks import check_fine, check_subsections, sections_schema_errors
from .fine import fine_sections
from .headings import HeadingRules, load_heading_rules
from .parts_rules import PartsRules, load_parts_rules
from .subsections import subsections
from .templates import TEMPLATES

HEADING_RULES = CONFIG / "segmentation" / "bvwg_headings.yaml"
PARTS_RULES = CONFIG / "segmentation" / "bvwg_parts.yaml"
# the hierarchy rules that can change a result, in the order they apply (parents and extent always apply)
HIERARCHY = ("zu_a_before_history", "inside_history", "own_part_in_history", "erwogen_after_legal", "subtitle",
             "sub_point", "point_of_evidence", "zu_a_encloses", "out_of_order")
DROPS = ("zu_a_before_history", "inside_history", "erwogen_after_legal", "subtitle", "fallback", "sub_point",
         "point_of_evidence")
SEGMENT_INDEX = ["ris_id", "decision_date", "ris_decision_type", "text_forms", "status", "template", "subsections",
                 "max_level", "subsection_types", "hierarchy_rules_applied", "headings_dropped", "problems"]
FINE_INDEX = ["ris_id", "decision_date", "template", "coarse_sections", "operative_sections", "parts",
              "point_assessments", "with_topic", "max_level", "nesting", "problems"]
FINE_DETECTIONS = ("heading_like", "label_structure", "revision_sentence")


@lru_cache(maxsize=None)
def heading_rules() -> HeadingRules:
    return load_heading_rules(HEADING_RULES)


@lru_cache(maxsize=None)
def parts_rules() -> PartsRules:
    return load_parts_rules(PARTS_RULES)


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(sorted(rows, key=lambda x: x["ris_id"]))


def text_form(forms: list[str | None]) -> str:
    """The form the document's operative parts state: judgment, order, mixed or no_form."""
    got = set(forms) - {None}
    if not got:
        return "no_form"
    if "judgment_and_order" in got or len(got) > 1:
        return "mixed"
    return got.pop()


# ------------------------------------------------------------------------------------------- segment: coarse sections

def segment_shard(path: str, out_dir: str) -> dict:
    """The documents of one year file with their coarse sections, and the counts for the report (in a worker)."""
    rules = heading_rules()
    year = Path(path).name.split(".")[0]
    st = {k: Counter() for k in ("by_type", "by_form", "applied", "outcomes", "levels", "errors", "templates")}
    index, fallback_docs, written = [], 0, 0
    out = GzipLines(Path(out_dir) / f"{year}.jsonl.gz")
    for doc in read_jsonl(Path(path)):
        coarse, heads = subsections(doc, rules)
        problems = [("schema", e) for e in sections_schema_errors(coarse)] + check_subsections(coarse, doc)
        subs = [x for x in coarse["sections"] if x["level"] > 1]
        tmpl = coarse["structure"]["status"]
        forms = [x["decision_form"] for x in coarse["sections"] if x["type"] == "operative_part"]
        dropped = Counter(h.dropped for h in heads if h.dropped)
        index.append({
            "ris_id": doc["ris_id"], "decision_date": doc["decision_date"], "ris_decision_type": doc["decision_type"],
            "text_forms": " ".join(f or "none" for f in forms), "status": "error" if problems else "ok",
            "template": tmpl, "subsections": len(subs),
            "max_level": max((x["level"] for x in coarse["sections"]), default=0),
            "subsection_types": " > ".join(x["type"] for x in subs),
            "hierarchy_rules_applied": " ".join(coarse["structure"]["hierarchy_rules_applied"]),
            "headings_dropped": " ".join(f"{k}:{dropped[k]}" for k in DROPS if dropped[k]),
            "problems": " | ".join(f"{c}: {m}" for c, m in problems)})
        if problems:
            st["errors"].update(c for c, _ in problems)
            continue
        out.write(json.dumps(coarse, ensure_ascii=False) + "\n")
        written += 1
        st["templates"][tmpl] += 1
        st["by_type"][(doc["decision_type"], tmpl)] += 1
        st["by_form"][(text_form(forms), tmpl)] += 1
        st["applied"].update(coarse["structure"]["hierarchy_rules_applied"])
        st["levels"][max(x["level"] for x in coarse["sections"])] += 1
        for h in heads:
            st["outcomes"][(h.rule.id, h.dropped or "section")] += 1
        if any(h.rule.fallback and not h.dropped for h in heads):
            fallback_docs += 1
    out.close()
    return {"year": year, "input_sha256": file_sha256(Path(path)), "written": written, "index": index,
            "fallback_docs": fallback_docs, **st}


def run_segment(workers: int) -> int:
    data = data_dir()
    documents = data / "processed" / "documents" / "bvwg"
    out = data / "processed" / "sections" / "bvwg"
    shards = year_files(documents, "stage 03")
    out.mkdir(parents=True, exist_ok=True)
    rules = heading_rules()
    with ProcessPoolExecutor(max(1, min(workers, len(shards)))) as pool:
        results = list(pool.map(segment_shard, shards, [str(out)] * len(shards)))
    remove_other_year_files(out, [f"{r['year']}.jsonl.gz" for r in results])
    total = {k: Counter() for k in ("by_type", "by_form", "applied", "outcomes", "levels", "errors")}
    index, per_year = [], {}
    for r in results:
        for k in total:
            total[k].update(r[k])
        index += r["index"]
        per_year[r["year"]] = r["templates"]
    write_csv(out / "index.csv", SEGMENT_INDEX, index)
    parse_path = documents / "parse_report.json"
    parse = json.loads(parse_path.read_text(encoding="utf-8")) if parse_path.exists() else {}
    report = {
        "stage": "05 segment",
        "input": {"texts": parse.get("inputs", {}).get("texts"),
                  "year_files_sha256": {Path(sh).name: r["input_sha256"] for sh, r in zip(shards, results)}},
        "totals": {"documents": len(index), "written": sum(r["written"] for r in results),
                   "errors": sum(1 for x in index if x["status"] == "error")},
        "errors": dict(sorted(total["errors"].items())),
        "templates_per_year": {y: {t: c.get(t, 0) for t in TEMPLATES} for y, c in sorted(per_year.items())},
        "templates_by_ris_type": {d: {t: total["by_type"][(d, t)] for t in TEMPLATES}
                                  for d in sorted({k[0] for k in total["by_type"]})},
        "templates_by_text_form": {f: {t: total["by_form"][(f, t)] for t in TEMPLATES}
                                   for f in ("judgment", "order", "mixed", "no_form")},
        "hierarchy_rules_documents": {h: total["applied"][h] for h in HIERARCHY},
        "headings": {rule.id: {o: total["outcomes"][(rule.id, o)] for o in ("section",) + DROPS}
                     for rule in rules.rules if rule.type},
        "fallback_documents": sum(r["fallback_docs"] for r in results),
        "max_level_documents": {str(k): v for k, v in sorted(total["levels"].items())},
    }
    (out / "segment_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "segment_report.md").write_text(render_segment(report), encoding="utf-8")
    t = report["totals"]
    print(f"{t['documents']:,} documents, {t['written']:,} written, {t['errors']:,} errors; "
          f"report: {out / 'segment_report.md'}")
    return 0 if not t["errors"] else 1


HIERARCHY_NAMES = {
    "zu_a_before_history": "'Zu A)' before the procedural history dropped",
    "inside_history": "headings before an 'erwogen' heading (the procedural history) dropped",
    "own_part_in_history": "the court's own part with the procedural history's label kept (exception to "
                           "inside_history)",
    "erwogen_after_legal": "'erwogen' right after a legal assessment heading dropped (its opening sentence)",
    "subtitle": "same-type heading right after a heading dropped (its subtitle)",
    "sub_point": "repeated type with a deeper label dropped (a sub-point)",
    "zu_a_encloses": "'Zu A)' encloses findings / evidence / legal assessment",
    "point_of_evidence": "findings heading inside the assessment of evidence dropped (its point)",
    "out_of_order": "findings / evidence / legal assessment out of order"}


def template_table(rows: dict[str, dict[str, int]]) -> list[str]:
    lines = ["| | Documents | " + " | ".join(TEMPLATES) + " |", "|---|---|" + "---|" * len(TEMPLATES)]
    for k, c in rows.items():
        n = sum(c.values())
        lines.append(f"| {k} | {n:,} | " + " | ".join(f"{c[t]:,} ({pct(c[t], n)})" for t in TEMPLATES) + " |")
    return lines


def render_segment(r: dict) -> str:
    texts = r["input"]["texts"] or {}
    years = dict(r["templates_per_year"])
    years["**all**"] = {t: sum(c[t] for c in r["templates_per_year"].values()) for t in TEMPLATES}
    lines = ["# Stage 05 segment: coarse sections", "",
             f"Input: the stage 03 documents, text fingerprint `{(texts.get('sha256') or '?')[:16]}…` "
             f"({texts.get('documents', 0):,} texts); SHA-256 of each year file in `segment_report.json`. Heading "
             "rules `config/segmentation/bvwg_headings.yaml`, hierarchy rules "
             "`src/asylum_corpus/segmentation/subsections.py`.", "",
             f"Documents: {r['totals']['documents']:,}; written {r['totals']['written']:,}; errors "
             f"{r['totals']['errors']:,}" + (f" ({', '.join(f'{k} {v:,}' for k, v in r['errors'].items())})"
                                              if r["errors"] else "") + ".", "",
             "## Templates per year", ""]
    lines += template_table(years)
    for title, key, note in (
            ("By RIS decision type", "templates_by_ris_type", "RIS's Entscheidungsart for the whole document."),
            ("By the form stated in the text", "templates_by_text_form",
             "The operative parts' introductory sentences (`decision_form`): all 'erkannt' (judgment), all "
             "'beschlossen' (order), both (mixed), none.")):
        lines += ["", f"## {title}", "", note, ""] + template_table(r[key])
    docs = r["totals"]["written"]
    lines += ["", "## Hierarchy rules", "", "Documents in which a rule changed the result (parents and extent always "
              "apply).", "",
              "| Rule | Documents | Share |", "|---|---|---|"]
    lines += [f"| {h} {HIERARCHY_NAMES[h]} | {c:,} | {pct(c, docs)} |"
              for h, c in r["hierarchy_rules_documents"].items()]
    lines += ["", f"Fallback rules used in {r['fallback_documents']:,} documents.", "",
              "## Heading outcomes per rule", "",
              "| Rule | Sections | " + " | ".join(f"dropped {d}" for d in DROPS) + " |",
              "|---|---|" + "---|" * len(DROPS)]
    lines += [f"| `{rule}` | {c['section']:,} | " + " | ".join(f"{c[d]:,}" for d in DROPS) + " |"
              for rule, c in r["headings"].items()]
    lines += ["", "## Depth", "", "Documents by their deepest section level: "
              + ", ".join(f"level {k} {v:,}" for k, v in r["max_level_documents"].items()) + "."]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------------- fine: fine sections

def fine_document(coarse: dict, rules: PartsRules) -> dict:
    """The document with its fine sections, from the document with its coarse sections."""
    sections, moved = fine_sections(coarse, rules)
    paragraphs = [{**p, "section_id": moved[p["id"]]} if p["id"] in moved else p for p in coarse["paragraphs"]]
    return {**coarse, "sections": sections, "paragraphs": paragraphs}


def operative_class(op: dict, new: list[dict]) -> str:
    """How an operative part is divided: into A / B, points, both or neither."""
    labelled = [s for s in new if s["detection"] == "label_structure"]
    kids = {s["type"] for s in labelled if s["parent_id"] == op["id"]} & {"operative_part_a", "operative_part_b"}
    points = any(s["type"] == "operative_point" and op["start"] <= s["start"] <= op["end"] for s in labelled)
    return ("A/B" if len(kids) == 2 else "A or B" if kids else "no A/B") + (" + points" if points else "")


def fine_shard(path: str, out_dir: str) -> dict:
    """The documents of one year file with their fine sections, and the counts for the report (in a worker)."""
    rules = parts_rules()
    topics = {t for t, _, _ in rules.topics}
    year = Path(path).name.split(".")[0]
    st = {k: Counter() for k in ("types", "levels", "nesting", "numbering", "topics", "errors", "op_classes")}
    index, written = [], 0
    out = GzipLines(Path(out_dir) / f"{year}.jsonl.gz")
    for coarse in read_jsonl(Path(path)):
        doc = fine_document(coarse, rules)
        secs = doc["sections"]
        problems = check_fine(doc, topics)
        new = [s for s in secs if s["detection"] in FINE_DETECTIONS]
        index.append({"ris_id": doc["ris_id"], "decision_date": doc["decision_date"],
                      "template": doc["structure"]["status"], "coarse_sections": len(secs) - len(new),
                      "operative_sections": sum(1 for s in new if s["detection"] in ("label_structure",
                                                                                     "revision_sentence")),
                      "parts": sum(1 for s in new if s["type"] == "part"),
                      "point_assessments": sum(1 for s in new if s["type"] == "point_assessment"),
                      "with_topic": sum(1 for s in new if s["topic"]),
                      "max_level": max(s["level"] for s in secs),
                      "nesting": " ".join(f"{k}:{v}" for k, v in sorted(Counter(s["nesting"] for s in new).items())),
                      "problems": " | ".join(f"{c}: {m}" for c, m in problems)})
        if problems:
            st["errors"].update(c for c, _ in problems)
            continue
        out.write(json.dumps(doc, ensure_ascii=False) + "\n")
        written += 1
        for op in (s for s in secs if s["type"] == "operative_part"):
            st["op_classes"][operative_class(op, new)] += 1
        for s in new:
            st["types"][s["type"]] += 1
            st["levels"][s["level"]] += 1
            st["nesting"][s["nesting"]] += 1
            if s["numbering"]:
                st["numbering"][s["numbering"]] += 1
            if s["topic"]:
                st["topics"][(s["type"], s["topic"])] += 1
    out.close()
    return {"year": year, "input_sha256": file_sha256(Path(path)), "written": written, "index": index, **st}


def run_fine(workers: int) -> int:
    processed = data_dir() / "processed"
    segmented = processed / "sections" / "bvwg"
    out = processed / "fine_sections" / "bvwg"
    shards = year_files(segmented, "stage 05 segment")
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(max(1, min(workers, len(shards)))) as pool:
        results = list(pool.map(fine_shard, shards, [str(out)] * len(shards)))
    remove_other_year_files(out, [f"{r['year']}.jsonl.gz" for r in results])
    total = {k: Counter() for k in ("types", "levels", "nesting", "numbering", "topics", "errors", "op_classes")}
    index = []
    for r in results:
        for k in total:
            total[k].update(r[k])
        index += r["index"]
    write_csv(out / "index.csv", FINE_INDEX, index)
    n_fine = sum(total["types"].values())
    report = {
        "stage": "05 fine",
        "input": {"year_files_sha256": {Path(sh).name: r["input_sha256"] for sh, r in zip(shards, results)}},
        "totals": {"documents": len(index), "written": sum(r["written"] for r in results),
                   "errors": sum(1 for x in index if x["problems"]), "fine_sections": n_fine,
                   "documents_with_parts": sum(1 for x in index if x["parts"] or x["point_assessments"])},
        "errors": dict(total["errors"]),
        "types": dict(total["types"].most_common()),
        "operative_parts": dict(total["op_classes"].most_common()),
        "levels": {str(k): v for k, v in sorted(total["levels"].items())},
        "nesting": dict(total["nesting"].most_common()),
        "point_numbering": dict(total["numbering"].most_common()),
        "topics": {f"{t} / {topic}": n for (t, topic), n in sorted(total["topics"].items(), key=lambda x: -x[1])},
        "parts_with_topic": sum(total["topics"].values()),
    }
    (out / "fine_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "fine_report.md").write_text(render_fine(report), encoding="utf-8")
    t = report["totals"]
    print(f"{t['documents']:,} documents, {t['written']:,} written, {t['errors']:,} errors, {n_fine:,} fine sections; "
          f"report: {out / 'fine_report.md'}")
    return 0 if not t["errors"] else 1


def render_fine(r: dict) -> str:
    t, n = r["totals"], r["totals"]["fine_sections"]
    lines = ["# Stage 05 fine: fine sections", "",
             "Input: the documents with their coarse sections (stage 05 segment); SHA-256 of each year file in "
             "`fine_report.json`. Rules `config/segmentation/bvwg_parts.yaml`.", "",
             f"Documents: {t['documents']:,}; written {t['written']:,}; errors {t['errors']:,}"
             + (f" ({', '.join(f'{k} {v:,}' for k, v in r['errors'].items())})" if r["errors"] else "")
             + f". Fine sections: {n:,}; documents with parts in the reasoning {t['documents_with_parts']:,}.", "",
             "## Fine sections by type", "", "| Type | Sections | Share |", "|---|---|---|"]
    lines += [f"| {k} | {v:,} | {pct(v, n)} |" for k, v in r["types"].items()]
    lines += ["", "## Operative parts", "", "| Divided into | Operative parts |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["operative_parts"].items()]
    lines += ["", "## Depth and nesting", "",
              "Sections by level: " + ", ".join(f"{k}: {v:,}" for k, v in r["levels"].items()) + ".", "",
              "How the fine sections were placed: " + ", ".join(f"{k} {v:,}" for k, v in r["nesting"].items()) + ".",
              "",
              "Points in the reasoning by numbering: "
              + (", ".join(f"{k} {v:,}" for k, v in r["point_numbering"].items()) or "–") + ".", "",
              "## Topics", "",
              f"Parts with a topic: {r['parts_with_topic']:,} of {n:,} ({pct(r['parts_with_topic'], n)}).",
              "", "| Type / topic | Sections |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in list(r["topics"].items())[:60]]
    return "\n".join(lines) + "\n"
