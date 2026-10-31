"""Stage 04: the head labels and the case history of every BVwG decision (schemas/head.schema.json).

Reads the documents with their coarse sections (stage 05, processed/sections/bvwg/) and writes to
processed/head/bvwg/:
    <year>.jsonl.gz      one record per decision (gzip without time stamp), checked against the schema; a record
                         that fails it is left out (also of head.csv) and the stage exits with 1
    head.csv             the same, flat (one row per decision)
    head_report.md/.json coverage of each label per year, value distributions, case-history events per rule and date
                         precision; the input (SHA-256 of the year files, text fingerprint)
The judge's name is personal data: the package (stage 10) replaces it by a pseudonymous judge ID.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from ..common import (
    data_dir, file_sha256, pct, read_jsonl, remove_other_year_files, schema_validator, write_lines_gz, year_files,
)
from .extract import HeadRules, extract


SCHEMA = "head.schema.json"
STEPS = ("first_questioning", "bfa_interview", "court_hearing")
# head.csv; lists of values are separated by " | ", lists of codes (representation kinds, scope, problems, history
# parts) by spaces; dates are ISO (day, year-month or year)
FLAT = ["ris_id", "decision_date", "judge_name", "judge_gender", "formation", "department", "appellants", "count_basis",
        "minors", "nationalities", "represented", "representation_kinds", "authority", "challenged_date",
        "file_number", "scope", "failure_to_act", "problems",
        # the case history; the earlier court decisions as "date court"
        "appellant_gender", "appellants_female", "appellants_male", "court_decisions", "hearing",
        # the interpreter per step (yes / no / not_mentioned / no_hearing; empty: the step is not mentioned, and for the
    # court hearing whether one was held is unknown)
        "interpreter_first_questioning", "interpreter_bfa_interview", "interpreter_court_hearing",
        "interpreter_languages", "pronouncement_date", "history_parts",
        # whether the proceeding runs under the law in force since 12 June 2026: yes / no / unknown, and its basis
        "new_law", "new_law_basis"]
COVERAGE = {"judge_name": "judge's name", "judge_gender": "judge's gender", "formation": "single judge / senate",
            "nationalities": "nationality", "represented": "represented", "authority": "challenged authority",
            "challenged_date": "its date", "file_number": "its file number", "minors": "minors named",
            "court_decisions": "earlier court decision", "pronouncement_date": "oral pronouncement"}
KNOWN = {"appellant_gender": "appellant's gender known", "hearing": "hearing known (held / not held)"}


def sections_dir() -> Path:
    return data_dir() / "processed" / "sections" / "bvwg"


def head_dir() -> Path:
    return data_dir() / "processed" / "head" / "bvwg"


def head_row(h: dict) -> dict:
    d = h["challenged_decisions"][0] if h["challenged_decisions"] else {}
    return {"ris_id": h["ris_id"], "decision_date": h["decision_date"], "judge_name": h["judge"]["name"] or "",
            "judge_gender": h["judge"]["gender"] or "", "formation": h["judge"]["formation"] or "",
            "department": h["judge"]["department"] or "", "appellants": h["appellants"]["count"] or "",
            "count_basis": h["appellants"]["count_basis"], "minors": h["appellants"]["minors"],
            "nationalities": " | ".join(h["nationality_set"]), "represented": h["representation"]["represented"],
            "representation_kinds": " ".join(h["representation"]["kinds"]),
            "authority": d.get("authority", ""), "challenged_date": d.get("date") or "",
            "file_number": d.get("file_number") or "",
            "scope": "whole" if h["scope"]["whole"] else " ".join(h["scope"]["points"]),
            "failure_to_act": h["failure_to_act"], "problems": " ".join(h["problems"]), **history_row(h)}


def history_row(h: dict) -> dict:
    gx, dt, iv = h["appellant_gender"], h["dates"], h["interpreter"]
    if gx["basis"] == "persons":
        female, male = (sum(p["gender"] == "female" for p in gx["persons"]),
                        sum(p["gender"] == "male" for p in gx["persons"]))
    else:
        female, male = int(gx["value"] == "female"), int(gx["value"] == "male")
    join = " | ".join
    return {"appellant_gender": gx["value"], "appellants_female": female, "appellants_male": male,
            "court_decisions": join(f'{x["date"]} {x["court"]}' for x in dt["court_decision_list"]),
            "hearing": h["hearing"]["value"],
            **{f"interpreter_{k}": iv[k]["interpreter"] or "" for k in STEPS},
            "interpreter_languages": join(sorted({x for v in iv.values() for x in v["languages"]})),
            "pronouncement_date": dt["pronouncement"] or "", "history_parts": " ".join(h["history"]["parts_read"]),
            "new_law": h["new_law"]["value"], "new_law_basis": h["new_law"]["basis"] or ""}


def shard(path: str) -> dict:
    """The records of one year file (runs in a worker process)."""
    rules = HeadRules()
    validator = schema_validator("head")
    rows, errors, lines = [], [], []
    events, precision = Counter(), Counter()
    for doc in read_jsonl(Path(path)):
        h = extract(doc, rules)
        bad = [e.message[:120] for e in validator.iter_errors(h)]
        if bad:
            errors.append({"ris_id": doc["ris_id"], "problems": bad[:3]})
            continue
        lines.append(json.dumps(h, ensure_ascii=False) + "\n")
        rows.append(head_row(h))
        for ev in h["history"]["events"]:
            events[(ev["type"], ev["rule"])] += 1
            precision[(ev["type"], ev["precision"])] += 1
    return {"year": Path(path).name.split(".")[0], "path": path, "sha256": file_sha256(Path(path)), "lines": lines,
            "rows": rows, "errors": errors, "events": events, "precision": precision}


def run(workers: int) -> int:
    documents, out = sections_dir(), head_dir()
    shards = year_files(documents, "stage 05 segment")
    out.mkdir(parents=True, exist_ok=True)
    with ProcessPoolExecutor(min(workers, len(shards))) as pool:
        results = list(pool.map(shard, shards))
    rows, errors = [], []
    for r in results:
        write_lines_gz(out / f"{r['year']}.jsonl.gz", r["lines"])
        rows += r["rows"]
        errors += r["errors"]
    remove_other_year_files(out, [f"{r['year']}.jsonl.gz" for r in results])
    rows.sort(key=lambda x: x["ris_id"])
    with (out / "head.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FLAT)
        w.writeheader()
        w.writerows(rows)
    segment_report = documents / "segment_report.json"
    seg = json.loads(segment_report.read_text(encoding="utf-8")) if segment_report.exists() else {}
    report = build_report(rows, errors, results, seg)
    (out / "head_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "head_report.md").write_text(render(report), encoding="utf-8")
    print(f"{len(rows):,} documents written, {len(errors):,} failing the schema; report: {out / 'head_report.md'}")
    return 0 if not errors else 1


def filled(x: dict, key: str) -> bool:
    return x[key] not in ("", None, False)


def coverage(rows: list[dict]) -> dict:
    return ({label: sum(1 for x in rows if filled(x, key)) for key, label in COVERAGE.items()}
            | {label: sum(1 for x in rows if x[key] != "unknown") for key, label in KNOWN.items()})


def build_report(rows: list[dict], errors: list[dict], results: list[dict], seg: dict) -> dict:
    events, precision = Counter(), Counter()
    for r in results:
        events.update(r["events"])
        precision.update(r["precision"])
    per_year: dict[str, list[dict]] = defaultdict(list)
    for x in rows:
        per_year[x["decision_date"][:4]].append(x)
    seg_input = seg.get("input") or {}
    return {
        "stage": "04 head", "schema": SCHEMA,
        "input": {"texts": seg_input.get("texts"),
                  "year_files_sha256": {Path(r["path"]).name: r["sha256"] for r in results}},
        "totals": {"documents": len(rows) + len(errors), "written": len(rows), "failing_schema": len(errors)},
        "errors": errors[:20],
        "coverage": coverage(rows),
        "coverage_per_year": {y: coverage(per_year[y]) | {"documents": len(per_year[y])} for y in sorted(per_year)},
        "judge_gender": dict(Counter(x["judge_gender"] or "none" for x in rows).most_common()),
        "formation": dict(Counter(x["formation"] or "none" for x in rows).most_common()),
        "departments": len({x["department"] for x in rows if x["department"]}),
        "appellants": dict(sorted(Counter(min(int(x["appellants"]), 10) if x["appellants"] else 0
                                          for x in rows).items())),
        "nationalities": dict(Counter(c for x in rows for c in x["nationalities"].split(" | ") if c).most_common(30)),
        "representation_kinds": dict(Counter(k for x in rows for k in (x["representation_kinds"].split() or ["none"]))
                                     .most_common()),
        "authority": dict(Counter(x["authority"] or "none" for x in rows).most_common()),
        "scope": dict(Counter("whole" if x["scope"] == "whole" else "points" for x in rows).most_common()),
        "failure_to_act": sum(1 for x in rows if x["failure_to_act"]),
        "problems": dict(Counter(p for x in rows for p in x["problems"].split()).most_common()),
        "appellant_gender": dict(Counter(x["appellant_gender"] for x in rows).most_common()),
        "hearing": dict(Counter(x["hearing"] for x in rows).most_common()),
        "new_law": dict(Counter(f'{x["new_law"]} ({x["new_law_basis"] or "-"})' for x in rows).most_common()),
        "interpreter": {k: dict(Counter(x[f"interpreter_{k}"] or "step not mentioned" for x in rows).most_common())
                        for k in STEPS},
        "interpreter_languages": dict(Counter(lang for x in rows for lang in x["interpreter_languages"].split(" | ")
                                              if lang).most_common(30)),
        "history_parts": dict(Counter(x["history_parts"] or "none" for x in rows).most_common()),
        "events_per_rule": {f"{t} {rule}": n for (t, rule), n in sorted(events.items())},
        "date_precision": {f"{t} {p}": n for (t, p), n in sorted(precision.items())},
    }


def render(r: dict) -> str:
    t = r["totals"]
    n = t["written"]
    texts = r["input"]["texts"] or {}
    lines = ["# Head labels (stage 04)", "",
             "Input: the documents with their coarse sections (stage 05), text fingerprint "
             f"`{(texts.get('sha256') or '?')[:16]}…`; SHA-256 of each year file in `head_report.json`. Rules "
             "`config/head/bvwg_head.yaml`, `config/head/bvwg_history.yaml`.", "",
             f"Documents: {t['documents']:,}; written {n:,}; failing the schema {t['failing_schema']:,}.", "",
             "## Coverage (printed and read)", "", "| Label | Decisions | Share |", "|---|---|---|"]
    lines += [f"| {k} | {v:,} | {pct(v, n)} |" for k, v in r["coverage"].items()]
    cols = list(r["coverage"])
    lines += ["", "## Coverage per year", "", "| Year | Decisions | " + " | ".join(cols) + " |",
              "|---|---|" + "---|" * len(cols)]
    for y, c in r["coverage_per_year"].items():
        lines.append(f"| {y} | {c['documents']:,} | " + " | ".join(pct(c[k], c["documents"]) for k in cols) + " |")
    for title, key in (("Judge's gender", "judge_gender"), ("Formation", "formation"),
                       ("Appellants per decision", "appellants"), ("Nationalities (top 30)", "nationalities"),
                       ("Representation", "representation_kinds"), ("Challenged authority", "authority"),
                       ("Scope of the appeal", "scope"), ("Problems", "problems"),
                       ("Appellant's gender", "appellant_gender"), ("Hearing", "hearing"),
                       ("New law (basis)", "new_law"),
                       ("Interpreter languages (top 30)", "interpreter_languages"),
                       ("Parts the case history was read from", "history_parts")):
        lines += ["", f"## {title}", "", ", ".join(f"{k}: {v:,}" for k, v in r[key].items()) + "."]
    lines += ["", "## Interpreter (step: value)", ""]
    lines += [f"- {k}: " + ", ".join(f"{a}: {b:,}" for a, b in v.items()) + "." for k, v in r["interpreter"].items()]
    lines += ["", "## Case-history events per rule", "", "| Event and rule | Events |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["events_per_rule"].items()]
    lines += ["", "## Date precision of the events", "", "| Event and precision | Events |", "|---|---|"]
    lines += [f"| {k} | {v:,} |" for k, v in r["date_precision"].items()]
    lines += ["", f"Departments: {r['departments']:,}. Complaints about failure to decide: {r['failure_to_act']:,}."]
    return "\n".join(lines) + "\n"
