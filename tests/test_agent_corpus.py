"""Stage 09: the split keeps every procedure in one of train, dev and test; the inputs show no cited norm."""

from asylum_corpus.agent_corpus import NORM, masked, split_of


def test_split_by_date_and_a_procedure_in_one_split():
    decisions = [("a", "2019-05-02", ["2100001"]), ("b", "2024-03-01", ["2100001"]),   # b: its procedure is in train
                 ("c", "2024-04-01", ["2200002"]), ("d", "2025-09-01", ["2200002"]),   # d: its procedure is in dev
                 ("e", "2025-08-01", ["2300003"]), ("f", "2025-10-01", ["2300003"]),   # e, f: one procedure in test
                 ("g", "2023-12-31", [])]
    assert split_of(decisions, "2024-01-01", "2025-07-01") == {
        "a": "train", "b": "seen_procedure", "c": "dev", "d": "seen_procedure", "e": "test", "f": "test",
        "g": "train"}


def test_cited_norms_become_norm():
    paragraphs = [{"id": "p1", "start": 10, "text": "gemäß § 3 Abs. 1 AsylG 2005 und BGBl. I Nr. 100/2005"},
                  {"id": "p2", "start": 70, "text": "ohne Zitat"}]
    citations = [{"paragraph_id": "p1", "start": 16, "end": 37},          # § 3 Abs. 1 AsylG 2005
                 {"paragraph_id": "p1", "start": 20, "end": 25},          # overlaps the one before: skipped
                 {"paragraph_id": "p1", "start": 42, "end": 62}]          # BGBl. I Nr. 100/2005
    assert masked(paragraphs, citations) == [f"gemäß {NORM} und {NORM}", "ohne Zitat"]
