"""The outcome and the protection outcome of a BVwG outcome unit, from the dispositions of all its operative parts.

issue table   per issue, the dispositions on it and how the issue was found (norm, wording, point, whole_decision);
              an unresolved numbered point of the authority's decision appears as "point:III"
outcome       first match wins, over the dispositions on the appeal - those not only about procedural issues
              (suspensive effect, legal aid, restitution, reopening):
    other         nothing favourable or unfavourable decided on the appeal; subtype discontinued (the proceedings are
                  discontinued), interim (suspensive effect, suspension), procedural (legal aid, correction, moot,
                  restitution, reopening) or unrecognized (no disposition found); the subtype is read from the
                  dispositions' types and from their issues, found in the sentence or by a resolved point
    partially_upheld  favourable and unfavourable dispositions (e.g. asylum dismissed, subsidiary protection granted;
                  asylum dismissed, return decision remanded)
    remanded      favourable only, with a remand
    upheld        favourable only (granted, upheld, annulled without remand, return decision permanently inadmissible)
    rejected      unfavourable only, all rejections
    dismissed     unfavourable only otherwise (with or without "mit der Maßgabe")
protection    the strongest status the appellant obtains - asylum > subsidiary_protection > residence_right (by a
              favourable disposition on an issue that `gives` it, bvwg_issues.yaml); else remanded, if a protection
              issue, the remaining points or the whole decision is remanded; else none; not_decided when the outcome is
              "other"
"""

from __future__ import annotations

from .rules import PROTECTION_ORDER, Rules

SUBTYPE_ORDER = ["discontinued", "interim", "procedural"]


def issue_table(dispositions: list[dict]) -> list[dict]:
    table: dict[str, dict] = {}
    for d in dispositions:
        keys = [(i["issue"], i["via"]) for i in d["issues"]]
        keys += [(p["issue"], "point") if p["issue"] else (f"point:{p['point']}", "point") for p in d["points"]]
        for issue, via in keys:
            row = table.setdefault(issue, {"issue": issue, "dispositions": [], "via": []})
            row["dispositions"] += [t for t in d["types"] if t not in row["dispositions"]]
            if via not in row["via"]:
                row["via"].append(via)
    return list(table.values())


def _procedural_only(d: dict, rules: Rules) -> bool:
    issues = [i["issue"] for i in d["issues"]] + [p["issue"] for p in d["points"] if p["issue"]]
    return bool(issues) and all((rules.issue(i) and rules.issue(i).kind == "procedural") for i in issues)


def outcome(dispositions: list[dict], rules: Rules) -> tuple[str, str | None]:
    if not dispositions:
        return "other", "unrecognized"
    effect = {d.id: d.effect for d in rules.dispositions}
    on_appeal = [d for d in dispositions if not _procedural_only(d, rules)]
    types = {t for d in on_appeal for t in d["types"]}
    fav = {t for t in types if effect[t] == "favourable"}
    unfav = {t for t in types if effect[t] == "unfavourable"}
    if not fav and not unfav:
        subtypes = {rules.disposition(t).subtype for d in dispositions for t in d["types"]
                    if effect[t] == "procedural"}
        issues = [i["issue"] for d in dispositions for i in d["issues"]]
        issues += [p["issue"] for d in dispositions for p in d["points"] if p["issue"]]   # resolved points too
        subtypes |= {rules.issue(i).subtype for i in issues if rules.issue(i) and rules.issue(i).kind == "procedural"}
        return "other", next((s for s in SUBTYPE_ORDER if s in subtypes), "procedural")
    if fav and unfav:
        return "partially_upheld", None
    if fav:
        return ("remanded" if "remanded" in fav else "upheld"), None
    return ("rejected" if unfav == {"rejected"} else "dismissed"), None


def protection(table: list[dict], outcome_: str, rules: Rules) -> str:
    if outcome_ == "other":
        return "not_decided"
    obtained, remanded = set(), False
    for row in table:
        issue = rules.issue(row["issue"])
        gives = issue.gives if issue else (rules.whole_decision_gives if row["issue"] == "whole_decision" else {})
        if not gives:
            continue
        if gives.get("status") and set(row["dispositions"]) & set(gives.get("by", [])):
            obtained.add(gives["status"])
        if gives.get("remand") and "remanded" in row["dispositions"]:
            remanded = True
    for status in PROTECTION_ORDER:
        if status in obtained:
            return status
    return "remanded" if remanded else "none"
