"""Which paragraphs of the reasoning read as headings (heading_like in bvwg_parts.yaml), and which numbered paragraphs
that are no headings end the parts before them (closers).

A paragraph is heading-like when it is short, its title (the text after the label) begins like a heading and does not
end like a sentence, and none of the exclusions applies; longer headings are allowed in the court's own forms ("Zu …",
"Auszug aus …", a heading on a point). Also a label alone before its text ("II.") and the court's numbered
introduction of the country reports are headings. Whether a heading is the court's own or part of quoted material is
decided later (court_text.py).
"""

from __future__ import annotations

import re

from .labels import Tokens, kinds, parse_label
from .parts_rules import PartsRules
from .titles import point_of

TITLE_START = re.compile(r"[A-ZÄÖÜ„\"“(]")
CLOSING_QUOTES = "“”\"»'"
SENTENCE_END = (".", ":", ";", "!", "?", ")")
Head = tuple[Tokens, str]       # (label tokens, title)


def heading_like(text: str, rules: PartsRules, previous: str | None = None,
                 following: str | None = None) -> Head | None:
    """(label tokens, title) if the paragraph reads as a heading, else None. `previous`, `following`: the texts of the
    paragraphs before and after it."""
    text = text.strip()
    for rx in rules.strip:
        text = rx.sub("", text).strip()
    for rx, replace in rules.normalize:
        text = rx.sub(replace, text)
    if not text:
        return None
    lz = rules.lower_zu                  # the court's "zur Person …" in lower case, after a finished line
    if lz["pattern"].match(text) and not lz["not"].match(text) and (
            previous is None or lz["previous"].search(previous.strip())):
        text = text[0].upper() + text[1:]
    tokens, title = parse_label(text, letter_dot=True)
    if not tokens and not title.rstrip(CLOSING_QUOTES).endswith(".") and any(
            rx.match(title) and len(title) <= n for rx, n in rules.long_forms):
        if any(rx.search(text) for rx in rules.exclude):
            return None
        return tokens, title             # "Auszug aus … :", "Ad A) …": the court's forms, longer
    generic = rules.long_generic
    plain = bool(title and not generic["not"].search(title) and not generic["not_start"].match(title)
                 and not title.rstrip(CLOSING_QUOTES).endswith(".")
                 and following is not None and len(following.strip()) >= generic["following_min_chars"])
    lp = rules.long_point                # an unlabelled point heading may be longer, with or without ":"
    point_long = bool(not tokens and title and len(title) <= lp["max_chars"] and len(title.split()) <= lp["max_words"]
                      and lp["title"].match(title) and not lp["not"].search(title) and point_of(title, rules))
    if len(text) > rules.max_chars and not point_long:
        long = rules.long_labelled
        if not (tokens and len(text) <= long["max_chars"] and title
                and (long["always"].search(title)
                     or long["title"].search(title) and not long["not"].search(title)
                     or plain and len(text) <= generic["max_chars"])):
            return None
    if any(k in rules.exclude_label_kinds for k, _ in tokens):
        return None
    start = rules.title_start_labelled if tokens else TITLE_START
    if not title or not start.match(title) or len(re.findall(r"[A-Za-zÄÖÜäöüß]", title)) < 3:
        return None
    if any(rx.search(text) for rx in rules.exclude) or any(rx.search(title) for rx in rules.exclude_title):
        return None
    words = len(title.split())
    end = title.rstrip(CLOSING_QUOTES)
    zu_period = bool(re.match(r"(?i)zu[mr]?\s", title) and not rules.zu_period.search(title))
    if end.endswith((",", ";", "?")) or (end.endswith(".") and words > rules.max_words_sentence and not zu_period):
        return None
    if any(rx.fullmatch(title.rstrip(" :.")) for rx in rules.not_parts):
        return None
    if not tokens:
        long = rules.long_unlabelled
        longer = bool((long["title"].search(title) or zu_period) and words <= long["max_words"]
                      and len(title) <= long["max_chars"]) \
            or bool(plain and words <= generic["max_words"] and len(title) <= generic["max_chars_unlabelled"]) \
            or point_long
        period = title.endswith(".") and not (words <= rules.max_words_sentence and point_of(title, rules)) \
            and not zu_period
        if period or (not longer and (words > rules.max_words_unlabelled or len(title) > rules.max_chars_unlabelled)):
            return None
        if any(rx.search(title) for rx in rules.exclude_unlabelled):
            return None
        if previous is not None and rules.continuation.search(previous.strip()):
            return None
    return tokens, title


def bare_label(text: str, rules: PartsRules, following: str) -> Head | None:
    """A paragraph that is only a label of the allowed kinds ("I.)", "II.", "A)"), followed by a paragraph long enough
    to be its text."""
    spec = rules.bare_labels
    text = text.strip()
    for rx, replace in rules.normalize:
        text = rx.sub(replace, text).strip()
    tokens, title = parse_label(text, bare_roman=True)
    if tokens and not title and set(kinds(tokens)) <= set(spec["kinds"]) \
            and len(following.strip()) >= spec["following_min_chars"] \
            and not spec["not_before"].match(following.strip()):
        return tokens, ""
    return None


def intro_part(text: str, rules: PartsRules) -> Head | None:
    """The court's numbered introduction of the country reports, a part up to long_labelled.max_chars characters
    ("1.3. Unter Bezugnahme auf das Länderinformationsblatt … werden folgende Feststellungen zur Lage in Syrien
    getroffen:")."""
    t = text.strip()
    for rx, replace in rules.normalize:
        t = rx.sub(replace, t)
    tokens, title = parse_label(t, letter_dot=True)
    if tokens and title and TITLE_START.match(title) and rules.quotes["part_intro"].search(title) \
            and len(t) <= rules.long_labelled["max_chars"]:
        return tokens, title
    return None


def dup_key(text: str) -> str:
    """A heading compared with the heading before it (printed twice): letters and digits only, without "Zu",
    "Spruchteil", "Spruchpunkt" ("Zu A)" and "Zu Spruchteil A):" are the same heading)."""
    t = re.sub(r"(?i)\b(?:zu[mr]?|spruchteil|spruchpunkt)\b", "", text)
    return re.sub(r"[\W_]+", "", t).lower()


def table_cells(run: list[dict], spec: dict) -> set[int]:
    """Positions of the paragraphs inside a run of short lines of which enough hold a number in their title: the cells
    of a table without separators, no headings - except the run's last line where a long paragraph follows."""
    short = [len(p["text"].strip()) <= spec["max_chars"] and not re.match(r"\s*[-–•*]", p["text"])
             and not p["text"].strip().rstrip(CLOSING_QUOTES).endswith((".", ":"))
             and not spec["not_member"].search(p["text"]) for p in run]
    out, start = set(), None
    for i, ok in enumerate(short + [False]):
        if ok and start is None:
            start = i
        elif not ok and start is not None:
            if i - start >= spec["min_run"] and sum(1 for p in run[start:i]
                                                    if spec["numbers"].search(parse_label(p["text"])[1])) \
                    >= spec["min_numbers"]:
                last = i - 1 if i < len(run) and len(run[i]["text"].strip()) >= spec["following_min_chars"] else i
                out.update(range(start, last))
            start = None
    return out


def closer_label(run: list[dict], i: int, heads: dict, heading_ids: set[str], rules: PartsRules) -> Tokens | None:
    """The label of a numbered paragraph that is no heading and may end the parts below it ("3.1. Die Beschwerde ist
    zulässig." before "3.2."): a title beginning with a capital, not an item of a list ("7. Verstöße …,"), not after a
    line broken inside a sentence, no source line, no question."""
    p = run[i]
    tokens, title = parse_label(p["text"], letter_dot=True)
    if not (tokens and title and TITLE_START.match(title) and set(kinds(tokens)) <= rules.closer_kinds):
        return None
    end = title.rstrip(CLOSING_QUOTES + " ")
    if re.search(r"(?:[,;]|\s(?:und|oder|sowie))$", end):
        return None
    broken = i and i - 1 not in heads and run[i - 1]["id"] not in heading_ids \
        and re.search(r"[a-zäöüß,]$", run[i - 1]["text"].rstrip())
    source = len(p["text"]) <= 200 and re.search(r"https?://|www\.|\bZugriff\b", p["text"])
    if broken or source or end.endswith("?"):
        return None
    return tokens


def join_broken_headings(run: list[dict], heads: dict[int, Head], closers: dict[int, Tokens],
                         rules: PartsRules) -> None:
    """A labelled heading broken at a line before its point is one heading: the next line's title ("… gemäß § 57" |
    "Asylgesetz 2005 (Spruchpunkt III …):"), or a point wholly in brackets on the next line ("(Spruchpunkt VI des
    angefochtenen Bescheides):"), is joined to it."""
    for i in sorted(heads):
        prev = heads.get(i - 1)
        if (i - 1 >= 0 and prev and prev[0] and not heads[i][0] and point_of(heads[i][1], rules)
                and not rules.unlabelled_form.match(heads[i][1])
                and not prev[1].rstrip(CLOSING_QUOTES).endswith(SENTENCE_END)):
            heads[i - 1] = (prev[0], f"{prev[1]} {heads[i][1]}")
            del heads[i]
    for i in sorted(heads):
        tokens, title = heads[i]
        nxt = run[i + 1]["text"].strip() if i + 1 < len(run) else ""
        if tokens and i + 1 not in heads and re.fullmatch(r"\(.*\)\s*:?", nxt) and point_of(nxt, rules) \
                and not title.rstrip(CLOSING_QUOTES).endswith(SENTENCE_END):
            heads[i] = (tokens, f"{title} {nxt}")
            closers.pop(i + 1, None)


def reasoning_heads(run: list[dict], section: dict, rules: PartsRules, heading_ids: set[str],
                    text_of: dict[str, str]) -> tuple[dict[int, Head], dict[int, Tokens]]:
    """The heading-like paragraphs of one stretch of a coarse section (position -> (tokens, title)) and its closers
    (position -> tokens). The headings of coarse sections are left out; so is a heading printed twice, a table cell, and
    the subtitle right after a bare "Zu B)" heading of the coarse section."""
    heads: dict[int, Head] = {}
    closers: dict[int, Tokens] = {}
    cells = table_cells(run, rules.table_run)
    subtitle = None
    if section["type"] in rules.subtitle_after and section.get("heading_paragraph_id"):
        if len(parse_label(text_of[section["heading_paragraph_id"]])[1].split()) <= 3:
            subtitle = next((i for i, p in enumerate(run) if p["id"] == section["heading_paragraph_id"]), None)
    for i, p in enumerate(run):
        if p["id"] in heading_ids:
            continue
        previous = run[i - 1]["text"] if i else None
        following = run[i + 1]["text"] if i + 1 < len(run) else None
        h = heading_like(p["text"], rules, previous, following)
        if h is None and section["type"] not in rules.bare_labels["not_in"]:
            h = bare_label(p["text"], rules, following or "")
        if h is None and section["type"] in rules.quotes["report_sections"]:
            h = intro_part(p["text"], rules)
        if h and i and (run[i - 1]["id"] in heading_ids or i - 1 in heads) \
                and dup_key(run[i - 1]["text"]) == dup_key(p["text"]):
            continue                                       # the heading before it, printed twice
        if h and not h[0] and (i in cells or (subtitle is not None and i == subtitle + 1
                                              and not point_of(h[1], rules) and not re.match(r"(?i)zu[mr]?\b", h[1]))):
            h = None
        if h:
            heads[i] = h
        elif (tokens := closer_label(run, i, heads, heading_ids, rules)) is not None:
            closers[i] = tokens
    join_broken_headings(run, heads, closers, rules)
    return heads, closers
