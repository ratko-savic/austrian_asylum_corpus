"""Outline labels ("II.1.", "A)", "a)", "(1)") read as tokens with their values.

    parse_label("II.1. Feststellungen")   -> ([("R", 2), ("#", 1)], "Feststellungen")
    parse_label("A) I. Die Beschwerde …") -> ([("A", 1), ("R", 1)], "Die Beschwerde …")
    parse_label("3.) Zur Person")         -> ([("#", 3)], "Zur Person")

Kinds: "R" roman numeral, "#" number, "A" capital letter, "a" small letter, "(#)" number in brackets. The values tell a
continuing outline ("1.3." after "1.2.") from a new one ("1." again).
"""

from __future__ import annotations

import re

# (signature, pattern), tried in this order at the current position of a label chain
LABEL_TOKENS = [
    ("R.", re.compile(r"[IVX]{1,4}\.(?=\s|\d|[A-ZÄÖÜ])")),     # I., II. (also directly before a capital or digit)
    ("#.", re.compile(r"\d{1,2}\.(?=\s|\d|[A-ZÄÖÜa-zäöü])")),  # 1., 2.1. read as two tokens
    ("#.", re.compile(r"\d{1,2}(?=\s+[A-ZÄÖÜ])")),               # 1 Verfahrensgang, 2.2 Feststellungen (no dot)
    ("#.", re.compile(r"\d{1,2}(?=\s+(?i:zu[mr]?\s+(?:spruchteil |spruchpunkt )?[ab]\.?\)))")),  # 3.2 zu A.)
    ("#.", re.compile(r"\d{1,2}\.\)(?=\s|$)")),                  # 2.) Feststellungen
    ("A)", re.compile(r"(?:[A-Z]\.?\)|[A-Z]\.)(?=\s|$)")),        # A), A.), B. (roman I., V., X. are read first)
    ("a)", re.compile(r"(?:[a-z]\.?\)|\([a-z]\)|[a-z]\.)(?=\s|$)")),  # a), (a), a.
    ("(#)", re.compile(r"\(\d{1,2}\)(?=\s|$)")),                 # (1)
]
ROMAN_BRACKET = ("R.", re.compile(r"[IVX]{1,4}\)(?=\s|$)"))     # "I)", "II)" in an operative part
BARE_ROMAN = ("R.", re.compile(r"[IVX]{1,4}\.(?=\s*$)"))        # "II." alone on its line
LETTER_DOT = ("A)", re.compile(r"[A-Z]\.(?=\d)"))               # "A.1.1. § 28 VwGVG lautet:"
MAX_TOKENS = 8      # "5.2.4.1.2. Innerstaatliche Fluchtalternative" has five

KIND = {"R.": "R", "#.": "#", "A)": "A", "a)": "a", "(#)": "(#)"}
ROMAN = {"I": 1, "V": 5, "X": 10}
Tokens = list[tuple[str, int]]


def roman_value(text: str) -> int:
    total, prev = 0, 0
    for ch in reversed(text):
        v = ROMAN[ch]
        total = total - v if v < prev else total + v
        prev = max(prev, v)
    return total


def roman_numeral(n: int) -> str:
    out = ""
    for v, s in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + s, n - v
    return out


def token_value(kind: str, text: str) -> int:
    core = re.sub(r"[^\dA-Za-z]", "", text)
    if kind == "R":
        return roman_value(core)
    if kind in ("#", "(#)"):
        return int(core)
    return ord(core.lower()) - 96


def parse_label(text: str, roman_brackets: bool = False, bare_roman: bool = False,
                letter_dot: bool = False) -> tuple[Tokens, str]:
    """(label tokens, title) of a paragraph. `roman_brackets`: "I)" is a roman numeral, not the letter I (operative
    parts "A) I) … II) …"); `bare_roman`: "II." alone on its line is a label; `letter_dot`: "A.1." is a letter and a
    number."""
    patterns = (([ROMAN_BRACKET] if roman_brackets else []) + ([BARE_ROMAN] if bare_roman else []) + LABEL_TOKENS
                + ([LETTER_DOT] if letter_dot else []))
    text, tokens, pos = text.strip(), [], 0
    for _ in range(MAX_TOKENS):
        match = next(((sig, m) for sig, rx in patterns if (m := rx.match(text[pos:]))), None)
        if not match:
            break
        sig, m = match
        kind = KIND[sig]
        tokens.append((kind, token_value(kind, m.group(0))))
        pos += m.end()
        pos += len(text[pos:]) - len(text[pos:].lstrip())
    return tokens, text[pos:].strip()


def kinds(tokens: Tokens) -> list[str]:
    return [k for k, _ in tokens]


def values(tokens: Tokens) -> list[int]:
    return [v for _, v in tokens]


def label_text(tokens: Tokens) -> str:
    """The label written in a normal form: "II.1.", "A) I.", "a)"."""
    out = []
    for kind, value in tokens:
        if kind == "R":
            out.append(roman_numeral(value) + ".")
        elif kind == "#":
            out.append(f"{value}.")
        elif kind == "(#)":
            out.append(f"({value})")
        else:
            out.append(chr(64 + value) + ")" if kind == "A" else chr(96 + value) + ")")
    return "".join(out) if all(k in ("R", "#") for k, _ in tokens) else " ".join(out)
