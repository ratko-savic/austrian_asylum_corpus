"""Stage 01: query rules, the RIS usage rules of the client, download and matching. Offline: HTTP is mocked."""

import importlib.util
import json
from argparse import Namespace
from datetime import date, datetime

import pytest
import requests

from asylum_corpus.common import CONFIG, ROOT
from asylum_corpus.ris import client as ris_client
from asylum_corpus.ris import connected
from asylum_corpus.ris.client import VIENNA, RisClient, WindowClosed, easter_sunday, in_bulk_window, summarize
from asylum_corpus.ris.download import Downloader
from asylum_corpus.ris.inventory import build_inventory
from asylum_corpus.ris.queries import UNION_QUERY_ID, Query, Slice, load_query_config, norm_matches
from asylum_corpus.ris.text import BVWG_GZ_RE, bvwg_full, bvwg_key, vfgh_gzs_of

QUERIES = load_query_config(CONFIG / "ris" / "queries.yaml")
QUERY = Query("q", "Bvwg", (("Norm", "AsylG 2005 §3"),), ("asylg_3",))


def record(ris_id: str, norms: list[str], day: str = "2016-12-30", doc_type: str = "Text") -> dict:
    return {"Data": {
        "Metadaten": {
            "Technisch": {"ID": ris_id, "Applikation": "Bvwg"},
            "Allgemein": {"Geaendert": "2017-01-20"},
            "Judikatur": {"Dokumenttyp": doc_type, "Geschaeftszahl": {"item": "W170 2126120-1"},
                          "Normen": {"item": norms}, "Entscheidungsdatum": day,
                          "Bvwg": {"Entscheidungsart": "Erkenntnis"}}},
        "Dokumentliste": {"ContentReference": {"ContentType": "MainDocument", "Urls": {"ContentUrl": [
            {"DataType": "Xml", "Url": f"https://ogd.ris.bka.gv.at/{ris_id}.xml"}]}}}}}


class FakeResponse:
    def __init__(self, payload=None, content: bytes = b"", status_code: int = 200):
        self.payload, self.content, self.status_code, self.headers = payload, content, status_code, {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


@pytest.fixture
def no_wait(monkeypatch):
    """Always inside the bulk window, and sleeping is recorded instead of done."""
    sleeps = []
    monkeypatch.setattr(ris_client, "in_bulk_window", lambda moment: True)
    monkeypatch.setattr(ris_client.time, "sleep", sleeps.append)
    return sleeps


def test_norm_matching_is_exact_per_section():
    assert norm_matches("AsylG 2005 §3", "AsylG 2005 §3 Abs1")
    assert norm_matches("AsylG 2005 §3", "AsylG 2005 § 3 Abs1")
    assert not norm_matches("AsylG 2005 §3", "AsylG 2005 §34")
    assert not norm_matches("AsylG 2005 §4", "AsylG 2005 §4a")
    assert norm_matches("StatusVO", "StatusVO 2024/1347 Art13")


def test_one_union_query_plus_one_query_per_params_indicator():
    ids = [q.id for q in QUERIES.queries]
    assert ids == [UNION_QUERY_ID, "avg_68_international_protection"]
    assert "AsylG 2005 §3 ODER AsylG 2005 §8" in QUERIES.queries[0].params_dict["Norm"]


def test_attribution_and_partitions():
    norms = ["AsylG 2005 §3 Abs1", "AsylG 2005 §57", "AVG §68 Abs1"]
    assert QUERIES.attribute(norms, {UNION_QUERY_ID}) == ["asylg_3"]
    assert QUERIES.attribute(norms, {UNION_QUERY_ID, "avg_68_international_protection"}) == \
        ["asylg_3", "avg_68_international_protection"]
    assert QUERIES.attribute(["AsylG 2005 §57", "AsylG 2005 §10"], {UNION_QUERY_ID}) == []
    assert QUERIES.partition("2026-06-12", ["AsylG 2005 §3 Abs1"], ["asylg_3"]) == "new_law_2026"
    assert QUERIES.partition("2026-05-20", ["StatusVO 2024/1347 Art13"], ["statusvo"]) == "new_law_2026"
    assert QUERIES.partition("2020-03-01", ["AVG §68 Abs1"], ["avg_68_international_protection"]) == \
        "subsequent_applications_avg68"
    assert QUERIES.partition("2020-03-01", ["AsylG 2005 §3 Abs1", "AVG §68 Abs1"],
                             ["asylg_3", "avg_68_international_protection"]) == "main"


def test_slices_per_year_and_their_splitting():
    slices = QUERIES.initial_slices()
    assert len(slices) == 13 * len(QUERIES.queries)
    assert (min(s.start for s in slices), max(s.end for s in slices)) == (date(2014, 1, 1), date(2026, 9, 30))
    months = Slice(QUERY, date(2024, 1, 1), date(2024, 12, 31)).split()
    assert len(months) == 12 and months[1].end == date(2024, 2, 29)
    halves = months[0].split()
    assert [(h.start.day, h.end.day) for h in halves] == [(1, 16), (17, 31)]
    assert len(halves[0].split()) == 16
    assert not Slice(QUERY, date(2024, 1, 1), date(2024, 1, 1)).splittable


def test_summary_of_a_ris_record():
    s = summarize(record("BVWGT_20161230_W170_2126120_1_00", ["AsylG 2005 §3 Abs1"]))
    assert s["case_numbers"] == ["W170 2126120-1"] and s["decision_type"] == "Erkenntnis"
    assert s["content_urls"] == {"xml": "https://ogd.ris.bka.gv.at/BVWGT_20161230_W170_2126120_1_00.xml"}
    assert s["document_url"].startswith("https://www.ris.bka.gv.at/Dokument.wxe?Abfrage=Bvwg&")


def test_bulk_window_office_hours_weekends_and_holidays():
    def at(s):
        return datetime.fromisoformat(s).replace(tzinfo=VIENNA)

    assert easter_sunday(2026) == date(2026, 4, 5)
    assert in_bulk_window(at("2026-09-30 20:00")) and in_bulk_window(at("2026-10-01 04:59"))
    assert not in_bulk_window(at("2026-10-01 05:00")) and not in_bulk_window(at("2026-10-02 19:59"))
    assert in_bulk_window(at("2026-10-03 12:00"))   # Saturday
    assert in_bulk_window(at("2026-10-26 12:00"))   # national holiday
    assert in_bulk_window(at("2026-06-04 12:00"))   # Corpus Christi


def test_client_refuses_more_than_half_a_request_per_second():
    with pytest.raises(ValueError):
        RisClient(min_pause=1.9)


def test_client_pauses_between_requests(monkeypatch, no_wait):
    now = iter([100.0, 100.5, 101.0, 101.5])
    monkeypatch.setattr(ris_client.time, "monotonic", lambda: next(now))
    c = RisClient(contact="someone@example.org")
    calls = []
    monkeypatch.setattr(c.session, "get", lambda url, params, timeout: calls.append(url) or FakeResponse())
    c.get("https://example.org/1")
    c.get("https://example.org/2")
    assert calls == ["https://example.org/1", "https://example.org/2"] and c.requests_made == 2
    assert no_wait == [pytest.approx(1.5)]  # 2 s after the end of the first request
    assert "someone@example.org" in c.session.headers["User-Agent"]


def test_client_retries_a_failed_request_after_the_pause(monkeypatch, no_wait):
    now = iter([100.0, 100.5, 101.0, 101.5])
    monkeypatch.setattr(ris_client.time, "monotonic", lambda: next(now))
    c = RisClient()
    responses = iter([FakeResponse(status_code=503), FakeResponse(content=b"ok")])
    monkeypatch.setattr(c.session, "get", lambda url, params, timeout: next(responses))
    assert c.get("https://example.org").content == b"ok" and c.requests_made == 2
    assert no_wait == [pytest.approx(3.5)]  # 2 s after the end of the first attempt, plus a backoff of 2 s


def test_client_retries_a_connection_lost_while_reading(monkeypatch, no_wait):
    c = RisClient()
    outcomes = iter([requests.exceptions.ChunkedEncodingError("lost"), FakeResponse(content=b"ok")])

    def flaky(url, params, timeout):
        got = next(outcomes)
        if isinstance(got, Exception):
            raise got
        return got

    monkeypatch.setattr(c.session, "get", flaky)
    assert c.get("https://example.org").content == b"ok" and c.requests_made == 2


def test_outside_the_window_the_client_waits_or_stops(monkeypatch):
    sleeps = []
    monkeypatch.setattr(ris_client, "in_bulk_window", lambda moment: False)
    monkeypatch.setattr(ris_client.time, "sleep", sleeps.append)
    waiting = RisClient()
    monkeypatch.setattr(waiting.session, "get", lambda url, params, timeout: FakeResponse())
    waiting.get("https://example.org")
    assert sleeps and sleeps[0] > 0
    with pytest.raises(WindowClosed):
        RisClient(stop_when_window_closes=True).get("https://example.org")
    assert not issubclass(WindowClosed, Exception)  # never recorded as a failed download


def test_search_reads_every_page(monkeypatch, no_wait):
    records = [record(f"BVWGT_201612{i:02d}_W170_2126120_1_00", ["AsylG 2005 §3"]) for i in range(1, 26)]

    def fake_get(url, params, timeout):
        page = records[(params["Seitennummer"] - 1) * 10:params["Seitennummer"] * 10]
        return FakeResponse({"OgdSearchResult": {"OgdDocumentResults": {
            "Hits": {"#text": str(len(records))}, "OgdDocumentReference": page}}})

    c = RisClient(page_size=10)
    monkeypatch.setattr(c.session, "get", fake_get)
    result = c.search("Bvwg", {"Norm": "AsylG 2005 §3"})
    assert (result.hits, result.pages, len(result.records), result.complete) == (25, 3, 25, True)
    assert c.requests_made == 3


class FakeClient:
    """search_page/count/get without network: one record per day, so that slices with more hits are split."""
    stop_when_window_closes = False

    def __init__(self, xml: dict[str, bytes] | None = None):
        self.requests_made, self.xml = 0, xml or {}

    def records(self, params):
        start = date.fromisoformat(params["EntscheidungsdatumVon"])
        end = date.fromisoformat(params["EntscheidungsdatumBis"])
        days = [date.fromordinal(o) for o in range(start.toordinal(), end.toordinal() + 1)]
        return [record(f"BVWGT_{d:%Y%m%d}_W170_2126120_1_00", ["AsylG 2005 §3 Abs1"], d.isoformat()) for d in days]

    def search_page(self, application, params, page=1, page_size=None):
        self.requests_made += 1
        return ris_client.SearchPage(len(self.records(params)), self.records(params))

    def search(self, application, params, first_page=None):
        recs = self.records(params)
        return ris_client.SearchResult(application, params, len(recs), recs, 1)

    def count(self, application, params):
        return 0

    def window_closed(self):
        return False

    def get(self, url):
        return FakeResponse(content=self.xml.get(url, b"not xml"))


def test_inventory_splits_large_slices_and_resumes(tmp_path):
    from asylum_corpus.ris.queries import QueryConfig, Indicator
    config = QueryConfig("Bvwg", date(2020, 1, 1), date(2020, 2, 10), 20,
                         [Indicator("asylg_3", "refugee_status", norm="AsylG 2005 §3")], {"refugee_status": "x"})
    report = build_inventory(FakeClient(), config, tmp_path, log=lambda *a: None)
    assert report["totals"]["candidates"] == 41 and report["totals"]["split_slices"] == 2
    rows = [json.loads(line) for line in (tmp_path / "inventory.jsonl").open()]
    assert rows[0]["categories"] == ["refugee_status"] and rows[0]["partition"] == "main"
    again = build_inventory(FakeClient(), config, tmp_path, log=lambda *a: None)
    assert again["totals"]["requests_this_run"] == 0  # stored slices are not queried again


def test_download_stores_xml_and_records_failures(tmp_path):
    from asylum_corpus.ris.queries import QueryConfig, Indicator
    config = QueryConfig("Bvwg", date(2020, 1, 1), date(2020, 1, 2), 20,
                         [Indicator("asylg_3", "refugee_status", norm="AsylG 2005 §3")], {"refugee_status": "x"})
    candidates, raw = tmp_path / "candidates", tmp_path / "raw"
    build_inventory(FakeClient(), config, candidates, log=lambda *a: None)
    good = "https://ogd.ris.bka.gv.at/BVWGT_20200101_W170_2126120_1_00.xml"
    client = FakeClient({good: b'<risdok xmlns="http://www.bka.gv.at"><nutzdaten/></risdok>'})
    downloader = Downloader(client, candidates, raw, log=lambda *a: None)
    rows = downloader.rows()
    assert downloader.write_metadata(rows) == 2
    assert dict(downloader.download(rows)) == {"ok": 1, "failed": 1}
    report = downloader.rebuild_manifest(rows)
    assert report["total"] == {"ok": 1, "failed": 1}
    assert (raw / "bvwg" / "main" / "2020" / "BVWGT_20200101_W170_2126120_1_00.xml").exists()
    assert downloader.pending(rows)[0]["ris_id"] == "BVWGT_20200102_W170_2126120_1_00"


def test_bulk_runs_need_the_announcement():
    spec = importlib.util.spec_from_file_location("download_script", ROOT / "scripts" / "01_download.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    settings = {"small_test_limit": 50, "contact": None, "bulk_announced_on": None}
    announced = {**settings, "contact": "a@b.org", "bulk_announced_on": "2026-10-01"}
    bulk = Namespace(command="download", limit=None, allow_office_hours=False)
    assert not script.network_allowed(bulk, settings, network=True)
    assert script.network_allowed(bulk, settings, network=False)
    assert script.network_allowed(bulk, announced, network=True)
    small = Namespace(command="download", limit=5, allow_office_hours=True)
    assert script.network_allowed(small, settings, network=True)
    assert not script.network_allowed(Namespace(command="download", limit=500, allow_office_hours=True), announced, True)
    pool = Namespace(command="connected", phases=["pool"], limit=5, allow_office_hours=False)
    assert not script.network_allowed(pool, settings, network=True)  # the pool query covers the whole date range


def test_case_numbers_in_joined_heads_and_vfgh_numbers():
    head = "W233 2252582-2/9EW233 2336469-1/7E L510 2305355-1 /12E"
    found = [(bvwg_key(m), bvwg_full(m)) for m in BVWG_GZ_RE.finditer(head)]
    assert found == [("2252582-2", "W233 2252582-2/9E"), ("2336469-1", "W233 2336469-1/7E"),
                     ("2305355-1", "L510 2305355-1/12E")]
    assert vfgh_gzs_of(["E 2858/2025-5", "E988/2025"]) == ["E2858/2025", "E988/2025"]
    assert vfgh_gzs_of(["Ra 2026/14/0011"]) == []
    assert connected.bvwg_case_keys(["W150 2159588-2", "2143175-2"]) == {"2159588-2": "W150 2159588-2",
                                                                         "2143175-2": "2143175-2"}


def test_resolution_of_a_mentioned_bvwg_decision():
    rows = [{"ris_id": "A", "decision_date": "2020-01-10"}, {"ris_id": "B", "decision_date": "2021-03-01"},
            {"ris_id": "C", "decision_date": "2023-01-01"}]
    heads = {"A": {"case_numbers": {"2159588-2": "W150 2159588-2/7E"}, "decision_dates": ["2020-01-10"], "vfgh_cited": []},
             "B": {"case_numbers": {"2159588-2": "W150 2159588-2/15E"}, "decision_dates": ["2021-03-01"], "vfgh_cited": []},
             "C": {"case_numbers": {"2159588-2": "W150 2159588-2"}, "decision_dates": ["2023-01-01"], "vfgh_cited": []}}
    index = connected.BvwgIndex(rows, heads)
    assert index.resolve("2159588-2", "W150 2159588-2/15E", set(), "2022-01-01")["confidence"] == "high"
    by_date = index.resolve("2159588-2", None, {"2020-01-10"}, "2022-01-01")
    assert (by_date["bvwg_ris_id"], by_date["confidence"]) == ("A", "medium")
    only = index.resolve("2159588-2", None, set(), "2020-06-01")
    assert (only["bvwg_ris_id"], only["confidence"]) == ("A", "low")
    assert index.resolve("2159588-2", None, set(), "2024-01-01")["bvwg_ris_id"] is None  # ambiguous: all listed
