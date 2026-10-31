"""Labels from the head of a BVwG decision: judge, appellants, nationality, representation, the challenged decision and
the scope of the appeal (rules: config/head/bvwg_head.yaml). The case history is read by history.py.

The head is the text of the decision's subject sections (the RIS field Spruch up to the introductory sentence).
Patterns are applied to it with its whitespace collapsed, and every span is mapped back to `full_text`. Values that
are not printed stay null or empty; nothing is inferred beyond the text, except:
    judge.gender          from "Richter" / "Richterin" (or "Einzelrichter(in)", "Vorsitzende(n)")
    judge.department      the court department from the decision's case number ("W123 2212345-1" -> "W123")
    authority             BAA, not BFA, where a decision under the BFA's name has a Bundesasylamt file number
    appellants.count      the largest of: the distinct case numbers without department and proceeding number (one
                          per person); the numbered list "1.) XXXX … 4.) XXXX" of the head; the distinct persons
                          (IFA numbers) of the challenged decisions' file numbers; where the head has no numbered
                          list, the unnumbered persons with an article ("der XXXX und des XXXX") in its first block;
                          1 where none of these counts anything
    appellants.minors     also where a representative is itself represented by a lawyer or an organisation (a
                          statutory representative)
    representation.kinds  the kinds of the appeal's representatives; a guardian only where no other one is named
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import yaml

from ..common import CONFIG
from ..ris.text import case_persons
from .history import HISTORY_CONFIG, HistoryRules, read_history

HEAD_CONFIG = CONFIG / "head" / "bvwg_head.yaml"
ROMAN = ["I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"]
MONTHS = {m: i for i, m in enumerate(["jänner", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                       "september", "oktober", "november", "dezember"], 1)} | {"januar": 1}
STATE_FORM = re.compile(r"^(?:islamische[n]?\s+republik|arabische[n]?\s+republik|föderale[n]?\s+demokratische[n]?\s+"
                        r"republik|kirgisische[n]?\s+republik|volksrepublik|bundesrepublik|republik|vr)\s+")


class HeadRules:
    def __init__(self, path: Path = HEAD_CONFIG, history_path: Path = HISTORY_CONFIG):
        spec = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.history = HistoryRules(history_path)
        j, a, n, r, c = (spec[k] for k in
                         ("judge", "appellants", "nationality", "representation", "challenged_decision"))
        self.judge_intro = re.compile(j["intro"])
        self.female, self.male = re.compile(j["female"]), re.compile(j["male"])
        self.single_judge, self.senate = re.compile(j["single_judge"]), re.compile(j["senate"])
        self.titles = re.compile(rf"\b{j['titles']}\s*,?\s*")
        self.name_trailing = re.compile(j["name_trailing"])
        self.numbered, self.births, self.minors = (re.compile(a[k]) for k in ("numbered", "births", "minors"))
        self.unnumbered, self.persons_start, self.persons_end, self.block = (
            re.compile(a[k]) for k in ("unnumbered", "persons_start", "persons_end", "block"))
        self.nationality, self.more_nationality = re.compile(n["printed"]), re.compile(n["more"])
        self.countries = {k.lower(): v for k, v in n["countries"].items()}
        self.representation, self.statutory = re.compile(r["printed"]), re.compile(r["statutory"])
        self.rep_chain, self.rep_for_all = re.compile(r["chain"]), re.compile(r["for_all"])
        self.rep_removed, self.also_lawyer = re.compile(r["removed"]), re.compile(r["also_lawyer"])
        self.rep_kinds = [(k["kind"], re.compile(k["pattern"])) for k in r["kinds"]]
        self.decision = re.compile(c["printed"])
        self.decision_without_date = re.compile(c["printed_without_date"])
        self.decision_without_authority = re.compile(c["printed_without_authority"])
        self.more_dates = re.compile(c["more_dates"])
        self.file_number = re.compile(c["file_number"])
        self.file_number_bare = re.compile(c["file_number_bare"])
        self.file_number_in_authority = re.compile(c["file_number_in_authority"])
        self.baa_file_number = re.compile(c["baa_file_number"])
        self.authorities = [(name, re.compile(p)) for name, p in c["authorities"].items()]
        self.scope = re.compile(c["scope"])
        self.failure_to_act = re.compile(c["failure_to_act"])

    def kind_of(self, printed: str) -> str:
        return next((k for k, rx in self.rep_kinds if rx.search(printed)), "other")


# ----------------------------------------------------------------------------------------------------------------- text

def head_text(doc: dict) -> tuple[str, list[int]]:
    """The subject sections' text with whitespace runs collapsed to one space, and each character's offset in
    full_text."""
    out, index = [], []
    for s in doc["sections"]:
        if s["type"] != "subject":
            continue
        if out:
            out.append(" ")
            index.append(s["start"])
        prev_space = False
        for i in range(s["start"], s["end"]):
            ch = doc["full_text"][i]
            if ch.isspace():
                if prev_space:
                    continue
                ch, prev_space = " ", True
            else:
                prev_space = False
            out.append(ch)
            index.append(i)
    return "".join(out), index


def span(index: list[int], start: int, end: int) -> dict:
    return {"start": index[start], "end": index[end - 1] + 1}


def iso_date(text: str) -> str | None:
    """'12.03.2018' or '14. Juni 2014' -> ISO date; None for 'XXXX' (anonymised) or an impossible date."""
    m = re.match(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{2,4})", text)
    n = re.match(r"(\d{1,2})\.\s?([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", text)
    if m:
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    elif n and n[2].lower() in MONTHS:
        d, mo, y = int(n[1]), MONTHS[n[2].lower()], int(n[3])
    else:
        return None
    y = y + 2000 if y < 100 else y
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None


def roman_points(text: str) -> list[str]:
    """The points of "I., II. und IV.", "II. bis IV." or "1. und 2." (Arabic numerals read as Roman)."""
    values = {r: i for i, r in enumerate(ROMAN, 1)}
    items = re.split(r"\s?(,|und|bis|sowie)\s?", text)
    tokens, links = [x.strip(" .") for x in items[0::2]], items[1::2]
    tokens = [ROMAN[int(x) - 1] if x.isdigit() and 1 <= int(x) <= 10 else x for x in tokens]
    out = []
    for k, t in enumerate(tokens):
        if t not in values:
            continue
        if k and links[k - 1] == "bis" and tokens[k - 1] in values:
            out += [ROMAN[n - 1] for n in range(values[tokens[k - 1]] + 1, values[t])]
        out.append(t)
    return out


def normalise_country(printed: str, countries: dict) -> str:
    """The country of a printed nationality (config list), or the printed name in title case."""
    text = re.sub(r"\s+", " ", printed.strip())
    if text.isupper():
        text = text.title()
    key = re.sub(r"\s+und$", "", text.lower())
    if key in countries:
        return countries[key]
    if not key.startswith(("demokratische", "dr ")):
        key = STATE_FORM.sub("", key)
    for k in (key, key[:-1] if key.endswith("s") else None):
        if k and k in countries:
            return countries[k]
    return " ".join(w[:1].upper() + w[1:] for w in key.split(" ")) if key != text.lower() else text


def person_of(file_number: str | None) -> str | None:
    """The person a challenged decision's file number names: a BFA file number begins with the person's IFA number
    (8 to 10 digits, after an optional two-digit prefix: "15-1085419804-151246981"); else the number itself. None for
    an anonymised or missing number."""
    if not file_number or file_number.upper().startswith("XXX") or not re.search(r"\d", file_number):
        return None
    m = re.match(r"(?:\d{2}-)?(\d{8,10})(?!\d)", file_number)
    return m[1] if m else re.sub(r"\s+", "", file_number)


def blocks(head: str, rules: HeadRules) -> list[tuple[int, int]]:
    """The head's blocks: each "Das Bundesverwaltungsgericht hat / erkennt / beschließt" begins one (a head repeated per
    appellant, or the parts I. and II. of a joined decision); the text before the first belongs to the first."""
    starts = [m.start() for m in rules.block.finditer(head)] or [0]
    starts[0] = 0
    return list(zip(starts, starts[1:] + [len(head)]))


def persons_part(head: str, a: int, b: int, rules: HeadRules) -> str:
    """The part of a head block that names the appellants: from its "(Säumnis)Beschwerde", "Vorlageantrag" or "Antrag"
    up to its "gegen den / die / das / jeweils / Spruch…", "wegen" or "betreffend"."""
    s = rules.persons_start.search(head, a, b)
    a = s.end() if s else a
    m = rules.persons_end.search(head, a, b)
    return head[a:m.start() if m else b]


# --------------------------------------------------------------------------------------------------------------- labels

def read_judge(doc: dict, head: str, index: list[int], rules: HeadRules) -> dict:
    judge = {"name": None, "gender": None, "formation": None, "department": None, "span": None}
    if m := rules.judge_intro.search(head):
        name = rules.name_trailing.sub("", rules.titles.sub("", m["name"]).strip(" ,")).strip(" ,")
        role = (m["role"] or "") + " " + head[m.end():m.end() + 40]
        judge.update({"name": name or None, "span": span(index, m.start("name"), m.end("name"))})
        judge["gender"] = "female" if rules.female.search(role) else "male" if rules.male.search(role) else None
    judge["formation"] = ("single_judge" if rules.single_judge.search(head) else
                          "senate" if rules.senate.search(head) else None)
    dep = re.match(r"\s*([WGLI])\s?(\d{3})\b", doc.get("case_number") or "")
    judge["department"] = f"{dep[1]}{dep[2]}" if dep else None
    return judge


def read_nationalities(head: str, index: list[int], rules: HeadRules) -> tuple[list[dict], set[str]]:
    nationalities, seen = [], set()
    known = set(rules.countries.values()) - {"anonymised", "unknown", "stateless"}
    for x in rules.nationality.finditer(head):
        g = next(k for k in ("country", "adjective", "stateless") if x[k])
        country = normalise_country(x[g], rules.countries)
        nationalities.append({"country": country, "printed": x[g], **span(index, x.start(g), x.end(g))})
        seen.add(country)
        more = rules.more_nationality.match(head, x.end())
        if g == "country" and more and normalise_country(more["country"], rules.countries) in known:
            second = normalise_country(more["country"], rules.countries)
            nationalities.append({"country": second, "printed": more["country"],
                                  **span(index, more.start("country"), more.end("country"))})
            seen.add(second)
    return nationalities, seen


def read_representation(head: str, index: list[int], rules: HeadRules) -> tuple[list[dict], set[str], bool]:
    """The representatives with their kinds, the kinds of the appeal's representation, and whether a representative
    is itself represented by a lawyer or an organisation (a statutory representative: the head names a minor)."""
    reps, printed, statutory_chain = [], set(), False
    for x in rules.representation.finditer(head):
        who = x["who"].strip(" ,")
        if who in printed:                          # the same representative printed again (a repeated head)
            continue
        printed.add(who)
        statutory = rules.statutory.search(head, max(0, x.start() - 25), x.start())
        cuts = list(rules.rep_chain.finditer(who))
        first = who[:cuts[0].start()] if cuts else who
        kind = "guardian" if statutory else rules.kind_of(first)
        if kind in ("anonymised", "other") and not cuts and rules.rep_for_all.match(head, x.start("who") + len(who)):
            kind = "guardian"
        later = [(c.end(), (cuts[i + 1].start() if i + 1 < len(cuts) else len(who))) for i, c in enumerate(cuts)]
        pro = next(((a, b) for a, b in later if rules.kind_of(who[a:b]) in ("lawyer", "organisation")
                    or (kind == "guardian" and rules.kind_of(who[a:b]) == "anonymised")), None)
        at = x.start("who")
        if kind in ("guardian", "anonymised", "other") and pro:
            reps.append({"kind": "guardian", "printed": first, **span(index, at, at + len(first))})
            reps.append({"kind": rules.kind_of(who[pro[0]:pro[1]]), "printed": who[pro[0]:pro[1]],
                         **span(index, at + pro[0], at + pro[1])})
            statutory_chain = True
        elif (lw := rules.also_lawyer.search(first)) and rules.kind_of(first[:lw.start()]) == "organisation":
            reps.append({"kind": "organisation", "printed": first[:lw.start()], **span(index, at, at + lw.start())})
            reps.append({"kind": "lawyer", "printed": who[lw.end():], **span(index, at + lw.end(), at + len(who))})
        else:
            reps.append({"kind": kind, "printed": who, **span(index, at, at + len(who))})
    for x in rules.rep_removed.finditer(head):      # "vertreten durch, gegen": the name removed by the anonymisation
        if not any(r["start"] <= index[x.start()] < r["end"] + 30 for r in reps):
            reps.append({"kind": "anonymised", "printed": "", **span(index, x.start(), x.end())})
    kinds = {r["kind"] for r in reps}
    if kinds - {"guardian"}:
        kinds.discard("guardian")
    return reps, kinds, statutory_chain


def authority_of(printed: str, rules: HeadRules) -> str | None:
    """The authority named first in the printed name ("Bundesasylamtes (nunmehr: Bundesamt für Fremdenwesen und
    Asyl)" is the BAA's); None where none is printed."""
    if not printed:
        return None
    found = [(m.start(), name) for name, rx in rules.authorities if (m := rx.search(printed))]
    return min(found)[1] if found else "other"


def file_number_after(head: str, at: int, rules: HeadRules) -> str | None:
    fn = rules.file_number.search(head, at)            # beginning within 60 characters of the date
    fn = fn if fn and fn.start() - at <= 60 else rules.file_number_bare.match(head, at)
    return fn["number"].rstrip(".,“\"") if fn else None


def read_decisions(head: str, index: list[int], rules: HeadRules) -> tuple[list[dict], list[int]]:
    """The challenged decisions (the ones after "gegen" first, then in their order) and where each begins."""
    matches = (list(rules.decision.finditer(head)) or list(rules.decision_without_date.finditer(head))[:1]
               or list(rules.decision_without_authority.finditer(head))[:1])
    matches.sort(key=lambda x: ("gegen" not in head[max(0, x.start() - 20):x.start() + 6], x.start()))
    decisions, starts = [], []
    for x in matches:
        dated = "date" in x.re.groupindex
        printed = (x["authority"] or "").strip(" ,") if "authority" in x.re.groupindex else ""
        authority = authority_of(printed, rules)
        fn = file_number_after(head, x.end(), rules)
        if inside := rules.file_number_in_authority.search(printed):   # a file number inside the authority's name
            printed = printed[:inside.start()].strip(" ,")
            fn = fn or inside["number"]
        if authority == "BFA" and fn and rules.baa_file_number.search(fn):
            authority = "BAA"                       # a Bundesasylamt file number under the BFA's name
        starts.append(x.start())
        decisions.append({"authority": authority, "authority_printed": printed,
                          "date": iso_date(x["date"]) if dated else None,
                          "file_number": fn, **span(index, x.start(), x.end())})
        at = x.end()
        while dated and (more := rules.more_dates.match(head, at)):
            g = "date" if more["date"] else "anonymised"
            starts.append(more.start(g))
            decisions.append({"authority": authority, "authority_printed": printed,
                              "date": iso_date(more[g]), "file_number": file_number_after(head, more.end(), rules),
                              **span(index, more.start(g), more.end(g))})
            at = more.end()
    return decisions, starts


def count_appellants(doc: dict, head: str, rules: HeadRules, decisions: list[dict],
                     starts: list[int]) -> tuple[int | None, str]:
    """The number of appellants and the count it rests on (the largest)."""
    persons = case_persons(doc.get("case_numbers"))
    head_blocks = blocks(head, rules)
    listed = max((int(x[1]) for x in rules.numbered.finditer(head)), default=0)
    first_part = persons_part(head, *head_blocks[0], rules) if head else ""
    unnumbered = len(rules.unnumbered.findall(first_part)) if not listed else 0
    ifa = {p for p in (person_of(d["file_number"]) for d in decisions) if p and p.isdigit()}
    per_block = set()
    for a, b in head_blocks:
        first_in = next((d for d, x in zip(decisions, starts) if a <= x < b), None)
        if first_in and person_of(first_in["file_number"]):
            per_block.add(person_of(first_in["file_number"]))
    by_file = max(len(ifa), len(per_block))
    count = max(len(persons), listed, by_file, unnumbered) or (1 if head else None)
    basis = max([("case_numbers", len(persons)), ("numbered_list", listed), ("file_numbers", by_file),
                 ("head_persons", unnumbered)], key=lambda b: b[1])[0]
    return count, basis


def read_scope(head: str, index: list[int], rules: HeadRules) -> dict:
    scopes = list(rules.scope.finditer(head))
    points = []
    for s in scopes:
        for p in roman_points(s["points"] or s["points2"]):
            if p not in points:
                points.append(p)
    points.sort(key=ROMAN.index)
    first = scopes[0] if scopes else None
    return {"whole": not points, "points": points, "span": span(index, first.start(), first.end()) if first else None}


def extract(doc: dict, rules: HeadRules) -> dict:
    head, index = head_text(doc)
    judge = read_judge(doc, head, index, rules)
    nationalities, seen = read_nationalities(head, index, rules)
    reps, kinds, statutory_chain = read_representation(head, index, rules)
    decisions, starts = read_decisions(head, index, rules)
    count, basis = count_appellants(doc, head, rules, decisions, starts)
    minors = [span(index, x.start(), x.end()) for x in rules.minors.finditer(head)]
    if statutory_chain:                         # a statutory representative names a minor
        minors += [{"start": r["start"], "end": r["end"]} for r in reps if r["kind"] == "guardian"][:1]
    history = read_history(doc, rules.history, head, index, [(d["authority"], d["date"]) for d in decisions], count)
    problems = ([] if judge["span"] else ["no_judge"]) + ([] if head else ["no_head"]) \
        + ([] if nationalities else ["no_nationality"]) + ([] if decisions else ["no_challenged_decision"])
    return {
        "document_id": doc["document_id"], "ris_id": doc["ris_id"], "decision_date": doc["decision_date"],
        "judge": judge,
        "appellants": {"count": count, "count_basis": basis, "births_printed": len(rules.births.findall(head)),
                       "minors": bool(minors), "minor_spans": minors},
        "nationalities": nationalities, "nationality_set": sorted(seen),
        "representation": {"represented": bool(reps), "kinds": sorted(kinds), "representatives": reps},
        "challenged_decisions": decisions,
        "scope": read_scope(head, index, rules),
        "failure_to_act": bool(rules.failure_to_act.search(head)),
        **history,
        "head_chars": len(head),
        "problems": problems,
    }
