"""Stage 10: the records name no person, keep to their schema, and lose nothing of the text."""

import json

import pytest
from jsonschema import Draft202012Validator

from asylum_corpus.common import SCHEMAS
from asylum_corpus.head.extract import HeadRules, extract
from asylum_corpus.package import (attributes, checksums, inline, judge_id, link, outcome, prepare, published_head,
                                   published_head_schema, record_schema, sections, spans)
from asylum_corpus.persons import masker, names_of

HEAD = ("Das Bundesverwaltungsgericht hat durch die Richterin Mag.a Eva MUSTER als Einzelrichterin über die Beschwerde "
        "des XXXX, geb. XXXX, StA. Somalia, vertreten durch Dr. Max BEISPIEL, Rechtsanwalt in 1010 Wien, gegen den "
        "Bescheid des Bundesamtes für Fremdenwesen und Asyl vom 12.03.2018, Zl. 1100000000-180123456, zu Recht erkannt:")


def record() -> dict:
    text = HEAD + "\nA) Die Beschwerde wird abgewiesen.\n"
    doc = {"document_id": "ris:bvwg:BVWGT_20200101_W123_2212345_1_00", "ris_id": "BVWGT_20200101_W123_2212345_1_00",
           "decision_date": "2020-01-01", "case_number": "W123 2212345-1", "case_numbers": ["W123 2212345-1"],
           "full_text": text, "sections": [{"type": "subject", "start": 0, "end": len(HEAD)},
                                            {"type": "operative_part", "start": len(HEAD) + 1, "end": len(text) - 1}]}
    return extract(doc, HeadRules())


def test_published_head_names_no_person():
    r = record()
    assert r["judge"]["name"] == "Eva MUSTER" and "BEISPIEL" in r["representation"]["representatives"][0]["printed"]
    p = published_head(r, "salt")
    assert "MUSTER" not in json.dumps(p) and "BEISPIEL" not in json.dumps(p)
    assert p["judge"]["id"] == judge_id("Eva  MUSTER", "salt") != judge_id("Eva MUSTER", "other salt")
    assert p["representation"]["representatives"][0]["kind"] == "lawyer"
    schema = published_head_schema(json.loads((SCHEMAS / "head.schema.json").read_text(encoding="utf-8")))
    assert not list(Draft202012Validator(schema).iter_errors(p))
    assert list(Draft202012Validator(schema).iter_errors(r))          # the stage's record does not pass


def test_attributes_leave_out_what_the_record_has():
    a = attributes(record(), "salt")
    assert not {"document_id", "ris_id", "decision_date"} & set(a)
    assert "MUSTER" not in json.dumps(a) and a["judge"]["id"] == judge_id("Eva MUSTER", "salt")


def test_copies_of_the_text_become_positions():
    text = "Erste Zeile\nZweite Zeile"
    items = [{"id": "p1", "start": 0, "end": 11, "text": "Erste Zeile"},
             {"id": "p2", "start": 12, "end": 24, "text": "Zweite Zeile"}]
    assert spans(text, items, "text", "X") == [{"id": "p1", "start": 0, "end": 11},
                                               {"id": "p2", "start": 12, "end": 24}]
    items[1]["text"] = "Zweite  Zeile"
    with pytest.raises(SystemExit):
        spans(text, items, "text", "X")
    doc = {"document_id": "ris:vwgh:A", "full_text": text}
    relation = {"relation_id": "rel:1", "evidence": [
        {"document_id": "ris:vwgh:A", "start": 0, "end": 5, "quote": "Erste"},
        {"document_id": "ris:bvwg:B", "start": 0, "end": 3, "quote": "Der"}]}
    assert [sorted(e) for e in link(relation, doc)["evidence"]] == [["document_id", "end", "start"]] * 2


def test_outcome_is_one_flat_object_without_copies():
    op = "ris:bvwg:X#s:01"
    doc = {"full_text": "A) Ab", "sections": [{"id": op, "type": "operative_part", "start": 0, "end": 5}]}
    a = {"annotation_id": "ris:bvwg:X#out:01", "document_id": "ris:bvwg:X", "type": "normalized_outcome",
         "method": "rule_based", "evidence": [{"start": 0, "end": 5, "role": "disposition"}],
         "value": {"court": "BVwG", "operative_part_id": op, "operative_part_ids": [op],
                   "parts": [{"operative_part_id": op}], "outcome": "dismissed", "protection": "none",
                   "dispositions": [{"start": 0, "end": 5, "text": "A) Ab", "types": ["dismissed"],
                                     "operative_part_id": op}]}}
    assert outcome(a, doc) == {"annotation_id": "ris:bvwg:X#out:01", "operative_part_id": op,
                               "operative_part_ids": [op], "parts": [{"operative_part_id": op}],
                               "outcome": "dismissed", "protection": "none",
                               "dispositions": [{"start": 0, "end": 5, "types": ["dismissed"],
                                                 "operative_part_id": op}],
                               "evidence": [{"start": 0, "end": 5, "role": "disposition"}]}
    doc["sections"][0]["type"] = "reasoning"
    with pytest.raises(SystemExit):
        outcome(a, doc)


def test_titles_and_printed_values_name_no_person():
    head = record()
    title = ("Das Bundesverwaltungsgericht beschließt durch die Richterin Mag.a EVA  MUSTER als Einzelrichterin über "
             "die Beschwerde des XXXX, vertreten durch Dr. Max BEISPIEL, Rechtsanwalt in 1010 Wien:")
    mask = masker(head)
    out = sections({"sections": [{"id": "s1", "title": title}, {"id": "s2", "title": None}]}, mask)
    assert "MUSTER" not in out[0]["title"] and "BEISPIEL" not in out[0]["title"]
    assert "[judge]" in out[0]["title"] and "[lawyer]" in out[0]["title"] and out[1] == {"id": "s2", "title": None}
    printed = "Bundesamtes vom Dr. Max BEISPIEL, Rechtsanwalt in 1010 Wien"
    head["challenged_decisions"] = [{"authority_printed": printed}]
    assert "BEISPIEL" not in json.dumps(attributes(head, "salt", mask))
    head["judge"]["name"] = "XXXX"
    assert "XXXX" not in names_of(head)


def test_record_schema_and_its_inlined_form():
    schema = record_schema()
    Draft202012Validator.check_schema(schema)
    flat = inline(schema)
    assert '"$ref"' not in json.dumps(flat)
    incomplete = {"court": "VwGH", "attributes": {"judge": {}}, "citations": [{"start": "0"}]}
    messages = [sorted(e.message for e in Draft202012Validator(s).iter_errors(incomplete)) for s in (schema, flat)]
    assert messages[0] and messages[0] == messages[1]


def test_the_build_writes_over_nothing(tmp_path):
    (tmp_path / "README.md").write_text("written by hand", encoding="utf-8")
    with pytest.raises(SystemExit):
        prepare(tmp_path)
    assert (tmp_path / "README.md").read_text(encoding="utf-8") == "written by hand"
    prepare(tmp_path / "new")
    assert (tmp_path / "new").is_dir()


def test_checksums_list_every_file_but_themselves(tmp_path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "README.md").write_text("b", encoding="utf-8")
    checksums(tmp_path)
    lines = (tmp_path / "SHA256SUMS").read_text(encoding="utf-8").splitlines()
    assert [line.split("  ")[1] for line in lines] == ["README.md", "a.txt"]
    assert lines[1].split()[0] == "ca978112ca1bbdcafac231b39a23dc4da786eff8147c4e72b9807785afee48bb"

