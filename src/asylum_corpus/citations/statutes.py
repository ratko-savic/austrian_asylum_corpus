"""Provision citations: a chain of designations followed by a known act (config/citations/acts.yaml). Each provision
of a chain becomes its own citation, so that each can be counted and queried on its own ("§§ 3 und 8 AsylG 2005" ->
§ 3, § 8). How a chain is read:

- The connectives are ",", "und", "bis", "sowie", "oder", "bzw.", "beziehungsweise", "iVm", "i.V.m." and "in
  Verbindung mit". A
  range gives its two ends ("§§ 3 bis 5" -> § 3, § 5): the provisions in between are not named.
- A bare number continues the innermost level that takes a number ("§ 3 Abs. 1 und 2" -> § 3 Abs. 2; "§ 29 Abs. 1
  zweiter Satz und 4" -> § 29 Abs. 4), the usual way of listing subsections. Where a subdivision follows that the
  provision before it has, the number starts a new provision ("§§ 31 Abs. 1, 7 Abs. 2 VwGVG" -> § 7 Abs. 2).
- In a chain that has already named several paragraphs (it opens with "§§", or a new paragraph came earlier), a bare
  number is a new paragraph where its continued reading ("§ 13 Abs. 22") is never cited explicitly in the BVwG
  decisions but the paragraph is (the table of attested designations): "§§ 13 Abs. 2 bis 5 und 22 VwGVG" -> § 22.
  Articles keep the continued reading ("Art. 52 Abs. 3 und 7 GRC" is Abs. 7).
- A subdivision after a connective names the provision before it from that level on ("§ 53 Abs. 1 iVm Abs. 2 Z 6
  FPG" -> § 53 Abs. 2 Z 6). A level the provision before it lacks is part of that provision after a comma ("Art. 1,
  Abschnitt A, Z. 2 GFK" is one citation) and not read after another connective ("§ 8 Abs. 1 iVm Z 13 AsylG" often
  lacks its own "§").
- The act ends the chain. A hyphen may follow it before a lower-case word ("Art. 3 EMRK-widrige Behandlung" -> EMRK
  Art. 3): an adjective cannot name another act, a capital can ("AsylG-DV").
- Every subdivision has one spelling ("§ 8 Absatz 1 Ziffer 2" -> "§ 8 Abs. 1 Z 2"), so that a provision is one string.

Known limits: foreign law quoted in a country report is read as the act whose name it carries, where that name is in
acts.yaml, and the name does not say which state; an act named without a provision is no citation; a hyphen and a
capital after the act ("Art. 3 EMRK-Verletzung") is not read, as a rule would need a word list; "leg. cit." takes the
act cited last, even where the text means an earlier one.
"""

from __future__ import annotations

import dataclasses
import json
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .normalize import designation

NUM = r"\d+[a-z]?"
DESIG = rf"(?:§§?\s?{NUM}|Art(?:ikel|\.)?\s?(?:{NUM}|[IVX]+\b))"
SUB = rf"(?:\s?(?:Abs\.?|Absatz|Z\.?|Ziffer|Satz|UAbs\.?|Unterabs\.?|Abschnitt)\s?(?:{NUM}|[A-Z]\b)" \
      rf"|\s?(?:lit\.?|litera)\s?[a-z]{{1,2}}\b|\s(?:erster|zweiter|dritter|letzter) Satz)"
CONN = r"\s?(?:,|und|bis|sowie|oder|bzw\.|beziehungsweise|iVm|i\.V\.m\.|in Verbindung mit)\s?"
CHAIN = rf"(?>{DESIG}(?:{SUB})*+(?:{CONN}(?:(?:{DESIG}|{NUM})(?:{SUB})*+|(?:{SUB})++))*+)"
# a subdivision word with its value, at the start of an item
SUB_WORD = rf"(?:(?:Abs\.?|Absatz|Z\.?|Ziffer|Satz|UAbs\.?|Unterabs\.?|Abschnitt)\s?(?:{NUM}|[A-Z]\b)" \
           rf"|(?:lit\.?|litera)\s?[a-z]{{1,2}}\b)"
OF = r"\s?(?:(?:des|der|dieses|dieser)\s)?"
ACT_END = r"(?!\w|-[^a-zäöüß])"   # the act ends the chain; a hyphen may follow it before a lower-case word

ITEM = re.compile(rf"(?P<head>(?P<kind>§§?|Art(?:ikel|\.)?)\s?(?P<num>{NUM}|[IVX]+\b)|(?P<lead>(?={SUB_WORD}))"
                  rf"|(?P<bare>{NUM}))(?P<subs>(?:{SUB})*)")
SUB_PART = re.compile(rf"(?P<word>Abs\.?|Absatz|Z\.?|Ziffer|Satz|UAbs\.?|Unterabs\.?|Abschnitt)\s?"
                      rf"(?P<value>{NUM}|[A-Z]\b)"
                      rf"|(?P<lword>lit\.?|litera)\s?(?P<lvalue>[a-z]{{1,2}})\b"
                      rf"|(?P<ord>(?:erster|zweiter|dritter|letzter) Satz)")


@dataclass(frozen=True)
class Acts:
    by_alias: dict            # alias -> (act, type)
    legislation: re.Pattern   # chain + act
    leg_cit: re.Pattern       # chain + "leg. cit."
    attested: dict | None = None   # act -> designations cited explicitly in the BVwG decisions; None: no table


def capital_i_variants(aliases: dict) -> dict:
    """Each alias with one lower-case "l" printed as a capital "I" -> the alias's (act, type); a variant that is an
    alias itself, or that two acts would share, is left out."""
    out, clash = {}, set()
    for alias, act in aliases.items():
        for i, ch in enumerate(alias):
            if ch != "l":
                continue
            v = alias[:i] + "I" + alias[i + 1:]
            if v in aliases:
                continue
            if v in out and out[v] != act:
                clash.add(v)
            out[v] = act
    return {v: a for v, a in out.items() if v not in clash}


def load_acts(path: Path) -> Acts:
    """The acts of the rules file and the patterns built from them."""
    spec = yaml.safe_load(Path(path).read_bytes())
    by_alias = {}
    for a in spec["acts"]:
        for alias in a["aliases"]:
            if alias in by_alias:
                raise ValueError(f"alias {alias!r} belongs to two acts")
            by_alias[alias] = (a["act"], a["type"])
    if spec.get("capital_i_for_l"):
        variants = capital_i_variants(dict(by_alias))
        by_alias.update(variants)
    alt = "|".join(re.escape(a) for a in sorted(by_alias, key=len, reverse=True))
    legislation = re.compile(rf"(?<![\w§]){CHAIN}{OF}(?P<act>{alt}){ACT_END}")
    leg_cit = re.compile(rf"(?<![\w§]){CHAIN}\s?leg\.?\s?cit\b\.?")
    return Acts(by_alias, legislation, leg_cit)


def with_attested(acts: Acts, path: Path) -> Acts:
    """The acts with the table of attested designations (attested_designations.json of stage 06)."""
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    return dataclasses.replace(acts, attested={act: frozenset(d) for act, d in spec["designations"].items()})


def _level(word: str) -> str:
    """The subdivision a word names, whatever its spelling ("Absatz", "Abs" and "Abs." are one level)."""
    return {"Absatz": "Abs", "Ziffer": "Z", "litera": "lit", "Unterabs": "UAbs"}.get(word.rstrip("."), word.rstrip("."))


def _text(parts: tuple) -> str:
    kind, num, psubs = parts
    return f"{kind} {num}" + "".join(f" {w} {v}" if w else f" {v}" for w, v in psubs)


def _read(chain: str, attested: frozenset | None = None) -> list[list]:
    """[start, end, designation, explicit] of each provision named in a chain (see the module docstring); explicit:
    printed with its own "§" / "Art."."""
    items, prev, several, last_end = [], None, False, 0
    for m in ITEM.finditer(chain):
        subs = [("", s["ord"]) if s["ord"] else (s["word"] or s["lword"], s["value"] or s["lvalue"])
                for s in SUB_PART.finditer(m["subs"])]
        if m["lead"] is not None:                                  # a subdivision after a connective
            if prev is None or not subs:
                continue
            kind, num, psubs = prev
            levels = [_level(w) if w else "Satz" for w, _ in psubs]
            level = _level(subs[0][0]) if subs[0][0] else "Satz"
            if level in levels:                                    # "§ 52 Abs. 2 Z 2 und Abs. 9" -> § 52 Abs. 9
                parts = (kind, num, psubs[:levels.index(level)] + subs)
            elif chain[last_end:m.start()].strip() == ",":         # "Art. 1, Abschnitt A, Z. 2": the same provision
                prev = (kind, num, psubs + subs)
                items[-1][1:3] = [m.end(), designation(_text(prev))]
                last_end = m.end()
                continue
            else:                                                  # "§ 8 Abs. 1 iVm Z 13": not read
                continue
            explicit = False
        elif m["bare"] is not None:
            if prev is None:
                continue
            kind, num, psubs = prev
            levels = [_level(w) for w, _ in psubs]
            new = (kind, m["bare"], subs)
            if subs and _level(subs[0][0]) in levels:              # "§§ 31 Abs. 1, 7 Abs. 2": a new provision
                parts = new
            else:
                # the innermost level that takes a number: not an ordinal sentence ("zweiter Satz") or a letter (lit.)
                k = len(psubs) - 1
                while k >= 0 and (psubs[k][0] == "" or _level(psubs[k][0]) == "lit"):
                    k -= 1
                parts = (kind, num, psubs[:k] + [(psubs[k][0] or "Satz", m["bare"])] + subs) if k >= 0 else new
                if parts is not new and attested is not None and several and kind == "§" and \
                        designation(_text(parts)) not in attested and designation(_text(new)) in attested:
                    parts = new                                    # "§§ 13 Abs. 2 bis 5 und 22" -> § 22
            several = several or parts is new                      # a new paragraph: the chain names several
            explicit = False
        else:
            kind = "§" if m["kind"].startswith("§") else "Art."
            parts = (kind, m["num"], subs)
            several = several or m["kind"] == "§§" or prev is not None
            explicit = True
        prev = parts
        items.append([m.start(), m.end(), designation(_text(parts)), explicit])
        last_end = m.end()
    return items


def chain_items(chain: str, attested: frozenset | None = None) -> list[tuple[int, int, str]]:
    """(start, end, designation) of each provision named in a chain; `attested`: the designations of the chain's act
    cited explicitly in the BVwG decisions."""
    return [(s, e, d) for s, e, d, _ in _read(chain, attested)]


def explicit_items(chain: str) -> list[str]:
    """The designations of a chain's provisions printed with their own "§" / "Art."; a deeper part printed after a
    comma belongs to it ("Art. 1, Abschnitt A, Z. 2")."""
    return [d for _, _, d, explicit in _read(chain) if explicit]
