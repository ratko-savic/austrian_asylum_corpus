"""BVwG candidate inventory: the decisions since 2014 that fall under an included category (metadata only).

Runs every query of the config slice by slice, keeps each finished slice on disk (an interrupted run resumes where
it stopped), deduplicates by RIS document ID and writes to `out`:

    inventory.jsonl        one line per candidate: RIS summary fields, partition, indicators and categories
                           (from the decision's RIS norms; an indicator with a query of its own from that query), the
                           queries that returned it
    records.jsonl.gz       the unchanged RIS record (OgdDocumentReference) of every candidate
    queries.jsonl          every query slice: parameters, hits, records retrieved, completeness
    inventory_report.md / .json   counts per category and year, completeness, signals for the filtering stage
"""

from __future__ import annotations

import gzip
import json
import re
import time
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from ..common import GzipLines, pct, utc_now, write_json, write_jsonl, write_jsonl_gz
from .client import API_URL, RisClient, record_id, summarize
from .queries import QueryConfig, Slice

# An included category's AsylG norm among the RIS norms (§ 4 must not match § 4a, § 3 not § 34).
INCLUDED_ASYLG_NORM_RE = re.compile(r"^AsylG 2005 §(?:3|4|4a|5|7|8|9|34|35)(?![0-9a-z])")
# Norms of matters excluded as standalone proceedings, listed next to an included norm (mixed decisions).
EXCLUDED_MATTER_SIGNALS = {
    "return decision (AsylG §10, FPG §52)": re.compile(r"^(?:AsylG 2005 §10|FPG §52)(?![0-9a-z])"),
    "humanitarian residence (AsylG §§ 55–57)": re.compile(r"^AsylG 2005 §5[5-7](?![0-9a-z])"),
    "detention (FPG §76, BFA-VG §22a)": re.compile(r"^(?:FPG §76|BFA-VG §22a)(?![0-9a-z])"),
    "entry ban (FPG §53)": re.compile(r"^FPG §53(?![0-9a-z])"),
}


class InventoryBuilder:
    """Runs the slices of one query config; the stored slices are reused, so a finished slice is queried once."""

    def __init__(self, client: RisClient | None, config: QueryConfig, out: Path, log=print):
        self.client, self.config, self.out, self.log = client, config, out, log
        self.slices_dir = out / "slices"
        self.cached_counts = self._load_cached_counts()

    def _load_cached_counts(self) -> dict:
        """RIS hit counts from an earlier report for the same date range, so that re-assembling needs no requests."""
        previous = self.out / "inventory_report.json"
        if not previous.exists():
            return {}
        report = json.loads(previous.read_text(encoding="utf-8"))
        if report.get("date_range") != [self.config.start_date.isoformat(), self.config.end_date.isoformat()]:
            return {}
        cached = {("year", y): v["bvwg_total"] for y, v in report.get("per_year", {}).items()}
        cached.update({("indicator", i["id"]): i["ris_hits_alone"] for i in report.get("indicators", [])
                       if "ris_hits_alone" in i})
        return cached

    def _count(self, key: tuple, params: dict) -> int:
        if key not in self.cached_counts:
            self.cached_counts[key] = self.client.count(self.config.application, params)
        return self.cached_counts[key]

    def leaf_file(self, s: Slice) -> Path:
        return self.slices_dir / f"{s.id}.json.gz"

    def split_file(self, s: Slice) -> Path:
        return self.slices_dir / f"{s.id}.split.json"

    def _process(self, s: Slice) -> list[Slice]:
        """Run one slice; return its children if it is split, else [] (its records are stored)."""
        if self.leaf_file(s).exists():
            if self.read_leaf(s)["params"] != s.params:
                raise SystemExit(f"{self.leaf_file(s)} was queried with other parameters: the query config changed. "
                                 f"Move {self.slices_dir} away to query again.")
            return []
        if self.split_file(s).exists():
            return s.split()
        application = s.query.application
        first = self.client.search_page(application, s.params, 1)
        if first.hits > self.config.max_hits_per_slice and s.splittable:
            children = s.split()
            write_json(self.split_file(s), {"slice_id": s.id, "hits": first.hits, "retrieved_at": utc_now(),
                                            "split_into": [c.id for c in children]})
            return children
        result = self.client.search(application, s.params, first)
        if not result.complete:  # the index changed while paging: try once more
            result = self.client.search(application, s.params)
        data = {
            "slice_id": s.id, "query": s.query.id, "indicators": list(s.query.indicators),
            "application": application, "params": s.params, "api_url": API_URL, "retrieved_at": utc_now(),
            "hits": result.hits, "retrieved": len(result.records), "unique": len(result.unique_ids),
            "complete": result.complete, "pages": result.pages, "records": result.records,
        }
        leaf = GzipLines(self.leaf_file(s))
        leaf.write(json.dumps(data, ensure_ascii=False))
        leaf.close()
        return []

    def collect(self) -> tuple[list[Slice], list[Slice]]:
        """Run all slices, splitting where needed; return the leaf slices and the split slices, sorted."""
        self.slices_dir.mkdir(parents=True, exist_ok=True)
        leaves, splits = [], []
        todo = self.config.initial_slices()
        started = time.monotonic()
        while todo:
            s = todo.pop(0)
            children = self._process(s)
            if children:
                splits.append(s)
                todo.extend(children)
                continue
            leaves.append(s)
            if len(leaves) % 25 == 0 or not todo:
                requests = self.client.requests_made if self.client else 0
                self.log(f"  {len(leaves)} slices done ({len(todo)} pending, {requests} requests, "
                         f"{time.monotonic() - started:.0f} s)")
        return sorted(leaves, key=lambda s: s.id), sorted(splits, key=lambda s: s.id)

    def read_leaf(self, s: Slice) -> dict:
        with gzip.open(self.leaf_file(s), "rt", encoding="utf-8") as fh:
            return json.load(fh)

    def assemble(self, leaves: list[Slice], splits: list[Slice]) -> dict:
        summaries: dict[str, dict] = {}
        queries_of: dict[str, set[str]] = defaultdict(set)
        first_seen: dict[str, str] = {}
        skipped_types: Counter = Counter()
        queries, incomplete = [], []
        query_hits: Counter = Counter()

        def new_records():
            for s in leaves:
                data = self.read_leaf(s)
                queries.append({k: v for k, v in data.items() if k != "records"})
                query_hits[s.query.id] += data["hits"]
                if not data["complete"]:
                    incomplete.append({"slice_id": s.id, "hits": data["hits"], "unique": data["unique"]})
                for record in data["records"]:
                    rid = record_id(record)
                    if rid not in summaries:
                        summary = summarize(record)
                        if summary["document_type"] != "Text":
                            skipped_types[summary["document_type"]] += 1
                            continue
                        summaries[rid] = summary
                        first_seen[rid] = data["retrieved_at"]
                        yield {"ris_id": rid, "record": record}
                    queries_of[rid].add(s.query.id)

        write_jsonl_gz(self.out / "records.jsonl.gz", new_records())
        queries += [json.loads(self.split_file(s).read_text(encoding="utf-8")) for s in splits]

        category_of = {i.id: i.category for i in self.config.indicators}
        rows = []
        for rid, summary in summaries.items():
            norms = summary["norms"]
            indicators = self.config.attribute(norms, queries_of[rid])
            rows.append({
                **summary,
                "partition": self.config.partition(summary["decision_date"], norms, indicators),
                "categories": sorted({category_of[i] for i in indicators}),
                "indicators": indicators,
                "queries": sorted(queries_of[rid]),
                "has_included_asylg_norm": any(INCLUDED_ASYLG_NORM_RE.match(n) for n in norms),
                "co_listed_excluded_matters": [label for label, rx in EXCLUDED_MATTER_SIGNALS.items()
                                               if any(rx.match(n) for n in norms)],
                "first_retrieved_at": first_seen[rid],
            })
        rows.sort(key=lambda r: (r["decision_date"] or "", r["ris_id"]))
        write_jsonl(self.out / "inventory.jsonl", rows)
        write_jsonl(self.out / "queries.jsonl", sorted(queries, key=lambda q: q["slice_id"]))
        return self.report(rows, leaves, splits, incomplete, query_hits, skipped_types)

    def report(self, rows, leaves, splits, incomplete, query_hits, skipped_types) -> dict:
        cfg = self.config
        years = list(range(cfg.start_date.year, cfg.end_date.year + 1))
        by_year = Counter(r["decision_date"][:4] for r in rows)
        by_year_category: dict[str, Counter] = defaultdict(Counter)
        for r in rows:
            for c in r["categories"]:
                by_year_category[r["decision_date"][:4]][c] += 1
        bvwg_total = {}
        for y in years:
            start, end = max(date(y, 1, 1), cfg.start_date), min(date(y, 12, 31), cfg.end_date)
            bvwg_total[str(y)] = self._count(("year", str(y)), {"EntscheidungsdatumVon": start.isoformat(),
                                                                "EntscheidungsdatumBis": end.isoformat()})
        span = {"EntscheidungsdatumVon": cfg.start_date.isoformat(), "EntscheidungsdatumBis": cfg.end_date.isoformat()}
        attributed = Counter(i for r in rows for i in r["indicators"])
        indicator_rows = []
        for i in cfg.indicators:
            ris = query_hits[i.id] if i.params else self._count(("indicator", i.id), {"Norm": i.norm, **span})
            indicator_rows.append({"id": i.id, "category": i.category, "norm": i.norm,
                                   "params": dict(i.params) if i.params else None, "note": i.note,
                                   "ris_hits_alone": ris, "attributed_candidates": attributed[i.id]})
        per_category = {}
        for c in dict.fromkeys(i.category for i in cfg.indicators):
            members = [r for r in rows if c in r["categories"]]
            per_category[c] = {"label": cfg.categories.get(c, c), "candidates": len(members),
                               "only_this_category": sum(1 for r in members if r["categories"] == [c])}
        without_asylg = [r for r in rows if not r["has_included_asylg_norm"]]
        labels = {p.id: p.label for p in cfg.partition_rules}
        partitions = {}
        for pid in [cfg.default_partition] + [p.id for p in cfg.partition_rules]:
            members = [r for r in rows if r["partition"] == pid]
            partitions[pid] = {"label": labels.get(pid, "Main corpus"), "candidates": len(members),
                               "per_year": dict(sorted(Counter(r["decision_date"][:4] for r in members).items()))}
        return {
            "generated_at": utc_now(),
            "api_url": API_URL,
            "query_config": cfg.source,
            "date_range": [cfg.start_date.isoformat(), cfg.end_date.isoformat()],
            "max_hits_per_slice": cfg.max_hits_per_slice,
            "queries": [{"id": q.id, "params": q.params_dict, "indicators": list(q.indicators),
                         "hits": query_hits[q.id]} for q in cfg.queries],
            "indicators": indicator_rows,
            "totals": {
                "candidates": len(rows),
                "records_fetched": sum(query_hits.values()),
                "unattributed_candidates": [r["ris_id"] for r in rows if not r["indicators"]],
                "records_skipped_not_decision_text": dict(skipped_types),
                "query_slices": len(leaves),
                "split_slices": len(splits),
                "incomplete_slices": incomplete,
                "requests_this_run": self.client.requests_made if self.client else 0,
            },
            "partitions": partitions,
            "per_category": per_category,
            "per_year": {str(y): {"bvwg_total": bvwg_total[str(y)], "candidates": by_year.get(str(y), 0),
                                  "per_category": dict(by_year_category.get(str(y), {}))} for y in years},
            "decision_types": dict(Counter(r["decision_type"] for r in rows)),
            "filter_signals": {
                "without_included_asylg_norm": {
                    "candidates": len(without_asylg),
                    "by_indicator": dict(Counter(i for r in without_asylg for i in r["indicators"])),
                },
                "co_listed_excluded_matters": dict(Counter(m for r in rows for m in r["co_listed_excluded_matters"])),
            },
        }


def render_report(r: dict) -> str:
    t, cats, years, parts = r["totals"], r["per_category"], r["per_year"], r["partitions"]
    lines = [
        "# BVwG candidate inventory", "",
        f"Generated {r['generated_at']} from the RIS OGD API (`{r['api_url']}`). Decision dates {r['date_range'][0]} "
        f"to {r['date_range'][1]}. Queries: `config/ris/{r['query_config']}`. Metadata only.", "",
        "A candidate is a BVwG decision returned by a query below and assigned to an included category by its RIS "
        "norms (subsequent applications: by the AVG §68 query). Exclusions are applied in the filtering stage.", "",
        "## Totals", "",
        f"- **Candidates (unique RIS documents): {t['candidates']:,}**",
        f"- Records fetched over all queries (with overlaps): {t['records_fetched']:,}",
        f"- Query slices: {t['query_slices']:,} (+ {t['split_slices']} split into smaller slices); "
        f"requests in this run: {t['requests_this_run']:,}",
        f"- Incomplete slices (retrieved ≠ hits reported by RIS): {len(t['incomplete_slices'])}",
    ]
    if t["records_skipped_not_decision_text"]:
        lines.append(f"- Records skipped because they are not decision texts: {t['records_skipped_not_decision_text']}")
    lines.append(f"- Candidates returned by RIS but matching no indicator locally: {len(t['unattributed_candidates'])}")
    years_seen = sorted({y for p in parts.values() for y in p["per_year"]})
    lines += ["", "## Partitions", "", "| Partition | Candidates | " + " | ".join(years_seen) + " |",
              "|---|---|" + "---|" * len(years_seen)]
    for pid, p in parts.items():
        lines.append(f"| {p['label']} (`{pid}`) | {p['candidates']:,} | "
                     + " | ".join(f"{p['per_year'].get(y, 0):,}" for y in years_seen) + " |")
    lines += ["", "## Queries sent to RIS", "", "| Query | RIS parameters | Hits |", "|---|---|---|"]
    for q in r["queries"]:
        params = ", ".join(f"`{k}={v}`" for k, v in q["params"].items())
        lines.append(f"| `{q['id']}` | {params} | {q['hits']:,} |")
    lines += ["", "## Indicators", "",
              "Norm indicators are assigned from the decision's RIS norms; the check compares that with RIS's own hit "
              "count for the norm alone.", "",
              "| Indicator | Category | Rule | RIS hits (alone) | Assigned candidates | Check |",
              "|---|---|---|---|---|---|"]
    for i in r["indicators"]:
        rule = f"norm `{i['norm']}`" if i["norm"] else \
            "query " + ", ".join(f"`{k}={v}`" for k, v in i["params"].items())
        ok = "ok" if i["ris_hits_alone"] == i["attributed_candidates"] else "**differs**"
        lines.append(f"| `{i['id']}` | {i['category']} | {rule} | {i['ris_hits_alone']:,} | "
                     f"{i['attributed_candidates']:,} | {ok} |")
    lines += ["", "## Candidates per category", "", "A decision can fall under several categories.", "",
              "| Category | Candidates | Only this category |", "|---|---|---|"]
    lines += [f"| {v['label']} (`{c}`) | {v['candidates']:,} | {v['only_this_category']:,} |" for c, v in cats.items()]
    lines += ["", "## Candidates per year", "",
              "| Year | BVwG decisions in RIS | Candidates | Share | " + " | ".join(f"`{c}`" for c in cats) + " |",
              "|---|---|---|---|" + "---|" * len(cats)]
    for y, v in years.items():
        share = pct(v["candidates"], v["bvwg_total"])
        lines.append(f"| {y} | {v['bvwg_total']:,} | {v['candidates']:,} | {share} | "
                     + " | ".join(f"{v['per_category'].get(c, 0):,}" for c in cats) + " |")
    fs = r["filter_signals"]
    by_indicator = sorted(fs["without_included_asylg_norm"]["by_indicator"].items())
    lines += ["", "## Signals for the filtering stage", "",
              f"- Candidates without an included AsylG norm (§§ 3, 4, 4a, 5, 7, 8, 9, 34, 35 AsylG 2005): "
              f"{fs['without_included_asylg_norm']['candidates']:,}, found via "
              + (", ".join(f"`{k}` {v:,}" for k, v in by_indicator) or "–") + ".",
              "- Candidates whose RIS norms also list a matter excluded as a standalone proceeding (mixed decisions):"]
    lines += [f"  - {k}: {v:,}" for k, v in fs["co_listed_excluded_matters"].items()] or ["  - none"]
    lines += ["", "## Decision types", ""] + [f"- {k}: {v:,}" for k, v in r["decision_types"].items()]
    if t["incomplete_slices"]:
        lines += ["", "## Incomplete slices", ""] + [
            f"- `{s['slice_id']}`: {s['unique']:,} of {s['hits']:,}" for s in t["incomplete_slices"]]
    return "\n".join(lines) + "\n"


def build_inventory(client: RisClient | None, config: QueryConfig, out: Path, log=print) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    builder = InventoryBuilder(client, config, out, log)
    started = time.monotonic()
    leaves, splits = builder.collect()
    report = builder.assemble(leaves, splits)
    report["runtime_seconds"] = round(time.monotonic() - started)
    write_json(out / "inventory_report.json", report)
    (out / "inventory_report.md").write_text(render_report(report), encoding="utf-8")
    return report
