"""The rules of the BVwG outcomes: config/outcomes/bvwg_dispositions.yaml, bvwg_issues.yaml and bvwg_units.yaml,
compiled.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from ..common import CONFIG
from .units import UnitRules, load_unit_rules

RULES = CONFIG / "outcomes"
PROTECTION_ORDER = ["asylum", "subsidiary_protection", "residence_right"]   # strongest first


@dataclass(frozen=True)
class Disposition:
    id: str
    effect: str                  # favourable / unfavourable / neutral / procedural
    pattern: re.Pattern
    removes: bool = False
    subtype: str | None = None   # for effect procedural: the subtype of "other"


@dataclass(frozen=True)
class Issue:
    id: str
    kind: str                    # substantive / procedural
    norms: list[re.Pattern]
    wording: list[re.Pattern]
    replaces: tuple[str, ...] = ()
    subtype: str | None = None
    gives: dict = field(default_factory=dict)   # {status, by, remand}
    point_wording: list[re.Pattern] = field(default_factory=list)   # only for resolving a numbered point
    wording_unless: re.Pattern | None = None     # the wording is not read in a sentence matching this


@dataclass(frozen=True)
class Rules:
    dispositions: list[Disposition]
    issues: list[Issue]
    cassation: re.Pattern
    revision: re.Pattern
    revision_inadmissible: re.Pattern
    quote: re.Pattern
    quote_open: dict[str, str]
    correction: re.Pattern
    keeps_with_correction: tuple[str, ...]
    whole_decision: re.Pattern
    whole_decision_gives: dict
    remaining: re.Pattern
    ambiguous_norms: re.Pattern
    point_mention: re.Pattern
    point_reference: re.Pattern
    point_window: int
    point_described_after: re.Pattern
    units: UnitRules

    def disposition(self, id_: str) -> Disposition:
        return next(d for d in self.dispositions if d.id == id_)

    def issue(self, id_: str) -> Issue | None:
        return next((i for i in self.issues if i.id == id_), None)


def load_rules(directory: Path = RULES) -> Rules:
    d = yaml.safe_load((directory / "bvwg_dispositions.yaml").read_text(encoding="utf-8"))
    i = yaml.safe_load((directory / "bvwg_issues.yaml").read_text(encoding="utf-8"))
    units = load_unit_rules(directory / "bvwg_units.yaml")
    points = i["points"]
    return Rules(
        dispositions=[Disposition(x["id"], x["effect"], re.compile(x["pattern"]), x.get("removes", False),
                                  x.get("subtype")) for x in d["dispositions"]],
        issues=[Issue(x["id"], x["kind"], [re.compile(p) for p in x.get("norms", [])],
                      [re.compile(p) for p in x.get("wording", [])], tuple(x.get("replaces", [])), x.get("subtype"),
                      x.get("gives", {}), [re.compile(p) for p in x.get("point_wording", [])],
                      re.compile(x["wording_unless"]) if x.get("wording_unless") else None) for x in i["issues"]],
        cassation=re.compile(d["cassation"]), revision=re.compile(d["revision"], re.S),
        revision_inadmissible=re.compile(d["revision_inadmissible"]), quote=re.compile(d["quote"]),
        quote_open=d["quote_open"], correction=re.compile(d["correction"]),
        keeps_with_correction=tuple(d["keeps_with_correction"]),
        whole_decision=re.compile(d["whole_decision"]), whole_decision_gives=i["whole_decision"]["gives"],
        remaining=re.compile(i["remaining"]), ambiguous_norms=re.compile(i["ambiguous_norms"]),
        point_mention=re.compile(points["mention"]), point_reference=re.compile(points["reference"]),
        point_window=points["window"], point_described_after=re.compile(points["described_after"]),
        units=units)
