"""Sections of the document from the RIS fields, and the character offsets of sections and paragraphs.

RIS field (XML `ct` attribute) -> type: Betreff -> subject; Spruch -> operative_part; Begründung and the BVwG field
Text -> reasoning; anything else -> other. In a BVwG Spruch, every introductory paragraph and everything before the
first of them is subject, so that BVwG and VwGH sections are comparable. An introductory paragraph is
    - one ending in 'zu Recht erkannt:', 'zu Recht:', 'beschlossen:' or 'den Beschluss:', or the closing word on its
      own line ("BESCHLUSS:");
    - only where a Spruch has none of these: the newer form "Das Bundesverwaltungsgericht erkennt / beschließt durch
      ... über die Beschwerde ...:", which ends with a colon after the head, followed by the operative text (a colon
      inside the head, "... StA.:", is no end);
    - never a quoted one: a correction order quotes the corrected decision's head inside its operative part.
Where a Spruch has the intro sentence's start but no introductory paragraph, the subject runs to the paragraph before
the first operative sentence ("A)", "Die Beschwerde wird ..."). Before a later introductory paragraph (joined
decisions), its case-number lines, "IM NAMEN DER REPUBLIK!" and the intro sentence's earlier lines belong to that
subject, not to the operative part before. A bare label between two head paragraphs ("A)" / "I. beschlossen:")
belongs to the intro. A paragraph "BEGRÜNDUNG:" inside the Spruch field starts the reasoning. VwGH / VfGH without a
Spruch field: the operative sentence that RIS tags as Begründung (its first paragraph) or Betreff (its last) is the
operative part.

Paragraphs are joined by a single "\\n"; full_text[start:end] == text for every paragraph. A new section starts
whenever the type or the RIS field label changes; types may repeat (a document can contain several decisions).
"""

from __future__ import annotations

import re

SECTION_BY_FIELD = {"betreff": "subject", "spruch": "operative_part", "begruendung": "reasoning", "text": "reasoning"}
# The sentence that ends the introductory part of a BVwG Spruch
INTRO_RE = re.compile(r"(?:zu Recht(?: erkannt)?|beschlossen|den Beschluss(?: gefasst)?|erkannt)\s*:$")
# The closing word on a line of its own, in any case ("Das Bundesverwaltungsgericht fasst durch ... den" / "BESCHLUSS:")
INTRO_ALONE = re.compile(
    r"(?i)^\s*(?:den\s+)?(?:Beschluss(?:\s+gefasst)?|beschlossen|zu\s+Recht(?:\s+erkannt)?|erkannt)\s*:\s*$")
# The start of the introductory sentence, and its end inside a paragraph (with the operative text after it)
INTRO_START = re.compile(r"\bDas\s+Bundesverwaltungsgericht\s+(?:hat|erkennt|beschließt|beschliesst|fasst)\b")
INTRO_END_INSIDE = re.compile(r"(?:zu Recht(?: erkannt)?|beschlossen|den Beschluss(?: gefasst)?|erkannt)\s*:(?=\s*\S)")
QUOTED = re.compile(r'^\s*[„"»“‚\']')
OPERATIVE_START = re.compile(
    r"^\s*(?:[A-D]\s?\)|[IVX]{1,4}\.\s+(?:Die|Der|Dem|Den|Das|Gemäß|In)\b|(?:Die|Der|Den|Dem)\s+(?:\w+\s+){0,2}?"
    r"(?:Beschwerden?|Anträgen?|Antrag|Revision|Bescheid|Vorlageantrag|Säumnisbeschwerde|Wiedereinsetzungsantrag)\b|"
    r"Das\s+(?:Verfahren|Beschwerdeverfahren)\b|In\s+Erledigung\b|Gemäß\s+§|Spruchpunkt\b|"
    r"(?:Die|Das|Der)\s+(?:\w+\s+){0,3}?(?:Erkenntnis(?:ses)?|Beschluss(?:es)?|Ausfertigung|Spruch)\b[^.]{0,200}?\b"
    r"(?:wird|werden)\b)")
HEAD_LINE = re.compile(
    r"^\s*(?:(?:[WGLI]\s?\d{3}\s+\d{5,7}\s?-\s?\d{1,2}(?:\s?/\s?\d{1,3}[A-Z])?[,;]?\s*)+$|IM\s+NAMEN\s+DER\s+REPUBLIK"
    r"|BESCHLUSS\b|ERKENNTNIS\b|TEILERKENNTNIS\b|BERICHTIGUNGSBESCHLUSS\b|(?:GEKÜRZTE\s+)?AUSFERTIGUNG\b|"
    r"Gekürzte\s+Ausfertigung\b)")
BARE_LABEL = re.compile(r"^\s*(?:[A-D]\s?\)|[IVX]{1,4}\.|\d{1,2}\.|[A-D]\)\s*[IVX]{1,4}\.)\s*$")
REASONING_HEADING = re.compile(r"^\s*(?:BEGRÜNDUNG|Begründung|ENTSCHEIDUNGSGRÜNDE|Entscheidungsgründe)\s*:?\s*$")
# VwGH / VfGH: an operative sentence tagged as Begründung or Betreff
DISPOSITION = re.compile(
    r"^\s*(?:Gemäß\s+§\s*\d+[a-z]?\s+Abs\.?\s*\d+\s+\S+\s+)?(?:(?:Die|Der|Das|Dem|Den)\s+(?:[\w-]+\s+){0,6}?)?"
    r"(?:wird|werden)\s+(?:[\w-]+\s+){0,8}?[\w-]*?(?:stattgegeben|zurückgewiesen|abgewiesen|aufgehoben|eingestellt|"
    r"abgelehnt|zuerkannt|zurückgestellt|für\s+gegenstandslos\s+erklärt)\b[^.]{0,200}\.?\s*$")
DISPOSITION_MAX = 600


def intro_by_closing_words(p) -> bool:
    """An introductory paragraph by its closing words ("... zu Recht erkannt:", "BESCHLUSS:" on its own)."""
    if p.field != "spruch" or QUOTED.match(p.text):
        return False
    return bool(INTRO_RE.search(p.text) or INTRO_ALONE.match(p.text))


def intro_by_colon(p, nxt) -> bool:
    """An introductory paragraph by the intro sentence's start and a colon at the end, followed by the operative text
    (or a head line, or the end of the Spruch)."""
    if p.field != "spruch" or QUOTED.match(p.text) or not INTRO_START.search(p.text):
        return False
    if not p.text.rstrip().endswith(":"):
        return False
    return nxt is None or nxt.field != "spruch" or bool(OPERATIVE_START.match(nxt.text) or HEAD_LINE.match(nxt.text))


def split_intro(paragraphs: list, court: str, make) -> list:
    """BVwG: a Spruch paragraph holding the end of the introductory sentence and, after it, the operative text
    ("... zu Recht erkannt: A) Die Beschwerde wird ...") is split after the intro's end. `make(p, text, first)` copies
    a paragraph with another text (the margin number stays with the first part)."""
    if court != "BVwG":
        return paragraphs
    out = []
    for p in paragraphs:
        rest = p
        while rest.field == "spruch" and not QUOTED.match(rest.text):
            cut = None
            m = INTRO_END_INSIDE.search(rest.text)
            if m and OPERATIVE_START.match(rest.text[m.end():]):
                cut = m.end()
            elif start := INTRO_START.search(rest.text):
                x = next((x for x in re.finditer(r":\s+", rest.text)
                          if x.start() > start.start() and OPERATIVE_START.match(rest.text[x.end():])), None)
                cut = x.start() + 1 if x else None
            if cut is None:
                break
            out.append(make(rest, rest.text[:cut].rstrip(), True))
            rest = make(rest, rest.text[cut:].strip(), False)
        out.append(rest)
    return out


def bvwg_spruch_types(paragraphs: list, types: list[str]) -> None:
    """Subject and operative part inside the BVwG Spruch field (in place)."""
    spruch = [i for i, p in enumerate(paragraphs) if p.field == "spruch"]
    nxt = {i: (paragraphs[i + 1] if i + 1 < len(paragraphs) else None) for i in spruch}
    # the closing words decide where a Spruch has them; the newer form's colon only where none has them
    intros = [i for i in spruch if intro_by_closing_words(paragraphs[i])] \
        or [i for i in spruch if intro_by_colon(paragraphs[i], nxt[i])]
    if not intros:  # the intro's closing words missing: up to the first operative sentence
        start = next((i for i in spruch if INTRO_START.search(paragraphs[i].text)
                      and not QUOTED.match(paragraphs[i].text)), None)
        if start is not None:
            after = [i for i in spruch if i > start and OPERATIVE_START.match(paragraphs[i].text)]
            intros = [after[0] - 1 if after else start]
    if intros:
        state = "subject"
        for i in spruch:
            if i in intros:
                types[i], state = "subject", "operative_part"
            else:
                types[i] = state
        for i in intros[1:]:  # a later head: back to its intro's start, and its case-number lines
            start = next((j for j in range(i - 1, max(-1, i - 9), -1) if paragraphs[j].field == "spruch"
                          and INTRO_START.search(paragraphs[j].text) and not QUOTED.match(paragraphs[j].text)), None)
            between = range(start + 1, i) if start is not None else range(0)
            if start is not None and types[start] == "operative_part" and all(
                    types[j] == "operative_part" and not OPERATIVE_START.match(paragraphs[j].text)
                    and not paragraphs[j].text.rstrip().endswith(".") for j in between):
                for j in range(start, i):
                    types[j] = "subject"
                i = start
            j = i - 1
            while j >= 0 and paragraphs[j].field == "spruch" and types[j] == "operative_part" and \
                    HEAD_LINE.match(paragraphs[j].text) and not QUOTED.match(paragraphs[j].text):
                types[j] = "subject"
                j -= 1
    for i in spruch:  # a bare label between two head paragraphs ("A)" / "I. beschlossen:") is part of the intro
        if (types[i] == "operative_part" and BARE_LABEL.match(paragraphs[i].text) and i > 0
                and types[i - 1] == "subject" and i + 1 < len(types) and types[i + 1] == "subject"):
            types[i] = "subject"


def section_types(paragraphs: list, court: str) -> list[str]:
    """Section type per paragraph (paragraphs with `field` and `text`)."""
    types = [SECTION_BY_FIELD.get(p.field, "other") for p in paragraphs]
    if court == "BVwG":
        bvwg_spruch_types(paragraphs, types)
    in_reasoning = False  # "BEGRÜNDUNG:" inside the Spruch field
    for i, p in enumerate(paragraphs):
        if p.field != "spruch":
            in_reasoning = False
            continue
        if REASONING_HEADING.match(p.text):
            in_reasoning = True
        if in_reasoning:
            types[i] = "reasoning"
    if court in ("VwGH", "VfGH") and "operative_part" not in types:
        reasoning = next((i for i, p in enumerate(paragraphs) if p.field == "begruendung"), None)
        subject = [i for i, p in enumerate(paragraphs) if p.field == "betreff"]
        for i in ([reasoning] if reasoning is not None else []) + (subject[-1:] if subject else []):
            if len(paragraphs[i].text) <= DISPOSITION_MAX and DISPOSITION.match(paragraphs[i].text):
                types[i] = "operative_part"
                break
    return types


def layout(raw: list, types: list[str], document_id: str) -> tuple[list[dict], list[dict], str]:
    """Sections, paragraphs and full_text for paragraphs with `field`, `label`, `text`, `marginal_number`."""
    sections, paragraphs, pos = [], [], 0
    for i, (p, t) in enumerate(zip(raw, types), 1):
        source_label = p.label or p.field or ""
        if not sections or sections[-1]["type"] != t or sections[-1]["source_label"] != source_label:
            sections.append({"id": f"{document_id}#s:{len(sections) + 1:02d}", "type": t, "source_label": source_label,
                             "start": pos, "end": pos})
        paragraphs.append({"id": f"{document_id}#p:{i:04d}", "section_id": sections[-1]["id"],
                           "marginal_number": p.marginal_number, "start": pos, "end": pos + len(p.text),
                           "text": p.text})
        sections[-1]["end"] = pos + len(p.text)
        pos += len(p.text) + 1
    return sections, paragraphs, "\n".join(p["text"] for p in paragraphs)
