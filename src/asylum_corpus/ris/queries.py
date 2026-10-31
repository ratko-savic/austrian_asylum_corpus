"""RIS queries for the BVwG candidate inventory, built from config/ris/queries.yaml.

Indicators say how a decision is attributed to an included category:
- a `norm` indicator matches the decision's RIS norm list ("AsylG 2005 §3" matches "AsylG 2005 §3 Abs1", not
  "AsylG 2005 §34"); all norm indicators are sent to RIS as one union query ("Norm=a ODER b ..."), because RIS
  returns each record at about 0.3 s and the categories overlap heavily;
- a `params` indicator (AVG §68 plus keywords) is its own query; membership in its result attributes it.

Every query is run per calendar year of decision date. A slice with more hits than `max_hits_per_slice` is split
(months, then half months, then days), so that each slice is paged completely at shallow depth.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import yaml

UNION_QUERY_ID = "included_norms"


def normalize_norm(norm: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"§\s+", "§", norm)).strip()


def norm_matches(indicator_norm: str, ris_norm: str) -> bool:
    """RIS matching of a norm indicator: same act and section, not a longer section number."""
    a, b = normalize_norm(indicator_norm), normalize_norm(ris_norm)
    return b.startswith(a) and (len(b) == len(a) or not b[len(a)].isalnum())


@dataclass(frozen=True)
class Indicator:
    id: str
    category: str
    norm: str | None = None
    params: tuple[tuple[str, str], ...] | None = None
    note: str | None = None


@dataclass(frozen=True)
class Query:
    id: str
    application: str
    params: tuple[tuple[str, str], ...]
    indicators: tuple[str, ...]

    @property
    def params_dict(self) -> dict:
        return dict(self.params)


@dataclass(frozen=True)
class Slice:
    query: Query
    start: date
    end: date

    @property
    def id(self) -> str:
        return f"{self.query.id}__{self.start:%Y%m%d}-{self.end:%Y%m%d}"

    @property
    def params(self) -> dict:
        return {**self.query.params_dict, "EntscheidungsdatumVon": self.start.isoformat(),
                "EntscheidungsdatumBis": self.end.isoformat()}

    @property
    def splittable(self) -> bool:
        return self.start < self.end

    def split(self) -> list[Slice]:
        """More than a month: calendar months; more than 15 days: two halves; otherwise single days."""
        span = (self.end - self.start).days
        if span > 31:
            months, current = [], self.start
            while current <= self.end:
                last = date(current.year, current.month, calendar.monthrange(current.year, current.month)[1])
                months.append(Slice(self.query, current, min(last, self.end)))
                current = last + timedelta(days=1)
            return months
        if span > 15:
            middle = self.start + timedelta(days=span // 2)
            return [Slice(self.query, self.start, middle), Slice(self.query, middle + timedelta(days=1), self.end)]
        return [Slice(self.query, self.start + timedelta(days=i), self.start + timedelta(days=i))
                for i in range(span + 1)]


@dataclass(frozen=True)
class PartitionRule:
    id: str
    label: str
    decision_date_from: str | None = None
    norms: tuple[str, ...] = ()
    only_indicators: tuple[str, ...] = ()

    def matches(self, decision_date: str, ris_norms: list[str], indicators: list[str]) -> bool:
        if self.decision_date_from and decision_date >= self.decision_date_from:
            return True
        if self.norms and any(norm_matches(n, r) for n in self.norms for r in ris_norms):
            return True
        return bool(self.only_indicators) and sorted(indicators) == sorted(self.only_indicators)


@dataclass
class QueryConfig:
    application: str
    start_date: date
    end_date: date
    max_hits_per_slice: int
    indicators: list[Indicator]
    categories: dict[str, str] = field(default_factory=dict)
    source: str = ""
    partition_rules: list[PartitionRule] = field(default_factory=list)
    default_partition: str = "main"

    def partition(self, decision_date: str, ris_norms: list[str], indicators: list[str]) -> str:
        """The first matching partition rule wins; otherwise the default partition (the main corpus)."""
        return next((p.id for p in self.partition_rules if p.matches(decision_date, ris_norms, indicators)),
                    self.default_partition)

    @property
    def queries(self) -> list[Query]:
        norm_indicators = [i for i in self.indicators if i.norm and not i.params]
        queries = []
        if norm_indicators:
            union = " ODER ".join(i.norm for i in norm_indicators)
            queries.append(Query(UNION_QUERY_ID, self.application, (("Norm", union),),
                                 tuple(i.id for i in norm_indicators)))
        for i in self.indicators:
            if i.params:
                queries.append(Query(i.id, self.application, i.params, (i.id,)))
        return queries

    def initial_slices(self) -> list[Slice]:
        slices = []
        for query in self.queries:
            for year in range(self.start_date.year, self.end_date.year + 1):
                start, end = max(date(year, 1, 1), self.start_date), min(date(year, 12, 31), self.end_date)
                if start <= end:
                    slices.append(Slice(query, start, end))
        return slices

    def attribute(self, ris_norms: list[str], query_ids: set[str]) -> list[str]:
        """Indicators a decision falls under: by its RIS norms, or by membership in an indicator's own query."""
        found = []
        for i in self.indicators:
            if i.params:
                if i.id in query_ids:
                    found.append(i.id)
            elif i.norm and any(norm_matches(i.norm, n) for n in ris_norms):
                found.append(i.id)
        return found


def load_query_config(path: Path) -> QueryConfig:
    """The BVwG inventory (`bvwg_inventory`) of the query config."""
    logic = yaml.safe_load(path.read_text(encoding="utf-8"))["bvwg_inventory"]
    indicators = []
    for i in logic["indicators"]:
        params = tuple(sorted((str(k), str(v)) for k, v in i["params"].items())) if i.get("params") else None
        if bool(i.get("norm")) == bool(params):
            raise ValueError(f"indicator {i['id']}: give either norm or params")
        indicators.append(Indicator(i["id"], i["category"], i.get("norm"), params, i.get("note")))
    ids = [i.id for i in indicators]
    if len(ids) != len(set(ids)) or UNION_QUERY_ID in ids:
        raise ValueError("indicator ids must be unique and differ from " + UNION_QUERY_ID)
    categories = dict(logic["categories"])
    unknown = {i.category for i in indicators} - set(categories)
    if unknown:
        raise ValueError(f"indicator categories without a label in the config: {sorted(unknown)}")
    end = date.fromisoformat(str(logic["end_date"])) if logic.get("end_date") else date.today()
    partitions = logic.get("partitions") or {}
    rules = [PartitionRule(r["id"], r.get("label", r["id"]), r.get("decision_date_from"), tuple(r.get("norms", [])),
                           tuple(r.get("only_indicators", []))) for r in partitions.get("rules", [])]
    return QueryConfig(logic.get("application", "Bvwg"), date.fromisoformat(str(logic["start_date"])), end,
                       int(logic["max_hits_per_slice"]), indicators, categories, path.name, rules,
                       partitions.get("default", "main"))
