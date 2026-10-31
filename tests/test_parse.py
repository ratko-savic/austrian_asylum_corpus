"""Stage 03: text rules, sections, the BVwG head, and the checks every document must pass."""

import copy
from datetime import date

import pytest

from asylum_corpus.parsing.checks import check_document, schema_errors
from asylum_corpus.parsing.document import EmptySource, build_document, document_id
from asylum_corpus.parsing.xml import clean
from asylum_corpus.ris.text import find_dates


def ris_record(ris_id: str, application: str, case_number: str, decision_type: str = "Erkenntnis") -> dict:
    court = {"Bvwg": "BVWG", "Vwgh": "VWGH", "Vfgh": "VFGH"}[application]
    return {"Data": {"Metadaten": {
        "Technisch": {"ID": ris_id, "Applikation": application},
        "Allgemein": {"Geaendert": "2020-06-01"},
        "Judikatur": {"Dokumenttyp": "Text", "Geschaeftszahl": {"item": case_number}, "Entscheidungsdatum": "2020-05-04",
                      "EuropeanCaseLawIdentifier": f"ECLI:AT:{court}:2020:X",
                      application: {"Entscheidungsart": decision_type}}}}}


RECORD = ris_record("BVWGT_20200504_W123_2212345_1_00", "Bvwg", "W123 2212345-1")
XML = """<risdok xmlns="http://www.bka.gv.at"><metadaten/><nutzdaten><abschnitt>
<ueberschrift typ="titel">Geschäftszahl</ueberschrift><absatz typ="erltext" ct="gz">W123 2212345-1</absatz>
<ueberschrift typ="titel">Spruch</ueberschrift>
<absatz typ="erltext" ct="spruch">W123 2212345-1/5E W123 2212346-1/4E</absatz>
<absatz typ="erltext" ct="spruch">GEKÜRZTE AUSFERTIGUNG DES AM 12.03.2020 MÜNDLICH VERKÜNDETEN ERKENNTNISSES</absatz>
<absatz typ="erltext" ct="spruch">Das Bundesverwaltungsgericht hat über die Beschwerde zu Recht erkannt:</absatz>
<absatz typ="erltext" ct="spruch">A) Die Beschwerde wird abgewiesen.</absatz>
<ueberschrift typ="titel">Text</ueberschrift>
<absatz typ="erltext" ct="text">I. Verfahrensgang:</absatz>
<liste><strichliste ebene="1"><listelem ct="text"><symbol stellen="1">-</symbol>niederschriftliche Einvernahmen</listelem>
<listelem ct="text"><symbol stellen="1">-</symbol>Bericht des AA<amp/>B</listelem></strichliste>
<schluss typ="Abs" ct="text">Zugriff 29.09.2014</schluss></liste>
EXTRA</abschnitt></nutzdaten></risdok>"""


def build(extra: str = "", xml: str = XML, record: dict = RECORD) -> tuple[dict, bytes]:
    data = xml.replace("EXTRA", extra).encode()
    return build_document(data, record), data


def test_text_rules():
    assert clean("\uf0b7 Punkt eins  und\tzwei\n") == "• Punkt eins und zwei"
    assert clean("Asyl\xadgesetz") == "Asylgesetz"
    assert clean("§ 3 – „Zitat“") == "§ 3 – „Zitat“"   # other characters unchanged
    assert find_dates("am 1. Jänner 2014 und am 18.08.2025, nicht 31.02.2020") == [date(2014, 1, 1), date(2025, 8, 18)]


def test_lists_sections_and_the_bvwg_head():
    doc, xml = build()
    texts = [p["text"] for p in doc["paragraphs"]]
    assert texts[-3:] == ["- niederschriftliche Einvernahmen", "- Bericht des AA&B", "Zugriff 29.09.2014"]
    assert "W123 2212345-1" not in texts  # the RIS header field is left out
    assert [s["type"] for s in doc["sections"]] == ["subject", "operative_part", "reasoning"]
    assert doc["case_numbers"] == ["W123 2212345-1", "W123 2212346-1"]
    assert doc["document_numbers"] == ["W123 2212345-1/5E", "W123 2212346-1/4E"]
    assert doc["pronouncement_date"] == "2020-03-12"
    assert doc["document_id"] == document_id("BVwG", doc["ris_id"]) == "ris:bvwg:BVWGT_20200504_W123_2212345_1_00"
    assert schema_errors(doc) == [] and check_document(doc, xml) == []


def test_intro_and_operative_text_in_one_paragraph_are_split():
    xml = XML.replace("zu Recht erkannt:</absatz>\n<absatz typ=\"erltext\" ct=\"spruch\">A)", "zu Recht erkannt: A)")
    doc, data = build(xml=xml)
    spruch = [(p["text"], s["type"]) for p in doc["paragraphs"] for s in doc["sections"] if s["id"] == p["section_id"]]
    assert ("Das Bundesverwaltungsgericht hat über die Beschwerde zu Recht erkannt:", "subject") in spruch
    assert ("A) Die Beschwerde wird abgewiesen.", "operative_part") in spruch
    assert check_document(doc, data) == []


def test_a_quoted_head_is_not_an_introduction():
    extra = ('<absatz typ="erltext" ct="spruch">„Das Bundesverwaltungsgericht hat zu Recht erkannt:</absatz>')
    xml = XML.replace('<ueberschrift typ="titel">Text</ueberschrift>', extra + '<ueberschrift typ="titel">Text</ueberschrift>')
    doc, _ = build(xml=xml)
    quoted = next(p for p in doc["paragraphs"] if p["text"].startswith("„"))
    assert next(s for s in doc["sections"] if s["id"] == quoted["section_id"])["type"] == "operative_part"


def test_margin_numbers_and_paragraphs_that_are_only_a_number():
    extra = ('<absatz typ="erltext" ct="text">1<tab position="35.4" /></absatz>'
             '<absatz typ="erltext" ct="text">4.1<tab position="35.4" />Zu Spruchpunkt A)</absatz>'
             '<absatz typ="erltext" ct="text">7<tab position="35.4" />Gemäß § 3 AsylG 2005</absatz>')
    doc, xml = build(extra)
    paras = [(p["marginal_number"], p["text"]) for p in doc["paragraphs"]][-3:]
    assert paras == [(None, "1"), (None, "4.1 Zu Spruchpunkt A)"), ("7", "Gemäß § 3 AsylG 2005")]
    assert check_document(doc, xml) == []


def test_footnotes_go_to_the_end_and_nested_tables_stay_in_their_cell():
    notes = '<fnen><fntext typ="f" nr="1"> Frau Dr. X ist Beraterin.</fntext></fnen>'
    table = ('<table><tr><td><absatz ct="text">Quelle</absatz></td><td><absatz ct="text">Inhalt</absatz>'
             '<table><tr><td><absatz ct="text">innen</absatz></td></tr></table></td><td/></tr></table>')
    xml = XML.replace("<nutzdaten>", "<nutzdaten>" + notes)
    doc, data = build(table + '<absatz ct="text">Text mit Fußnote<fnzeichen typ="f"/> danach.</absatz>', xml)
    texts = [p["text"] for p in doc["paragraphs"]]
    assert "Quelle | Inhalt innen |" in texts and "innen" not in texts  # empty cell kept, nested table read once
    assert texts[-2:] == ["Text mit Fußnote danach.", "Frau Dr. X ist Beraterin."]
    assert (doc["sections"][-1]["type"], doc["sections"][-1]["source_label"]) == ("other", "Fußnoten")
    assert schema_errors(doc) == [] and check_document(doc, data) == []


def test_vwgh_operative_sentence_tagged_as_reasoning():
    record = ris_record("JWT_2020010001_20200504X00", "Vwgh", "Ra 2020/01/0001", "Beschluss")
    xml = """<risdok xmlns="http://www.bka.gv.at"><nutzdaten><abschnitt>
<ueberschrift typ="titel">Betreff</ueberschrift><absatz ct="betreff">Der Verwaltungsgerichtshof hat über die Revision
des A gegen das Erkenntnis des Bundesverwaltungsgerichts vom 2. Jänner 2020, W123 2212345-1/5E, den Beschluss gefasst:</absatz>
<ueberschrift typ="titel">Begründung</ueberschrift><absatz ct="begruendung">Die Revision wird zurückgewiesen.</absatz>
<absatz ct="begruendung">1 <tab/>Mit dem angefochtenen Erkenntnis wies das Gericht die Beschwerde ab.</absatz>
</abschnitt></nutzdaten></risdok>"""
    doc = build_document(xml.encode(), record)
    assert [s["type"] for s in doc["sections"]] == ["subject", "operative_part", "reasoning"]
    assert doc["decision_type"] == "order" and doc["case_numbers"] == ["Ra 2020/01/0001"]
    assert doc["document_numbers"] == [] and doc["pronouncement_date"] is None  # the head rules are BVwG only


def test_lost_text_and_broken_offsets_are_errors():
    doc, xml = build('<unbekannt ct="text">Dieser Text fehlt sonst</unbekannt>')
    assert [code for code, _ in check_document(doc, xml)] == ["text_lost"]
    doc, xml = build()
    broken = copy.deepcopy(doc)
    broken["paragraphs"][1]["start"] += 1
    broken["sections"][1]["start"] += 2
    assert {"offsets", "partition"} <= {code for code, _ in check_document(broken, xml)}
    broken = copy.deepcopy(doc)
    broken["decision_type"] = "decision"
    assert schema_errors(broken)


def test_a_decision_without_text_raises_empty_source():
    xml = """<risdok xmlns="http://www.bka.gv.at"><metadaten/><nutzdaten><abschnitt>
<ueberschrift typ="titel">Geschäftszahl</ueberschrift><absatz ct="gz">W123 2212345-1</absatz>
</abschnitt></nutzdaten></risdok>"""
    with pytest.raises(EmptySource):
        build_document(xml.encode(), RECORD)


def test_unknown_decision_type_is_an_error():
    with pytest.raises(ValueError):
        build(record=ris_record("BVWGT_20200504_W123_2212345_1_00", "Bvwg", "W123 2212345-1", "Bescheid"))
