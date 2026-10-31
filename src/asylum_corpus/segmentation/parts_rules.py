"""The rules of the finer segmentation (config/segmentation/bvwg_parts.yaml), with their patterns compiled.

The fields follow the keys of the file: heading_like (which paragraphs read as headings), nesting, operative, scope,
quotes (the court's own text and quoted material), point_assessment and topics.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml


def patterns(items: list, flags: int = 0) -> list[re.Pattern]:
    return [re.compile(x["pattern"] if isinstance(x, dict) else x, flags) for x in items]


def substitutions(items: list) -> list[tuple[re.Pattern, str]]:
    return [(re.compile(x["pattern"]), x["replace"]) for x in items]


@dataclass(frozen=True)
class PartsRules:
    # heading_like
    max_chars: int
    max_words_sentence: int
    max_words_unlabelled: int
    max_chars_unlabelled: int
    exclude: list[re.Pattern]
    exclude_unlabelled: list[re.Pattern]
    exclude_title: list[re.Pattern]
    exclude_label_kinds: set[str]
    not_parts: list[re.Pattern]
    strip: list[re.Pattern]
    normalize: list[tuple[re.Pattern, str]]
    title_start_labelled: re.Pattern
    continuation: re.Pattern
    long_labelled: dict          # title, not, always (patterns), max_chars
    long_unlabelled: dict        # title (pattern), max_words, max_chars
    long_generic: dict           # not, not_start (patterns), following_min_chars, max_chars, max_words, ...
    long_forms: list[tuple[re.Pattern, int]]
    long_point: dict             # title, not (patterns), max_words, max_chars
    lower_zu: dict               # pattern, not, previous (patterns)
    zu_period: re.Pattern
    unlabelled_form: re.Pattern
    bare_labels: dict            # kinds, following_min_chars, not_in, not_before (pattern)
    table_run: dict              # min_run, max_chars, min_numbers, following_min_chars, not_member, numbers (patterns)
    # nesting
    repeat_kinds: tuple[str, ...]
    closer_kinds: set[str]
    beside_topics: set[str]
    takes_in: re.Pattern
    subtitle_after: set[str]
    # operative part
    operative: dict              # not_point, revision_b, letters_by_revision, revision_ruling (patterns),
                                 # normalize (substitutions), broken_line (title, previous patterns)
    # scope
    no_parts: set[str]
    procedural_in: set[str]
    procedural_title: re.Pattern
    # quoted material
    quotes: dict
    # point_assessment
    point: re.Pattern
    point_legal: re.Pattern
    numbering_authority: re.Pattern
    numbering_court: re.Pattern
    # topics: (topic, title pattern, group court / coi / any), in order
    topics: list[tuple[str, re.Pattern, str]]
    topic_drop: list[re.Pattern]
    topic_none_below: set[str]

    @property
    def group(self) -> dict[str, str]:
        return {t: g for t, _, g in self.topics}


def load_quotes(q: dict) -> dict:
    out = {k: re.compile(q[k], re.I) for k in ("court_title", "intro_title", "lead_in", "paragraph_intro", "part_intro",
                                               "main_title")}
    out.update({k: re.compile(q[k]) for k in ("end", "not_end")})
    out.update({k: set(q[k]) for k in ("intro_topics", "court_topics", "report_sections", "provision_sections",
                                       "quoted_topics")})
    return out


def load_parts_rules(path: Path) -> PartsRules:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    h, nest, op, scope, pa = (spec["heading_like"], spec["nesting"], spec["operative"], spec["scope"],
                              spec["point_assessment"])
    ll, lu, lg, lp, lz, bl, tr = (h["long_labelled"], h["long_unlabelled"], h["long_generic"], h["long_point"],
                                  h["lower_zu"], h["bare_labels"], h["table_run"])
    return PartsRules(
        max_chars=h["max_chars"], max_words_sentence=h["max_words_sentence"],
        max_words_unlabelled=h["max_words_unlabelled"], max_chars_unlabelled=h["max_chars_unlabelled"],
        exclude=patterns(h["exclude"]), exclude_unlabelled=patterns(h["exclude_unlabelled"]),
        exclude_title=patterns(h["exclude_title"]), exclude_label_kinds=set(h["exclude_label_kinds"]),
        not_parts=patterns(h["not_parts"], re.I), strip=patterns(h["strip"], re.I),
        normalize=substitutions(h["normalize"]),
        title_start_labelled=re.compile(h["title_start_labelled"]),
        continuation=re.compile(h["continuation"]["pattern"]),
        long_labelled={"title": re.compile(ll["title"], re.I), "not": re.compile(ll["not"], re.I),
                       "always": re.compile(ll["always"], re.I), "max_chars": int(ll["max_chars"])},
        long_unlabelled={"title": re.compile(lu["title"], re.I), "max_words": int(lu["max_words"]),
                         "max_chars": int(lu["max_chars"])},
        long_generic={**lg, "not": re.compile(lg["not"]), "not_start": re.compile(lg["not_start"])},
        long_forms=[(re.compile(x["title"]), int(x["max_chars"])) for x in h["long_forms"]],
        long_point={**lp, "title": re.compile(lp["title"], re.I), "not": re.compile(lp["not"], re.I)},
        lower_zu={k: re.compile(lz[k]) for k in ("pattern", "not", "previous")},
        zu_period=re.compile(h["zu_period"], re.I),
        unlabelled_form=re.compile(h["unlabelled_form"]),
        bare_labels={**bl, "not_in": set(bl["not_in"]), "not_before": re.compile(bl["not_before"])},
        table_run={**tr, "not_member": re.compile(tr["not_member"]), "numbers": re.compile(tr["numbers"])},
        repeat_kinds=tuple(nest["repeat_kinds"]), closer_kinds=set(nest["closer_kinds"]),
        beside_topics=set(nest["beside_topics"]), takes_in=re.compile(nest["takes_in"], re.I),
        subtitle_after=set(nest["subtitle_after"]),
        operative={**{k: re.compile(op[k]) for k in ("not_point", "revision_b", "letters_by_revision",
                                                     "revision_ruling")},
                   "normalize": substitutions(op["normalize"]),
                   "broken_line": {k: re.compile(op["broken_line"][k]) for k in ("title", "previous")}},
        no_parts=set(scope["no_parts"]), procedural_in=set(scope["procedural_in"]),
        procedural_title=re.compile(scope["procedural_title"], re.I),
        quotes=load_quotes(spec["quotes"]),
        point=re.compile(pa["point"], re.I), point_legal=re.compile(pa["legal"], re.I),
        numbering_authority=re.compile(pa["authority"], re.I), numbering_court=re.compile(pa["court"], re.I),
        topics=[(t["topic"], re.compile(t["title"], re.I), t.get("group", "any")) for t in spec["topics"]],
        topic_drop=patterns(spec["topic_drop"], re.I),
        topic_none_below=set(spec["topic_none_below"]))
