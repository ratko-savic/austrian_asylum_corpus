"""Stage 06: the main citation rules and their limits."""

import json

from jsonschema import Draft202012Validator

from asylum_corpus.citations.cases import build_index, prefer_own_case
from asylum_corpus.citations.extract import document_citations, explicit_provisions
from asylum_corpus.citations.statutes import capital_i_variants, chain_items, load_acts, with_attested
from asylum_corpus.common import CONFIG, SCHEMAS

ACTS = load_acts(CONFIG / "citations" / "acts.yaml")
SCHEMA = Draft202012Validator(json.loads((SCHEMAS / "citation.schema.json").read_text(encoding="utf-8")))
DOC_ID = "ris:bvwg:BVWGT_20200101_W123_2212345_1_00"


def one(text, doc_id=DOC_ID, index=None, acts=ACTS):
    """The citations of a one-paragraph reasoning."""
    doc = {"document_id": doc_id, "court": "BVwG", "full_text": text, "document_numbers": [],
           "sections": [{"id": doc_id + "#s:01", "type": "reasoning"}],
           "paragraphs": [{"id": doc_id + "#p:0001", "section_id": doc_id + "#s:01", "start": 0, "end": len(text),
                           "text": text}]}
    return document_citations(doc, acts, index or {})[0]


def norms(text, **kw):
    return [c["normalized"] for c in one(text, **kw)]


def test_one_citation_per_provision_of_a_chain():
    assert norms("gemäß § 3 Abs. 1 und 2 AsylG 2005") == ["AsylG 2005 § 3 Abs. 1", "AsylG 2005 § 3 Abs. 2"]
    assert norms("gemäß §§ 3 und 8 AsylG 2005") == ["AsylG 2005 § 3", "AsylG 2005 § 8"]
    assert norms("Art. 2 und 3 EMRK") == ["EMRK Art. 2", "EMRK Art. 3"]
    c = one("gemäß § 3 Abs. 1 und § 8 Abs. 1 AsylG 2005")
    assert [x["text"] for x in c] == ["§ 3 Abs. 1", "§ 8 Abs. 1 AsylG 2005"]      # the last span carries the act
    assert {x["rule"] for x in c} == {"legislation_provision_chain"}
    assert norms("§ 8 Absatz 1 Ziffer 2 AsylG 2005") == ["AsylG 2005 § 8 Abs. 1 Z 2"]   # one spelling
    assert norms("Art. 1 Abschnitt A Z 2 GFK") == ["GFK Art. 1 Abschnitt A Z 2"]       # "Abschnitt" is no "Abs."


def test_bare_numbers_in_chains():
    assert norms("§§ 31 Abs. 1, 7 Abs. 2 VwGVG") == ["VwGVG § 31 Abs. 1", "VwGVG § 7 Abs. 2"]
    assert norms("§ 3 Abs. 1 und 3 Z 1 AsylG 2005") == ["AsylG 2005 § 3 Abs. 1", "AsylG 2005 § 3 Abs. 3 Z 1"]
    assert norms("§ 34 Abs. 2 Z 1 bis 3 AsylG 2005") == ["AsylG 2005 § 34 Abs. 2 Z 1", "AsylG 2005 § 34 Abs. 2 Z 3"]
    assert norms("§ 29 Abs. 1 zweiter Satz, 2a und 4 VwGVG") == \
        ["VwGVG § 29 Abs. 1 zweiter Satz", "VwGVG § 29 Abs. 2a", "VwGVG § 29 Abs. 4"]


def test_bare_numbers_read_with_the_attested_designations():
    def items(chain, attested):
        return [d for _, _, d in chain_items(chain, frozenset(attested))]

    assert items("§§ 13 Abs. 2 bis 5 und 22", {"§ 13 Abs. 2", "§ 13 Abs. 5", "§ 22", "§ 5"}) == \
        ["§ 13 Abs. 2", "§ 13 Abs. 5", "§ 22"]
    assert items("§§ 21 Abs. 3, 6a und 7", {"§ 21 Abs. 6a", "§ 21 Abs. 7", "§ 7"}) == \
        ["§ 21 Abs. 3", "§ 21 Abs. 6a", "§ 21 Abs. 7"]           # both readings attested: it continues
    assert items("§ 53 Abs. 3 Z 6, 7 oder 8", {"§ 8"}) == ["§ 53 Abs. 3 Z 6", "§ 53 Abs. 3 Z 7", "§ 53 Abs. 3 Z 8"]
    assert items("Art. 47 Abs. 2 iVm Art. 52 Abs. 3 und 7", {"Art. 7"})[-1] == "Art. 52 Abs. 7"   # articles continue
    assert [d for _, _, d in chain_items("§§ 10 Abs. 1 Z 3, 57")] == ["§ 10 Abs. 1 Z 3", "§ 10 Abs. 1 Z 57"]  # no table


def test_the_table_of_attested_designations(tmp_path):
    text = "gemäß §§ 4a, 10 Abs. 1 Z 1, 57 AsylG 2005; gemäß § 57 AsylG 2005"
    doc = {"document_id": "ris:bvwg:X", "court": "BVwG", "full_text": text, "document_numbers": [], "sections": [],
           "paragraphs": [{"id": "ris:bvwg:X#p:0001", "section_id": None, "start": 0, "end": len(text), "text": text}]}
    explicit = explicit_provisions(doc, ACTS)
    assert explicit[("AsylG 2005", "§ 57")] == 1 and ("AsylG 2005", "§ 10 Abs. 1 Z 57") not in explicit
    table = tmp_path / "attested_designations.json"
    table.write_text(json.dumps({"designations": {"AsylG 2005": sorted(d for _, d in explicit)}}), encoding="utf-8")
    cits, _ = document_citations(doc, with_attested(ACTS, table), {})
    assert [c["normalized"] for c in cits][2] == "AsylG 2005 § 57"


def test_subdivision_after_a_connective():
    assert norms("sowie § 52 Abs. 2 Z 2 und Abs. 9, § 46 und § 55 FPG 2005 idgF") == \
        ["FPG § 52 Abs. 2 Z 2", "FPG § 52 Abs. 9", "FPG § 46", "FPG § 55"]
    assert [x["text"] for x in one("gemäß § 53 Abs. 1 iVm Abs. 2 Z 6 FPG")] == ["§ 53 Abs. 1", "Abs. 2 Z 6 FPG"]
    assert norms("im Sinne des Art. 1, Abschnitt A, Z. 2 der Genfer Flüchtlingskonvention droht") == \
        ["GFK Art. 1 Abschnitt A Z 2"]


def test_leg_cit_and_acts_without_a_year():
    assert norms("Gemäß § 3 Abs. 1 AsylG 2005 … nach § 3 Abs. 3 leg. cit. ist") == \
        ["AsylG 2005 § 3 Abs. 1", "AsylG 2005 § 3 Abs. 3"]
    assert one("Nach § 3 Abs. 3 leg. cit. ist") == []                         # no act before it in the paragraph
    assert norms("gemäß § 7 AsylG 1997") == ["AsylG 1997 § 7"]
    assert norms("gemäß § 7 AsylG") == ["AsylG § 7"]                           # no year: not resolved


def test_spellings_of_an_act():
    c = one("keine Art. 3 EMRK-widrige Behandlung")
    assert [(x["text"], x["normalized"]) for x in c] == [("Art. 3 EMRK", "EMRK Art. 3")]
    assert norms("nach § 4 AsylG-DV") == []                                    # a compound with a capital: not read
    assert norms("nach § 35 AsyIG 2005") == ["AsylG 2005 § 35"]               # a capital I for l
    assert capital_i_variants({"AsylG": ("AsylG", "t"), "AsyIG": ("X", "t")}) == {}   # a variant that is an alias


def test_acts_named_by_their_titles():
    assert norms("Art. 33 Z 1 der Konvention über die Rechtsstellung der Flüchtlinge, BGBl. Nr. 55/1955") == \
        ["GFK Art. 33 Z 1", "BGBl. Nr. 55/1955"]
    assert norms("wenn dadurch Art. 2 oder 3 der Europäischen Menschenrechtskonvention verletzt") == \
        ["EMRK Art. 2", "EMRK Art. 3"]
    assert norms("Gemäß § 25a Abs. 1 des Verwaltungsgerichtshofgesetzes 1985 (VwGG)") == ["VwGG § 25a Abs. 1"]
    assert [x["text"] for x in one("§ 25a Abs. 1 Verwaltungsgerichtshofgesetz 1985")] == \
        ["§ 25a Abs. 1 Verwaltungsgerichtshofgesetz 1985"]                   # the longest alias wins
    assert norms("idF des Art. 1 Abs. 2 des Protokolls über die Rechtsstellung der Flüchtlinge") == \
        ["Protokoll über die Rechtsstellung der Flüchtlinge Art. 1 Abs. 2"]
    assert norms("Art. 3 Abs. 2 Dublin III VO; Art. 10 Abs. 1 Dublin-II-Verordnung") == \
        ["Dublin III-VO Art. 3 Abs. 2", "Dublin II-VO Art. 10 Abs. 1"]
    assert norms("Art. 8 Abs. 2 Strafprozessordnung; Art. 4 des Staatsbürgerschaftsgesetzes der Republik Armenien") == []


def test_bzw_joins_provisions():
    assert norms("eine Verletzung von Art 2 bzw. Art 3 EMRK") == ["EMRK Art. 2", "EMRK Art. 3"]
    assert norms("gem. Art. 2 bzw. 3 EMRK") == ["EMRK Art. 2", "EMRK Art. 3"]
    assert norms("gemäß §§ 3 bzw. 8 Asylgesetz") == ["AsylG § 3", "AsylG § 8"]
    assert norms("§ 3 Abs. 1 beziehungsweise § 8 Abs. 1 AsylG 2005") == ["AsylG 2005 § 3 Abs. 1", "AsylG 2005 § 8 Abs. 1"]

def test_an_article_of_the_stgb_is_no_citation():
    assert norms("Artikel 94 des Strafgesetzbuches; Art. 314 StGB; Artikel 41, Absatz 1 des Strafgesetzbuches") == []
    c = one("die Artikel 285 bis 287 des Strafgesetzbuches, nicht § 83 Abs. 1 StGB oder Art. 8 EMRK")
    assert [(x["citation_id"][-4:], x["normalized"]) for x in c] == [("0001", "StGB § 83 Abs. 1"), ("0002", "EMRK Art. 8")]
    assert norms("Art. 300 leg. cit.; § 278b StGB; Art. 7 leg. cit.") == ["StGB § 278b"]
    assert norms("Art. I StGB-Novelle") == []


def test_decisions_and_their_targets():
    assert norms("VwGH 28.05.2014, Ra 2014/20/0017, 0018") == ["VwGH Ra 2014/20/0017", "VwGH Ra 2014/20/0018"]
    assert norms("VwGH 28.05.2014, Ra 2014/20/0017, 2015") == ["VwGH Ra 2014/20/0017"]   # 2015 is a year
    assert one("VwGH 28.05.2014, Ra 2014/20/0017, 0018")[1]["target_id"] == "case:vwgh:Ra 2014/20/0018"
    c = one("vgl. das Erkenntnis W123 2212345-1/5E")                          # another decision of the own case
    assert [(x["normalized"], x["target_id"]) for x in c] == [("BVwG W123 2212345-1/5E", "case:bvwg:W123 2212345-1")]
    a, b = "ris:bvwg:BVWGT_20220103_G301_2238544_1_00", "ris:bvwg:BVWGT_20220103_G301_2238548_1_00"
    numbers = {"BVwG G301 2238544-1/12E", "BVwG G301 2238548-1/13E"}
    index = prefer_own_case(build_index([(a, numbers), (b, numbers)]), {a: "G301 2238544-1", b: "G301 2238548-1"})
    assert one("vgl. BVwG G301 2238548-1/13E", index=index)[0]["target_id"] == b   # the copy of its own case


def test_law_gazette_and_eu_act_numbers():
    c = one("gemäß § 3 Abs. 1 Asylgesetz 2005, BGBl. I Nr. 100/2005 idF BGBl I 24/2016,")
    assert [(x["normalized"], x["type"]) for x in c] == [
        ("AsylG 2005 § 3 Abs. 1", "national_legislation"), ("BGBl. I Nr. 100/2005", "publication_reference"),
        ("BGBl. I Nr. 24/2016", "publication_reference")]
    assert norms("gemäß VwGVG, BGBl. I 2013/33 idgF") == ["BGBl. I Nr. 33/2013"]
    assert norms("VO (EU) 604/2013, Verordnung (EU) 2024/1347, Status-RL 2011/95/EU, (2013/32/EU)") == \
        ["Verordnung (EU) Nr. 604/2013", "Verordnung (EU) 2024/1347", "Richtlinie 2011/95/EU", "2013/32/EU"]
    assert norms("gemäß Art. 12 Abs. 2 der VO (EU) Nr. 604/2013; Art. 17 leg. cit.") == \
        ["Verordnung (EU) Nr. 604/2013 Art. 12 Abs. 2", "Verordnung (EU) Nr. 604/2013 Art. 17"]


def test_ecthr_application_numbers_after_rs():
    assert norms("EGMR 18.06.2013, Rs 53852/11 Halimi/Österreich und Italien") == ["EGMR 53852/11"]
    assert norms("vgl. EGMR; EuGH 21.12.2012, Rs. 4110/10") == []                     # after "EuGH": a CJEU case
    assert norms("EuGH 6.10.1982, C.I.L.F.I.T, Rs 283/81; OLG Wien, 10 Rs 66/18a") == []   # old CJEU, a court file


def test_citations_are_schema_valid():
    for c in one("§ 3 Asylgesetz 2005, BGBl. I Nr. 100/2005; Art. 15 der Verordnung (EU) 2024/1347; RL 2013/33/EU; "
                 "§ 53 Abs. 1 iVm Abs. 2 FPG; EGMR, Rs 35989/03; VwGH 28.05.2014, Ra 2014/20/0017; EuGH C-608/22"):
        assert not list(SCHEMA.iter_errors(c)), c
