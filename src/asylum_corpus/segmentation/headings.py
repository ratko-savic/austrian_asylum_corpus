"""Heading rules for the reasoning of BVwG decisions (config/segmentation/bvwg_headings.yaml).

A heading is an ordinary paragraph: the RIS XML does not mark it. A paragraph is split into the signature of its
outline label and its title ("II.1. Feststellungen:" -> "R.#." and "Feststellungen"); the first rule whose pattern
matches the whole title and whose labels allow the signature gives the section type. Signatures: "R." roman numeral,
"#." number (also "2.)", "3)"), "A)" capital letter (also "B."), "a)" small letter (also "a."),
"(#)" number in brackets; "II.1." is "R.#.", "2.2 Feststellungen" is "#.#.", "A) 2." is "A) #.".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .labels import LABEL_TOKENS

MAX_HEADING_CHARS = 100
LEADING_DASH = re.compile(r"^\s*[-–—•]\s+")                  # "- 2. Beweiswürdigung:"
NUMBER_BRACKET = ("#.", re.compile(r"\d{1,2}\)(?=\s)"))     # "3) Rechtliche Beurteilung", read like "3.)"
MAX_LABEL_TOKENS = 4


def split_label(text: str) -> tuple[str, str]:
    """(label signature, title) of a candidate heading; trailing colons and periods leave the title."""
    text, signature, pos = text.strip(), "", 0
    for _ in range(MAX_LABEL_TOKENS):
        match = next(((sig, m) for sig, rx in LABEL_TOKENS + [NUMBER_BRACKET] if (m := rx.match(text[pos:]))), None)
        if not match:
            break
        sig, m = match
        signature += sig if not signature or signature.endswith(".") and sig in ("R.", "#.") else " " + sig
        pos += m.end()
        pos += len(text[pos:]) - len(text[pos:].lstrip())
    return signature, text[pos:].rstrip(" :.").strip()


@dataclass(frozen=True)
class HeadingRule:
    id: str
    type: str | None          # None: the title of the whole reasoning (no section of its own)
    title: re.Pattern
    labels: frozenset[str]    # allowed label signatures ('' = no label)
    fallback: bool = False    # counts only where the decision has no heading of this type from the other rules
    max_chars: int = MAX_HEADING_CHARS   # longer paragraphs never match
    prefix: bool = False      # matches the part of a paragraph before its first ": "


@dataclass
class HeadingRules:
    rules: list[HeadingRule]

    @property
    def max_chars(self) -> int:
        """The longest paragraph any rule can match (any length where a prefix rule exists)."""
        return 10 ** 9 if any(r.prefix for r in self.rules) else max(r.max_chars for r in self.rules)

    def matched(self, text: str, fallback: bool = False) -> tuple[HeadingRule, str] | None:
        """(rule, label signature) of the first regular (or fallback) rule that matches the paragraph; a leading dash
        is left out. Where none matches, a prefix rule may match the part before the first ": "."""
        text = LEADING_DASH.sub("", text, count=1)
        signature, title = split_label(text)
        rule = next((r for r in self.rules if not r.prefix and r.fallback == fallback and len(text) <= r.max_chars
                     and signature in r.labels and r.title.fullmatch(title)), None)
        if rule:
            return rule, signature
        cut = text.find(": ")
        if 0 < cut < MAX_HEADING_CHARS:
            signature, title = split_label(text[:cut + 1])
            rule = next((r for r in self.rules if r.prefix and r.fallback == fallback and signature in r.labels
                         and r.title.fullmatch(title)), None)
            if rule:
                return rule, signature
        return None


def load_heading_rules(path: Path) -> HeadingRules:
    """The rules of the file, in order; ids must be unique."""
    spec = yaml.safe_load(path.read_bytes())
    rules = []
    for r in spec["rules"]:
        labels = frozenset("" if lab in ("none", None) else lab for lab in r["labels"])
        rules.append(HeadingRule(r["id"], r.get("type"), re.compile(r["title"], re.I), labels, bool(r.get("fallback")),
                                 int(r.get("max_chars", MAX_HEADING_CHARS)), bool(r.get("prefix"))))
    ids = [r.id for r in rules]
    if len(ids) != len(set(ids)):
        raise ValueError("heading rule ids must be unique")
    return HeadingRules(rules)
