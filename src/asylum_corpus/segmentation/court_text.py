"""The court's own text and quoted material in one stretch of the reasoning, decided in document order.

Only the court's headings become parts; quoted country reports, provisions and other quoted text stay unsegmented
inside the court's part they lie in. `nesting.build` asks, for each paragraph in order, `paragraph` (may a numbered
line end sections?) or `head` (is this heading the court's?), and tells `placed` where an accepted heading went.

A quote begins with a heading or paragraph opening a quotation mark, a quoted provision's title, a report topic in the
sections where reports are quoted, or after an introduction (a heading or sentence such as "Zur Situation in Afghanistan
wird festgestellt:", "… lautet:"). Inside a quote a heading is the court's when its label continues the court's open
outline, when it reads as the court's ("Zu …", a point) or has a court topic; a fitting label below the part that
introduced the quote is the court's number on a chapter of the quote, which goes on. A quote begun with a quotation
mark ends when its marks are closed; any quote ends at the court's next numbered paragraph where its place in the
court's outline is known, and one not begun with a mark also before a paragraph that speaks of the parties or the case.

Also decided here: no parts in the procedural history (except the court's next main part that the heading rules missed),
none below a court part on the procedure, no list items, and the places of point and ruling headings ("Zu Spruchpunkt
I.", "Zu A)") relative to the numbered parts around them.
"""

from __future__ import annotations

import re

from .labels import Tokens, kinds, parse_label, values
from .nesting import Open, extends, place
from .parts_rules import PartsRules
from .titles import point_of, point_set, topic_of

OPEN_MARKS, CLOSE_MARKS = "„»", "“”«"
MARK = re.compile(r"\s*[„\"“»]")


def ending(text: str) -> str:
    return text.rstrip("“”\"»') ")[-1:]


def depth(text: str) -> int:
    """Opening minus closing quotation marks of a paragraph (a straight mark opens where the count is odd)."""
    odd = text.count('"') % 2 == 1
    return (sum(text.count(c) for c in OPEN_MARKS) - sum(text.count(c) for c in CLOSE_MARKS)
            + (1 if odd and MARK.match(text) else -1 if odd else 0))


def ends_quote(text: str) -> bool:
    """The paragraph ends with a quotation mark that closes a quote opened before it."""
    t = text.rstrip()
    last = t.rstrip(".;,:) ").rstrip()
    if not last or last[-1] not in CLOSE_MARKS + '"':
        return False
    opens, closes = sum(t.count(c) for c in OPEN_MARKS), sum(t.count(c) for c in CLOSE_MARKS)
    return closes > opens or (last[-1] == '"' and t.count('"') % 2 == 1)


def quoted_whole(raw: str) -> bool:
    """A heading that opens and closes its quotation marks: a quote of this paragraph only."""
    last = raw.rstrip().rstrip(".;,:) ").rstrip()
    if not last or last[-1] not in CLOSE_MARKS + '"':
        return False
    if last[-1] == '"':
        return raw.count('"') % 2 == 0
    return sum(raw.count(c) for c in OPEN_MARKS) == sum(raw.count(c) for c in CLOSE_MARKS)


def previous_label(tokens: Tokens) -> tuple:
    """The label before this one in its sequence ("1.3." -> "1.2.")."""
    return tuple(tokens[:-1]) + ((tokens[-1][0], tokens[-1][1] - 1),)


class CourtText:
    """The state of one stretch while its paragraphs are read in order: the open quote and the court's labels."""

    # The anchor of a quote that an unlabelled sentence or heading introduced: only a heading of the court's form ends
    # it, not a label of its outline.
    UNLABELLED = Open([], -1, None)

    def __init__(self, run: list[dict], rules: PartsRules, section_type: str):
        self.run, self.rules, self.q = run, rules, rules.quotes
        self.group = rules.group
        self.reports = section_type in self.q["report_sections"]     # where country reports are quoted
        self.legal = section_type in self.q["provision_sections"]    # where provisions are quoted
        self.closed = section_type in rules.no_parts                 # the procedural history
        self.procedural = rules.procedural_title if section_type in rules.procedural_in else None
        # the open quote: first position, anchor (the court's part it lies in), marked (begun with a quotation mark),
        # depth, prefix, its own labels
        self.region: dict | None = None
        self.blocked: list[Open] = []        # court parts on the procedure: no parts below them
        self.chapters: dict[Open, Open] = {}  # a part numbering a quote's chapter -> the part that introduced the quote
        self.used: set[tuple] = set()        # the labels of the court's parts so far in the stretch
        self.seen: set[tuple] = set()        # and of its numbered paragraphs (parts and sentences)
        self.printed_out: set[tuple] = set()  # the labels of its parts and numbered sentences outside quotes
        self.items: set[tuple] = set()       # the labels of list items so far (and of the numbered lines after them)
        self.last: tuple | None = None       # the label of the last of the court's numbered paragraphs
        self.intro = False                   # set by head for placed: the heading introduces a quote
        self.chapter: Open | None = None     # set by head for placed: the heading numbers a chapter of this quote
        self.parent: tuple | None = None     # set by head for build: (parent index, nesting) where the heading goes
        self.cur: int | None = None          # the position of the heading being decided
        self.head_at: int | None = None      # the position of the last accepted heading
        self.last_open: Open | None = None   # and its open section
        self.beside: tuple | None = None     # (labelled part, its parent) that an unlabelled heading went beside

    # ---------------------------------------------------------------------------------------------- quotes

    def anchor(self, stack: list[Open]) -> Open:
        """The court's open part a quote begun now lies in: stack[-1], or, under an unlabelled introduction ("Auszug aus
        dem Länderinformationsblatt …:"), the numbered part above it (the quote's chapters are numbered below that)."""
        top = stack[-1]
        if top.tokens == [] and top.section is not None \
                and self.q["intro_title"].search(top.section.get("title") or ""):
            return next((e for e in reversed(stack[1:-1]) if e.tokens), top)
        return top

    def open(self, i: int, anchor: Open, marked: bool = False, prefix: tuple | None = None) -> None:
        self.region = {"first": i, "anchor": anchor, "marked": marked, "depth": 0, "prefix": prefix, "labels": set()}

    def close(self) -> None:
        self.region = None

    def fits(self, stack: list[Open], tokens: Tokens, probe: tuple) -> bool:
        """The label continues the court's open outline: the next number of an open part, or one more number of the
        innermost one. A label the court used already in the stretch, one going back below a number it printed, a new
        sequence ("1." under "1.3."), one of another kind or continuing nothing open: a quote's own numbering."""
        if self.cur is not None and self.head_at == self.cur - 1 and tuple(tokens) == self.last:
            return True                       # the number of the heading right before it printed again
        if tuple(tokens) in self.used or any(e.tokens == tokens for e in stack[1:]):
            return False
        if self.printed(tokens):
            return False
        idx, nesting, _ = probe
        if nesting == "sibling" or (nesting == "child" and extends(tokens, stack[idx].tokens)):
            return True
        if len(tokens) < 2:
            return False
        prefix = tuple(tokens[:-1])           # the court's number before it, or its parent's, printed with a sentence
        before = previous_label(tokens)
        known = prefix == self.last or any(tuple(e.tokens or ()) == prefix for e in stack)
        if known and any(len(x) > len(before) and x[:len(before)] == before for x in self.seen):
            return True                       # the number before it was not printed, its sub-numbers were
        return known and (before in self.seen or tokens[-1][1] == 1)

    def printed(self, tokens: Tokens) -> bool:
        """The court printed a higher number with the same leading numbers already in the stretch (a part, or a
        numbered sentence outside a quote, not a list item): a label going back is not the court's."""
        return any(len(x) == len(tokens) and kinds(list(x)) == kinds(tokens)
                   and values(list(x))[:-1] == values(tokens)[:-1] and x[-1][1] > tokens[-1][1]
                   for x in self.printed_out - self.items)

    def paragraph(self, i: int, p: dict, closer: Tokens | None, stack: list[Open]) -> bool:
        """A paragraph that is no heading: whether its label may end sections (a closer). It may open a quote (a
        quotation mark left open, a lead-in "… lautet:", an introduction of the country reports) or end one."""
        text = p["text"]
        opens = MARK.match(text) and sum(text.count(c) for c in OPEN_MARKS) > sum(text.count(c) for c in CLOSE_MARKS)
        if closer and (self.region is None
                       or len(closer) >= 2 and self.fits(stack, closer, place(list(stack), closer))):
            self.seen.add(tuple(closer))
            self.last = tuple(closer)
            if self.region is None:
                self.printed_out.add(tuple(closer))
        if self.items:
            label = parse_label(text, letter_dot=True)[0]
            if label and previous_label(label) in self.items:
                self.items.add(tuple(label))  # the next item of a list, printed as a sentence
        if self.region is None:
            if self.closed:
                return True
            if opens:
                self.open(i, self.anchor(stack), marked=True)
                self.region["depth"] = depth(text)
            elif self.q["lead_in"].search(text.strip()):
                self.open(i + 1, self.anchor(stack))
            elif self.reports and self.q["paragraph_intro"].search(text.strip()):
                self.open(i + 1, self.anchor(stack) if closer else self.UNLABELLED,
                          prefix=tuple(closer) if closer else None)
            return True
        region = self.region
        if not region["marked"] and i == region["first"] and not opens and MARK.match(text) \
                and text.rstrip()[-1:] in CLOSE_MARKS + '"' and depth(text) == 0:
            self.close()                      # the quote is this one paragraph, quoted whole
            return False
        if opens and not region["marked"]:
            region["marked"], region["depth"] = True, 0
        known = bool(region["anchor"].tokens or region["prefix"])      # the quote's place in the court's outline
        if known and closer and len(closer) >= 2 and self.fits(stack, closer, place(list(stack), closer)):
            self.close()                      # the court's next numbered paragraph
            return True
        if region["marked"]:
            region["depth"] += depth(text)
            if region["depth"] <= 0 and i > region["first"]:
                self.close()                  # its quotation marks are closed again
            return False
        if ends_quote(text):
            self.close()
            return False
        if self.q["end"].search(text) and not self.q["not_end"].search(text):
            self.close()                      # the court speaks of the parties or the case again
            return True
        return False

    # ---------------------------------------------------------------------------------------------- headings

    def head(self, i: int, tokens: Tokens, title: str, stack: list[Open]) -> bool:
        """Whether a heading-like paragraph is a heading of the court (a section)."""
        return self.rejection(i, tokens, title, stack) is None

    def rejection(self, i: int, tokens: Tokens, title: str, stack: list[Open]) -> str | None:
        """Why the heading is no section of the court, or None where it is one."""
        q, rules = self.q, self.rules
        self.intro, self.chapter, self.parent = False, None, None
        self.cur = i
        raw = self.run[i]["text"]
        marked = bool(MARK.match(raw))
        probe = place(list(stack), tokens)
        if self.closed:                       # the procedural history: only the court's next main part
            if tokens and probe[1] == "continues_section" and not marked and (
                    i + 1 < len(self.run) or q["main_title"].match(title)):
                self.closed = False
            else:
                return "procedural history"
        if any(e in self.blocked for e in stack[1:probe[0] + 1]):
            return "below a part on the procedure"
        court_topic, other_topic = topic_of(title, rules, "court"), topic_of(title, rules, "coi")
        if self.group.get(court_topic) != "court":
            court_topic = None
        report = bool(other_topic and (self.group.get(other_topic) == "coi" or other_topic in q["quoted_topics"])
                      and not court_topic)
        point = point_of(title, rules, self.legal)
        courtish = bool(point or q["court_title"].match(title))
        fit = bool(tokens) and self.fits(stack, tokens, probe)
        if tokens and not courtish and self.list_item(i, tokens, title):
            return "list item"
        if self.region is not None:
            reason = self.inside_quote(tokens, raw, marked, point, courtish, court_topic, fit, probe, stack)
            if reason:
                return reason
        if self.reports and tokens and probe[1] == "continues_section" and not courtish \
                and not q["main_title"].match(title):
            return "outline"                  # continues the coarse section's number without a main heading: a report's
                                              # own outline
        lead_in = bool(q["lead_in"].search(title))
        back = bool(self.reports and tokens and not courtish and not point and not court_topic
                    and self.printed(tokens))     # a court topic keeps it the court's
        if self.region is None and (marked or not courtish and not lead_in and (
                other_topic == "quoted_provision" or (report and self.reports and not fit) or back)):
            # a report's number going back opens a quote that only a heading of the court's form ends
            unlabelled = back and not (marked or other_topic == "quoted_provision" or report)
            self.open(i, self.UNLABELLED if unlabelled else self.anchor(stack), marked)
            if marked:
                self.region["depth"] = depth(raw)
                if quoted_whole(raw):
                    self.close()
            return "quoted"                   # a quote begins with this heading
        if not tokens and point and not rules.unlabelled_form.match(title) and i:
            prev = self.run[i - 1]["text"].strip()
            if len(prev) <= 200 and parse_label(prev)[0] and not prev.rstrip("“”\"»'").endswith(
                    (".", ":", ";", "!", "?", ")")):
                return "broken line"          # the second line of a heading broken at a line
        if not tokens:
            if not (point or rules.unlabelled_form.match(title)):
                if self.region is None and (q["intro_title"].search(title) or self.reports and court_topic in q[
                        "intro_topics"]):
                    self.open(i + 1, self.UNLABELLED)   # no part, but it introduces a quote
                return "unlabelled form"      # unlabelled parts: only "Zu …", "Ad …", "Auszug aus …" and points
            self.place_unlabelled(i, title, point, court_topic, probe, stack)
        else:
            self.place_labelled(tokens, point, probe, stack)
        if self.parent is None and len(tokens) >= 2 and tokens[-1][1] == 1 and probe[1] == "unmatched":
            # beside the part before its unprinted parent ("2.2." | "2.3.1."): its next number goes beside too
            for j in range(len(stack) - 1, 0, -1):
                e = stack[j].tokens
                if e and kinds(e) == kinds(tokens)[:-1] and values(e)[:-1] == values(tokens)[:-2] \
                        and values(e)[-1] == values(tokens)[-2] - 1:
                    self.beside = (stack[j], stack[j - 1])
                    break
        if self.region is None and (lead_in or q["intro_title"].search(title)
                                    or (self.reports and court_topic in q["intro_topics"])):
            self.intro = True
        return None

    def list_item(self, i: int, tokens: Tokens, title: str) -> bool:
        """A labelled heading that is no "Zu …" or point heading is an item of a list when the next item (no heading)
        follows at once, a short item not ending like a sentence is before it, or the item before it was one."""
        run = self.run
        nxt = parse_label(run[i + 1]["text"], letter_dot=True)[0] if i + 1 < len(run) else []
        prev = run[i - 1]["text"].strip() if i else ""
        after = run[i + 1]["text"].strip() if i + 1 < len(run) else ""
        if not (nxt and (len(after) > 100 or ending(after) in (".", ";", ",") or ending(title) != ":")):
            nxt = []                          # unless both are headings with a colon
        before = parse_label(prev, letter_dot=True)[0] \
            if len(prev) <= 160 and ending(prev) not in (".", ":") and self.head_at != i - 1 else []
        if any(x and kinds(x) == kinds(tokens) and values(x)[:-1] == values(tokens)[:-1]
               and values(x)[-1] == values(tokens)[-1] + d for x, d in ((nxt, 1), (before, -1))) \
                or previous_label(tokens) in self.items:
            self.items.add(tuple(tokens))
            return True
        return False

    def inside_quote(self, tokens: Tokens, raw: str, marked: bool, point, courtish: bool,
                     court_topic: str | None, fit: bool, probe: tuple, stack: list[Open]) -> str | None:
        """A heading inside an open quote: "quoted", or None where the court's text resumes with it (the quote ends) or
        it numbers a chapter of the quote (the quote goes on)."""
        region = self.region
        anchor, prefix = region["anchor"], region["prefix"]
        under = (bool(tokens) and len(tokens) > len(prefix) and tuple(tokens[:len(prefix)]) == prefix if prefix
                 else anchor is not stack[0] and anchor in stack[1:probe[0] + 1])
        known = bool(anchor.tokens or prefix)
        seq = bool(tokens and not courtish and not point and not under and previous_label(tokens) in region["labels"])
        if tokens:                            # (the number after one of the quote's own headings continues the quote)
            region["labels"].add(tuple(tokens))
        if region["marked"]:
            if marked or not (point or known and fit and not under and not seq and (courtish or len(tokens) >= 2)):
                region["depth"] += depth(raw)
                if region["depth"] <= 0:
                    self.close()
                return "quoted"               # a heading of the quote, until its closing quotation mark
            self.close()                      # the court's point heading: the closing mark was missed
        elif marked:
            return "quoted"
        elif courtish or court_topic in self.q["court_topics"] or (fit and not under and known and not seq):
            self.close()                      # the court's text resumes with its next heading
        elif fit and under:
            self.chapter = anchor             # the court's number on a chapter of the quote: a part; the quote goes on
            region["labels"].discard(tuple(tokens))
        else:
            return "quoted"
        return None

    def place_unlabelled(self, i: int, title: str, point, court_topic: str | None, probe: tuple,
                         stack: list[Open]) -> None:
        """Where an unlabelled heading of the court's form goes, where the plain nesting would not put it."""
        intro_title = self.q["intro_title"]
        intro_form = bool(intro_title.search(title))
        top = stack[-1]
        if intro_form and top.tokens == [] and top.section is not None \
                and not intro_title.search(top.section.get("title") or ""):
            self.parent = (len(stack) - 1, "unlabelled")     # "Zum Herkunftsstaat:" | "Auszug aus …:" - its child
        elif not intro_form and len(stack) > 2 and top.tokens == [] and stack[-2].tokens == [] \
                and top.section is not None and intro_title.search(top.section.get("title") or ""):
            self.parent = (len(stack) - 3, "unlabelled")     # the next unlabelled heading goes beside that one
        # after the court's numbers on a quote's chapters: beside the part that introduced the quote
        if self.parent is None and not intro_form:
            for j in range(probe[0], 0, -1):
                anchor = self.chapters.get(stack[j])
                if anchor is not None and anchor in stack:
                    self.parent = (stack.index(anchor) - 1, "unlabelled")
                    break
        if (point or self.rules.takes_in.match(title)) and self.parent is None:
            k = next((j for j in range(len(stack) - 1, 0, -1) if stack[j].tokens == []), None)
            # a point or ruling heading after the labelled parts of an unlabelled one: beside it
            if k is not None and k < len(stack) - 1 and self.ruling(stack[k].section):
                self.parent = (k - 1, "unlabelled")
        if self.parent is None and (point or court_topic in self.rules.beside_topics):
            k = next((j for j in range(len(stack) - 1, 0, -1) if stack[j].tokens and stack[j].section is not None
                      and stack[j].section.get("type") == "point_assessment"), None)
            if k is not None and not (self.last_open is stack[k] and self.head_at == i - 1) \
                    and not any(e.tokens == [] for e in stack[k + 1:]) and (
                    court_topic in self.rules.beside_topics and not point
                    or point and not point_set(point[0]) <= point_set(stack[k].section["point"])):
                # beside the labelled point part before it: a heading on other points or on the whole decision
                # ("Zum Entfall der mündlichen Verhandlung"), unless right after it
                self.parent = (k - 1, "unlabelled")
                self.beside = (stack[k], stack[k - 1])

    def place_labelled(self, tokens: Tokens, point, probe: tuple, stack: list[Open]) -> None:
        """Where a labelled heading goes, where the plain nesting would not put it."""
        takes_in = self.rules.takes_in
        top = stack[-1]
        if self.beside is not None and self.beside[1] in stack and kinds(tokens) == kinds(self.beside[0].tokens) \
                and values(tokens)[:-1] == values(self.beside[0].tokens)[:-1] \
                and values(tokens)[-1] >= values(self.beside[0].tokens)[-1] \
                and (probe[1] in ("unmatched", "restart") or probe[0] < stack.index(self.beside[1])):
            # the next number after a part that an unlabelled heading went beside: beside them
            self.parent = (stack.index(self.beside[1]), "sibling")
        elif top.tokens == [] and self.ruling(top.section) and (
                point is None and (probe[1] in ("unmatched", "restart") or probe[1] == "child"
                                   and not extends(tokens, stack[probe[0]].tokens))
                or probe[1] != "continues_section" and probe[0] >= len(stack) - 2 and (
                    point is None or takes_in.match(top.section.get("title") or "")
                    or point_set(point[0]) <= point_set(top.section["point"]))):
            # a numbered part after a point or ruling heading: under it, unless it ends the part around the heading
            # or, after a point heading naming no ruling, is on other points
            self.parent = (len(stack) - 1, "child")
        elif point is not None and (k := next(
                (j for j in range(len(stack) - 2, 0, -1) if stack[j].tokens == [] and stack[j].section is not None
                 and stack[j].section.get("type") == "point_assessment"), None)) is not None \
                and probe[0] >= k and not point_set(point[0]) <= point_set(stack[k].section["point"]) \
                and not (takes_in.match(stack[k].section.get("title") or "") or probe[0] > k):
            # a numbered point heading after the numbered parts of an unlabelled one goes beside that one
            self.parent = (k - 1, probe[1])
        elif point is None and top.section is not None \
                and (top.section.get("type") == "point_assessment" or top.tokens and not top.section.get("title")) \
                and self.last_open is top and probe[1] in ("child", "unmatched") and probe[0] < len(stack) - 1 \
                and tokens[-1][1] == 1:
            # a numbered part beginning a sequence right after a point heading or a label alone goes under it
            self.parent = (len(stack) - 1, "child")

    def ruling(self, section: dict | None) -> bool:
        """An unlabelled part that is the court's heading for a point or for a ruling ("Zu A)")."""
        return section is not None and (section.get("type") == "point_assessment"
                                        or bool(self.rules.takes_in.match(section.get("title") or "")))

    def placed(self, i: int, stack: list[Open]) -> None:
        """The heading at i was accepted and is stack[-1]: a court part on the procedure blocks the parts below it; an
        introduction opens a quote after it."""
        self.head_at = i
        self.last_open = stack[-1]
        if stack[-1].tokens:
            label = tuple(stack[-1].tokens)
            self.used.add(label)
            self.seen.add(label)
            self.printed_out.add(label)
            self.last = label
        if self.procedural is not None and self.procedural.match(stack[-1].section.get("title") or ""):
            self.blocked.append(stack[-1])
        if self.chapter is not None:
            self.chapters[stack[-1]] = self.chapter
        if self.intro:
            self.open(i + 1, self.anchor(stack))
