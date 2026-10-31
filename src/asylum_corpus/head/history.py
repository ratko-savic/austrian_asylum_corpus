"""The case history of a BVwG decision: earlier court decisions with their dates and courts, the court's hearing in
this proceeding (held / not held / unknown), the interpreter at the police first questioning, the BFA interview and
the court hearing, the appellant's gender, and whether the proceeding runs under the law in force since 12 June 2026
(rules: config/head/bvwg_history.yaml).

Reads the coarse sections of stage 05: the procedural history and the findings (without them, the reasoning
outside the court's assessment), the head and, for the hearing, the assessment of evidence and the legal assessment.
The text is split into sentences; a rule binds a date to its event within one sentence. Every event records its rule,
the part it was read from and its span in `full_text`. Nothing is inferred beyond the text, except:
    kind of a court decision  "this decision" where its date is the decision's or the pronouncement's date
    court of a decision       the court whose case number is printed after the date, else the court named, else the
                              appeal court of the decision's date (court_inferred)
    pronouncement             a head saying "mündlich verkündet" without a date: the date of the hearing it names
    hearing                   held where the head says so, the decision was pronounced orally, or a hearing is dated
                              on or after the challenged decision (any dated hearing where none is dated), else where
                              a sentence names the court hearing; not held where the court says it could do without
                              one
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from ..common import CONFIG

HISTORY_CONFIG = CONFIG / "head" / "bvwg_history.yaml"
MACRO = re.compile(r"\{([A-Z][A-Z0-9_]*)\}")
MONTHS = {m: i for i, m in enumerate(["jänner", "februar", "märz", "april", "mai", "juni", "juli", "august",
                                       "september", "oktober", "november", "dezember"], 1)} | {"januar": 1, "feber": 2}
EVENT_TYPES = ("court_decision", "hearing")
HISTORY_PARTS = {"procedural_history", "procedural_history_and_findings", "reasoning"}
# a sentence ends at a period, "!", "?" or ";" followed by an upper-case word or a numbered point, or at a line break
END = re.compile(r"[.!?;]\s+(?=[A-ZÄÖÜ„\"(\[]|(?:\d{1,2}|[IVX]{1,4})(?:\.\d{1,2})*\.\s+[A-ZÄÖÜ])|\n+")
TOKEN = re.compile(r"(\S+)$")
# an insertion between dashes ("brachten die BF – nach zwischenzeitlicher Einreise in Deutschland … – die zweiten
# Anträge … ein"): its dates and places are not those of the event
DASHED = re.compile(r"\s[–—-]\s[^–—]{3,240}?\s[–—-](?=\s)")
HEAD_PERSONS_END = re.compile(r"\bgegen\s+(?:den|die|das)\b|\bwegen\b")


def expand(pattern: str, macros: dict) -> str:
    for _ in range(8):
        new = MACRO.sub(lambda m: macros.get(m[1], m[0]), pattern)
        if new == pattern:
            return new
        pattern = new
    raise ValueError(f"macros nested too deep: {pattern[:80]}")


@dataclass(frozen=True)
class Rule:
    id: str
    pattern: re.Pattern
    reason: str
    same_day: bool = False      # the date is the last day date before the match in the sentence ("am selben Tag")


class HistoryRules:
    def __init__(self, path: Path = HISTORY_CONFIG):
        spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        mac = spec["macros"]

        def rx(pattern: str) -> re.Pattern:
            return re.compile(expand(pattern, mac))

        self.parts, self.fallback_without = tuple(spec["parts"]), set(spec["fallback_without"])
        self.hearing_parts, self.no_hearing_parts = tuple(spec["hearing_parts"]), tuple(spec["no_hearing_parts"])
        self.abbreviations = set(spec["sentences"]["abbreviations"])
        self.date_token = rx("{DATE}")
        self.appellant, self.relative = rx(spec["appellant"]), rx(spec["relative"])
        self.appellant_strict = rx(spec["appellant_strict"])
        self.relative_of_appellant, self.representation = rx(spec["relative_of_appellant"]), rx(spec["representation"])
        self.relative_pronoun = rx(spec["relative_pronoun"])
        self.relative_of_coappellant = rx(spec["relative_of_coappellant"])
        self.date_correction = rx(spec["date_correction"])
        self.pronouncement = rx(spec["pronouncement"])
        self.pronounced_without_date = rx(spec["pronounced_without_date"])
        self.pronouncement_chars = int(spec["pronouncement_reasoning_chars"])
        self.groups = {kind: compile_group(spec[kind], rx) for kind in ("court_decisions", "hearings", "applications")}
        self.new_law_from = str(spec["new_law"]["from"])
        iv = spec["interviews"]
        self.interview_trigger = rx(iv["trigger"])
        self.steps = [(name, rx(p)) for name, p in iv["steps"].items()]
        self.not_bfa, self.not_hearing = rx(iv["not_bfa"]), rx(iv["not_hearing"])
        self.interpreter, self.no_interpreter = rx(iv["interpreter"]), rx(iv["no_interpreter"])
        self.interpreter_skip, self.language = rx(iv["skip"]), rx(iv["language"])
        self.languages = {k.lower(): v for k, v in iv["languages"].items()}
        gx = spec["appellant_gender"]
        self.gender_sections = tuple(gx["sections"])
        self.min_score, self.min_share = float(gx["min_score"]), float(gx["min_share"])
        self.one_sided_min_score = float(gx["one_sided_min_score"])
        self.full_word_cues = set(gx["full_word_cues"])
        self.gender_cues = [(c["id"], g, float(c["weight"]), re.compile(c["pattern"])) for g in ("female", "male")
                         for c in gx["cues"][g]]
        self.head_person = re.compile(gx["head_person"])
        self.head_person_unnumbered = re.compile(gx["head_person_unnumbered"])
        self.head_block = re.compile(gx["head_block"])
        self.ordinal, self.ordinals = re.compile(gx["ordinal"]), {k.lower(): v for k, v in gx["ordinals"].items()}
        self.ordinal_weight = float(gx["ordinal_weight"])
        self.person_cues = [(c["id"], c["gender"], re.compile(c["pattern"])) for c in gx["person_cues"]]
        self.person_cue_weight = float(gx["person_cue_weight"])


def compile_group(g: dict, rx) -> dict:
    """An event group of the config: its rules, its patterns, its named patterns and its other values."""
    out = {}
    for key, value in g.items():
        if key == "rules":
            out[key] = [Rule(r["id"], rx(r["pattern"]), r["reason"], bool(r.get("same_day"))) for r in value]
        elif isinstance(value, str):
            out[key] = rx(value)
        elif isinstance(value, dict):
            out[key] = [(name, rx(p)) for name, p in value.items()]
        else:
            out[key] = value
    return out


# ----------------------------------------------------------------------------------------------------------------- text

def ranges(doc: dict, types: tuple[str, ...] | set[str]) -> list[tuple[int, int, str]]:
    """The character ranges of the document's sections of these types, with their type; overlapping ranges (the
    findings inside procedural_history_and_findings) are merged."""
    out = sorted((s["start"], s["end"], s["type"]) for s in doc["sections"] if s["type"] in types)
    merged: list[list] = []
    for s, e, t in out:
        if merged and s < merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e, t])
    return [tuple(m) for m in merged]


def history_ranges(doc: dict, rules: HistoryRules) -> list[tuple[int, int, str]]:
    """The history parts; without them the top-level reasoning minus the court's assessment."""
    found = ranges(doc, rules.parts)
    if found:
        return found
    out = []
    for r in (s for s in doc["sections"] if s["type"] == "reasoning" and s.get("level", 1) == 1):
        cuts = sorted((s["start"], s["end"]) for s in doc["sections"]
                      if s["type"] in rules.fallback_without and r["start"] <= s["start"] < r["end"])
        pos = r["start"]
        for cs, ce in cuts:
            if cs > pos:
                out.append((pos, cs, "reasoning"))
            pos = max(pos, ce)
        if pos < r["end"]:
            out.append((pos, r["end"], "reasoning"))
    return out


def sentences(text: str, start: int, end: int, abbreviations: set[str]) -> list[tuple[int, int]]:
    """Sentence spans in text[start:end]: a period, "!", "?" or ";" followed by an upper-case word or a numbered point
    ("2. Am ...") ends a sentence, a period not where the word before it is an abbreviation, a number (except a
    four-digit year), an ordinal or an initial; a line break always ends one."""
    out, pos, seg = [], start, text[start:end]
    for m in END.finditer(seg):
        cut = start + m.start() + 1
        if seg[m.start()] == ".":
            tok = TOKEN.search(seg, max(0, m.start() - 40), m.start())
            word = (tok[1] if tok else "").lstrip("([„\"")
            if word in abbreviations or (len(word) == 1 and word.isalpha()):
                continue
            if word[:1].isdigit() and not re.fullmatch(r"(?:\d{1,2}\.){0,2}(?:19|20)\d{2}", word):
                continue
        if cut > pos:
            out.append((pos, cut))
        pos = start + m.end()
    if pos < end:
        out.append((pos, end))
    return out


# ---------------------------------------------------------------------------------------------------------------- dates

def parse_date(printed: str) -> tuple[str | None, str, bool]:
    """(value, precision, anonymised): value an ISO date, year-month or year; precision day / month / year /
    anonymised."""
    t = " ".join(printed.split())
    if m := re.fullmatch(r"(\d{1,2})\.\s?(\d{1,2})\.\s?(\d{4})", t):
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
    elif (m := re.fullmatch(r"(\d{1,2})\.\s?([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", t)) and m[2].lower() in MONTHS:
        d, mo, y = int(m[1]), MONTHS[m[2].lower()], int(m[3])
    elif (m := re.fullmatch(r"(?:(?:Anfang|Mitte|Ende)\s+)?([A-Za-zäöüÄÖÜ]+)\s+(\d{4})", t)) and m[1].lower() in MONTHS:
        return f"{m[2]}-{MONTHS[m[1].lower()]:02d}", "month", False
    elif m := re.fullmatch(r"(?:(?:Frühjahr|Frühling|Sommer|Herbst|Winter)|(?:dem\s+)?Jahre?)\s+(\d{4})", t):
        return m[1], "year", False
    elif m := re.fullmatch(r"XXXX\s?\.?\s?(\d{1,2})\s?\.\s?(\d{4})", t):      # "XXXX .09.2022", "XXXX 08.2019"
        return (f"{m[2]}-{int(m[1]):02d}", "month", True) if 1 <= int(m[1]) <= 12 else (None, "anonymised", True)
    elif m := re.fullmatch(r"XXXX\s?\.?\s?(\d{4})", t):
        return m[1], "year", True
    elif t.startswith("XXXX"):
        return None, "anonymised", True
    else:
        return None, "anonymised", False
    try:
        return date(y, mo, d).isoformat(), "day", False
    except ValueError:
        return None, "anonymised", False


def dates_in(text: str, offset: int, rules: HistoryRules) -> list[dict]:
    """Every date of a date group ("16. und 17.07.2019" gives both days), with its offset in full_text."""
    out = []
    for m in rules.date_token.finditer(text):
        value, precision, anon = parse_date(m[0])
        out.append({"date": value, "precision": precision, "anonymised": anon, "printed": " ".join(m[0].split()),
                    "at": offset + m.start()})
        if precision != "day":
            continue
        lead, days = text[:m.start()], []
        while lm := re.search(r"(\d{1,2})\.\s?(?:,|und|sowie)\s?$", lead):
            days.insert(0, (int(lm[1]), lm.start()))
            lead = lead[:lm.start()]
        for d, at in days:
            try:
                v = date(int(value[:4]), int(value[5:7]), d).isoformat()
            except ValueError:
                continue
            out.append({"date": v, "precision": "day", "anonymised": False, "printed": f"{d}. ({m[0]})",
                        "at": offset + at})
    return sorted(out, key=lambda x: x["at"])


def date_group(m: re.Match) -> tuple[str, int] | None:
    for name in ("date", "date2", "date3"):
        if name in m.re.groupindex and m[name] is not None:
            return m[name], m.start(name)
    return None


# --------------------------------------------------------------------------------------------------------------- events

def about_someone_else(sentence: str, upto: int, rules: HistoryRules) -> bool:
    """The sentence (up to the event) is about a relative or witness, not the appellant: it names the appellant's
    relative ("die Mutter des BF"), or a relative without naming the appellant, or a relative clause about the relative
    named last ("…, welcher mit Bescheid …"), and is no representation ("für ihn")."""
    head = sentence[:upto]
    if rules.representation.search(sentence):
        return False
    if rules.relative_of_appellant.search(head):
        return True
    co = rules.relative_of_coappellant.search(head)
    if co and not rules.appellant.search(head, 0, co.start()):
        return True
    rel = [m.end() for m in rules.relative.finditer(head)]
    app = [m.end() for m in rules.appellant.finditer(head)]
    if rel and not app:
        return True
    if rel and rel[-1] > app[-1]:
        return bool(rules.relative_pronoun.search(head, rel[-1]))
    return False


def abroad(sent: str, m: re.Match, g: dict, date_end: int) -> bool:
    """An application in another state: a EURODAC sentence, a foreign place at the event ("in Polen", "dort"), or a
    relative clause ("..., wo er am X einen Antrag stellte") whose place, the last named before "wo", is not Austria.
    Not where the text places it in Austria right after its date ("Asylantragstellung am 10.03.2018 in Österreich")."""
    if g["here_after"].match(sent, date_end):
        return False
    if g["foreign_sentence"].search(sent):
        return True
    if g["foreign"].search(DASHED.sub(" ", sent[max(0, m.start() - 40):m.end() + 60])):
        return True
    before = sent[:m.start()]
    rel = list(g["relative_clause"].finditer(before))
    if rel and len(before) - rel[-1].end() < 80:
        upto = before[:rel[-1].start()]
        home = [x.end() for x in g["place_austria"].finditer(upto)]
        other = [x.end() for x in g["place_other"].finditer(upto)]
        return bool(other) and (not home or max(other) > max(home))
    return False


def first_match(pairs: list[tuple[str, re.Pattern]], *texts: str) -> str | None:
    for t in texts:
        for name, rx in pairs:
            if rx.search(t):
                return name
    return None


def nearest(pairs: list[tuple[str, re.Pattern]], sentence: str, at: int) -> str | None:
    """The name whose mention ends closest before position `at` in the sentence, else the first one after it."""
    found = [(m.start(), m.end(), name) for name, rx in pairs for m in rx.finditer(sentence)]
    before = [f for f in found if f[1] <= at]
    if before:
        return max(before, key=lambda f: f[1])[2]
    after = [f for f in found if f[0] >= at]
    return min(after)[2] if after else None


def own_numbers(doc: dict) -> set[str]:
    """The decision's own case numbers without department ("W286 2164690-2" -> "2164690-2")."""
    return {m[0] for c in doc.get("case_numbers") or [] for m in [re.search(r"\d{7}-\d+", c)] if m}


def appeal_court(day: str | None, eras: list) -> str | None:
    """The appeal court of a date (the first era whose end lies after it)."""
    if not day:
        return None
    return next((court for end, court in eras if day < str(end)), None)


def court_of(sent: str, m: re.Match, date_end: int, g: dict) -> tuple[str, tuple[str, str] | None]:
    """The court of a court decision, and the court and case number printed right after its date, if any."""
    at = g["court_number_at"].match(sent, date_end).end()
    by_number = next(((name, nm[0]) for name, rx in g["court_numbers"] if (nm := rx.match(sent, at))), None)
    near = sent[max(0, m.start() - 12):m.end()]
    court = ((by_number[0] if by_number else None) or first_match(g["courts"], near)
             or nearest(g["courts"], sent, m.start()) or "unnamed")
    return court, by_number


def read_events(doc: dict, rules: HistoryRules, spans: list[tuple[int, int, str]], kinds: tuple[str, ...],
                challenged: set[str], embassy: bool = False) -> list[dict]:
    text, events = doc["full_text"], []
    own = {doc.get("decision_date"), doc.get("pronouncement_date")} - {None}
    numbers = own_numbers(doc)
    for start, end, part in spans:
        for s, e in sentences(text, start, end, rules.abbreviations):
            sent = text[s:e]
            dashed = [(x.start(), x.end()) for x in DASHED.finditer(sent)]
            for kind in kinds:
                g = rules.groups[kind]
                if not g["trigger"].search(sent):
                    continue
                for rule in g["rules"]:
                    for m in rule.pattern.finditer(sent):
                        dg = date_group(m)
                        if rule.same_day:
                            before = [d for d in rules.date_token.finditer(sent, 0, m.start())
                                      if parse_date(d[0])[1] == "day"]
                            dg = (before[-1][0], before[-1].start()) if before else None
                        if dg is None:
                            continue
                        if corr := rules.date_correction.match(sent, dg[1] + len(dg[0])):
                            dg = (corr["date"], corr.start("date"))
                        date_end = dg[1] + len(dg[0])
                        if any(a <= dg[1] < b and not a <= m.start() < b for a, b in dashed):
                            continue        # the date of a dash insertion, the event outside it
                        if (skip := g.get("skip_sentence")) and skip.search(sent):
                            continue
                        if kind != "hearings" and part != "subject" and about_someone_else(sent, m.end(), rules):
                            continue
                        if (kind == "court_decisions" and rules.relative.search(sent)
                                and not rules.appellant.search(sent)):
                            continue        # a relative named after the event, the appellant not at all
                        if kind == "applications" and abroad(sent, m, g, date_end):
                            continue
                        ev = {"type": kind[:-1], "rule": rule.id, "part": part,
                              "start": s + m.start(), "end": s + m.end()}
                        if kind == "court_decisions":
                            if g["citation"].search(sent) or not g["decided"].search(sent):
                                continue
                            if g["other"].search(sent[max(0, m.start() - 15):m.end()]) or \
                                    g["criminal"].search(sent[max(0, m.start() - 60):m.end() + 40]):
                                continue
                            ev["court"], by_number = court_of(sent, m, date_end, g)
                            if ev["court"] == "unnamed" and not g["case"].search(sent):
                                continue
                            if embassy and g["sponsor_status"].search(sent):
                                continue    # a status granted in an entry procedure is the sponsor's
                            # another proceeding of the same person on this decision's date ("W286 2164690-3/7E"
                            # beside this "2164690-2") is another decision, not this one; a relative's case of the
                            # same date (another person's number) stays this decision's date
                            other_number = bool(by_number and by_number[0] == "BVwG"
                                                and (mm := re.search(r"\d{7}-\d+", by_number[1]))
                                                and mm[0] not in numbers
                                                and mm[0].split("-")[0] in {n.split("-")[0] for n in numbers})
                        elif kind == "hearings":
                            ev["court"] = first_match(g["courts"], sent) or "unnamed"
                        for d in dates_in(dg[0], s + dg[1], rules):
                            x = {**ev, **d}
                            if kind == "court_decisions":
                                x["kind"] = "this_decision" if d["date"] in own and not other_number else "earlier"
                                era = appeal_court(d["date"], g["appeal_court_by_date"])
                                if x["court"] == "unnamed" and era:
                                    x["court"], x["court_inferred"] = era, True
                            if kind == "hearings":
                                latest = max(challenged, default=None)
                                x["kind"] = (None if not d["date"] else "this_proceeding"
                                             if latest is None or d["date"] >= latest else "earlier")
                            events.append(x)
    return unique_events(events)


def unique_events(events: list[dict]) -> list[dict]:
    """One event per type and date position, in text order."""
    seen, out = set(), []
    for ev in sorted(events, key=lambda x: (x["at"], x["type"], x["rule"])):
        if (ev["type"], ev["at"]) not in seen:
            seen.add((ev["type"], ev["at"]))
            out.append(ev)
    return out


# ----------------------------------------------------------------------------------------------------------- interviews

def interviews(doc: dict, rules: HistoryRules, spans: list[tuple[int, int, str]]) -> dict:
    """Per step: whether its sentences name an interpreter (yes), say there was none (no), or neither
    (not_mentioned); null where the step is not mentioned. Outside the procedural history a sentence counts only when
    it is about the appellant (not a country report). The interpreter may be named in the step's sentence or, where
    that names none, in the next sentence of the same paragraph that names no other step; in a sentence naming several
    steps it belongs to the step named nearest to it. With the interpreter's languages and up to 5 spans."""
    text = doc["full_text"]
    out = {name: None for name, _ in rules.steps}
    langs: dict[str, list[str]] = {name: [] for name, _ in rules.steps}
    marks: dict[str, list[dict]] = {name: [] for name, _ in rules.steps}
    rank = {"yes": 3, "no": 2, "not_mentioned": 1}
    first: dict[str, dict] = {}        # the first sentence naming each step

    def interp(sent: str) -> tuple[str, int] | None:
        if m := rules.no_interpreter.search(sent):
            return "no", m.start()
        if (m := rules.interpreter.search(sent)) and not rules.interpreter_skip.search(sent):
            return "yes", m.start()
        return None

    def steps_of(sent: str, part: str) -> dict[str, int]:
        found = {}
        if part not in HISTORY_PARTS and not rules.appellant_strict.search(sent):
            return found
        for name, rx in rules.steps:
            if name == "bfa_interview" and rules.not_bfa.search(sent):
                continue
            if name == "court_hearing" and rules.not_hearing.search(sent):
                continue
            if m := rx.search(sent):
                found[name] = m.start()
        return found

    for start, end, part in spans:
        sents = sentences(text, start, end, rules.abbreviations)
        for i, (s, e) in enumerate(sents):
            sent = text[s:e]
            if not rules.interview_trigger.search(sent):
                continue
            names = steps_of(sent, part)
            if not names:
                continue
            found, evidence = interp(sent), (s, e)
            if found is None and i + 1 < len(sents):
                ns, ne = sents[i + 1]
                nxt = text[ns:ne]
                if "\n" not in text[e:ns] and not steps_of(nxt, part) and interp(nxt):
                    found, evidence = (interp(nxt)[0], -1), (ns, ne)
            owner = None
            if found and found[1] >= 0 and len(names) > 1:
                owner = min(names, key=lambda n: abs(names[n] - found[1]))
            for name in names:
                value = (found[0] if found and (owner is None or owner == name) else "not_mentioned")
                if out[name] is None or rank[value] > rank[out[name]]:
                    out[name] = value
                if name not in first:
                    first[name] = {"start": s, "end": e, "part": part}
                if value != "yes":
                    continue
                add_languages(text[evidence[0]:evidence[1]], rules, langs[name])
                if len(marks[name]) < 5:
                    marks[name].append({"start": evidence[0], "end": evidence[1], "part": part})
    return {name: {"interpreter": out[name], "languages": langs[name], "spans": marks[name], "first": first.get(name)}
            for name, _ in rules.steps}


def add_languages(sentence: str, rules: HistoryRules, found: list[str]) -> None:
    for lm in rules.language.finditer(sentence):
        for g in ("name", "name2", "adjective", "adjective2"):
            if not lm[g]:
                continue
            for word in re.split(r"\s*(?:,|und|bzw\.?|sowie|oder|/)\s*", lm[g]):
                key = word.lower()
                if g.startswith("adjective"):
                    key = re.sub(r"ische$", "isch", key)
                if key in rules.languages and rules.languages[key] not in found:
                    found.append(rules.languages[key])


# -------------------------------------------------------------------------------------------------------------- hearing

def pronouncement(doc: dict, rules: HistoryRules, head: str, challenged: set[str]) -> tuple[str | None, str | None]:
    """The oral pronouncement's date and its source: stage 03, or else the head and the start of the reasoning read
    here without regard to case; a date before the latest challenged decision is an earlier decision's. A head that
    says "mündlich verkündet" without a date: the date of the one hearing the head names."""
    latest = max(challenged, default="")
    if doc.get("pronouncement_date") and doc["pronouncement_date"] >= latest:
        return doc["pronouncement_date"], "stage 03"
    reasoning = next((s for s in doc["sections"] if s["type"] == "reasoning" and s.get("level", 1) == 1), None)
    texts = [head] + ([doc["full_text"][reasoning["start"]:reasoning["start"] + rules.pronouncement_chars]]
                      if reasoning else [])
    for t in texts:
        for m in rules.pronouncement.finditer(t):
            dg = date_group(m)
            value, precision, _ = parse_date(dg[0]) if dg else (None, None, None)
            if value and precision == "day" and value >= latest:
                return value, "head (stage 04)"
    if rules.pronounced_without_date.search(head):
        days = {d["date"] for m in rules.groups["hearings"]["head"].finditer(head) if (dg := date_group(m))
                for d in dates_in(dg[0], 0, rules) if d["precision"] == "day"}
        if len(days) == 1 and min(days) >= latest:
            return days.pop(), "head hearing date (stage 04)"
    return None, None


def hearing(doc: dict, rules: HistoryRules, head: str, head_index: list[int], events: list[dict],
            pronounced: str | None) -> dict:
    g = rules.groups["hearings"]
    text = doc["full_text"]
    in_head, head_dates = False, []
    for m in g["head"].finditer(head):
        in_head = True
        if dg := date_group(m):
            for d in dates_in(dg[0], 0, rules):
                d["at"] = head_index[dg[1] + d["at"]]
                head_dates.append({"type": "hearing", "rule": "head", "part": "subject", "court": "BVwG",
                                   "start": head_index[m.start()], "end": head_index[m.end() - 1] + 1,
                                   "kind": "this_proceeding" if d["date"] else None, **d})
    not_held = None
    for start, end, part in ranges(doc, rules.no_hearing_parts) or ranges(doc, ("reasoning",)):
        for s, e in sentences(text, start, end, rules.abbreviations):
            sent = text[s:e]
            if "erhandlung" in sent and (m := g["not_held"].search(sent)):
                not_held = {"start": s + m.start(), "end": s + m.end(), "part": part}
                break
        if not_held:
            break
    current = [ev for ev in events if ev["type"] == "hearing" and ev.get("kind") == "this_proceeding"]
    if not_held and current and not in_head and not pronounced:
        decided = [ev["date"] for ev in events if ev["type"] == "court_decision" and ev.get("kind") == "earlier"
                   and ev["date"] and len(ev["date"]) == 10]
        if decided and all(ev["date"] and ev["date"] < max(decided) for ev in current):
            current = []        # the hearings of an earlier round, decided since
    if in_head or pronounced or current:
        value = "held"
    elif not_held:
        value = "not_held"
    else:
        value = "unknown"
    return {"value": value, "in_head": in_head, "head_events": head_dates, "not_held_span": not_held}


# --------------------------------------------------------------------------------------------------------------- gender

def text_of(doc: dict, types: tuple[str, ...]) -> str:
    return "\n".join(doc["full_text"][s:e] for s, e, _ in ranges(doc, types))


def decide(scores: dict, rules: HistoryRules, one_sided: bool = False) -> str | None:
    """A gender where the weights reach min_score with a share of min_share; with one_sided also where every cue points
    one way and they reach one_sided_min_score."""
    total = scores["female"] + scores["male"]
    lead = max(scores, key=lambda g: (scores[g], g))
    if one_sided and total >= rules.one_sided_min_score and scores[lead] == total:
        return lead
    if total < rules.min_score:
        return None
    return lead if scores[lead] / total >= rules.min_share else None


def score(text: str, rules: HistoryRules, only: set | None = None) -> tuple[dict, dict]:
    scores, cues = {"female": 0.0, "male": 0.0}, {}
    for cid, gender, weight, rx in rules.gender_cues:
        if only is not None and cid not in only:
            continue
        n = len(rx.findall(text))
        if n:
            cues[cid] = n
            scores[gender] += n * weight
    return scores, cues


def gender_of(text: str, rules: HistoryRules, one_sided: bool) -> tuple[str | None, dict, dict]:
    """The whole-word cues where they decide, else all cues; one-sided cues decide where allowed."""
    s, c = score(text, rules, rules.full_word_cues)
    if (v := decide(s, rules)) is not None:
        return v, s, c
    s, c = score(text, rules)
    return decide(s, rules, one_sided=one_sided), s, c


def one_appellant_gender(doc: dict, rules: HistoryRules) -> dict:
    value, scores, cues = gender_of(text_of(doc, rules.gender_sections), rules, True)
    basis = "cues"
    if value is None:       # too few cues in those parts: the cues of the whole reasoning
        wider = gender_of(text_of(doc, ("subject", "operative_part", "reasoning")), rules, False)
        if wider[0]:
            (value, scores, cues), basis = wider, "cues_reasoning"
    return {"value": value or "unknown", "basis": basis, "persons": [], "scores": scores, "cues": cues}


def appellant_gender(doc: dict, rules: HistoryRules, head: str, count: int | None) -> dict:
    if not count or count == 1:
        return one_appellant_gender(doc, rules)
    body = text_of(doc, rules.gender_sections)
    per: dict[int, dict] = {}

    def add(n: int, gender: str, weight: float) -> None:
        per.setdefault(n, {"female": 0.0, "male": 0.0})[gender] += weight

    numbered = list(rules.head_person.finditer(head))
    for m in numbered:
        if m["n3"]:                     # "1.) Herrn XXXX", "2.) Frau XXXX"
            add(int(m["n3"]), "female" if m["title3"] == "Frau" else "male", rules.min_score)
        else:
            n, art = (int(m["n"]), m["article"]) if m["n"] else (int(m["n2"]), m["article2"])
            add(n, "female" if art == "der" else "male", rules.min_score)
    if not numbered:                    # unnumbered persons of the head, in their order, block by block
        starts = [m.start() for m in rules.head_block.finditer(head)] or [0]
        starts[0], n = 0, 0
        for a, b in zip(starts, starts[1:] + [len(head)]):
            cut = HEAD_PERSONS_END.search(head, a, b)
            for m in rules.head_person_unnumbered.finditer(head, a, cut.start() if cut else b):
                n += 1
                add(n, "female" if m["article"] == "der" else "male", rules.min_score)
    for m in rules.ordinal.finditer(body):
        if m["ord"]:
            n, gender = rules.ordinals.get(m["ord"].lower()), "female" if m["f"] else "male"
        else:                           # "die fünfte Beschwerdeführerin", "des zweiten Beschwerdeführers"
            n = rules.ordinals.get(m["ord2"].lower().removesuffix("e"))
            gender = "female" if m["f2"] else "male" if m["m2"] or not m["n2end"] else None
        if n and gender:
            add(n, gender, rules.ordinal_weight)
    for _, gender, rx in rules.person_cues:
        for m in rx.finditer(body):
            add(int(m["n"]), gender, rules.person_cue_weight)
    persons = [{"person": n, "gender": decide(s, rules), "scores": s} for n, s in sorted(per.items()) if n <= count]
    known = {p["gender"] for p in persons if p["gender"]}
    complete = sum(1 for p in persons if p["gender"]) == count
    value = ("mixed" if len(known) == 2 else next(iter(known)) if known and complete else "unknown")
    return {"value": value, "basis": "persons", "persons": persons,
            "scores": {"female": float(sum(p["gender"] == "female" for p in persons)),
                       "male": float(sum(p["gender"] == "male" for p in persons))}, "cues": {}}


# -------------------------------------------------------------------------------------------------------------- new law

def before_day(d: str, day: str) -> bool:
    """Certainly before the day: "2026-05" and "2025" are, "2026-06" is not (it may be on or after it)."""
    return d < day[:len(d)]


def from_day(d: str, day: str) -> bool:
    """Certainly on or after the day: "2026-06-12" and "2026-07" are, "2026-06" is not."""
    return d >= day if len(d) >= len(day) else d > day[:len(d)]


def new_law(doc: dict, rules: HistoryRules, challenged: list[tuple[str, str | None]]) -> dict:
    """Whether the proceeding runs under the law in force since `new_law.from` (the EU asylum and migration pact), by
    the start of the proceeding. No where the decision or a challenged decision is dated before the day (an application
    precedes its decision); else yes where the latest application dated in the head or the procedural history (and not
    after the latest challenged decision) is dated on or after it, no where every such application is before it;
    unknown otherwise."""
    day = rules.new_law_from
    if (doc.get("decision_date") or "") < day:
        return {"value": "no", "basis": "decision_date", "date": doc.get("decision_date"), "span": None}
    old = [d for _, d in challenged if d and before_day(d, day)]
    if old:
        return {"value": "no", "basis": "challenged_decision", "date": old[0], "span": None}
    spans = ranges(doc, ("subject",)) + history_ranges(doc, rules)
    apps = [ev for ev in read_events(doc, rules, spans, ("applications",), set()) if ev.get("date")]
    latest_challenged = max((d for _, d in challenged if d), default=None)
    if latest_challenged:       # an application after the challenged decision began another proceeding
        apps = [ev for ev in apps if ev["date"][:len(latest_challenged)] <= latest_challenged]
    if apps:
        latest = max(apps, key=lambda ev: (ev["date"], -ev["start"]))
        span = {"start": latest["start"], "end": latest["end"], "part": latest["part"]}
        if from_day(latest["date"], day):
            return {"value": "yes", "basis": "application", "date": latest["date"], "span": span}
        if all(before_day(ev["date"], day) for ev in apps):
            return {"value": "no", "basis": "application", "date": latest["date"], "span": span}
    return {"value": "unknown", "basis": None, "date": None, "span": None}


# --------------------------------------------------------------------------------------------------------------- record

def distinct(values) -> list[str]:
    """The distinct dates, without a less precise one that a more precise one refines ("2015" next to "2015-08")."""
    found = {v for v in values if v}
    return sorted(v for v in found if not any(w != v and w.startswith(v) for w in found))


def read_history(doc: dict, rules: HistoryRules, head: str, head_index: list[int],
                 challenged: list[tuple[str, str | None]], appellant_count: int | None) -> dict:
    """The record's parts history, hearing, interpreter, appellant_gender, dates and new_law. `challenged`: the head's
    challenged decisions as (authority, date), in the order of the head."""
    chall = {d for _, d in challenged if d}
    embassy = bool(challenged) and challenged[0][0] == "embassy"
    spans = history_ranges(doc, rules)
    events = read_events(doc, rules, spans, ("court_decisions",), chall, embassy)
    hspans = ranges(doc, rules.hearing_parts) or spans
    events += read_events(doc, rules, hspans, ("hearings",), chall)
    pronounced, source = pronouncement(doc, rules, head, chall)
    hear = hearing(doc, rules, head, head_index, events, pronounced)
    events = unique_events(events + hear.pop("head_events"))
    iv = interviews(doc, rules, spans + [x for x in hspans if x not in spans])
    court_step = iv["court_hearing"]
    if hear["value"] == "not_held" and court_step["interpreter"] in (None, "not_mentioned"):
        court_step["interpreter"] = "no_hearing"
    elif hear["value"] == "held" and court_step["interpreter"] is None:
        court_step["interpreter"] = "not_mentioned"
    elif hear["value"] == "unknown" and court_step["interpreter"]:
        hear["value"] = "held"              # a hearing reported without a date; the sentence is the evidence
        hear["reported_span"] = court_step["first"]
    courts: dict[str, list[str]] = {}
    for ev in events:
        if ev["type"] == "court_decision" and ev["kind"] == "earlier":
            courts.setdefault(ev["court"], []).append(ev["date"])
    # a less precise date refined by a more precise one of the same court is dropped ("2020" of the VfGH stays beside
    # "2020-02-13" of the BVwG)
    court_list = sorted({(d, c) for c, ds in courts.items() for d in distinct(ds)})
    dates = {
        "decision": doc.get("decision_date"), "pronouncement": pronounced, "pronouncement_source": source,
        "court_decisions": sorted({d for d, _ in court_list}),
        "court_decision_list": [{"date": d, "court": c} for d, c in court_list],
        "anonymised": {t: sum(1 for ev in events if ev["type"] == t and ev["date"] is None) for t in EVENT_TYPES},
    }
    return {
        "history": {"parts_read": sorted({p for _, _, p in spans}), "events": events},
        "hearing": {"value": hear["value"], "in_head": hear["in_head"], "not_held_span": hear["not_held_span"],
                    "reported_span": hear.get("reported_span")},
        "interpreter": {name: {k: v[k] for k in ("interpreter", "languages", "spans")} for name, v in iv.items()},
        "appellant_gender": appellant_gender(doc, rules, head, appellant_count),
        "dates": dates,
        "new_law": new_law(doc, rules, challenged),
    }
