"""Stage 08: typical wordings of the BVwG operative parts, outcome units, and the VwGH / VfGH outcomes."""

import json

from jsonschema import Draft202012Validator

from asylum_corpus.citations.statutes import load_acts
from asylum_corpus.common import CONFIG, SCHEMAS
from asylum_corpus.outcomes.annotate import document_outcomes
from asylum_corpus.outcomes.higher import HigherRules, outcome_of
from asylum_corpus.outcomes.rules import load_rules

ACTS = load_acts(CONFIG / "citations" / "acts.yaml")
RULES = load_rules()
DOC_ID = "ris:bvwg:BVWGT_20200101_W123_2212345_1_00"


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")))


def doc(operative: str, reasoning: str = "Begründung: …", intro: str = "zu Recht erkannt:") -> dict:
    """A one-appellant document: subject (ending with the introductory sentence), operative part, reasoning."""
    parts, sections, paragraphs, pos = [("subject", ["Das Bundesverwaltungsgericht hat …", intro]),
                                        ("operative_part", operative.split("\n")),
                                        ("reasoning", reasoning.split("\n"))], [], [], 0
    text = ""
    for k, (typ, lines) in enumerate(parts, 1):
        sid = f"{DOC_ID}#s:{k:02d}"
        start = pos
        for line in lines:
            paragraphs.append({"id": f"{DOC_ID}#p:{len(paragraphs) + 1:04d}", "section_id": sid, "start": pos,
                               "end": pos + len(line), "text": line})
            text += line + "\n"
            pos += len(line) + 1
        sections.append({"id": sid, "type": typ, "start": start, "end": pos - 1, "level": 1})
    return {"document_id": DOC_ID, "ris_id": DOC_ID.split(":")[-1], "court": "BVwG", "full_text": text,
            "sections": sections, "paragraphs": paragraphs}


def value(operative: str, **kw) -> dict:
    anns = document_outcomes(doc(operative, **kw), ACTS, RULES)
    assert len(anns) == 1
    return anns[0][0]["value"]


def issues(v: dict) -> dict:
    return {r["issue"]: r["dispositions"] for r in v["issues"]}


def test_dismissed_as_a_whole():
    v = value("A) Die Beschwerde wird als unbegründet abgewiesen.\nB) Die Revision ist gemäß Art. 133 Abs. 4 B-VG nicht "
              "zulässig.")
    assert (v["outcome"], v["outcome_subtype"], v["protection"], v["revision"]) == \
        ("dismissed", None, "none", "inadmissible")
    assert issues(v) == {"whole_decision": ["dismissed"]}
    assert v["decision_form"] == "judgment"


def test_partially_upheld_subsidiary_protection():
    v = value("A) I. Die Beschwerde wird gemäß § 3 Abs. 1 AsylG 2005 als unbegründet abgewiesen.\n"
              "II. Der Beschwerde wird stattgegeben und XXXX gemäß § 8 Abs. 1 AsylG 2005 der Status des subsidiär "
              "Schutzberechtigten zuerkannt. 2. Gemäß § 8 Abs. 4 AsylG 2005 wird eine befristete Aufenthaltsberechtigung "
              "erteilt.")
    assert (v["outcome"], v["protection"]) == ("partially_upheld", "subsidiary_protection")
    assert issues(v)["asylum"] == ["dismissed"] and "granted" in issues(v)["subsidiary_protection"]


def test_asylum_granted():
    v = value("A) Der Beschwerde wird stattgegeben und XXXX gemäß § 3 Abs. 1 AsylG 2005 der Status des "
              "Asylberechtigten zuerkannt. Gemäß § 3 Abs. 5 AsylG 2005 wird festgestellt, dass XXXX damit kraft Gesetzes "
              "die Flüchtlingseigenschaft zukommt.")
    assert (v["outcome"], v["protection"]) == ("upheld", "asylum")


def test_cassation_is_a_remand():
    v = value("A) In Erledigung der Beschwerde wird der angefochtene Bescheid behoben und die Angelegenheit gemäß § 28 "
              "Abs. 3 zweiter Satz VwGVG zur Erlassung eines neuen Bescheides an das Bundesamt zurückverwiesen.")
    assert (v["outcome"], v["protection"]) == ("remanded", "remanded")
    v = value("A) Der Beschwerde wird gemäß § 21 Abs. 3 zweiter Satz BFA-VG stattgegeben und der bekämpfte Bescheid "
              "behoben.")
    assert (v["outcome"], v["protection"]) == ("remanded", "remanded")       # no "zurückverwiesen"


def test_remand_of_the_return_decision_only():
    v = value("A) Die Beschwerde wird gemäß §§ 3 Abs. 1 und 8 Abs. 1 AsylG 2005 als unbegründet abgewiesen.\n"
              "Gemäß § 75 Abs. 20 AsylG 2005 wird das Verfahren hinsichtlich der Rückkehrentscheidung an das Bundesamt "
              "zurückverwiesen.")
    assert (v["outcome"], v["protection"]) == ("partially_upheld", "none")
    assert issues(v)["return_decision"] == ["remanded"]


def test_other_with_subtypes():
    v = value("A) Das Verfahren wird wegen Zurückziehung der Beschwerde gemäß §§ 28 Abs. 1, 31 Abs. 1 VwGVG eingestellt.",
              intro="beschlossen:")
    assert (v["outcome"], v["outcome_subtype"], v["protection"]) == ("other", "discontinued", "not_decided")
    assert v["decision_form"] == "order"
    v = value("Der Beschwerde wird die aufschiebende Wirkung nicht zuerkannt.", intro="beschlossen:")
    assert (v["outcome"], v["outcome_subtype"]) == ("other", "interim")
    assert value("Der Antrag auf Verfahrenshilfe wird abgewiesen.")["outcome_subtype"] == "procedural"
    assert value("A) Vorbringen ohne Entscheidung.")["outcome_subtype"] == "unrecognized"


def test_letter_spacing_and_revision_without_full_stop():
    v = value("A) Die Beschwerden werden als unbegründet a b g e w i e s e n B) Die Revision ist gemäß Art. 133 Abs. 4 "
              "B-VG nicht zulässig.")
    assert (v["outcome"], v["revision"]) == ("dismissed", "inadmissible")
    assert v["dispositions"][0]["text"].endswith("a b g e w i e s e n")      # the span is the text as printed


def test_quoted_correction_is_not_read():
    v = value("A) Die Beschwerde wird mit der Maßgabe als unbegründet abgewiesen, dass der Spruch zu lauten hat: "
              "„Der Status wird nicht zuerkannt“.")
    assert v["dispositions"][0]["types"] == ["dismissed", "modified"]
    assert v["outcome"] == "dismissed"


def test_numbered_points_resolved_from_the_reasoning():
    reasoning = ("I. Verfahrensgang: Mit Bescheid wies das Bundesamt den Antrag bezüglich des Status des "
                 "Asylberechtigten ab (Spruchpunkt I.), erteilte keinen Aufenthaltstitel (Spruchpunkt II.), erließ eine "
                 "Rückkehrentscheidung (Spruchpunkt III.) und ein Einreiseverbot (Spruchpunkt IV.).")
    d = doc("A) I. Die Beschwerde gegen Spruchpunkt I. wird als unbegründet abgewiesen.\n"
            "II. Die Spruchpunkte II. bis IV. werden ersatzlos behoben.", reasoning=reasoning)
    v = document_outcomes(d, ACTS, RULES)[0][0]["value"]
    assert issues(v) == {"asylum": ["dismissed"], "residence_title": ["annulled"], "return_decision": ["annulled"],
                         "entry_ban": ["annulled"]}
    points = [p for x in v["dispositions"] for p in x["points"]]
    assert [p["point"] for p in points] == ["I", "II", "III", "IV"]
    assert [d["full_text"][p["start"]:p["end"]] for p in points] == \
        ["Status des Asylberechtigten", "Aufenthaltstitel", "Rückkehrentscheidung", "Einreiseverbot"]
    v = value("A) Die Beschwerde gegen Spruchpunkt III. wird abgewiesen.")       # nothing in the reasoning
    assert issues(v) == {"point:III": ["dismissed"]} and v["problems"] == ["unresolved_point"]


def test_spans_and_schema():
    d = doc("A) I. Die Beschwerde wird gemäß § 3 Abs. 1 AsylG 2005 als unbegründet abgewiesen.\n"
            "II. Der Beschwerde gegen Spruchpunkt IV. wird stattgegeben.\nB) Die Revision ist nicht zulässig.")
    for ann, sections in document_outcomes(d, ACTS, RULES):
        assert not list(validator("outcome").iter_errors(ann))
        assert not list(validator("annotation").iter_errors(ann))
        by_id = {s["id"]: s for s in sections}
        for x in ann["value"]["dispositions"]:
            section = by_id[x["operative_part_id"]]
            assert section["start"] <= x["start"] < x["end"] <= section["end"]
            assert x["text"] == d["full_text"][x["start"]:x["end"]]


def test_schema_lists_the_rules_ids():
    schema = json.loads((SCHEMAS / "outcome.schema.json").read_text(encoding="utf-8"))
    value_props = schema["properties"]["value"]["properties"]
    types = value_props["dispositions"]["items"]["properties"]["types"]["items"]["enum"]
    assert types == [x.id for x in RULES.dispositions]
    issue_enum = value_props["dispositions"]["items"]["properties"]["issues"]["items"]["properties"]["issue"]["enum"]
    assert issue_enum == [i.id for i in RULES.issues] + ["whole_decision"]


# ---------------------------------------------------------------------------------------------------------------------
# One appellant's judgment and order form one outcome unit


def multi(parts: list[tuple[str, str]], reasoning: str = "Begründung: …", case_numbers=("W123 2212345-1",)) -> dict:
    """A document of several (subject, operative part) pairs and a reasoning."""
    sections, paragraphs, text, pos = [], [], "", 0
    blocks = [(typ, body) for subject, op in parts for typ, body in (("subject", subject), ("operative_part", op))]
    for k, (typ, body) in enumerate(blocks + [("reasoning", reasoning)], 1):
        sid = f"{DOC_ID}#s:{k:02d}"
        start = pos
        for line in body.split("\n"):
            paragraphs.append({"id": f"{DOC_ID}#p:{len(paragraphs) + 1:04d}", "section_id": sid, "start": pos,
                               "end": pos + len(line), "text": line})
            text += line + "\n"
            pos += len(line) + 1
        sections.append({"id": sid, "type": typ, "start": start, "end": pos - 1, "level": 1})
    return {"document_id": DOC_ID, "ris_id": DOC_ID.split(":")[-1], "court": "BVwG", "full_text": text,
            "sections": sections, "paragraphs": paragraphs, "case_numbers": list(case_numbers)}


HEAD = ("Das Bundesverwaltungsgericht hat durch den Richter Dr. X als Einzelrichter über die Beschwerde von XXXX, "
        "geb. XXXX, StA. Afghanistan, gegen den Bescheid des Bundesamtes für Fremdenwesen und Asyl vom {date}, Zl. "
        "XXXX, ")


def test_unit_continuation_judgment_and_order():
    # "I. zu Recht erkannt" (asylum dismissed) and "II. folgenden Beschluss gefasst" (II, III remanded)
    reasoning = ("Das Bundesamt wies den Antrag bezüglich des Status des Asylberechtigten (Spruchpunkt I.) und des "
                 "Status des subsidiär Schutzberechtigten ab (Spruchpunkt II.) und erließ eine Rückkehrentscheidung "
                 "(Spruchpunkt III.).")
    d = multi(reasoning=reasoning, parts=[(HEAD.format(date="16.07.2015") + "\nA)\nI. zu Recht erkannt:",
                "Die Beschwerde gegen Spruchpunkt I. des angefochtenen Bescheides wird gemäß § 3 Abs. 1 AsylG 2005 als "
                "unbegründet abgewiesen."),
               ("II. folgenden Beschluss gefasst:",
                "In Erledigung der Beschwerde wird der angefochtene Bescheid betreffend die Spruchpunkte II. und III. "
                "gemäß § 28 Abs. 3 2. Satz VwGVG behoben und die Angelegenheit zur Erlassung eines neuen Bescheides an "
                "das Bundesamt zurückverwiesen.")])
    anns = document_outcomes(d, ACTS, RULES)
    assert len(anns) == 1
    v = anns[0][0]["value"]
    assert (v["outcome"], v["protection"], v["decision_form"]) == ("partially_upheld", "remanded", "judgment_and_order")
    assert [p["joined_by"] for p in v["parts"]] == [None, "continuation"]
    assert [(p["outcome"], p["protection"]) for p in v["parts"]] == [("dismissed", "none"), ("remanded", "remanded")]
    assert [x["operative_part_id"] for x in v["dispositions"]] == v["operative_part_ids"]
    assert not list(validator("outcome").iter_errors(anns[0][0]))


def test_unit_repeated_head_same_appellant_and_decision():
    # one person, the same challenged decision, an order (discontinued) and a judgment (permanent): one unit
    d = multi([("I. " + HEAD.format(date="22.01.2018") + "beschlossen:",
                "A) Das Verfahren wird wegen Zurückziehung der Beschwerde gemäß § 28 Abs. 1 VwGVG eingestellt."),
               ("II. " + HEAD.format(date="22.01.2018") + "zu Recht erkannt:",
                "A) Der Beschwerde wird stattgegeben und festgestellt, dass eine Rückkehrentscheidung gemäß § 9 BFA-VG "
                "auf Dauer für unzulässig erklärt wird.")])
    anns = document_outcomes(d, ACTS, RULES)
    assert len(anns) == 1
    v = anns[0][0]["value"]
    assert (v["outcome"], v["protection"]) == ("upheld", "residence_right")
    assert [p["joined_by"] for p in v["parts"]] == [None, "repeated_head"]


def test_units_stay_apart():
    # two challenged decisions
    d = multi([(HEAD.format(date="12.06.2024") + "zu Recht erkannt:", "A) Die Beschwerde wird abgewiesen."),
               ("BESCHLUSS " + HEAD.format(date="21.02.2024") + "beschlossen:",
                "A) Die Beschwerde wird als verspätet zurückgewiesen.")])
    assert [a["value"]["outcome"] for a, _ in document_outcomes(d, ACTS, RULES)] == ["dismissed", "rejected"]
    # a head repeated per appellant ("1.) ...", "2.) ..."): several persons, kept apart
    d = multi([("1.) " + HEAD.format(date="23.06.2017") + "zu Recht erkannt:", "A) Die Beschwerde wird abgewiesen."),
               ("BESCHLUSS 2.) " + HEAD.format(date="23.06.2017") + "beschlossen:",
                "A) Der Antrag, der Beschwerde die aufschiebende Wirkung zuzuerkennen, wird zurückgewiesen.")],
              case_numbers=("W123 2212345-1", "W123 2212346-1"))
    assert len(document_outcomes(d, ACTS, RULES)) == 2
    # a later appellant's head left inside the earlier part (a stage 03 boundary error): kept apart
    d = multi([(HEAD.format(date="01.02.2018") + "beschlossen:",
                "I. Das Verfahren wird eingestellt. 2. Das Bundesverwaltungsgericht beschließt durch den Richter Dr. X "
                "über die Beschwerde von XXXX"),
               ("und erkennt zu Recht:", "II. Der Beschwerde wird stattgegeben.")])
    assert len(document_outcomes(d, ACTS, RULES)) == 2


def test_unit_all_appellants_listed_in_both_heads():
    # several appellants, both heads list all of them
    head = ("Das Bundesverwaltungsgericht hat durch die Richterin Mag. X über die Beschwerden von 1.) XXXX, StA. "
            "Russische Föderation, 2.) XXXX, 3.) mj. XXXX, gegen die Bescheide des Bundesasylamtes vom 11.05.2011, ")
    d = multi([("I. " + head + "beschlossen:", "A) Die Verfahren werden wegen Zurückziehung der Beschwerden gegen "
                "Spruchpunkt I. und II. der bekämpften Bescheide gemäß §§ 28 Abs. 1, 31 Abs. 1 VwGVG eingestellt."),
               ("II. " + head + "zu Recht erkannt:", "A) Gemäß § 28 Abs. 2 VwGVG und § 9 Abs. 2 BFA-VG wird "
                "festgestellt, dass eine Rückkehrentscheidung auf Dauer unzulässig ist.")],
              case_numbers=("W112 1415543-1", "W112 1419474-1", "W112 1419476-1"))
    anns = document_outcomes(d, ACTS, RULES)
    assert len(anns) == 1 and (anns[0][0]["value"]["outcome"], anns[0][0]["value"]["protection"]) == \
        ("upheld", "residence_right")


def test_disposition_wordings():
    v = value("A) Der Beschwerde wird stattgegeben, eine Rückkehrentscheidung gemäß § 9 BFA-VG auf Dauer für "
              "unzulässig erklärt.")
    assert "permanent" in v["dispositions"][0]["types"] and v["protection"] == "residence_right"
    v = value("Dem Antrag auf Wiederaufnahme wird gemäß § 32 Abs. 1 Z 2 VwGVG nicht stattgegeben.")
    assert v["dispositions"][0]["types"] == ["dismissed"]
    v = value("A) Der Beschwerde gegen Spruchpunkt II. wird mit der Maßgabe stattgegeben, dass im genannten "
              "Spruchpunkt das Wort „nicht“ ersatzlos gestrichen wird.")
    assert "annulled" not in v["dispositions"][0]["types"]


def test_hyphen_ranges_of_points():
    reasoning = ("Das Bundesamt wies den Antrag bezüglich des Status des Asylberechtigten ab (Spruchpunkt I.), "
                 "erteilte keinen Aufenthaltstitel (Spruchpunkt II.) und erließ eine Rückkehrentscheidung "
                 "(Spruchpunkt III.).")
    for printed in ("I-III.", "I.-III.", "I. - III.", "I. – III."):
        v = value(f"A) Die Beschwerde gegen die Spruchpunkte {printed} wird abgewiesen.", reasoning=reasoning)
        assert [p["point"] for p in v["dispositions"][0]["points"]] == ["I", "II", "III"], printed


def test_quoted_wording():
    # the quotation runs to its closing mark, across sentences; the court's next point after '."' is read
    v = value("A) Die Beschwerde wird mit der Maßgabe als unbegründet abgewiesen, dass der Spruch zu lauten hat:\n"
              "„I. Der Antrag wird abgewiesen.\nII. Eine „Aufenthaltsberechtigung besonderer Schutz“ wird nicht "
              "erteilt.“\nII. Der Beschwerde gegen Spruchpunkt III. wird stattgegeben und festgestellt, dass eine "
              "Rückkehrentscheidung auf Dauer unzulässig ist.")
    assert [x["types"] for x in v["dispositions"]] == [["dismissed", "modified"], ["upheld", "permanent", "declared"]]
    v = value('A) I. Die Beschwerde wird mit der Maßgabe abgewiesen, dass Spruchpunkt I. zu lauten hat wie folgt:\n'
              '"Ihr Antrag wird abgewiesen."\nII. Der Beschwerde wird stattgegeben und festgestellt, dass eine '
              'Rückkehrentscheidung auf Dauer unzulässig ist.')
    assert len(v["dispositions"]) == 2 and "permanent" in v["dispositions"][1]["types"]
    # "hat zu lauten" is the court's modification of a point
    v = value("A) Der jeweilige Spruchpunkt VII. hat zu lauten: Die Frist für die freiwillige Ausreise beträgt 14 Tage.")
    assert v["dispositions"][0]["types"] == ["modified"]


def test_point_resolution():
    reasoning = ("Mit Bescheid wies das Bundesamt den Antrag bezüglich der Zuerkennung des Status der/des "
                 "Asylberechtigten gemäß § 3 Abs. 1 AsylG 2005 ab (Spruchpunkt I.), erließ gemäß § 10 Abs. 1 Z 3 AsylG "
                 "2005 eine Rückkehrentscheidung (Spruchpunkt II.) und stellte fest, dass die Abschiebung gemäß § 46 "
                 "FPG nach Afghanistan zulässig sei (Spruchpunkt III.). Gemäß § 55 Abs. 1 bis 3 FPG betrage die Frist "
                 "für die freiwillige Ausreise zwei Wochen ab Rechtskraft der Rückkehrentscheidung (Spruchpunkt IV.).")
    v = value("A) Die Spruchpunkte I. bis IV. werden ersatzlos behoben.", reasoning=reasoning)
    assert [(p["point"], p["issue"]) for p in v["dispositions"][0]["points"]] == \
        [("I", "asylum"), ("II", "return_decision"), ("III", "deportation"), ("IV", "departure_deadline")]


def test_issue_wording():
    v = value("A) Der Beschwerde wird gemäß § 28 Abs. 2 VwGVG iVm § 16 Abs. 1 VwGVG stattgegeben und der angefochtene "
              "Bescheid infolge Unzuständigkeit der Behörde ersatzlos aufgehoben.")
    assert issues(v) == {"whole_decision": ["upheld", "annulled"]}
    v = value("A) Der Beschwerde gegen Spruchpunkt VII. wird stattgegeben. Im Übrigen wird die Beschwerde als "
              "unbegründet abgewiesen.")
    assert issues(v)["remaining_points"] == ["dismissed"]
    v = value('Der Antrag vom 30.01.2020, "das Asylverfahren des XXXX" wiederaufzunehmen, wird zurückgewiesen.',
              intro="beschlossen:")
    assert (v["outcome"], v["outcome_subtype"]) == ("other", "procedural")
    v = value("Gemäß Art. 24 Abs. 1 StatusVO iVm § 3 Abs. 1 AsylG 2005 wird XXXX ein Aufenthaltstitel für die Dauer "
              "von drei Jahren erteilt.")
    assert "residence_title" not in issues(v)


def test_subtype_from_a_resolved_point():
    reasoning = "Mit Spruchpunkt I. wies das Bundesamt den Antrag ab, die aufschiebende Wirkung zuzuerkennen (Spruchpunkt II.)."
    v = value("A) Der Beschwerde gegen Spruchpunkt II. des bekämpften Bescheides wird mit der Maßgabe Folge gegeben, "
              "dass das Wort „nicht“ ersatzlos gestrichen wird.", reasoning=reasoning, intro="beschlossen:")
    assert v["dispositions"][0]["points"][0]["issue"] == "suspensive_effect"
    assert (v["outcome"], v["outcome_subtype"]) == ("other", "interim")


def test_quoted_wording_of_a_changed_decision_is_read():
    # the court upholds and changes the decision: the wording after "wie folgt" is its new decision
    v = value("A) Der Beschwerde wird Folge gegeben und der angefochtene Bescheid wie folgt abgeändert: I. XXXX, geb. "
              "XXXX, StA. Afghanistan, wird gemäß § 3 Abs 1 AsylG 2005 der Status des Asylberechtigten zuerkannt.")
    assert (v["outcome"], v["protection"]) == ("upheld", "asylum")


def test_a_correction_decides_nothing_else():
    v = value("A) Das Erkenntnis des Bundesverwaltungsgerichtes vom 13.06.2019, mit welchem der Beschwerde stattgegeben "
              "und dem Beschwerdeführer gemäß § 3 Abs 1 AsylG 2005 der Status des Asylberechtigten zuerkannt wurde, wird "
              "gemäß § 62 Abs. 4 AVG dahingehend berichtigt, dass das Geburtsdatum richtig XXXX zu lauten hat.",
              intro="beschlossen:")
    assert v["dispositions"][0]["types"] == ["modified", "corrected"]
    assert (v["outcome"], v["outcome_subtype"]) == ("other", "procedural")
    v = value("D) Die Spruchpunkte IV. bis VI. des Bescheids vom 21. Februar 2018, berichtigt durch den Bescheid vom "
              "9. März 2018, werden ersatzlos behoben.")
    assert "annulled" in v["dispositions"][0]["types"]


def test_appeal_rejected_as_late_is_no_suspensive_effect():
    v = value("A) Die Beschwerde wird gemäß § 16 Abs. 1 BFA-VG als verspätet zurückgewiesen.")
    assert (v["outcome"], issues(v)) == ("rejected", {"whole_decision": ["rejected"]})
    v = value("Der Beschwerde wird gemäß § 18 Abs. 5 BFA-VG die aufschiebende Wirkung nicht zuerkannt.",
              intro="beschlossen:")
    assert (v["outcome"], v["outcome_subtype"]) == ("other", "interim")


# ---------------------------------------------------------------------------------------------------------------------
# VwGH and VfGH

HIGHER = HigherRules.load(CONFIG / "outcomes" / "higher_courts.yaml")


def higher_doc(court: str, subject: str, operative: str, ris_id: str = "JWT_TEST") -> dict:
    text = subject + "\n" + operative + "\nBegründung ..."
    s1, s2 = len(subject), len(subject) + 1 + len(operative)
    return {"document_id": f"ris:{court.lower()}:{ris_id}", "ris_id": ris_id, "court": court, "full_text": text,
            "sections": [{"type": "subject", "start": 0, "end": s1, "level": 1},
                         {"type": "operative_part", "start": s1 + 1, "end": s2, "level": 1},
                         {"type": "reasoning", "start": s2 + 1, "end": len(text), "level": 1}]}


def higher(court, subject, operative, ris_id="JWT_TEST"):
    ann = outcome_of(higher_doc(court, subject, operative, ris_id), HIGHER)
    assert not list(validator("higher_court_outcome").iter_errors(ann))
    return ann["value"]


def test_vwgh_forms():
    v = higher("VwGH", "in der Revisionssache des X Y, vertreten durch Dr. Z, gegen das Erkenntnis des BVwG",
              "Die Revision wird zurückgewiesen.")
    assert (v["outcome"], v["revision_party"]) == ("rejected", "party")
    v = higher("VwGH", "über die Revision des Bundesamtes für Fremdenwesen und Asyl gegen das Erkenntnis des BVwG "
                      "(mitbeteiligte Partei: A, vertreten durch Rechtsanwalt B)",
              "Das angefochtene Erkenntnis wird wegen Rechtswidrigkeit seines Inhaltes aufgehoben.")
    assert (v["outcome"], v["grounds"], v["revision_party"], v["partial"]) == ("annulled", ["content"], "authority",
                                                                               False)
    v = higher("VwGH", "über die Revision des A", "Das angefochtene Erkenntnis wird im Anfechtungsumfang (Abweisung der "
              "Beschwerde hinsichtlich der Nichtzuerkennung des Status des subsidiär Schutzberechtigten), soweit damit "
              "die Beschwerde abgewiesen wurde, wegen Rechtswidrigkeit infolge Verletzung von Verfahrensvorschriften "
              "aufgehoben. Im Übrigen wird die Revision zurückgewiesen. Der Bund hat dem Revisionswerber Aufwendungen "
              "in der Höhe von € 1.106,40 binnen zwei Wochen bei sonstiger Exekution zu ersetzen.")
    assert (v["outcome"], v["grounds"], v["partial"]) == ("annulled", ["procedure"], True)
    assert all("Aufwendungen" not in d["text"] for d in v["dispositions"])           # costs are not read
    assert higher("VwGH", "über den Antrag des A, der gegen das Erkenntnis ... Revision, die aufschiebende Wirkung "
                         "zuzuerkennen", "Gemäß § 30 Abs. 2 VwGG wird dem Antrag nicht stattgegeben.")["outcome"] == \
        "interim"
    assert higher("VwGH", "über den Antrag des A auf aufschiebende Wirkung",
                 "Dem Antrag wird nicht stattgegeben.")["outcome"] == "interim"
    assert higher("VwGH", "in der Revisionssache des A", "Das Revisionsverfahren wird bis zur Vorabentscheidung durch "
                 "den Gerichtshof der Europäischen Union in der Rechtssache C-1/24 ausgesetzt.")["outcome"] == "interim"
    assert higher("VwGH", "über die Revision des A", "Die Revision wird als gegenstandslos geworden erklärt und das "
                 "Verfahren eingestellt.")["outcome"] == "discontinued"
    assert higher("VwGH", "über den Antrag des A auf Bewilligung der Verfahrenshilfe",
                 "Der Antrag auf Bewilligung der Verfahrenshilfe wird abgewiesen.")["outcome"] == "legal_aid"


def test_vfgh_forms():
    v = higher("VfGH", "", "I. Der Beschwerdeführer ist durch das angefochtene Erkenntnis, soweit damit seine "
              "Beschwerde gegen die Nichtzuerkennung des Status des subsidiär Schutzberechtigten abgewiesen wird, im "
              "verfassungsgesetzlich gewährleisteten Recht auf Gleichbehandlung von Fremden untereinander verletzt. "
              "Das Erkenntnis wird insoweit aufgehoben. II. Im Übrigen wird die Behandlung der Beschwerde abgelehnt und "
              "die Beschwerde insoweit dem Verwaltungsgerichtshof zur Entscheidung abgetreten.")
    assert (v["outcome"], v["partial"], v["transferred"], v["revision_party"]) == ("annulled", True, True, "party")
    v = higher("VfGH", "", "Die Behandlung der Beschwerde wird abgelehnt. Die Beschwerde wird dem "
                          "Verwaltungsgerichtshof zur Entscheidung abgetreten.")
    assert (v["outcome"], v["transferred"]) == ("declined", True)
    assert higher("VfGH", "", "Der Beschwerdeführer ist durch das angefochtene Erkenntnis weder in einem "
                             "verfassungsgesetzlich gewährleisteten Recht noch wegen Anwendung einer rechtswidrigen "
                             "generellen Norm in seinen Rechten verletzt worden. Die Beschwerde wird abgewiesen.")[
        "outcome"] == "dismissed"
    assert higher("VfGH", "", "Die Wortfolge im Gesetz wird als verfassungswidrig aufgehoben. Der Bundeskanzler ist zur "
                             "unverzüglichen Kundmachung verpflichtet.")["outcome"] == "norm_review"


def test_registers_fix_the_outcome():
    sub = "über den Fristsetzungsantrag des A gegen das Bundesverwaltungsgericht wegen Verletzung der Entscheidungspflicht"
    for op in ("Das Verfahren wird eingestellt.", "Der Fristsetzungsantrag wird als gegenstandslos geworden erklärt "
               "und das Verfahren eingestellt.", "Der Fristsetzungsantrag wird zurückgewiesen."):
        v = higher("VwGH", sub, op, ris_id="JWT_2021180004_20210505F00")
        assert (v["outcome"], v["register"]) == ("deadline_application", "F")
    for op in ("Der Antrag wird abgewiesen.", "Der Antrag wird zurückgewiesen.", "Das Verfahren wird eingestellt."):
        assert higher("VfGH", "", op, ris_id="JFT_20160308_15G00440_00")["outcome"] == "norm_review"
    assert higher("VfGH", "", "Der Antrag wird zurückgewiesen.", ris_id="JFT_20170617_16V00067_00")["outcome"] == \
        "norm_review"
    assert higher("VfGH", "", "Die Beschwerde wird abgewiesen.", ris_id="JFT_20180627_18E00013_00")["outcome"] == \
        "dismissed"


def test_cjeu_reference_is_interim():
    v = higher("VwGH", "in der Rechtssache betreffend die Revision des A", "Dem Gerichtshof der Europäischen Union "
              "werden nach Art. 267 AEUV folgende Fragen zur Vorabentscheidung vorgelegt: 1. Führt die Versäumung der "
              "Frist zu einem Zuständigkeitsübergang? 2. Kann der ersuchte Mitgliedstaat zustimmen, wenn er das "
              "Aufnahmegesuch zuvor fristgerecht abgelehnt hat? Ist das Gesuch zurückgewiesen?")
    assert v["outcome"] == "interim" and [d["types"] for d in v["dispositions"]] == [["referral"]]


def test_sentences_not_cut_at_dates_or_labels():
    v = higher("VwGH", "über die Revision des E M", "Das angefochtene Erkenntnis wird im Umfang seiner Anfechtung und "
              "sohin insoweit, als damit die Beschwerde gegen die Spruchpunkte II. bis IV. des Bescheides vom 28. Juli "
              "2017 abgewiesen wurde, wegen Rechtswidrigkeit infolge Verletzung von Verfahrensvorschriften aufgehoben.")
    assert (v["outcome"], v["partial"], v["grounds"]) == ("annulled", True, ["procedure"])
    v = higher("VwGH", "über die Revision des Bundesamtes für Fremdenwesen und Asyl", "Das Erkenntnis wird im "
              "angefochtenen Umfang, sohin hinsichtlich seiner Spruchpunkte A. III. und A. IV., wegen Rechtswidrigkeit "
              "seines Inhaltes aufgehoben.")
    assert (v["outcome"], v["partial"], v["grounds"]) == ("annulled", True, ["content"])
    v = higher("VwGH", "über die Revision des A", "1.) Die Revision wird zurückgewiesen. 2.) Der Antrag auf "
              "Wiedereinsetzung in den vorigen Stand wird zurückgewiesen.")
    assert v["outcome"] == "rejected" and len(v["dispositions"]) == 2


def test_annulment_wording():
    v = higher("VwGH", "über die Revision des A H", "Im Übrigen wird der angefochtene Spruchpunkt A.I. wegen "
              "Rechtswidrigkeit seines Inhaltes aufgehoben.")
    assert (v["outcome"], v["partial"]) == ("annulled", True)
    v = higher("VwGH", "über die Revision des B A", "Das angefochtene Erkenntnis wird im angefochtenen Umfang, soweit "
              "damit die Beschwerde gegen die Erlassung einer Rückkehrentscheidung und die Aberkennung der aufschiebenden "
              "Wirkung abgewiesen wurde, wegen Verletzung von Verfahrensvorschriften aufgehoben.")
    assert (v["outcome"], v["grounds"]) == ("annulled", ["procedure"])
    v = higher("VfGH", "", "Das angefochtene Erkenntnis wird daher insoweit und soweit damit die Kostenersatzanträge "
              "der Parteien abgewiesen werden, aufgehoben.")
    assert v["outcome"] == "annulled"                    # an annulling sentence naming costs is not ancillary
    assert higher("VwGH", "über die Revision des A", "Rechtwidrigkeit seines Inhaltes: Das angefochtene Erkenntnis "
                 "wird wegen Rechtwidrigkeit seines Inhaltes aufgehoben.")["grounds"] == ["content"]


def test_bare_application_anonymised_party_election():
    v = higher("VwGH", "in der Rechtssache des Antrages des S A, den Beschluss des Bundesverwaltungsgerichts gemäß § 30 "
              "Abs. 3 VwGG abzuändern und der Revision die aufschiebende Wirkung zuzuerkennen", "Der Antrag wird "
              "abgewiesen.")
    assert v["outcome"] == "interim"
    assert higher("VfGH", "", "Der Anfechtung wird nicht stattgegeben.",
                 ris_id="JFT_20161015_16W_IV00001_00")["outcome"] == "dismissed"
    assert higher("VwGH", "in der Revisionssache *****", "Das Verfahren wird eingestellt. Der Bund hat den "
                 "revisionswerbenden Parteien Aufwendungen in der Höhe von € 553,20 zu ersetzen.")["revision_party"] \
        == "party"
    assert higher("VwGH", "in der Rechtssache des Vorlageantrags des J N", "Das Verfahren wird eingestellt.")["revision_party"] == \
        "party"


def test_higher_rules_and_schema_agree():
    schema = json.loads((SCHEMAS / "higher_court_outcome.schema.json").read_text(encoding="utf-8"))
    outcomes = schema["properties"]["value"]["properties"]["outcome"]["enum"]
    used = {o for o, _ in HIGHER.order} | {v for c in HIGHER.registers.values() for v in c.values()}
    assert used <= set(outcomes)
    types = schema["properties"]["value"]["properties"]["dispositions"]["items"]["properties"]["types"]["items"]["enum"]
    assert {t for t, _ in HIGHER.dispositions} <= set(types)
