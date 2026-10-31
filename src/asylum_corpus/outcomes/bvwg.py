"""Dispositions of the BVwG's operative parts: what the court decides, on which issues.

Each operative part is read sentence by sentence (the parts that decide the same appellants form one outcome unit:
units.py):
    text        the section's text with letter-spaced words closed up ("a b g e w i e s e n"); offsets are kept
    sentences   split at "." / "!" (also '."' / '.“') before a capital letter or a numbered point ("2. Der …"),
                not after an abbreviation ("Abs.", "Z.", "BGBl.", "mj.", "StA."), a number, a roman numeral or a
                single letter (labels "I.", "1.", "A.")
    revision    the clause on the admissibility of a revision is cut out and recorded; the rest is read
    quote       where the court's words before it dismiss, reject or correct (restates_the_authority), the
                authority's corrected operative part quoted after "zu lauten hat" / "hat zu lauten" is not read:
                up to the closing quotation mark where the quotation opens with one (also across sentences), else to
                the end of the sentence; a quotation closed right before the words is not read either
    dispositions the patterns of config/outcomes/bvwg_dispositions.yaml; an annulment under a cassation provision or
                with a cassation wording (`cassation`) is also a remand
    issues      the provisions cited in the sentence (stage 06 extractor) and its wording, by
                config/outcomes/bvwg_issues.yaml; otherwise a numbered point of the authority's decision
                ("Spruchpunkt III.", "Spruchpunkte III. bis VI.", "I.-V."; a misprinted "l." read as "I."), looked up
                in the reasoning (in the passage after the point mentioned before it, the longest of the phrases
                ending last, with the issues' point_wording); otherwise "Im Übrigen ..." concerns the remaining points,
                and a sentence about the appeal or the challenged decision as a whole whole_decision
The outcome, its subtype and the protection outcome are derived in normalize.py.
"""

from __future__ import annotations

import re

from ..citations.extract import attested_for, find, provision_citations
from ..citations.statutes import Acts
from .rules import Rules

LETTER_SPACED = re.compile(r"(?<!\w)(?:(?:[a-zäöüß] ){3,}[a-zäöüß]|(?:[A-ZÄÖÜ] ){3,}[A-ZÄÖÜ])(?!\w)")   # one case
ABBREVIATION = re.compile(r"(?:^|[\s(])(?:Abs|Z|Nr|Art|lit|BGBl|BGBL|idgF|i\.d\.g\.F|idF|i\.d\.F|iVm|i\.V\.m|vgl|gem|"
                          r"Zl|ZI|Zlen|GZ|bzw|ua|u\.a|geb|Dr|Mag|St|Fa|leg|cit|Pkt|ff|lt|etc|ca|bzgl|hins|insb|zB|"
                          r"z\.B|Rz|Rn|Ziff|UAbs|Unterabs|AZ|Mio|Hr|Fr|Nov|Zahl|vom|StGB|i\.d\.F\.d|d\.h|sog|s|S|"
                          r"mj|minderj|mind|StA|Sta)$")
# also before a numbered point "2. Der …"; the period may be followed by a closing quotation mark ('."')
SPLIT = re.compile(r"([.!][\"“”»]?)\s+(?=[A-ZÄÖÜ„\"(]|\d{1,2}\.\s+[A-ZÄÖÜ])")
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10, "xi": 11,
         "xii": 12, "xiii": 13, "xiv": 14, "xv": 15}
LABEL = re.compile(r"[(\[]?(?:\d+|[IVXivx]+|[A-Za-z])")


def close_up(text: str) -> tuple[str, list[int]]:
    """The text with letter-spaced words closed up, and for each of its characters the offset in `text`."""
    out, index, pos = [], [], 0
    for m in LETTER_SPACED.finditer(text):
        out.append(text[pos:m.start()])
        index.extend(range(pos, m.start()))
        for i in range(m.start(), m.end()):
            if text[i] != " ":
                out.append(text[i])
                index.append(i)
        pos = m.end()
    out.append(text[pos:])
    index.extend(range(pos, len(text)))
    return "".join(out), index


def lower(text: str) -> str:
    """Lower case, one character for one (so that offsets stay valid)."""
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def sentences(text: str) -> list[tuple[int, int]]:
    """(start, end) of each sentence of `text`."""
    out, start = [], 0
    for m in SPLIT.finditer(text):
        before = text[start:m.start()].split()
        last = before[-1] if before else ""
        if ABBREVIATION.search(" " + last) or LABEL.fullmatch(last):
            continue
        out.append((start, m.start() + len(m[1])))
        start = m.end()
    if text[start:].strip():
        out.append((start, len(text)))
    return out


def closing(text: str, at: int, rules: Rules) -> int | None:
    """The position of the quotation mark closing the quotation opened at `at` (inner „ … “ counted), or None."""
    opener, closers, depth = text[at], rules.quote_open[text[at]], 1
    for j in range(at + 1, len(text)):
        c = text[j]
        if c in closers:
            depth -= 1
            if depth == 0:
                return j
        elif c == opener and opener not in closers:
            depth += 1
    return None


def opening(text: str, at: int, lo: int, rules: Rules) -> int | None:
    """The position of the quotation mark opening the quotation closed at `at` (not before `lo`), or None."""
    for j in range(at - 1, lo - 1, -1):
        if text[j] in rules.quote_open and text[at] in rules.quote_open[text[j]]:
            return j
    return None


def restates_the_authority(court: str, rules: Rules) -> bool:
    """The court's words before "zu lauten hat" dismiss or reject (the authority's decision stands, its corrected
    wording repeats it) or correct an earlier decision: the quoted wording is no disposition of this decision. Where
    the court upholds or changes the decision ("Der Beschwerde wird stattgegeben und ... zu lauten hat: '... wird der
    Status ... zuerkannt'"), the quoted wording is the court's new decision and is read."""
    effects, rest = set(), court
    for d in rules.dispositions:
        if d.pattern.search(rest):
            effects.add("corrected" if d.id == "corrected" else d.effect)
            if d.removes:
                rest = d.pattern.sub(" ", rest)
    return "corrected" in effects or ("unfavourable" in effects and "favourable" not in effects)


def quoted_spans(low: str, bounds: list[tuple[int, int]], rules: Rules) -> list[tuple[int, int]]:
    """The spans of the authority's corrected wording that are not read: from after the words "zu lauten hat" (or the
    others of `quote`) to the closing mark of a quotation opened right after them, else to the end of the sentence;
    and a quotation closed right before the words. Only where the court's own words restate the authority's decision
    (restates_the_authority); where they uphold or change it, the wording is the court's new decision and is read."""
    spans = []
    for m in rules.quote.finditer(low):
        start, end = next(((s, e) for s, e in bounds if s <= m.start() < e), (0, len(low)))
        if not restates_the_authority(low[start:m.start()], rules):
            continue                                    # the court's new decision: read
        after = re.match(r"[\s:,;]*", low[m.end():]).end() + m.end()
        again = rules.quote.match(low, after)          # "zu lauten hat wie folgt:"
        if again:
            after = re.match(r"[\s:,;]*", low[again.end():]).end() + again.end()
        if after < len(low) and low[after] in rules.quote_open:
            close = closing(low, after, rules)
            end = close + 1 if close is not None else end
        spans.append((m.end(), end))                    # the words themselves are read (type modified)
        before = low[:m.start()].rstrip()
        if before and any(before[-1] in c for c in rules.quote_open.values()):
            open_ = opening(low, len(before) - 1, start, rules)
            if open_ is not None:
                spans.append((open_, m.start()))
    return spans


def norm_issues(sentence: str, acts: Acts, rules: Rules) -> tuple[list[str], bool]:
    """Issues named by the provisions cited in a sentence; and whether an ambiguous "AsylG" provision was cited."""
    found, ambiguous, last_act = [], False, None
    for _, _, rule, payload in find({"text": sentence}, acts):
        if rule == "legislation_provision":
            act, typ = acts.by_alias[payload["act"]]
            last_act = (act, typ)
        elif rule == "leg_cit" and last_act:
            act, typ = last_act
        else:
            continue
        for *_, norm in provision_citations(payload, act, typ, rule, attested_for(acts, act)):
            if rules.ambiguous_norms.search(norm):
                ambiguous = True
                continue
            issue = next((i.id for i in rules.issues if any(rx.search(norm) for rx in i.norms)), None)
            if issue and issue not in found:
                found.append(issue)
    return found, ambiguous


def wording_issues(low: str, rules: Rules) -> list[str]:
    return [i.id for i in rules.issues if any(rx.search(low) for rx in i.wording)
            and not (i.wording_unless and i.wording_unless.search(low))]


def point_references(low: str, rules: Rules) -> list[str]:
    """The numbered points of the authority's decision a sentence names, in order; ranges expanded ("iii. bis vi.",
    "i-vi.", "i.-v.", "iii. - vi.")."""
    refs = []
    for m in rules.point_reference.finditer(low):
        items = re.split(r"\s?(,|und|bis|-|–)\s?", m[1])
        values = [x.strip(" .").replace("l", "i") for x in items[0::2]]
        links = items[1::2]
        for k, v in enumerate(values):
            if not v:
                continue
            if k and links[k - 1] in ("bis", "-", "–") and values[k - 1] in ROMAN and v in ROMAN:
                inv = {n: r for r, n in ROMAN.items()}
                for n in range(ROMAN[values[k - 1]] + 1, ROMAN[v]):
                    if inv[n] not in refs:
                        refs.append(inv[n])
            if v not in refs:
                refs.append(v)
    return refs


def resolve_point(reasoning_low: str, reasoning_start: int, point: str, rules: Rules) -> dict:
    """The issue whose wording ends last within the window before the point's first mention in the reasoning. The
    window starts after the point mentioned before it where that is nearer; the issues' point_wording is read too; of
    the phrases ending last the longest wins ("ab Rechtskraft der Rückkehrentscheidung"); and where the mention
    introduces its description ("Mit Spruchpunkt IV. wurde ein Einreiseverbot erlassen", `described_after`), the window
    runs from the mention to the next one and the phrase beginning first wins."""
    mentions = list(rules.point_mention.finditer(reasoning_low))
    k = next((i for i, m in enumerate(mentions) if m[1] == point), None)
    if k is not None:
        m = mentions[k]
        after = bool(rules.point_described_after.search(reasoning_low, max(0, m.start() - 20), m.start()))
        if after:
            lo = m.end()
            hi = min(mentions[k + 1].start() if k + 1 < len(mentions) else len(reasoning_low), lo + rules.point_window)
        else:
            lo, hi = max(0, m.start() - rules.point_window), m.start()
            if k:
                lo = max(lo, mentions[k - 1].end())
        best = None
        for issue in rules.issues:
            for rx in issue.wording + issue.point_wording:
                for x in rx.finditer(reasoning_low, lo, hi):
                    if after:
                        better = best is None or x.start() < best[0] or (x.start() == best[0] and x.end() > best[1])
                    else:
                        better = best is None or x.end() > best[1] or (x.end() == best[1] and x.start() < best[0])
                    if better:
                        best = (x.start(), x.end(), issue.id)
        if best:
            return {"point": point.upper(), "issue": best[2], "start": reasoning_start + best[0],
                    "end": reasoning_start + best[1]}
    return {"point": point.upper(), "issue": None, "start": None, "end": None}


def operative_part(doc: dict, section: dict, acts: Acts, rules: Rules, reasoning: tuple[str, int] | None) -> dict:
    """Dispositions, revision and problems of one operative part. `reasoning`: (lower-case text of the reasoning, its
    offset in full_text), for the point lookup."""
    base = section["start"]
    raw = doc["full_text"][base:section["end"]]
    text, index = close_up(raw)
    low = lower(text)
    bounds = sentences(text)
    quoted = quoted_spans(low, bounds, rules)
    unblanked = low                                     # the revision clause is read before the quote is blanked
    if quoted:                                          # the authority's corrected wording is not read (blanked)
        chars, lchars = list(text), list(low)
        for a, b in quoted:
            for j in range(a, b):
                if not chars[j].isspace():
                    chars[j], lchars[j] = " ", " "
        text, low = "".join(chars), "".join(lchars)
    dispositions, revision, problems = [], set(), []
    for s, e in bounds:
        sent, sl = text[s:e], low[s:e]
        rev = rules.revision.search(unblanked[s:e])
        if rev:                                         # the revision clause is no disposition
            revision.add("inadmissible" if rules.revision_inadmissible.search(rev[0]) else "admissible")
            if rev.start() > 0 and sl[:rev.start()].strip(" .;,"):
                e = s + rev.start()
            elif sl[rev.end():].strip(" .;,"):
                s = s + rev.end()
            else:
                continue
            sent, sl = text[s:e], low[s:e]
        rest, types = sl, []
        for d in rules.dispositions:
            if d.pattern.search(rest):
                types.append(d.id)
                if d.removes:
                    rest = d.pattern.sub(" ", rest)
        if "corrected" in types and rules.correction.search(sl):     # the rest describes the corrected decision
            types = [t for t in types if t in rules.keeps_with_correction]
        if not types:
            continue
        if "annulled" in types and "remanded" not in types and rules.cassation.search(sl):
            types.append("remanded")
        issues, ambiguous = norm_issues(sent, acts, rules)
        via = {i: "norm" for i in issues}
        for i in wording_issues(sl, rules):
            via.setdefault(i, "wording")
        for i in list(via):
            for r in rules.issue(i).replaces:
                via.pop(r, None)
        if ambiguous:
            problems.append("ambiguous_asylg")
        points = []
        if not via:
            refs = point_references(sl, rules)
            if refs:
                for r in refs:
                    p = resolve_point(*reasoning, r, rules) if reasoning else \
                        {"point": r.upper(), "issue": None, "start": None, "end": None}
                    points.append(p)
                    if p["issue"] is None:
                        problems.append("unresolved_point")
            elif rules.remaining.search(sl):
                via["remaining_points"] = "wording"
            elif rules.whole_decision.search(sl):
                via["whole_decision"] = "whole_decision"
            else:
                problems.append("unattributed_sentence")
        # span: trimmed, mapped back to the section's original text
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        start, end = base + index[s], base + index[e - 1] + 1
        dispositions.append({"start": start, "end": end, "text": doc["full_text"][start:end], "types": types,
                             "issues": [{"issue": i, "via": v} for i, v in via.items()], "points": points,
                             "operative_part_id": section["id"]})
    if not dispositions:
        problems.append("no_disposition")
    rev = "partly" if revision == {"inadmissible", "admissible"} else (revision.pop() if revision else "none")
    return {"dispositions": dispositions, "revision": rev, "problems": sorted(set(problems))}
