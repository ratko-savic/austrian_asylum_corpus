"""The rule engine of the filtering stage.

A rule set (config/filtering/asylum_filter.yaml) has ordered verdict rules and independent flags. The first verdict
rule that matches sets verdict and group; the last rule is the default. A rule has a `when` (all keys must hold) and
an optional `unless` (one key is enough to stop it), or a list of named `alternatives`, each with its own `when` and
`unless`. Conditions read only the prepared metadata of a candidate, so the same candidates and rules always give the
same verdicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


CONDITION_KEYS = {"decision_type", "decision_date_from", "norms", "keywords", "indicators_only",
                  "shares_case_number_and_date", "ris_id_not_matching", "any_of"}
ALTERNATIVE_KEYS = {"name", "when", "unless", "reason"}
VERDICTS = {"include", "exclude"}


@dataclass(frozen=True)
class Candidate:
    """The metadata a rule can read (norms and keywords already normalized)."""

    ris_id: str
    decision_type: str
    decision_date: str
    norms: tuple[str, ...]
    keywords: str
    indicators: tuple[str, ...]
    shares_case_number_and_date: bool = False


class Condition:
    """A `when` block (all keys must hold) or an `any_of` / `unless` block (one key is enough)."""

    def __init__(self, spec: dict | None, require_all: bool):
        self.spec = spec or {}
        unknown = set(self.spec) - CONDITION_KEYS
        if unknown:
            raise ValueError(f"unknown condition keys: {sorted(unknown)}")
        self.require_all = require_all
        self.norms = [re.compile(p) for p in self.spec.get("norms", [])]
        self.keywords = [re.compile(p) for p in self.spec.get("keywords", [])]
        pattern = self.spec.get("ris_id_not_matching")
        self.ris_id_not = re.compile(pattern) if pattern else None
        self.any_of = Condition(self.spec["any_of"], require_all=False) if "any_of" in self.spec else None

    def evaluate(self, c: Candidate) -> tuple[bool, list[str]]:
        """Whether the condition holds, and the evidence: the norms, keywords or values that made it hold."""
        s, parts = self.spec, []
        if "decision_type" in s:
            parts.append((c.decision_type == s["decision_type"], [f"decision type {c.decision_type}"]))
        if "decision_date_from" in s:
            parts.append((c.decision_date >= str(s["decision_date_from"]), [f"decided {c.decision_date}"]))
        if self.norms:
            hits = [n for n in c.norms if any(p.match(n) for p in self.norms)]
            parts.append((bool(hits), [f"norm {n}" for n in hits]))
        if self.keywords:
            hits = [m.group(0) for p in self.keywords if (m := p.search(c.keywords))]
            parts.append((bool(hits), [f"keyword {k}" for k in dict.fromkeys(hits)]))
        if "indicators_only" in s:
            ok = sorted(c.indicators) == sorted(s["indicators_only"])
            parts.append((ok, [f"found only by {', '.join(sorted(c.indicators))}"]))
        if "shares_case_number_and_date" in s:
            ok = c.shares_case_number_and_date == bool(s["shares_case_number_and_date"])
            parts.append((ok, ["same case number and decision date as another candidate"]))
        if self.ris_id_not:
            parts.append((not self.ris_id_not.search(c.ris_id), [f"RIS ID {c.ris_id}"]))
        if self.any_of:
            parts.append(self.any_of.evaluate(c))
        if self.require_all:
            ok = all(held for held, _ in parts)
            return ok, [e for _, evidence in parts for e in evidence] if ok else []
        return any(held for held, _ in parts), [e for held, ev in parts if held for e in ev]


@dataclass(frozen=True)
class Alternative:
    name: str
    when: Condition
    unless: Condition | None


@dataclass
class Rule:
    id: str
    label: str
    alternatives: tuple[Alternative, ...]   # a plain rule is one alternative without a name
    verdict: str | None = None
    group: str | None = None

    def match(self, c: Candidate) -> tuple[bool, list[str]]:
        for alt in self.alternatives:
            held, evidence = alt.when.evaluate(c)
            if held and not (alt.unless and alt.unless.evaluate(c)[0]):
                return True, ([f"alternative: {alt.name}"] if alt.name else []) + evidence
        return False, []


@dataclass
class RuleSet:
    verdict_rules: list[Rule]
    flags: list[Rule]

    def classify(self, c: Candidate) -> dict:
        rule, evidence = next((r, ev) for r in self.verdict_rules for held, ev in [r.match(c)] if held)
        flags = {}
        for f in self.flags:
            held, ev = f.match(c)
            if held:
                flags[f.id] = ev
        return {"verdict": rule.verdict, "group": rule.group if rule.verdict == "include" else None, "rule": rule.id,
                "evidence": evidence, "flags": list(flags), "flag_evidence": flags}


def alternative(name: str | None, spec: dict, rule_id: str) -> Alternative:
    if not spec.get("when") and name:
        raise ValueError(f"rule {rule_id}: alternative {name!r} needs a `when`")
    unless = Condition(spec["unless"], require_all=False) if spec.get("unless") else None
    return Alternative(name, Condition(spec.get("when"), require_all=True), unless)


def build_rule(r: dict, verdict_rule: bool) -> Rule:
    if verdict_rule and r.get("verdict") not in VERDICTS:
        raise ValueError(f"rule {r.get('id')}: verdict must be one of {sorted(VERDICTS)}")
    if r.get("alternatives"):
        if r.get("when") or r.get("unless"):
            raise ValueError(f"rule {r['id']}: use either when/unless or alternatives")
        for a in r["alternatives"]:
            if set(a) - ALTERNATIVE_KEYS or not a.get("name"):
                raise ValueError(f"rule {r['id']}: each alternative needs a name and only the keys "
                                 f"{sorted(ALTERNATIVE_KEYS)}")
        alts = tuple(alternative(a["name"], a, r["id"]) for a in r["alternatives"])
    else:
        alts = (alternative(None, r, r["id"]),)
    return Rule(r["id"], r.get("label", r["id"]), alts, r.get("verdict"), r.get("group"))


def load_rules(path: Path) -> RuleSet:
    """Load and check a rule set."""
    spec = yaml.safe_load(path.read_bytes())
    verdict_rules = [build_rule(r, True) for r in spec["verdict_rules"]]
    flags = [build_rule(f, False) for f in spec.get("flags", [])]
    ids = [r.id for r in verdict_rules + flags]
    if len(ids) != len(set(ids)):
        raise ValueError("rule and flag ids must be unique")
    if spec["verdict_rules"][-1].get("when") or spec["verdict_rules"][-1].get("unless"):
        raise ValueError("the last verdict rule must be the default (empty `when`, no `unless`)")
    return RuleSet(verdict_rules, flags)
