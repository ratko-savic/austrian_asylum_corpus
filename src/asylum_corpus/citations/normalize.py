"""The normalized form of a provision designation (`normalized` of a citation).

One spelling per subdivision, so that "§ 3 Absatz 1" and "§ 3 Abs 1" are both "§ 3 Abs. 1"; "§§" before a single
provision becomes "§"; "Abschnitt" is kept ("Art. 1 Abschnitt A Z 2 GFK").
"""

from __future__ import annotations

import re

SUBDIVISIONS = [  # (pattern, normalized word), in this order; the "\b" after an abbreviation keeps "Abschnitt"
    (re.compile(r"\b(?:Absatz|Abs\b\.?)\s?"), "Abs. "),
    (re.compile(r"\b(?:Unterabsatz|Unterabs\b\.?|UAbs\b\.?)\s?"), "UAbs. "),
    (re.compile(r"\b(?:Ziffer|Z\b\.?)\s?(?=\d|[A-Z]\b)"), "Z "),
    (re.compile(r"\b(?:litera|lit\b\.?)\s?"), "lit. "),
]


def designation(text: str) -> str:
    """'§§ 3 Absatz 1 Ziffer 2' -> '§ 3 Abs. 1 Z 2'; 'Artikel 8' -> 'Art. 8'."""
    text = re.sub(r"\s+", " ", text).strip(" ,")
    text = re.sub(r"§§?\s?", "§ ", text)
    text = re.sub(r"\bArt(?:ikel|\.)?\s?(?=\d|[IVX])", "Art. ", text)
    for rx, word in SUBDIVISIONS:
        text = rx.sub(word, text)
    return re.sub(r"\s+", " ", text).strip()
