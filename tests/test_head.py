"""Stage 04: the head labels and the case history (config/head/), on short made-up decisions."""

import json

from jsonschema import Draft202012Validator

from asylum_corpus.common import SCHEMAS
from asylum_corpus.head.extract import HeadRules, extract, normalise_country
from asylum_corpus.head.history import before_day, from_day, parse_date, sentences

RULES = HeadRules()
SCHEMA = Draft202012Validator(json.loads((SCHEMAS / "head.schema.json").read_text(encoding="utf-8")))
INTRO = "Das Bundesverwaltungsgericht hat durch {judge} als Einzelrichter über die Beschwerde "
TAIL = ("gegen den Bescheid des Bundesamtes für Fremdenwesen und Asyl vom 12.03.2018, Zl. 1100000000-180123456, zu "
        "Recht erkannt:")
HEAD = ("Das Bundesverwaltungsgericht hat durch den Richter Mag. Max MUSTER als Einzelrichter über die Beschwerde "
        "{who} XXXX, geb. XXXX, StA. Afghanistan, gegen den Bescheid des Bundesamtes für Fremdenwesen und Asyl vom "
        "12.03.2018, Zl. 1100000000-180123456, {hearing}zu Recht erkannt:")


def head_doc(head: str, case_numbers=("W123 2212345-1",)) -> dict:
    text = head + "\nA) Die Beschwerde wird abgewiesen.\n"
    return {"document_id": "ris:bvwg:BVWGT_20200101_W123_2212345_1_00", "ris_id": "BVWGT_20200101_W123_2212345_1_00",
            "decision_date": "2020-01-01", "case_number": case_numbers[0], "case_numbers": list(case_numbers),
            "full_text": text, "sections": [{"type": "subject", "start": 0, "end": len(head)},
                                             {"type": "operative_part", "start": len(head) + 1, "end": len(text) - 1}]}


def history_doc(history: str, legal: str = "Die Beschwerde ist unbegründet.", who: str = "des", hearing: str = "",
                case_numbers=("W123 2212345-1",), pronounced=None) -> dict:
    parts = [HEAD.format(who=who, hearing=hearing), "A) Die Beschwerde wird abgewiesen.", "Entscheidungsgründe:",
             "I. Verfahrensgang:", history, "II. Rechtliche Beurteilung:", legal]
    offsets, pos = [], 0
    for p in parts:
        offsets.append((pos, pos + len(p)))
        pos += len(p) + 1
    sections = [("subject", 0, 0, 1), ("operative_part", 1, 1, 1), ("reasoning", 2, 6, 1),
                ("procedural_history", 3, 4, 2), ("legal_assessment", 5, 6, 2)]
    return {"document_id": "ris:bvwg:BVWGT_20200101_W123_2212345_1_00", "ris_id": "BVWGT_20200101_W123_2212345_1_00",
            "decision_date": "2020-01-01", "pronouncement_date": pronounced, "case_number": case_numbers[0],
            "case_numbers": list(case_numbers), "full_text": "\n".join(parts),
            "sections": [{"type": t, "start": offsets[a][0], "end": offsets[b][1], "level": lv}
                         for t, a, b, lv in sections]}


def labels(d: dict) -> dict:
    h = extract(d, RULES)
    assert not list(SCHEMA.iter_errors(h))
    return h


def test_typical_head():
    head = ("W123 2212345-1/5E IM NAMEN DER REPUBLIK! Das Bundesverwaltungsgericht hat durch die Richterin Mag.a Eva "
            "MUSTER als Einzelrichterin über die Beschwerde des XXXX, geb. XXXX, StA. Afghanistan, vertreten durch "
            "die BBU GmbH, gegen den Bescheid des Bundesamtes für Fremdenwesen und Asyl, Regionaldirektion Wien, vom "
            "12.03.2018, Zl. 1100000000-180123456, zu Recht erkannt:")
    d = head_doc(head)
    h = labels(d)
    assert (h["judge"]["name"], h["judge"]["gender"], h["judge"]["formation"], h["judge"]["department"]) == \
        ("Eva MUSTER", "female", "single_judge", "W123")
    assert d["full_text"][h["judge"]["span"]["start"]:h["judge"]["span"]["end"]].endswith("MUSTER")
    assert h["nationality_set"] == ["Afghanistan"] and h["appellants"]["count"] == 1 and not h["appellants"]["minors"]
    assert h["representation"]["kinds"] == ["organisation"]
    c = h["challenged_decisions"][0]
    assert (c["authority"], c["date"], c["file_number"]) == ("BFA", "2018-03-12", "1100000000-180123456")
    assert h["scope"] == {"whole": True, "points": [], "span": None} and h["problems"] == []


def test_family_minors_and_scope():
    head = ("Das Bundesverwaltungsgericht hat durch Mag. BEISPIEL als Einzelrichter über die Beschwerden von 1.) "
            "XXXX, geb. XXXX, 2.) mj. XXXX, geb. XXXX, beide iranische Staatsangehörige, 2.) vertreten durch die "
            "Mutter XXXX, gegen Spruchpunkte II. bis IV. der Bescheide des Bundesasylamtes jeweils vom 14. Juni "
            "2013, Zl. XXXX, zu Recht erkannt:")
    h = labels(head_doc(head, ("W111 1400001-1", "W111 1400002-1")))
    assert (h["judge"]["name"], h["judge"]["gender"]) == ("BEISPIEL", "male")
    assert h["appellants"]["count"] == 2 and h["appellants"]["minors"] and h["nationality_set"] == ["Iran"]
    assert h["representation"]["kinds"] == ["guardian"]
    c = h["challenged_decisions"][0]
    assert (c["authority"], c["date"], c["file_number"]) == ("BAA", "2013-06-14", "XXXX")
    assert h["scope"]["points"] == ["II", "III", "IV"] and not h["scope"]["whole"]


def test_no_head_and_no_challenged_decision():
    d = head_doc("")
    d["sections"] = d["sections"][1:]
    h = extract(d, RULES)
    assert "no_head" in h["problems"] and h["judge"]["name"] is None and h["appellants"]["count"] == 1
    h = labels(head_doc("Das Bundesverwaltungsgericht hat durch den Richter Dr. Hans BEISPIEL als Einzelrichter über "
                        "die Säumnisbeschwerde von XXXX, staatenlos, vertreten durch RA Mag. Anna ANWALT, wegen "
                        "Verletzung der Entscheidungspflicht des Bundesamtes für Fremdenwesen und Asyl beschlossen:"))
    assert h["nationality_set"] == ["stateless"] and h["representation"]["kinds"] == ["lawyer"]
    assert h["failure_to_act"] and h["challenged_decisions"] == [] and "no_challenged_decision" in h["problems"]


def test_titles_and_countries():
    for judge, name in [("den Richter Dr. Drucker", "Drucker"), ("die Richterin Magdalena BAUM", "Magdalena BAUM"),
                        ("den Richter Mag. Dr. Ernst MUSTER, MAS", "Ernst MUSTER")]:
        assert labels(head_doc(INTRO.format(judge=judge) + "des XXXX, StA. Irak, " + TAIL))["judge"]["name"] == name
    for printed, country in {"RUSSISCHE FÖDERATION": "Russische Föderation", "Islamischen Republik Afghanistan":
                             "Afghanistan", "VR China": "China", "Afghanistans": "Afghanistan",
                             "Demokratische Republik Kongo": "Demokratische Republik Kongo", "Kongo": "Kongo"}.items():
        assert normalise_country(printed, RULES.countries) == country, printed


def test_number_of_appellants():
    block = ("Das Bundesverwaltungsgericht hat durch die Richterin Mag. A B über die Beschwerde von XXXX, StA.: "
             "Afghanistan, gegen den Bescheid des Bundesasylamtes vom 11.07.2012, Zl. 11 11.31{n}-BAG, zu Recht "
             "erkannt: ")
    h = labels(head_doc("".join(block.format(n=n) for n in (1, 2, 4))))
    assert (h["appellants"]["count"], h["appellants"]["count_basis"]) == (3, "file_numbers")
    h = labels(head_doc(INTRO.format(judge="den Richter Mag. X Y") + "der XXXX, geb. XXXX, StA. Albanien und des XXXX, "
                        "geb. XXXX, StA. Albanien, " + TAIL))
    assert (h["appellants"]["count"], h["appellants"]["count_basis"]) == (2, "head_persons")
    # one person with two proceeding numbers
    assert labels(head_doc(INTRO.format(judge="den Richter Mag. X Y") + "des XXXX, StA. Irak, " + TAIL,
                           ("2143175-2", "W182 2143175-2")))["appellants"]["count"] == 1


def test_representation_of_the_appeal():
    def kinds(rep: str) -> list[str]:
        return labels(head_doc(INTRO.format(judge="den Richter Mag. X Y") + "des XXXX, StA. Syrien, " + rep + ", "
                               + TAIL))["representation"]["kinds"]
    assert kinds("vertreten durch: ARGE Rechtsberatung") == ["organisation"]
    assert kinds("gesetzlich vertreten durch das Land Tirol") == ["guardian"]
    # a guardian counts only where no other representative is named
    assert kinds("gesetzlich vertreten durch die Mutter XXXX, alle vertreten durch die BBU GmbH") == ["organisation"]
    assert kinds("vertreten durch die Mutter XXXX, diese vertreten durch die BBU GmbH") == ["organisation"]
    h = labels(head_doc(INTRO.format(judge="den Richter Mag. X Y") + "der XXXX, StA. Afghanistan, vertreten durch ihre "
                        "gesetzlichen Vertreter XXXX (Mutter) und XXXX (Vater), diese nunmehr vertreten durch "
                        "Rechtsanwalt Mag. A X, " + TAIL))
    assert h["representation"]["kinds"] == ["lawyer"] and h["appellants"]["minors"]


def test_case_history_hearing_and_interpreter():
    h = labels(history_doc(
        "1. Der Beschwerdeführer reiste am 10.10.2015 illegal in das österreichische Bundesgebiet ein und stellte am "
        "12.10.2015 einen Antrag auf internationalen Schutz. 2. Am 20.03.2017 wurde der BF vor dem BFA im Beisein "
        "eines Dolmetschers für die Sprache Dari niederschriftlich einvernommen. 3. Mit Erkenntnis des "
        "Asylgerichtshofes vom 02.10.2013 wurde eine frühere Beschwerde als unbegründet abgewiesen. 4. Das "
        "Bundesverwaltungsgericht führte am 05.06.2019 in Anwesenheit einer Dolmetscherin für die arabische Sprache "
        "eine öffentliche mündliche Verhandlung durch."))
    assert {e["type"] for e in h["history"]["events"]} == {"hearing", "court_decision"}   # no entries or applications
    assert h["dates"]["court_decision_list"] == [{"date": "2013-10-02", "court": "AsylGH"}]
    assert h["hearing"]["value"] == "held"
    iv = h["interpreter"]
    assert (iv["bfa_interview"]["interpreter"], iv["bfa_interview"]["languages"]) == ("yes", ["Dari"])
    assert (iv["court_hearing"]["interpreter"], iv["court_hearing"]["languages"]) == ("yes", ["Arabisch"])
    assert iv["first_questioning"]["interpreter"] is None          # the step is not mentioned
    assert h["appellant_gender"]["value"] == "male" and h["new_law"]["basis"] == "decision_date"


def test_hearing_not_held_and_pronouncement():
    h = labels(history_doc("Der BF stellte am 01.11.2015 einen Antrag auf internationalen Schutz.",
                           legal="Eine mündliche Verhandlung konnte gemäß § 21 Abs. 7 BFA-VG unterbleiben."))
    assert h["hearing"]["value"] == "not_held" and h["interpreter"]["court_hearing"]["interpreter"] == "no_hearing"
    h = labels(history_doc("Der BF stellte am 01.11.2015 einen Antrag.", hearing="nach Durchführung einer mündlichen "
                           "Verhandlung am 16. und 17.07.2019 "))
    assert h["hearing"]["value"] == "held" and h["hearing"]["in_head"]
    h = labels(history_doc("Der BF stellte am 01.11.2015 einen Antrag.", pronounced="2019-08-12"))
    assert h["hearing"]["value"] == "held" and h["dates"]["pronouncement"] == "2019-08-12"
    # case law cited as authority is no earlier decision in the case
    h = labels(history_doc("In der Beschwerde wurde auf das Erkenntnis des VwGH vom 21.02.2017, Ra 2016/18/0137, "
                           "verwiesen, wonach die Behörde zu ermitteln habe."))
    assert h["dates"]["court_decision_list"] == []


def test_appellant_gender_of_several_appellants():
    h = labels(history_doc("Die Beschwerdeführer stellten am 19.06.2014 Anträge auf internationalen Schutz.",
                           who="der XXXX, geb. XXXX, und des", case_numbers=("W123 2212345-1", "W123 2212346-1")))
    assert [(p["person"], p["gender"]) for p in h["appellant_gender"]["persons"]] == [(1, "female"), (2, "male")]
    assert h["appellant_gender"]["value"] == "mixed"


def test_new_law_by_the_start_of_the_proceeding():
    def label(history: str, decided: str = "2026-07-20", challenged: str = "12.03.2018") -> dict:
        d = history_doc(history)
        d["decision_date"] = decided
        d["full_text"] = d["full_text"].replace("vom 12.03.2018", f"vom {challenged}")
        return labels(d)["new_law"]
    lodged = "Der Beschwerdeführer stellte am 20.06.2026 einen Antrag auf internationalen Schutz."
    assert label(lodged, decided="2026-05-04")["value"] == "no"                     # decided before the day
    assert label(lodged)["basis"] == "challenged_decision"                          # the decision of 2018 came first
    assert label(lodged, challenged="15.07.2026")["value"] == "yes"
    assert label("Der Beschwerdeführer stellte am 03.03.2026 einen Antrag auf internationalen Schutz.",
                 challenged="15.07.2026")["value"] == "no"                          # begun before the day
    # an application after the challenged decision, an undated one, or one in another state tells nothing
    assert label("Am 20.07.2026 stellte der Beschwerdeführer einen Antrag auf internationalen Schutz.",
                 challenged="15.07.2026")["value"] == "unknown"
    assert label("Der Beschwerdeführer stellte am XXXX einen Antrag auf internationalen Schutz.",
                 challenged="15.07.2026")["value"] == "unknown"
    assert label("Der Beschwerdeführer stellte am 25.06.2026 in Italien einen Antrag auf internationalen Schutz.",
                 challenged="15.07.2026")["value"] == "unknown"


def test_sentences_and_dates():
    text = "Mit Bescheid vom 12.03.2018, Zl. 123, wies das BFA gem. § 3 Abs. 1 ab. Der BF reiste am 1.2.2015 ein."
    spans = sentences(text, 0, len(text), RULES.history.abbreviations)
    assert [text[a:b] for a, b in spans] == ["Mit Bescheid vom 12.03.2018, Zl. 123, wies das BFA gem. § 3 Abs. 1 ab.",
                                             "Der BF reiste am 1.2.2015 ein."]
    assert parse_date("Mitte März 2018") == ("2018-03", "month", False)
    assert parse_date("XXXX .09.2022") == ("2022-09", "month", True)
    assert parse_date("12. Juni 2014") == ("2014-06-12", "day", False)
    day = "2026-06-12"           # only dates certainly before or after the day count
    assert before_day("2026-05", day) and not before_day("2026-06", day) and not from_day("2026-06", day)
    assert from_day("2026-07", day) and from_day("2026-06-12", day)
