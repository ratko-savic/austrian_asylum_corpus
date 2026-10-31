"""Nesting of the fine sections by their outline labels, within one stretch of a coarse section.

The same rule serves the operative part and the reasoning, starting from the label of the coarse section:
    sibling     the same kinds, the same leading values, a larger last value ("1.3." after "1.2."; gaps allowed)
    child       the open label plus one more token whose value is the first ("1.1." under "1."), or a label of
                another kind starting at its first value ("a)" under "1.1.")
    restart     a label that continues nothing open and starts at its first value ("1." while "3." is open) is a
                child of the innermost open section; when the open sequence of that kind began as such a restart
                itself, the new one is its sibling, so that repeated lists stay at one depth
    unmatched   any other label is a child of the innermost open section, except where its numbers place it (tested in
                this order): beside the part before its unprinted parent number, under the part it extends by one
                number, beside the part it repeats with one number off by one (a misprint), beside an open unmatched
                section of the same shape
    continues_section  a label continuing the coarse section's own label ("2." inside "1. Feststellungen") stays inside
                that section as its child
    unlabelled  a heading without a label is a child of the innermost open labelled section; consecutive unlabelled
                headings are siblings
A label identical to an open one opens the next section at that level ("2.2." printed twice), and a label that the next
label of the same paragraph closes again ("2. B)": "B)" ends the "2.") opens no section. A section runs from its heading
to just before the next heading (or closer) at its level or higher in its stretch, else to the end of the stretch.
"""

from __future__ import annotations

import bisect
import heapq
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .labels import Tokens, kinds, values

if TYPE_CHECKING:
    from .court_text import CourtText

Placing = tuple[int, str, "Open | None"]    # (index of the parent in the stack, nesting, the run a restart began under)


@dataclass(eq=False)
class Open:
    tokens: Tokens | None   # label tokens ([] unlabelled; None for a stretch's coarse section without a label)
    level: int
    section: dict | None
    run_parent: Open | None = None   # for a sequence that restarted its numbering: the section it started under


def extends(tokens: Tokens, parent: Tokens | None) -> bool:
    """The label continues the parent's by one more number of the parent's own outline ("1.3.1." under "1.3.")."""
    return bool(parent) and len(tokens) == len(parent) + 1 and kinds(tokens)[:-1] == kinds(parent) \
        and values(tokens)[:-1] == values(parent)


def place(stack: list[Open], tokens: Tokens, operative: bool = False) -> Placing:
    """Where a new heading goes. In an operative part, an "A)" after an open "A)" at the top starts the next ruling, and
    a "B)" with no "A)" open goes to the top; in the reasoning, misprinted and quoted numberings are placed by their
    numbers (`unmatched` in the module docstring)."""
    while len(stack) > 1 and stack[-1].tokens == []:      # unlabelled headings are leaves of their run
        if not tokens:
            return len(stack) - 2, "unlabelled", None
        stack.pop()
    if not tokens:
        return len(stack) - 1, "unlabelled", None
    k, v = kinds(tokens), values(tokens)
    if operative and k == ["A"] and v == [1]:             # "A)" again at the top of an operative part: the next ruling
        top = next((i for i in range(1, len(stack)) if stack[i].tokens and kinds(stack[i].tokens) == ["A"]), None)
        if top is not None:
            return top - 1, "restart", None
    if operative and k == ["A"] and v == [2] and not any(e.tokens and kinds(e.tokens) == ["A"] for e in stack[1:]):
        return 0, "child", None                           # "B)" with no "A)" open (the subject took it): at the top
    for i in range(len(stack) - 1, -1, -1):
        e = stack[i].tokens
        if not e:
            continue
        ek, ev = kinds(e), values(e)
        if ek == k and ev[:-1] == v[:-1] and v[-1] > ev[-1]:
            if i == 0:                  # continues the coarse section's own label: stays inside it (a missed heading)
                return 0, "continues_section", None
            return i - 1, "sibling", stack[i].run_parent
        if len(k) == len(ek) + 1 and k[:-1] == ek and v[:-1] == ev and v[-1] == 1:
            return i, "child", None
    if not operative and len(v) >= 2 and v[-1] == 1:      # "2.3.1." after "2.2." when no "2.3." is printed
        for i in range(len(stack) - 1, 0, -1):
            e = stack[i].tokens
            if e and kinds(e) == k[:-1] and values(e)[:-1] == v[:-2] and values(e)[-1] == v[-2] - 1:
                return i - 1, "unmatched", stack[i].run_parent
    if v[-1] == 1:
        if stack[-1].tokens is None or kinds(stack[-1].tokens or []) != k:
            return len(stack) - 1, "child", None
        for i in range(len(stack) - 1, 0, -1):            # a restart of a restarted (quoted) sequence: its sibling
            if stack[i].tokens and kinds(stack[i].tokens) == k and stack[i].run_parent is not None:
                return stack.index(stack[i].run_parent), "restart", stack[i].run_parent
        return len(stack) - 1, "restart", stack[-1]
    if not operative:
        found = misprinted(stack, tokens)
        if found is not None:
            return found
        for i in range(len(stack) - 1, 0, -1):            # beside an open unmatched section of the same label kinds
            e = stack[i]
            if e.tokens and kinds(e.tokens) == k and e.section is not None and e.section.get("nesting") == "unmatched":
                return i - 1, "unmatched", e.run_parent
    return len(stack) - 1, "unmatched", None


def misprinted(stack: list[Open], tokens: Tokens) -> Placing | None:
    """A label that continues nothing open, placed by its numbers: under the open part it extends by one number although
    the numbers before it are no parts ("3.5." under "3." while "3.3." and "3.4." are numbered sentences), or beside the
    innermost labelled part whose label it repeats with one number off by one before its last and a last number not
    below it ("1.2.2." after "1.3.1.", "3.3." after "2.3."). Recorded as "unmatched"."""
    k, v = kinds(tokens), values(tokens)
    for i in range(len(stack) - 1, -1, -1):
        e = stack[i].tokens
        if e and len(k) == len(e) + 1 and k[:-1] == kinds(e) and v[:-1] == values(e):
            return i, "unmatched", None
    inner = next((i for i in range(len(stack) - 1, 0, -1) if stack[i].tokens), None)
    if inner is not None and len(v) >= 2 and kinds(stack[inner].tokens) == k:
        ev = values(stack[inner].tokens)
        off = [j for j in range(len(v) - 1) if v[j] != ev[j]]
        if len(off) == 1 and abs(v[off[0]] - ev[off[0]]) == 1 and v[-1] >= ev[-1]:
            return inner - 1, "unmatched", stack[inner].run_parent
    return None


# (paragraph, tokens, title, level, parent id, nesting) -> the section
NewSection = Callable[[dict, Tokens, str, int, str, str], dict]


def build(stretch: list[dict], heads: dict[int, tuple[Tokens, str]], own_tokens: Tokens | None, level: int,
          parent_id: str, new_section: NewSection, repeat_kinds: tuple[str, ...], *, operative: bool = False,
          closers: dict[int, Tokens] | None = None, tracker: CourtText | None = None,
          repeat_at: set[int] | None = None, top: frozenset[int] = frozenset()) -> list[dict]:
    """The sections for the headings `heads` (position -> (tokens, title)) of one stretch of paragraphs.

    `closers`: positions of numbered paragraphs that are no headings; one that continues an open labelled sequence ends
    the sections below it (one continuing the stretch's own label only as its next number and ending like a sentence).
    `tracker` (reasoning): decides in document order which headings are the court's and whether a closer counts.
    `repeat_kinds`, `repeat_at`: a label identical to an open one opens the next section at that level where its kinds
    are allowed (and only at these positions); else it opens nothing. `top`: positions placed at the top."""
    root = Open(own_tokens if own_tokens else None, level, None)
    stack, made, ends = [root], [], []      # ends: (position, level) of the closers
    closers = closers or {}
    for i, p in enumerate(stretch):
        if tracker is not None and i not in heads and not tracker.paragraph(i, p, closers.get(i), stack):
            continue                                                  # a numbered line of a quote ends nothing
        if tracker is not None and i in heads and not tracker.head(i, heads[i][0], heads[i][1], stack):
            continue                                                  # not the court's heading: no section
        if i in closers and i not in heads:
            tokens = closers[i]
            parent_index, nesting, _ = place(list(stack), tokens)
            # a list item with a gap ("11. …" in "1. Feststellungen"), or a line broken inside a sentence
            if nesting == "continues_section" and (
                    values(tokens)[-1] != values(root.tokens)[-1] + 1
                    or not re.search(r"[.:!?)\]“”\"»']$", p["text"].rstrip())):
                continue
            if nesting in ("sibling", "continues_section"):
                del stack[parent_index + 1:]
                ends.append((i, stack[-1].level + 1))
            continue
        if i not in heads:
            continue
        tokens, title = heads[i]
        same = next((j for j, e in enumerate(stack) if j and tokens and e.tokens == tokens), None)
        if same is not None:                                          # the same label again
            if not set(kinds(tokens)) <= set(repeat_kinds) or (repeat_at is not None and i not in repeat_at):
                continue                                              # ("1. A)" … "1. B)": it is still open)
            parent_index, nesting, run_parent = place(list(stack), tokens)
            if not (nesting == "sibling" and parent_index + 1 > same):   # no deeper sequence takes it as its sibling:
                parent_index, nesting, run_parent = same - 1, "sibling", stack[same].run_parent   # next at that level
        elif tracker is not None and tracker.parent is not None:      # placed by the court-text rules
            (parent_index, nesting), run_parent = tracker.parent, None
        elif i in top:
            parent_index, nesting, run_parent = 0, "child", None
        else:
            parent_index, nesting, run_parent = place(stack, tokens, operative)
        del stack[parent_index + 1:]
        parent = stack[-1]
        if made and stretch[made[-1]["_first"]] is p and parent.section is not made[-1]:
            made.pop()                      # "2. B)": "B)" closes the "2." of the same paragraph at once - no section
        sec = new_section(p, tokens, title, parent.level + 1, parent.section["id"] if parent.section else parent_id,
                          nesting)
        sec["_first"] = i
        made.append(sec)
        stack.append(Open(tokens, parent.level + 1, sec, run_parent))
        if tracker is not None:
            tracker.placed(i, stack)                                  # the new section is stack[-1]
    set_extents(stretch, made, ends)
    return made


def set_extents(stretch: list[dict], made: list[dict], ends: list[tuple[int, int]]) -> None:
    """Each section to just before the next heading or closer at the same or a higher level, else to the end of the
    stretch (the later headings and closers merged in document order, read up to the first that ends it)."""
    for j, sec in enumerate(made):
        end = len(stretch) - 1
        later = heapq.merge(((made[k]["_first"], made[k]["level"]) for k in range(j + 1, len(made))),
                            (ends[k] for k in range(bisect.bisect_right(ends, (sec["_first"], 10 ** 9)), len(ends))))
        for pos, lv in later:
            if lv <= sec["level"]:
                end = pos - 1
                while end > sec["_first"] and stretch[end] is stretch[pos]:   # not into the next heading's paragraph
                    end -= 1
                break
        sec["start"], sec["end"] = stretch[sec["_first"]]["start"], stretch[end]["end"]
    for sec in made:
        del sec["_first"]
