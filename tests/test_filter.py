"""Stage 02: the verdict rules and flags of config/filtering/asylum_filter.yaml, and determinism."""

import gzip
import json

from asylum_corpus.filtering.rules import Candidate, load_rules
from asylum_corpus.filtering.stage import RULES, run_filter

RULE_SET = load_rules(RULES)
AVG68 = ("avg_68_international_protection",)


def classify(norms, decision_type="Erkenntnis", date="2020-05-04", keywords="", indicators=("asylg_3",), **kw):
    return RULE_SET.classify(Candidate("BVWGT_20200504_W123_2212345_1_00", decision_type, date, tuple(norms), keywords,
                                       tuple(indicators), **kw))


def flags(norms, decision_type="Erkenntnis", **kw):
    return classify(norms, decision_type, **kw)["flags"]


def test_included_norm_and_mixed_case_rule():
    r = classify(["AsylG 2005 §3 Abs1", "AsylG 2005 §10 Abs1 Z3", "AsylG 2005 §57", "FPG §52 Abs2 Z2", "FPG §53"])
    assert (r["verdict"], r["group"], r["rule"]) == ("include", "main", "included_norm")
    assert r["flags"] == ["mixed:return_decision", "mixed:humanitarian_residence", "mixed:entry_ban"]


def test_section_numbers_are_exact():
    assert classify(["AsylG 2005 §34 Abs2"])["rule"] == "included_norm"
    assert classify(["AsylG 2005 §57", "AsylG 2005 §10 Abs1"])["rule"] == "default"   # § 5 does not match § 57
    assert classify(["AsylG 2005 §55"])["verdict"] == "exclude"


def test_detention_is_excluded_even_with_an_asylum_norm():
    r = classify(["AsylG 2005 §5 Abs1", "FPG §76 Abs2 Z2", "BFA-VG §22a Abs1"])
    assert (r["verdict"], r["rule"], r["group"]) == ("exclude", "detention", None)


def test_new_law_is_a_flag_and_its_norms_are_included():
    r = classify(["AsylG 2005 §3 Abs1"], date="2026-06-12")
    assert (r["group"], r["rule"]) == ("main", "included_norm") and "decided_from_2026_06_12" in r["flags"]
    assert "decided_from_2026_06_12" not in flags(["AsylG 2005 §3 Abs1"], date="2026-06-11")
    for norm in ("StatusVO 2024/1347 Art13", "AsylVfVO 2024/1348 Art54", "AMMVO 2024/1351 Art43", "NAG §46a Abs1"):
        assert classify([norm], date="2026-07-01", indicators=("statusvo",))["group"] == "main", norm


def test_other_matters_are_excluded():
    # a repeated application for a toleration card; with an AsylG norm it is one for protection
    assert classify(["AVG §68 Abs1", "FPG §46a Abs1 Z3"], indicators=AVG68)["rule"] == "other_matter"
    assert classify(["AVG §68 Abs1", "FPG §46a", "AsylG 2005 §57"], indicators=AVG68)["rule"] == \
        "subsequent_applications_avg68"
    # return only after a § 75 Abs. 20 referral; with § 3 it stays
    assert classify(["AsylG 2005 §34", "AsylG 2005 §75 Abs20"], date="2014-02-06")["rule"] == "other_matter"
    assert classify(["AsylG 2005 §34", "AsylG 2005 §3", "AsylG 2005 §75 Abs20"])["rule"] == "included_norm"
    # travel documents of protection holders (StatusVO Art. 25); with a norm on the status it stays
    r = classify(["FPG §88 Abs2a", "StatusVO 2024/1347 Art25 Abs2"], date="2026-07-01")
    assert r["rule"] == "other_matter"
    assert r["evidence"][0] == "alternative: content of protection only (StatusVO Art. 20 ff.)"
    assert classify(["StatusVO 2024/1347 Art13", "StatusVO 2024/1347 Art25"], date="2026-07-01")["group"] == "main"


def test_separate_group_of_repeated_applications():
    assert classify(["AVG §68 Abs1"], indicators=AVG68)["group"] == "subsequent_applications_avg68"
    assert classify(["AVG §68 Abs1", "AsylG 2005 §3"], indicators=("asylg_3",) + AVG68)["group"] == "main"


def test_interim_flag_and_its_limits():
    assert "interim_procedural" in flags(["AsylG 2005 §5", "BFA-VG §17"], "Beschluss")
    assert "interim_procedural" in flags(["AsylG 2005 §3"], "Beschluss", keywords="aufschiebende Wirkung")
    # a final-decision signal blocks the flag, except for suspensive effect
    assert "interim_procedural" not in flags(["AsylG 2005 §3", "AVG §62 Abs4"], "Beschluss",
                                             keywords="Berichtigung Verfahrenseinstellung")
    assert "interim_procedural" in flags(["BFA-VG §18 Abs5", "VwGVG §28 Abs1"], "Beschluss")
    # an Erkenntnis on suspensive effect is flagged without merits norms only
    assert "interim_procedural" in flags(["BFA-VG §18 Abs5", "BFA-VG §21 Abs7", "VwGVG §24"])
    assert "interim_procedural" not in flags(["AsylG 2005 §3 Abs1", "AsylG 2005 §8 Abs1", "BFA-VG §18 Abs5"])
    # a discontinuation after a withdrawal is final
    assert "interim_procedural" not in flags(["AsylG 2005 §3", "VwGVG §28 Abs1", "VwGVG §31 Abs1"], "Beschluss",
                                             keywords="Einstellung Zurückziehung")
    # the flag never changes the verdict
    assert classify(["AsylG 2005 §5", "BFA-VG §17"], "Beschluss")["verdict"] == "include"


def test_duplicates_odd_ids_and_the_default_rule():
    assert "possible_duplicate" in flags(["AsylG 2005 §3"], shares_case_number_and_date=True)
    odd = RULE_SET.classify(Candidate("BVWGT_00070902_W153_2001835_1_00", "Erkenntnis", "2014-07-31",
                                      ("AsylG 2005 §3",), "", ("asylg_3",)))
    assert "data_quality:ris_id" in odd["flags"]
    assert RULE_SET.verdict_rules[-1].id == "default"


def test_same_inputs_give_identical_files(tmp_path):
    rows, records = [], []
    for i, (norms, kind) in enumerate([(["AsylG 2005 §3 Abs1"], "Erkenntnis"),
                                       (["AsylG 2005 §5", "BFA-VG §17"], "Beschluss"),
                                       (["FPG §76", "AsylG 2005 §5"], "Erkenntnis")]):
        rid = f"BVWGT_2020050{i}_W123_221234{i}_1_00"
        rows.append({"ris_id": rid, "decision_type": kind, "decision_date": f"2020-05-0{i}", "norms": norms,
                     "case_numbers": [f"W123 221234{i}-1"], "indicators": ["asylg_3"], "document_url": "u"})
        records.append({"ris_id": rid, "record": {"Data": {"Metadaten": {"Judikatur": {"Schlagworte": "x<br/>y"}}}}})
    cand = tmp_path / "candidates"
    cand.mkdir()
    (cand / "inventory.jsonl").write_text("".join(json.dumps(r) + "\n" for r in reversed(rows)), encoding="utf-8")
    with gzip.open(cand / "records.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.writelines(json.dumps(r) + "\n" for r in records)
    outputs = []
    for run in ("a", "b"):
        run_filter(cand, tmp_path / run)
        outputs.append({f.name: f.read_bytes() for f in sorted((tmp_path / run).iterdir())})
    assert outputs[0] == outputs[1]
    lines = [json.loads(line) for line in outputs[0]["filter_decisions.jsonl"].decode().splitlines()]
    assert [d["ris_id"] for d in lines] == sorted(r["ris_id"] for r in rows)
    assert [d["verdict"] for d in lines] == ["include", "include", "exclude"]
