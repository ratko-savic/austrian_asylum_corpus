"""Stage 05: heading rules, hierarchy rules and templates (coarse sections), and the fine sections."""

import pytest

from asylum_corpus.common import CONFIG
from asylum_corpus.segmentation.checks import check_fine, check_subsections, sections_schema_errors
from asylum_corpus.segmentation.headings import load_heading_rules, split_label
from asylum_corpus.segmentation.heading_like import heading_like
from asylum_corpus.segmentation.labels import parse_label
from asylum_corpus.segmentation.nesting import Open, place
from asylum_corpus.segmentation.parts_rules import load_parts_rules
from asylum_corpus.segmentation.stage import fine_document
from asylum_corpus.segmentation.subsections import subsections
from asylum_corpus.segmentation.titles import point_of, topic_of

HEADINGS = load_heading_rules(CONFIG / "segmentation" / "bvwg_headings.yaml")
PARTS = load_parts_rules(CONFIG / "segmentation" / "bvwg_parts.yaml")
RIS_ID = "BVWGT_20200504_W123_2212345_1_00"
FILLER = "Der Beschwerdeführer stellte einen Antrag auf internationalen Schutz, den die Behörde abwies. " * 3

SUBJECT = ["W123 2212345-1/5E", "IM NAMEN DER REPUBLIK!",
           "Das Bundesverwaltungsgericht hat durch den Richter über die Beschwerde zu Recht erkannt:"]
OPERATIVE = ["A) Die Beschwerde wird als unbegründet abgewiesen.",
             "B) Die Revision ist gemäß Art. 133 Abs. 4 B-VG nicht zulässig."]
MERITS = ["ENTSCHEIDUNGSGRÜNDE:", "I. Verfahrensgang:", FILLER, "II. Das Bundesverwaltungsgericht hat erwogen:",
          "1. Feststellungen:", FILLER, "1.1. Zur Person des Beschwerdeführers:", FILLER, "2. Beweiswürdigung:", FILLER,
          "3. Rechtliche Beurteilung:", "Zu A)", "3.1. Zu Spruchpunkt I. des angefochtenen Bescheides:", FILLER,
          "3.2. Zu den Spruchpunkten II. und III. des angefochtenen Bescheides:", FILLER,
          "Zu B) Unzulässigkeit der Revision:", FILLER]


def document(parts: list[tuple[str, str, list[str]]]) -> dict:
    """A stage 03 document from (section type, RIS field, paragraph texts)."""
    doc_id = f"ris:bvwg:{RIS_ID}"
    sections, paragraphs, pos = [], [], 0
    for typ, label, texts in parts:
        sid = f"{doc_id}#s:{len(sections) + 1:02d}"
        sections.append({"id": sid, "type": typ, "source_label": label, "start": pos, "end": pos})
        for t in texts:
            paragraphs.append({"id": f"{doc_id}#p:{len(paragraphs) + 1:04d}", "section_id": sid,
                               "marginal_number": None, "start": pos, "end": pos + len(t), "text": t})
            sections[-1]["end"] = pos + len(t)
            pos += len(t) + 1
    return {"document_id": doc_id, "ris_id": RIS_ID, "court": "BVwG",
            "ecli": "ECLI:AT:BVWG:2020:W123.2212345.1.00", "case_number": "W123 2212345-1",
            "case_numbers": ["W123 2212345-1"], "document_numbers": ["W123 2212345-1/5E"],
            "decision_date": "2020-05-04", "pronouncement_date": None, "decision_type": "judgment",
            "source_url": f"https://www.ris.bka.gv.at/Dokument.wxe?Abfrage=Bvwg&Dokumentnummer={RIS_ID}",
            "ris_metadata": {"Technisch": {}, "Allgemein": {}, "Judikatur": {}},
            "full_text": "\n".join(p["text"] for p in paragraphs), "sections": sections, "paragraphs": paragraphs}


def decision(reasoning: list[str]) -> dict:
    return document([("subject", "Spruch", SUBJECT), ("operative_part", "Spruch", OPERATIVE),
                     ("reasoning", "Text", reasoning)])


def segmented(reasoning: list[str]) -> dict:
    doc = decision(reasoning)
    coarse, _ = subsections(doc, HEADINGS)
    assert sections_schema_errors(coarse) == [] and check_subsections(coarse, doc) == []
    return coarse


def tree(doc: dict) -> list[tuple]:
    """(level, type, first words of the heading) of the sections below level 1."""
    text = {p["id"]: p["text"] for p in doc["paragraphs"]}
    return [(s["level"], s["type"], text[s["heading_paragraph_id"]][:24]) for s in doc["sections"] if s["level"] > 1]


# ------------------------------------------------------------------------------------------------ coarse sections

def test_label_signatures():
    assert split_label("II.1. Feststellungen:") == ("R.#.", "Feststellungen")
    assert split_label("2.2 Feststellungen") == ("#.#.", "Feststellungen")
    assert split_label("3) Rechtliche Beurteilung") == ("#.", "Rechtliche Beurteilung")
    assert split_label("A) 2. Beweiswürdigung") == ("A) #.", "Beweiswürdigung")
    assert split_label("Zu A)") == ("", "Zu A)")


def test_heading_rules_match_whole_titles_with_allowed_labels():
    def rule(text):
        return (HEADINGS.matched(text) or HEADINGS.matched(text, fallback=True) or (None,))[0]

    assert rule("1. Feststellungen:").type == "findings"
    assert rule("- 2. Beweiswürdigung:").type == "evidence_assessment"          # a leading dash is no part of it
    assert rule("1.1. Feststellungen zum Herkunftsstaat") is None                # a sub-point: not the whole title
    assert rule("C) Feststellungen") is None                                     # the authority's outline, quoted
    assert rule("Der oben angeführte Verfahrensgang ergibt sich aus dem Akt.") is None
    assert rule("1. Feststellungen: Der Beschwerdeführer ist Staatsangehöriger.").type == "findings"   # prefix rule


def test_merits_structure_and_hierarchy():
    coarse = segmented(MERITS)
    assert coarse["structure"]["status"] == "merits"
    assert tree(coarse) == [(2, "procedural_history", "I. Verfahrensgang:"),
                        (2, "considerations", "II. Das Bundesverwaltung"),
                        (3, "findings", "1. Feststellungen:"),
                        (3, "evidence_assessment", "2. Beweiswürdigung:"),
                        (3, "legal_assessment", "3. Rechtliche Beurteilun"),
                        (4, "ruling_a", "Zu A)"),
                        (4, "revision_admissibility", "Zu B) Unzulässigkeit der")]
    forms = [s["decision_form"] for s in coarse["sections"] if s["type"] == "operative_part"]
    assert forms == ["judgment"]


def test_inside_history_erwogen_after_legal_and_templates():
    coarse = segmented(["I. Verfahrensgang:", FILLER, "1. Feststellungen:", FILLER,          # the authority's, quoted
                    "II. Das Bundesverwaltungsgericht hat erwogen:", FILLER])
    assert coarse["structure"]["status"] == "short" and "inside_history" in coarse["structure"]["hierarchy_rules_applied"]
    coarse = segmented(["II. Das Bundesverwaltungsgericht hat erwogen:", "1. Feststellungen:", FILLER,
                    "2. Beweiswürdigung:", FILLER, "3. Rechtliche Beurteilung:",
                    "Das Bundesverwaltungsgericht hat erwogen:", FILLER])     # the opening sentence of 3.
    assert coarse["structure"]["hierarchy_rules_applied"] == ["erwogen_after_legal"]
    assert [t for _, t, _ in tree(coarse)] == ["considerations", "findings", "evidence_assessment", "legal_assessment"]
    assert segmented([FILLER * 3])["structure"]["status"] == "none"
    assert segmented(["Gekürzte Ausfertigung gemäß § 29 Abs. 5 VwGVG. " + FILLER])["structure"]["status"] == "abridged"
    assert segmented(["Kurz."])["structure"]["status"] == "no_reasoning"


# -------------------------------------------------------------------------------------------------- fine sections

def test_labels_with_values():
    assert parse_label("II.1. Feststellungen") == ([("R", 2), ("#", 1)], "Feststellungen")
    assert parse_label("A) I. Die Beschwerde") == ([("A", 1), ("R", 1)], "Die Beschwerde")
    assert parse_label("II) Die Revision", roman_brackets=True)[0] == [("R", 2)]
    assert parse_label("II.", bare_roman=True)[0] == [("R", 2)]


def test_nesting_by_labels():
    stack = [Open([("#", 3)], 3, None), Open([("#", 3), ("#", 2)], 4, {"id": "x"})]   # "3. Rechtliche …" > "3.2."
    assert place(list(stack), [("#", 3), ("#", 3)])[:2] == (0, "sibling")       # "3.3."
    assert place(list(stack), [("#", 3), ("#", 2), ("#", 1)])[:2] == (1, "child")
    assert place(list(stack), [("#", 4)])[:2] == (0, "continues_section")      # a main heading the heading rules missed
    assert place(list(stack), [("#", 1)])[:2] == (1, "child")                  # another shape: under the innermost
    stack = [Open(None, 3, None), Open([("#", 3)], 4, {"id": "y"})]
    assert place(list(stack), [("#", 1)])[:2] == (1, "restart")                # "1." while "3." is open: a new list
    stack = [Open(None, 2, None), Open([("A", 2)], 3, {"id": "b"})]           # "A) …", "B) …", then "A)" again
    assert place(list(stack), [("A", 1)], operative=True)[:2] == (0, "restart")   # the next ruling, beside
    assert place(list(stack), [("A", 1)])[:2] == (1, "restart")


def test_heading_like_points_and_topics():
    assert heading_like("3.2. Zu Spruchpunkt I. des angefochtenen Bescheides:", PARTS) is not None
    assert heading_like("4. Der Beschwerdeführer ist gesund und arbeitsfähig.", PARTS) is None   # a numbered sentence
    assert heading_like("Länderinformationsblatt der Staatendokumentation (8.1.2019):", PARTS) is None
    assert point_of("Zu den Spruchpunkten I., II. und III. des angefochtenen Bescheides", PARTS) == ("I-III", "authority")
    assert point_of("Zu Spruchteil A) II.", PARTS) == ("II", "court")
    assert point_of("Zu I) Abweisung der Beschwerde", PARTS) is None
    assert point_of("Zu I) Abweisung der Beschwerde", PARTS, legal=True) == ("I", "unclear")
    assert topic_of("Zur Person des Beschwerdeführers", PARTS, "court") == "person"
    assert topic_of("Sicherheitslage", PARTS, "court") is None and topic_of("Sicherheitslage", PARTS, "coi")


def fine(coarse: dict) -> dict:
    doc = fine_document(coarse, PARTS)
    assert check_fine(doc, {t for t, _, _ in PARTS.topics}) == []
    return doc


def test_fine_operative_part_and_points():
    doc = fine(segmented(MERITS))
    new = [s for s in doc["sections"] if s["nesting"]]
    by_type = {s["type"]: s for s in new}
    assert by_type["operative_part_a"]["label"] == "A)" and by_type["operative_part_b"]["label"] == "B)"
    points = [(s["title"], s["point"], s["numbering"]) for s in new if s["type"] == "point_assessment"]
    assert points == [("Zu Spruchpunkt I. des angefochtenen Bescheides:", "I", "authority"),
                      ("Zu den Spruchpunkten II. und III. des angefochtenen Bescheides:", "II-III", "authority")]
    person = next(s for s in new if s["type"] == "part")
    assert person["title"] == "Zur Person des Beschwerdeführers:" and person["topic"] == "person"
    coarse = {s["id"] for s in segmented(MERITS)["sections"]}
    assert all(s["id"] not in coarse for s in new)       # new ids follow the coarse ones


def test_fine_quoted_material_is_not_segmented():
    reasoning = ["ENTSCHEIDUNGSGRÜNDE:", "II. Das Bundesverwaltungsgericht hat erwogen:", "1. Feststellungen:",
                 "1.1. Zur Person des Beschwerdeführers:", FILLER, "1.2. Zur Lage im Herkunftsstaat:",
                 "Aus dem Länderinformationsblatt der Staatendokumentation ergibt sich:", "1. Politische Lage",
                 "Das Land ist eine Republik mit einem Präsidenten an der Spitze.", "2. Sicherheitslage",
                 "Die Lage ist in den Provinzen unterschiedlich.", "1.3. Zum Fluchtvorbringen:", FILLER,
                 "2. Beweiswürdigung:", FILLER]
    doc = fine(segmented(reasoning))
    parts = [(s["level"], s["title"], s["topic"]) for s in doc["sections"] if s["type"] == "part"]
    assert parts == [(4, "Zur Person des Beschwerdeführers:", "person"),     # the report's own "1." and "2." are no
                     (4, "Zur Lage im Herkunftsstaat:", "country_information"),   # parts; the court's "1.3." is
                     (4, "Zum Fluchtvorbringen:", "flight_reasons")]


@pytest.mark.parametrize("text", ["2. Die Beschwerde ist zulässig.", "Sicherheitslage - Letzte Änderung: 16.09.2021"])
def test_heading_like_limits(text):
    h = heading_like(text, PARTS)
    assert h is None or h[1] == "Sicherheitslage"      # a sentence; the update note is no part of the title


def test_facts_parts_split_the_facts_part_in_three():
    from asylum_corpus.segmentation.facts import facts_parts
    texts = ["Entscheidungsgründe:", "Beweis wurde erhoben durch Einsicht in den Akt.",
             "I. Verfahrensgang und Sachverhalt:", "Der BF stellte einen Antrag.",
             "II. Das Bundesverwaltungsgericht hat erwogen:", "1. Feststellungen:",
             "Der BF ist Staatsangehöriger Syriens.", "2. Beweiswürdigung:", "Die Angaben sind glaubhaft.",
             "3. Rechtliche Beurteilung:", "Gemäß § 3 AsylG ..."]
    paras, pos = [], 0
    for i, t in enumerate(texts):
        paras.append({"id": f"p{i}", "start": pos, "end": pos + len(t), "text": t})
        pos += len(t) + 1

    def at(i):
        return paras[i]["start"]

    secs = [{"type": "reasoning", "level": 1, "start": 0, "end": pos},
            {"type": "procedural_history_and_findings", "level": 2, "start": at(2), "end": at(4)},
            {"type": "considerations", "level": 2, "start": at(4), "end": pos},
            {"type": "findings", "level": 3, "start": at(5), "end": at(7)},
            {"type": "evidence_assessment", "level": 3, "start": at(7), "end": at(9)},
            {"type": "legal_assessment", "level": 3, "start": at(9), "end": pos}]
    parts = facts_parts({"sections": secs, "paragraphs": paras}, at(9))
    assert parts == {"procedural_history": [1, 2, 3], "findings": [5, 6], "evidence_assessment": [7, 8],
                     "other": [0, 4]}
