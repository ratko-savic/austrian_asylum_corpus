"""The form of each part of a decision (judgment, order or both), as its introductory sentence states it.

"… hat durch … zu Recht erkannt:" before an operative part makes it a judgment (Erkenntnis), "… beschlossen:" an order
(Beschluss), "… erkannt und beschlossen:" both. Recorded as printed in sections[].decision_form; RIS's decision type
for the whole document is kept unchanged.
"""

from __future__ import annotations

import re

from ..parsing.sections import INTRO_RE

FORM_RES = [   # combined sentences first
    ("judgment_and_order", re.compile(r"(?:erkannt|zu Recht)\s+und\s+(?:den\s+)?beschl(?:ossen|uss(?: gefasst)?)\s*:$"
                                      r"|beschlossen\s+und\s+(?:zu Recht\s+)?erkannt\s*:$")),
    ("order", re.compile(r"(?:beschlossen|den Beschluss(?: gefasst)?)\s*:$")),
    ("judgment", re.compile(r"(?:zu Recht(?: erkannt)?|erkannt)\s*:$")),
]


def form_of(sentence: str) -> str | None:
    return next((form for form, rx in FORM_RES if rx.search(sentence)), None)


def decision_forms(sections: list[dict], paragraphs: list[dict]) -> dict[str, str | None]:
    """operative_part section id -> the form stated by the last introductory sentence of the subject section just
    before it; None where there is none."""
    texts: dict[str, list[str]] = {}
    for p in paragraphs:
        texts.setdefault(p["section_id"], []).append(p["text"])
    forms, previous = {}, None
    for s in sections:
        if s["type"] == "operative_part":
            intros = [t for t in texts.get(previous["id"], []) if INTRO_RE.search(t)] \
                if previous and previous["type"] == "subject" else []
            forms[s["id"]] = form_of(intros[-1]) if intros else None
        if s.get("level", 1) == 1:
            previous = s
    return forms
