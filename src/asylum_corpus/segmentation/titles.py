"""What the title of a part names: the operative points it is about, and its topic (bvwg_parts.yaml)."""

from __future__ import annotations

import re

from .labels import roman_numeral, roman_value
from .parts_rules import PartsRules

POINT_TAIL = re.compile(r"\.?\)?\s*(,|und|sowie|bzw\.?|bis|-|–)\s*(?:spruch(?:punkt|teil)\w*\s+)?(?:a\.?\s?)?"
                        r"([ivx]+)\b", re.I)
CHALLENGED = re.compile(r"\.?\)?\s*(?:des|der|dem|die)\s+(?:angefochtenen|bekämpften)\b", re.I)


def points_text(first: str, title: str, pos: int) -> str:
    """The first point and those joined to it, runs written as ranges ("I., II. und III." -> "I-III", "III. bis V." ->
    "III-V", "I. und IV." -> "I, IV")."""
    found = [roman_value(first)]
    while m := POINT_TAIL.match(title, pos):
        v = roman_value(m.group(2).upper())
        if m.group(1).lower() in ("bis", "-", "–") and v > found[-1]:
            found += list(range(found[-1] + 1, v + 1))
        elif v != found[-1]:
            found.append(v)
        pos = m.end()
    found = sorted(set(found))
    runs = [[found[0]]]
    for v in found[1:]:
        if v == runs[-1][-1] + 1:
            runs[-1].append(v)
        else:
            runs.append([v])
    return ", ".join(roman_numeral(r[0]) if len(r) == 1 else f"{roman_numeral(r[0])}-{roman_numeral(r[-1])}"
                     for r in runs)


def point_set(point: str | None) -> set[int]:
    """The values of a recorded point ("III-V" -> {3, 4, 5}, "I, IV" -> {1, 4})."""
    out: set[int] = set()
    for part in (point or "").split(","):
        a, _, b = part.strip().partition("-")
        try:
            lo = roman_value(a.strip())
            out |= set(range(lo, roman_value(b.strip()) + 1)) if b else {lo}
        except (KeyError, ValueError):
            continue
    return out


def point_of(title: str, rules: PartsRules, legal: bool = False) -> tuple[str, str] | None:
    """(points, numbering) where the title names operative points: numbering "court" (the court's own "Spruchteil
    A) I."), "authority" (the challenged decision's "Spruchpunkt I. des angefochtenen Bescheides") or "unclear". A point
    named in the court's form anywhere in the title is the one recorded. In the legal part (`legal`) "Zu I)" and "Ad
    II.:" name points too."""
    m = rules.point.search(title)
    if not m and legal:
        m = rules.point_legal.match(title)
    if not m:
        return None
    m = next((x for x in rules.point.finditer(title)
              if rules.numbering_court.search(x.group(0)) and not CHALLENGED.match(title[x.end():])), m)
    group = next(k for k, g in enumerate(m.groups(), 1) if g)
    point = points_text(m.group(group).upper().rstrip("."), title, m.end(group))
    if rules.numbering_court.search(m.group(0)) and not CHALLENGED.match(title[m.end():]):
        return point, "court"
    numbering = ("authority" if rules.numbering_authority.search(title) else
                 "court" if rules.numbering_court.search(title) else "unclear")
    return point, numbering


def normal_title(title: str, rules: PartsRules) -> str:
    """The title as the topic patterns read it: "XXXX" -> "X", the authority's name dropped, numbers -> "#" except
    after "§" ("§ 57 AsylG" is the residence title, "§ 3 AsylG" asylum), spaces collapsed, quotes and brackets around
    it and a final ":" / "." removed."""
    t = title.replace("XXXX", "X")
    for rx in rules.topic_drop:
        t = rx.sub("", t)
    t = re.sub(r"(?<!§)(?<!§ )(?<!\d)\d+", "#", t)
    return re.sub(r"\s+", " ", t).strip(" :.;–-\"„“”'()[]")


def topic_of(title: str, rules: PartsRules, context: str) -> str | None:
    """The first topic of the context's group ("court": the court's own parts, "coi": quoted country reports) or of
    group "any" whose pattern matches the whole normalised title."""
    t = normal_title(title, rules)
    return next((topic for topic, rx, group in rules.topics if group in ("any", context) and rx.fullmatch(t)), None)
