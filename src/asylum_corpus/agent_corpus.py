"""Stage 09: the agent corpus (rules: config/agent_corpus.yaml), for evaluating systems that predict the norms and the
outcome of a decision from its facts.

The BVwG decisions in which the facts, the legal assessment, the outcome and the cited norms can be told apart most
reliably, split by decision date into train, dev and test; a dev or test decision of a procedure that an earlier split
holds goes to seen_procedure. Writes processed/agent_corpus/: agent_corpus.csv (the split, the start of the legal
assessment, outcome, protection outcome and the norms the legal assessment cites), agent_corpus_report.{json,md}, and
per decision its texts in texts/<year>.jsonl.gz: the procedural history, the findings and the assessment of evidence
apart (paragraphs in none of them as "other"), the legal assessment, the operative part of its outcome unit, and the
procedural history and the findings once more with the norms they cite as "[NORM]". In every text the judge and the
representatives the head names appear by their role.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import yaml

from .common import CONFIG, data_dir, read_csv, read_jsonl, remove_other_year_files, require, write_jsonl_gz, year_files
from .persons import masker
from .segmentation import facts

RULES = CONFIG / "agent_corpus.yaml"
STEM = re.compile(r"\b(\d{7})-\d+\b")       # the procedure of a case number: "2212345" of "W123 2212345-1"
NORM = "[NORM]"
SPLITS = ("train", "dev", "test", "seen_procedure")


def legal_norms(year_file: str, legal_sections: list[str], norm_types: list[str]) -> dict[str, tuple[int, list]]:
    """RIS ID -> (start of the legal assessment, sorted distinct norms cited in it) for one year file."""
    processed = data_dir() / "processed"
    legal = {}
    for d in read_jsonl(processed / "sections" / "bvwg" / year_file):
        spans = [(s["start"], s["end"]) for s in d["sections"] if s["type"] in legal_sections]
        if spans:
            pars = {p["id"] for p in d["paragraphs"] if any(a <= p["start"] < b for a, b in spans)}
            legal[d["document_id"]] = (d["ris_id"], min(a for a, _ in spans), pars)
    norms = defaultdict(set)
    for c in read_jsonl(processed / "citations" / "bvwg" / year_file):
        doc = legal.get(c["source_document_id"])
        if doc and c["type"] in norm_types and c["paragraph_id"] in doc[2]:
            norms[c["source_document_id"]].add(c["normalized"])
    return {rid: (start, sorted(norms.get(did, ()))) for did, (rid, start, _) in legal.items()}


def masked(paragraphs: list[dict], citations: list[dict]) -> list[str]:
    """The paragraphs' texts with each citation replaced by [NORM]; a citation overlapping the one before is skipped."""
    by_paragraph = defaultdict(list)
    for c in citations:
        by_paragraph[c["paragraph_id"]].append((c["start"], c["end"]))
    out = []
    for p in paragraphs:
        text, pos, pieces = p["text"], 0, []
        for s, e in sorted(by_paragraph.get(p["id"], [])):
            s, e = s - p["start"], e - p["start"]
            if s < pos:
                continue
            pieces += [text[pos:s], NORM]
            pos = e
        out.append("".join(pieces) + text[pos:])
    return out


def texts(year_file: str, wanted: dict, cfg: dict) -> dict[str, list[str]]:
    """Write the texts of the selected decisions of one year file; returns the procedures of each (RIS ID -> stems)."""
    processed = data_dir() / "processed"
    heads = {h["ris_id"]: h for h in read_jsonl(processed / "head" / "bvwg" / year_file) if h["ris_id"] in wanted}
    documents = {f"ris:bvwg:{rid}" for rid in wanted}
    citations = defaultdict(list)
    for c in read_jsonl(processed / "citations" / "bvwg" / year_file):
        if c["source_document_id"] in documents and c["type"] in cfg["masked_types"]:
            citations[c["source_document_id"]].append(c)
    out, stems = [], {}
    for d in read_jsonl(processed / "sections" / "bvwg" / year_file):
        w = wanted.get(d["ris_id"])
        if not w:
            continue
        secs, paras, mask = d["sections"], d["paragraphs"], masker(heads.get(d["ris_id"]))
        legal = [s for s in secs if s["type"] in cfg["legal_sections"]]
        l_start, l_end = min(s["start"] for s in legal), max(s["end"] for s in legal)
        parts = facts.facts_parts(d, l_start)
        ops = [s for s in secs if s["id"].rsplit(":", 1)[-1] in w["unit_sections"]]
        if len(ops) != len(w["unit_sections"]) or any(s["type"] != "operative_part" for s in ops):
            raise SystemExit(f"{d['ris_id']}: the sections {w['unit_sections']} of its outcome unit are not its "
                             "operative parts")
        inputs = {f"{part}_masked": mask("\n".join(masked([paras[i] for i in parts[part]],
                                                          citations[d["document_id"]])))
                  for part in cfg["masked_parts"]}
        out.append({"ris_id": d["ris_id"], "decision_date": d["decision_date"], "case_number": d["case_number"],
                    "source_url": d["source_url"],
                    **{part: mask("\n".join(paras[i]["text"] for i in idx)) for part, idx in parts.items()},
                    "legal_assessment": mask("\n".join(p["text"] for p in paras if l_start <= p["start"] < l_end)),
                    "operative_part": mask("\n".join(d["full_text"][s["start"]:s["end"]] for s in ops)),
                    **inputs, "outcome": w["outcome"], "protection": w["protection"], "norms": w["norms"]})
        stems[d["ris_id"]] = sorted(set(STEM.findall(" ".join(d["case_numbers"]))))
    write_jsonl_gz(processed / "agent_corpus" / "texts" / year_file, out)
    return stems


def split_of(decisions: list[tuple[str, str, list[str]]], dev_from: str, test_from: str) -> dict[str, str]:
    """RIS ID -> split for (RIS ID, decision date, procedures): train, dev and test by date; a dev or test decision
    whose procedure has a decision in an earlier split: seen_procedure."""
    by_date = {rid: "train" if date < dev_from else "dev" if date < test_from else "test" for rid, date, _ in decisions}
    held = {"train": set(), "dev": set()}
    for rid, _, stems in decisions:
        if by_date[rid] in held:
            held[by_date[rid]].update(stems)
    earlier = {"train": set(), "dev": held["train"], "test": held["train"] | held["dev"]}
    return {rid: "seen_procedure" if set(stems) & earlier[by_date[rid]] else by_date[rid]
            for rid, _, stems in decisions}


def build(workers: int) -> int:
    cfg = yaml.safe_load(RULES.read_text(encoding="utf-8"))
    processed = data_dir() / "processed"
    out = processed / "agent_corpus"
    docs = {r["ris_id"]: r for r in read_csv(require(processed / "documents" / "bvwg" / "index.csv", "stage 03"))}
    coarse = {r["ris_id"]: r for r in read_csv(require(processed / "sections" / "bvwg" / "index.csv",
                                                        "stage 05 segment"))}
    head = {r["ris_id"]: r for r in read_csv(require(processed / "head" / "bvwg" / "head.csv", "stage 04"))}
    units = defaultdict(list)
    for r in read_csv(require(processed / "outcomes" / "bvwg" / "index.csv", "stage 08")):
        units[r["ris_id"]].append(r)

    files = [Path(f).name for f in year_files(processed / "citations" / "bvwg", "stage 06")]
    norms = {}
    with ProcessPoolExecutor(workers) as pool:
        for part in pool.map(legal_norms, files, [cfg["legal_sections"]] * len(files),
                             [cfg["norm_types"]] * len(files)):
            norms.update(part)

    def issues(rid):
        return {x.split("=")[0].strip() for x in units[rid][0]["issues"].split(";") if x.strip()}

    def types(rid):
        return set(coarse.get(rid, {}).get("subsection_types", "").replace(">", " ").split())

    tests = {
        "main_group": lambda rid: docs[rid]["group"] == "main",
        "not_interim": lambda rid: docs[rid]["interim_procedural"] != "True",
        "old_law": lambda rid: head.get(rid, {}).get("new_law") == "no",
        "merits_structure": lambda rid: coarse.get(rid, {}).get("template") == "merits"
                                        and {"findings", "legal_assessment"} <= types(rid),
        "one_appellant": lambda rid: len(units.get(rid, [])) == 1,
        "clear_outcome": lambda rid: (units[rid][0]["outcome"] in cfg["clear_outcomes"]
                                      and not units[rid][0]["problems"]),
        "protection_application": lambda rid: not issues(rid) & set(cfg["other_proceedings"]),
        "norms_in_legal_assessment": lambda rid: bool(norms.get(rid, (0, []))[1]),
    }
    selected = [rid for rid in sorted(docs) if docs[rid]["status"] == "ok"]
    flow = [{"step": "parsed decisions", "reason": "stage 03 status ok", "after": len(selected), "removed": 0}]
    for c in cfg["criteria"]:
        keep = [rid for rid in selected if tests[c["id"]](rid)]
        flow.append({"step": c["id"], "reason": c["reason"], "after": len(keep), "removed": len(selected) - len(keep)})
        selected = keep

    by_year = defaultdict(dict)
    for rid in selected:
        u = units[rid][0]
        by_year[docs[rid]["decision_date"][:4] + ".jsonl.gz"][rid] = {
            "unit_sections": u["unit_sections"].split(), "outcome": u["outcome"], "protection": u["protection"],
            "norms": norms[rid][1]}
    years_files = sorted(by_year)
    stems = {}
    with ProcessPoolExecutor(workers) as pool:
        for part in pool.map(texts, years_files, [by_year[y] for y in years_files], [cfg] * len(years_files)):
            stems.update(part)
    if len(stems) != len(selected):
        raise SystemExit(f"texts written for {len(stems)} of {len(selected)} decisions")
    remove_other_year_files(out / "texts", years_files)
    split = split_of([(rid, docs[rid]["decision_date"], stems[rid]) for rid in selected],
                     str(cfg["split"]["dev_from"]), str(cfg["split"]["test_from"]))

    with (out / "agent_corpus.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ris_id", "decision_date", "split", "legal_assessment_start", "outcome", "protection", "norms"])
        for rid in selected:
            u = units[rid][0]
            start, ns = norms[rid]
            w.writerow([rid, docs[rid]["decision_date"], split[rid], start, u["outcome"], u["protection"],
                        " | ".join(ns)])
    outcomes = Counter(units[rid][0]["outcome"] for rid in selected)
    years = Counter(docs[rid]["decision_date"][:4] for rid in selected)
    protections = sorted({units[rid][0]["protection"] for rid in selected})
    by_split = {s: Counter(units[rid][0]["protection"] for rid in selected if split[rid] == s) for s in SPLITS}
    report = {"decisions": len(selected), "flow": flow, "outcomes": dict(outcomes.most_common()),
              "years": dict(sorted(years.items())), "split": {**cfg["split"]},
              "splits": {s: {"decisions": sum(c.values()), **{p: c[p] for p in protections}}
                         for s, c in by_split.items()}}
    (out / "agent_corpus_report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                                  encoding="utf-8")
    lines = ["# Agent corpus", "", f"{len(selected):,} decisions.", "",
             "| Step | Why | Remaining | Removed |", "|---|---|---:|---:|"]
    lines += [f"| {f['step']} | {f['reason']} | {f['after']:,} | {f['removed']:,} |" for f in flow]
    lines += ["", "| Outcome | Decisions |", "|---|---:|"] + [f"| {k} | {v:,} |" for k, v in outcomes.most_common()]
    lines += ["", f"Split by decision date: dev from {cfg['split']['dev_from']}, test from "
                  f"{cfg['split']['test_from']}; seen_procedure: dev and test decisions of a procedure an earlier "
                  "split holds.", "",
              "| Split | Decisions | " + " | ".join(protections) + " |", "|---|---:|" + "---:|" * len(protections)]
    lines += [f"| {s} | {sum(c.values()):,} | " + " | ".join(f"{c[p]:,}" for p in protections) + " |"
              for s, c in by_split.items()]
    (out / "agent_corpus_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(selected):,} decisions ({', '.join(f'{s} {sum(c.values()):,}' for s, c in by_split.items())}) "
          f"-> {out}")
    return 0
