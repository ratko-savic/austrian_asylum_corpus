"""Stage 02: a verdict for every BVwG candidate of the inventory, from its RIS metadata only.

Reads the inventory (interim/candidates: inventory.jsonl, and records.jsonl.gz for the RIS keywords), applies the
rules of config/filtering/asylum_filter.yaml and writes to interim/filtered/:

    filter_decisions.jsonl     one line per candidate, sorted by RIS ID: verdict, group, rule, evidence, flags
    filter_decisions.csv       the same, flat
    filter_report.md / .json   counts per rule, group, flag and year; input hashes

The outputs carry no time stamps: the same inventory and rules always write identical files.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from ..common import CONFIG, data_dir, file_sha256, read_jsonl, require
from ..ris.queries import normalize_norm
from .rules import Candidate, RuleSet, load_rules

RULES = CONFIG / "filtering" / "asylum_filter.yaml"
CSV_COLUMNS = ["ris_id", "decision_date", "case_number", "decision_type", "verdict", "group", "rule", "flags",
               "evidence", "document_url"]


def candidates_dir() -> Path:
    return data_dir() / "interim" / "candidates"


def filtered_dir() -> Path:
    return data_dir() / "interim" / "filtered"


def normalize_keywords(text: str | None) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<br\s*/?>", " ", text or "")).strip()


def first_case_number(row: dict) -> str:
    return row["case_numbers"][0].strip() if row["case_numbers"] else row["ris_id"]


def load_candidates(folder: Path) -> tuple[list[dict], list[Candidate]]:
    """Inventory rows (sorted by RIS ID) and the metadata the rules read."""
    rows = sorted(read_jsonl(require(folder / "inventory.jsonl", "stage 01 inventory")), key=lambda r: r["ris_id"])
    keywords = {item["ris_id"]: item["record"]["Data"]["Metadaten"]["Judikatur"].get("Schlagworte")
                for item in read_jsonl(require(folder / "records.jsonl.gz", "stage 01 inventory"))}
    same_day = Counter((first_case_number(r), r["decision_date"]) for r in rows)
    candidates = [Candidate(
        ris_id=r["ris_id"], decision_type=r["decision_type"], decision_date=r["decision_date"],
        norms=tuple(normalize_norm(n) for n in r["norms"]), keywords=normalize_keywords(keywords.get(r["ris_id"])),
        indicators=tuple(r["indicators"]),
        shares_case_number_and_date=same_day[(first_case_number(r), r["decision_date"])] > 1,
    ) for r in rows]
    return rows, candidates


def run_filter(folder: Path | None = None, out: Path | None = None, rules_file: Path = RULES) -> dict:
    folder, out = folder or candidates_dir(), out or filtered_dir()
    rules = load_rules(rules_file)
    rows, candidates = load_candidates(folder)
    out.mkdir(parents=True, exist_ok=True)
    decisions = []
    for row, c in zip(rows, candidates):
        decisions.append({
            "ris_id": c.ris_id, "decision_date": c.decision_date, "case_number": "; ".join(row["case_numbers"]),
            "decision_type": c.decision_type, **rules.classify(c), "document_url": row["document_url"],
        })
    with (out / "filter_decisions.jsonl").open("w", encoding="utf-8") as fh:
        for d in decisions:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with (out / "filter_decisions.csv").open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for d in decisions:
            writer.writerow({**d, "flags": "; ".join(d["flags"]), "evidence": "; ".join(d["evidence"])})
    inputs = {"inventory.jsonl": file_sha256(folder / "inventory.jsonl"),
              "records.jsonl.gz": file_sha256(folder / "records.jsonl.gz"),
              "rules": f"{rules_file.name} sha256 {file_sha256(rules_file)}"}
    report = build_report(decisions, rules, inputs)
    write_report(out, report)
    return report


def build_report(decisions: list[dict], rules: RuleSet, inputs: dict) -> dict:
    included = [d for d in decisions if d["verdict"] == "include"]
    per_year: dict[str, Counter] = defaultdict(Counter)
    for d in decisions:
        per_year[d["decision_date"][:4]][d["group"] if d["verdict"] == "include" else "excluded"] += 1
    flags_by_group: dict[str, Counter] = defaultdict(Counter)
    for d in included:
        for f in d["flags"]:
            flags_by_group[f][d["group"]] += 1
    return {
        "inputs": inputs, "candidates": len(decisions),
        "verdicts": dict(sorted(Counter(d["verdict"] for d in decisions).items())),
        "groups": dict(sorted(Counter(d["group"] for d in included).items())),
        "rules": [{"id": r.id, "label": r.label, "verdict": r.verdict, "group": r.group,
                   "count": sum(1 for d in decisions if d["rule"] == r.id)} for r in rules.verdict_rules],
        "flags": [{"id": f.id, "label": f.label, "count": sum(1 for d in decisions if f.id in d["flags"]),
                   "included_by_group": dict(sorted(flags_by_group[f.id].items()))} for f in rules.flags],
        "included_mixed_any": sum(1 for d in included if any(f.startswith("mixed:") for f in d["flags"])),
        "per_year": {y: dict(sorted(c.items())) for y, c in sorted(per_year.items())},
        "decision_types_included": dict(sorted(Counter(d["decision_type"] for d in included).items())),
    }


def write_report(out: Path, report: dict) -> None:
    (out / "filter_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "filter_report.md").write_text(render_report(report), encoding="utf-8")


def render_report(r: dict) -> str:
    groups = sorted({g for c in r["per_year"].values() for g in c if g != "excluded"}) + ["excluded"]
    lines = ["# Filtering: BVwG candidates", "",
             "Deterministic, from RIS metadata only; the same inputs and rules give identical outputs.", "",
             "Inputs: " + "; ".join(f"`{k}` {v[:16] if len(v) == 64 else v}" for k, v in r["inputs"].items()), "",
             f"- Candidates: {r['candidates']:,}",
             f"- Included: {r['verdicts'].get('include', 0):,}; excluded: {r['verdicts'].get('exclude', 0):,}",
             "- Included by group: " + ", ".join(f"`{g}` {n:,}" for g, n in r["groups"].items()),
             f"- Included decisions that also decide an excluded matter (mixed-case rule): {r['included_mixed_any']:,}",
             "", "## Verdict rules (first match wins)", "",
             "| Rule | Verdict | Group | Decisions |", "|---|---|---|---|"]
    for rule in r["rules"]:
        lines.append(f"| `{rule['id']}` {rule['label']} | {rule['verdict']} | {rule['group'] or '–'} "
                     f"| {rule['count']:,} |")
    default = r["rules"][-1]
    if default["count"]:
        lines.append(f"\n**Warning:** {default['count']:,} candidates matched no rule but the default.")
    lines += ["", "## Flags (do not change the verdict)", "", "| Flag | All | Included, by group |", "|---|---|---|"]
    for f in r["flags"]:
        by_group = ", ".join(f"{g} {n:,}" for g, n in f["included_by_group"].items()) or "–"
        lines.append(f"| `{f['id']}` {f['label']} | {f['count']:,} | {by_group} |")
    lines += ["", "## Per year", "", "| Year | " + " | ".join(groups) + " |", "|---|" + "---|" * len(groups)]
    for y, c in r["per_year"].items():
        lines.append(f"| {y} | " + " | ".join(f"{c.get(g, 0):,}" for g in groups) + " |")
    return "\n".join(lines) + "\n"
