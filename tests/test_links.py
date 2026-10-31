"""Stage 07: relations between BVwG, VwGH and VfGH decisions - the matches, the checks and their limits."""

import json

from jsonschema import Draft202012Validator

from asylum_corpus.common import CONFIG, SCHEMAS
from asylum_corpus.links import court_links as cl

CFG = cl.load_config(CONFIG / "links" / "court_links.yaml")
SCHEMA = Draft202012Validator(json.loads((SCHEMAS / "relation.schema.json").read_text(encoding="utf-8")))


def doc(doc_id: str, paragraphs: list[str], types=None) -> dict:
    text, paras, pos = "", [], 0
    for k, p in enumerate(paragraphs):
        paras.append({"id": f"{doc_id}#p:{k + 1:04d}", "start": pos, "end": pos + len(p), "text": p})
        text += p + "\n"
        pos += len(p) + 1
    types = types or ["subject"] + ["reasoning"] * (len(paragraphs) - 1)
    sections = [{"type": t, "start": p["start"], "end": p["end"] + 1, "level": 1} for t, p in zip(types, paras)]
    return {"document_id": doc_id, "full_text": text, "paragraphs": paras, "sections": sections}


def test_keys_and_patterns():
    assert cl.key("W150 2159588-2/7E") == "2159588-2" and cl.key("L508 1422401-1") == "1422401-1"
    p = cl.identifier_pattern("L508 1422401-1/9E")
    assert p.search("GZ. L508 1422401-1/9E,") and p.search("ZL508 1422401-1/9E")        # a glued letter in RIS
    assert not p.search("L508 1422401-1/95E") and not p.search("1L508 1422401-1/9E")
    assert cl.identifier_pattern("E76/2014").search("Beschluss vom 3. März 2014, E 76/2014-5, abgetreten")
    d = cl.date_pattern("2014-02-04")
    assert d.search("vom 4. Februar 2014,") and d.search("vom 04.02.2014") and not d.search("14. Februar 2014")
    assert cl.date_pattern("2015-01-08").search("vom 8. Jänner 2015")


def test_reviews_prefer_the_copy_of_the_named_appellant_and_check_dates():
    bvwg = {rid: {"ris_id": rid, "document_id": f"ris:bvwg:{rid}", "case_number": cn, "decision_date": dt,
                  "pronouncement_date": None, "own_evidence": None}
            for rid, cn, dt in [("BVWGT_20200525_W212_2206963_1_00", "W212 2206963-1", "2020-05-25"),
                                ("BVWGT_20200525_W212_2206969_1_00", "W212 2206969-1", "2020-05-25"),
                                ("BVWGT_20190214_W212_2206969_1_00", "W212 2206969-1", "2019-02-14")]}
    hd = cl.Doc(doc("ris:vfgh:JFT_X", ["gegen das Erkenntnis des Bundesverwaltungsgerichtes vom 25. Mai 2020, Zlen "
                                        "W212 2206969-1/28E, W212 2206963-1/27E, wird abgewiesen."]))
    match = {"method": "bvwg_case_number", "mention": "challenged", "confidence": "high",
             "bvwg_case_number": "W212 2206969-1/28E", "bvwg_ris_id": "BVWGT_20200525_W212_2206963_1_00",
             "bvwg_candidates": list(bvwg), "how": "Ordnungsnummer"}
    row = {"decision_date": "2021-04-21", "matches": [match, {**match, "mention": "cited"},
                                                       {**match, "bvwg_ris_id": None, "confidence": None}]}
    rels, unresolved, counts = cl.reviews_of(row, hd, bvwg, CFG)
    assert [r["target_id"] for r in rels] == ["ris:bvwg:BVWGT_20200525_W212_2206969_1_00"]   # the named appellant
    r = rels[0]
    assert r["match_type"] == "exact_identifier" and not list(SCHEMA.iter_errors(r))
    ev = r["evidence"][0]
    assert hd.text[ev["start"]:ev["end"]] == ev["quote"] and "2206969-1/28E" in ev["quote"]
    assert counts["cited, not a review"] == 1 and unresolved[0]["reason"] == "ambiguous"
    late = {"decision_date": "2019-01-01", "matches": [match]}                 # the review predates the decision
    rels, unresolved, _ = cl.reviews_of(late, hd, bvwg, CFG)
    assert not rels and unresolved[0]["reason"] == "identified decision dated after the review"


def test_same_case_and_transfers():
    bvwg = {}
    for rid, dt in [("A", "2017-01-10"), ("B", "2019-03-01"), ("C", "2021-05-05")]:
        d = doc(f"ris:bvwg:{rid}", [f"W150 2159588-2/{rid}E", "text"])
        bvwg[rid] = {**cl.bvwg_info({**d, "ris_id": rid, "case_number": "W150 2159588-2", "decision_date": dt,
                                    "pronouncement_date": None}, CFG), "case_number": "L519 2159588-2" if rid == "C"
                     else "W150 2159588-2"}
    rels = cl.same_case(bvwg)
    assert [(r["source_id"], r["target_id"]) for r in rels] == [("ris:bvwg:A", "ris:bvwg:B"),
                                                                ("ris:bvwg:B", "ris:bvwg:C")]   # chamber change kept
    assert all(not list(SCHEMA.iter_errors(r)) for r in rels)
    vw = cl.Doc(doc("ris:vwgh:JWT_Y", ["Revision", "Die Beschwerde wurde mit Beschluss vom 3. März 2014, E 76/2014-5, "
                                                    "dem Verwaltungsgerichtshof abgetreten."]))
    t, unresolved, _ = cl.transfers_of({"transfers_named": ["E76/2014", "E99/2015"]}, vw, {}, CFG)
    assert [(r["source_id"], r["target_id"]) for r in t] == [("case:vfgh:E76/2014", "ris:vwgh:JWT_Y")]
    assert not list(SCHEMA.iter_errors(t[0])) and not unresolved


def info(rid: str, case_number: str, decision_date: str, text: str = "", pronounced: str | None = None,
         operative: str = "Die Beschwerde wird als unbegründet abgewiesen.") -> dict:
    """A BVwG document's link info as stage 07 reads it."""
    d = doc(f"ris:bvwg:{rid}", [text or case_number, "IM NAMEN DER REPUBLIK!", operative],
            ["subject", "subject", "operative_part"])
    numbers = [text.split("\n")[0]] if "/" in (text or "").split("\n")[0] and "-" in text.split("/")[0] else []
    return cl.bvwg_info({**d, "ris_id": rid, "case_number": case_number, "decision_date": decision_date,
                         "pronouncement_date": pronounced, "document_numbers": numbers}, CFG)


def test_transfer_chronology():
    """The VfGH number must fit the year of the BVwG decision that the VwGH decision reviews."""
    vw = cl.Doc(doc("ris:vwgh:JWT_Z", ["Revision", "Der Verfassungsgerichtshof lehnte mit Beschluss vom 17. September "
                                                    "2015, E 1095/2015-7, die Behandlung ab und trat sie ab.",
                                       "Mit Beschluss vom 3. März 2017, E 300/2017, abgetreten."]))
    reviewed = [info("BVWGT_20170110_W1_1_00", "W150 2159588-2", "2017-01-10")]
    t, unresolved, _ = cl.transfers_of({"transfers_named": ["E1095/2015", "E300/2017"]}, vw, {}, CFG, reviewed)
    assert [r["source_id"] for r in t] == ["case:vfgh:E300/2017"]        # 2017 fits a decision of January 2017
    assert unresolved[0]["vfgh_case_number"] == "E1095/2015" and unresolved[0]["reason"].startswith("transfer:")
    t, unresolved, _ = cl.transfers_of({"transfers_named": ["E1095/2015"]}, vw, {}, CFG, [])
    assert len(t) == 1 and not unresolved                                 # no reviewed decision: not checked


def test_identifier_only_needs_the_candidates_own_ordnungsnummer():
    """A cited document number that is not the only candidate's own names another document."""
    assert cl.ordnungsnummer("L526 1430850-3/10Z") == "10Z" and cl.ordnungsnummer("L526 1430850-3") is None
    z = info("BVWGT_20181130_L526_1430850_3_00", "L526 1430850-3", "2018-11-30", "L526 1430850-3/10Z")
    typo = info("BVWGT_20170912_L526_2169962_1_00", "L526 2169962-1", "2017-09-12", "L526 2169962/2Z\nBESCHLUSS")
    none = info("BVWGT_20150127_G307_2016516_1_00", "G307 2016516-1", "2015-01-27", "Beschluss")
    assert cl.own_ordnungsnummer(z, "1430850-3") == "10Z" and cl.own_ordnungsnummer(typo, "2169962-1") == "2Z"
    assert cl.own_ordnungsnummer(none, "2016516-1") is None
    bvwg = {i["ris_id"]: i for i in (z, typo, none)}
    hd = cl.Doc(doc("ris:vwgh:JWT_A", ["Revision gegen das Erkenntnis des Bundesverwaltungsgerichts vom 8. März 2023, "
                                       "L507 1430850-3/61E, L526 2169962-1/50E, G307 2016516-1/2E, L526 1430850-3/10Z"]))

    def match(cited, rid):
        return {"method": "bvwg_case_number", "mention": "challenged", "confidence": "low", "bvwg_case_number": cited,
                "bvwg_ris_id": rid, "bvwg_candidates": [rid], "how": "only candidate"}
    row = {"decision_date": "2023-07-28", "matches": [match("L507 1430850-3/61E", z["ris_id"]),
                                                      match("L526 2169962-1/50E", typo["ris_id"]),
                                                      match("G307 2016516-1/2E", none["ris_id"])]}
    rels, unresolved, _ = cl.reviews_of(row, hd, bvwg, CFG)
    assert [r["target_id"] for r in rels] == [none["document_id"]]        # own number unknown: kept
    assert [u["candidates"] for u in unresolved] == [[z["ris_id"]], [typo["ris_id"]]]
    assert all(u["reason"].startswith("identifier only:") for u in unresolved)
    rels, unresolved, _ = cl.reviews_of({"decision_date": "2023-07-28",
                                         "matches": [match("L526 1430850-3/10Z", z["ris_id"])]}, hd, bvwg, CFG)
    assert len(rels) == 1 and not unresolved and rels[0]["match_type"] == "identifier_only"
    # the oral decision's number next to its pronouncement date: the written copy, identifier_and_date
    oral = info("BVWGT_20180718_L519_2175862_1_00", "L519 2175862-1", "2018-07-18", "L519 2175862-1/14E",
                pronounced="2018-02-05")
    od = cl.Doc(doc("ris:vwgh:JWT_B", ["Revision gegen das Erkenntnis des Bundesverwaltungsgerichts vom 5. Februar "
                                       "2018, L519 2175862-1/8Z, betreffend Angelegenheiten nach dem AsylG 2005"]))
    rels, unresolved, _ = cl.reviews_of({"decision_date": "2018-12-12", "matches": [
        match("L519 2175862-1/8Z", oral["ris_id"])]}, od, {oral["ris_id"]: oral}, CFG)
    assert not unresolved and rels[0]["match_type"] == "identifier_and_date"
    assert rels[0]["rule"] == "connected:bvwg_case_number:low+pronouncement_date" and not list(SCHEMA.iter_errors(rels[0]))


def vfgh(paragraphs: list[str]) -> cl.Doc:
    return cl.Doc(doc("ris:vfgh:JFT_20200626_20E01689_00", ["Spruch"] + paragraphs, ["operative_part"] +
                      ["reasoning"] * len(paragraphs)))


def df_match(rid: str, bfa: str) -> dict:
    return {"method": "date_fallback", "mention": "challenged", "confidence": "medium", "bvwg_case_number": None,
            "bvwg_ris_id": rid, "bvwg_candidates": [rid],
            "how": f"BVwG decision date X next to 'Bundesverwaltungsgericht' and BFA Bescheid date {bfa} in the text; "
                   "verify manually"}


def test_date_fallback_checks():
    """The date_fallback checks 1-5 and the evidence of both dates."""
    t = info("BVWGT_20200225_I421_2204275_1_00", "I421 2204275-1", "2020-02-25", pronounced="2020-01-15")
    bvwg = {t["ris_id"]: t}
    good = ["2. Mit Bescheid des Bundesamtes für Fremdenwesen und Asyl (im Folgenden: BFA) vom 12. Juli 2018 wurde der "
            "Antrag abgewiesen.",
            "3. Das Bundesverwaltungsgericht wies die Beschwerde nach einer Verhandlung am 15. Jänner 2020 mit dem am "
            "selben Tag mündlich verkündeten Erkenntnis ab."]
    row = {"decision_date": "2020-06-26", "case_numbers": ["E1689/2020"], "matches": [df_match(t["ris_id"],
                                                                                               "2018-07-12")]}
    rels, unresolved, _ = cl.reviews_of(row, vfgh(good), bvwg, CFG)
    assert len(rels) == 1 and not unresolved and rels[0]["match_type"] == "date_fallback"
    assert [("15. Jänner 2020" in e["quote"], "12. Juli 2018" in e["quote"]) for e in rels[0]["evidence"]] == \
        [(True, False), (False, True)] and not list(SCHEMA.iter_errors(rels[0]))

    def reason(paragraphs, case_numbers=("E1689/2020",), target=t, bfa="2018-07-12"):
        r = {**row, "case_numbers": list(case_numbers), "matches": [df_match(target["ris_id"], bfa)]}
        _, unresolved, _ = cl.reviews_of(r, vfgh(paragraphs), {target["ris_id"]: target}, CFG)
        return unresolved[0]["reason"] if unresolved else None
    assert reason(good, ["G212/2020"]).endswith("(register not E)")
    cited = ["Bescheid vom 12. Juli 2018.", "Das Bundesverwaltungsgericht hat die Rechtsprechung (vgl VfGH "
             "15.1.2020, E 3714/2019) verkannt; mit Beschluss vom 25. Februar 2020 gab der Verfassungsgerichtshof dem "
             "Antrag auf Verfahrenshilfe statt."]
    assert reason(cited).endswith("not given as a BVwG decision's date")
    no_bescheid = [good[1], "Am 12. Juli 2018 stellte er beim BFA einen Antrag."]
    assert reason(no_bescheid).endswith("not given as a Bescheid date")
    assert reason(good, ["E1689/2022"]).endswith("year does not fit the decision")
    assert reason(good, ["E4424/2019 ua"]) is None      # registered before the written copy: not checked
    won = info("BVWGT_20200225_I421_2204275_1_00", "I421 2204275-1", "2020-02-25", pronounced="2020-01-15",
               operative="Der Beschwerde wird stattgegeben und XXXX gemäß § 3 Abs. 1 AsylG 2005 der Status des "
                         "Asylberechtigten zuerkannt.")
    assert won["granted_in_full"] and not t["granted_in_full"]
    assert reason(good, target=won).endswith("granted asylum in full")


def test_date_fallback_keeps_the_latest_decision_of_a_file():
    """Check 6: the earlier decision of the same file is the history; another person's decision is kept only where
    several complaints are decided together."""
    first = info("BVWGT_20190107_L502_2117938_1_00", "L502 2117938-1", "2019-01-07")
    later = info("BVWGT_20191028_L502_2117938_1_00", "L502 2117938-1", "2019-10-28")
    other = info("BVWGT_20190107_W257_2147344_1_00", "W257 2147344-1", "2019-01-07")
    bvwg = {i["ris_id"]: i for i in (first, later, other)}
    text = ["Mit Bescheid vom 16. November 2015 wies das BFA den Antrag ab.",
            "Die Beschwerde wies das Bundesverwaltungsgericht mit Erkenntnis vom 7. Jänner 2019 ab. Diese Entscheidung "
            "hob der Verfassungsgerichtshof auf.",
            "Das Bundesverwaltungsgericht wies die Beschwerde mit nunmehr angefochtenem Erkenntnis vom 28. Oktober 2019 "
            "ab."]
    row = {"decision_date": "2020-02-25", "case_numbers": ["E4470/2019"],
           "matches": [df_match(i["ris_id"], "2015-11-16") for i in (first, later, other)]}
    rels, unresolved, counts = cl.reviews_of(row, vfgh(text), bvwg, CFG)
    assert [r["target_id"] for r in rels] == [later["document_id"]]      # one complaint: one decision
    assert sorted(u["candidates"][0] for u in unresolved) == sorted([first["ris_id"], other["ris_id"]])
    rels, unresolved, counts = cl.reviews_of({**row, "case_numbers": ["E4470/2019 ua"]}, vfgh(text), bvwg, CFG)
    assert sorted(r["target_id"] for r in rels) == sorted([later["document_id"], other["document_id"]])
    assert [u["candidates"] for u in unresolved] == [[first["ris_id"]]] and counts["relation date_fallback"] == 2
    assert cl.one_complaint(["E4470/2019"]) and not cl.one_complaint(["E1944-1948/2025"])
    assert cl.one_complaint(["E4624/2024 (E4624/2024-15)"]) and not cl.one_complaint(["E3608/2021 ua"])


def test_joined_decisions_link_their_copies():
    """An ambiguous match whose candidates are the copies of one joined decision: by case number, the copy whose own
    number is named with its date next to it; by date, where the VfGH decides several complaints together, every copy -
    each passing the date_fallback checks."""
    numbers = ["L519 2210155-1/12E", "L519 2210154-1/10E"]
    a = {**info("BVWGT_20210505_L519_2210155_1_00", "L519 2210155-1", "2021-05-05", pronounced="2021-03-08"),
         "document_numbers": numbers}
    b = {**info("BVWGT_20210505_L519_2210154_1_00", "L519 2210154-1", "2021-05-05", pronounced="2021-03-08"),
         "document_numbers": numbers}
    other = {**info("BVWGT_20190101_L519_2210155_1_00", "L519 2210155-1", "2019-01-01"), "document_numbers": []}
    bvwg = {i["ris_id"]: i for i in (a, b, other)}
    assert cl.copies_of_one_decision([a, b]) and not cl.copies_of_one_decision([a, other])
    vw = cl.Doc(doc("ris:vwgh:JWT_C", ["Revision gegen das Erkenntnis des Bundesverwaltungsgerichts vom 8. März 2021, "
                                       "Zlen. 1. L519 2210155-1/8Z, 2. L519 2210154-1/6Z, betreffend AsylG 2005"]))

    def amb(cited, cands):
        return {"method": "bvwg_case_number", "mention": "challenged", "confidence": None, "bvwg_case_number": cited,
                "bvwg_ris_id": None, "bvwg_candidates": cands, "how": "several candidates"}
    row = {"decision_date": "2021-07-20", "matches": [amb("L519 2210155-1/8Z", [a["ris_id"], b["ris_id"]])]}
    rels, unresolved, _ = cl.reviews_of(row, vw, bvwg, CFG)
    assert [r["target_id"] for r in rels] == [a["document_id"]] and not unresolved
    assert rels[0]["match_type"] == "identifier_and_date" and not list(SCHEMA.iter_errors(rels[0]))
    assert rels[0]["rule"] == "connected:bvwg_case_number:ambiguous+own_copy_and_date"
    undated = cl.Doc(doc("ris:vwgh:JWT_D", ["Revision gegen das Erkenntnis, L519 2210155-1/8Z, betreffend AsylG"]))
    _, unresolved, _ = cl.reviews_of(row, undated, bvwg, CFG)                    # no date next to the number
    assert unresolved[0]["reason"] == "ambiguous"
    row = {"decision_date": "2021-07-20", "matches": [amb("L519 2210155-1/8Z", [a["ris_id"], other["ris_id"]])]}
    assert cl.reviews_of(row, vw, bvwg, CFG)[1][0]["reason"] == "ambiguous"     # different decisions
    # by date, several complaints decided together
    text = ["Mit Bescheiden des BFA vom 3. April 2020 wurden die Anträge abgewiesen.",
            "Das Bundesverwaltungsgericht wies die Beschwerden mit Erkenntnis vom 5. Mai 2021 ab."]
    by_date = {"method": "date_fallback", "mention": "challenged", "confidence": None, "bvwg_case_number": None,
               "bvwg_ris_id": None, "bvwg_candidates": [a["ris_id"], b["ris_id"]],
               "how": "2 candidates decided on 2021-05-05 share a BFA Bescheid date with the text"}
    copies = {k: {**v, "bfa_dates": ["2020-04-03"]} for k, v in bvwg.items()}
    row = {"decision_date": "2021-09-29", "case_numbers": ["E2530/2021 ua"], "matches": [by_date]}
    rels, unresolved, _ = cl.reviews_of(row, vfgh(text), copies, CFG)
    assert sorted(r["target_id"] for r in rels) == sorted([a["document_id"], b["document_id"]]) and not unresolved
    assert all(r["match_type"] == "date_fallback" and len(r["evidence"]) == 2 for r in rels)
    rels, unresolved, _ = cl.reviews_of({**row, "case_numbers": ["E2530/2021"]}, vfgh(text), copies, CFG)
    assert not rels and unresolved[0]["reason"] == "ambiguous"                   # one complaint: which copy?
    rels, unresolved, _ = cl.reviews_of(row, vfgh(text[1:]), copies, CFG)         # check 3 applies to each copy
    assert not rels and [u["reason"].endswith("not given as a Bescheid date") for u in unresolved] == [True, True]
