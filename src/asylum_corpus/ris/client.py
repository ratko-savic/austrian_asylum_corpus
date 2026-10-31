"""Client for the RIS OGD API (Judikatur endpoint, v2.6) that follows the RIS usage rules (config/ris/client.yaml):
one request at a time, a pause of at least 2 s after each, and bulk retrieval only in the allowed window, where it waits
for the window to open (or stops, for a scheduled run).

Facts about RIS this client relies on: without `Dokumenttyp.SucheInEntscheidungstexten=true`, VwGH/VfGH queries
return only legal headnotes (Rechtssätze); there is no filter for a RIS document ID; deep pages are slow (about
30 s at page 1,000), so large queries are sliced by decision date. Records are returned exactly as RIS sent them.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

API_URL = "https://data.bka.gv.at/ris/api/v2.6/Judikatur"
PUBLIC_DOCUMENT_URL = "https://www.ris.bka.gv.at/Dokument.wxe?Abfrage={application}&Dokumentnummer={ris_id}"
USER_AGENT = "austrian-asylum-corpus (research use of RIS OGD)"
VIENNA = ZoneInfo("Europe/Vienna")
MIN_PAUSE_SECONDS = 2.0
RETRY_STATUS = (429, 500, 502, 503, 504)
PAGE_SIZES = {10: "Ten", 20: "Twenty", 50: "Fifty", 100: "OneHundred"}


class RisApiError(RuntimeError):
    pass


class WindowClosed(BaseException):
    """The bulk window closed during a scheduled run. A BaseException, so that it is never recorded as a failed
    download: the next run resumes."""


def as_list(value) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def record_id(record: dict) -> str:
    return record["Data"]["Metadaten"]["Technisch"]["ID"]


def summarize(record: dict) -> dict:
    """The fields of a RIS record (OgdDocumentReference) needed to select and download a decision."""
    meta = record["Data"]["Metadaten"]
    jud = meta.get("Judikatur", {})
    application = meta["Technisch"]["Applikation"]
    content = {}
    for ref in as_list(record["Data"].get("Dokumentliste", {}).get("ContentReference")):
        if ref.get("ContentType") == "MainDocument":
            for url in as_list(ref.get("Urls", {}).get("ContentUrl")):
                content[url["DataType"].lower()] = url["Url"]
    ris_id = meta["Technisch"]["ID"]
    return {
        "ris_id": ris_id,
        "application": application,
        "document_type": jud.get("Dokumenttyp"),
        "case_numbers": as_list((jud.get("Geschaeftszahl") or {}).get("item")),
        "decision_date": jud.get("Entscheidungsdatum"),
        "decision_type": (jud.get(application) or {}).get("Entscheidungsart"),
        "ecli": jud.get("EuropeanCaseLawIdentifier"),
        "norms": as_list((jud.get("Normen") or {}).get("item")),
        "published": meta.get("Allgemein", {}).get("Veroeffentlicht"),
        "changed": meta.get("Allgemein", {}).get("Geaendert"),
        "document_url": PUBLIC_DOCUMENT_URL.format(application=application, ris_id=ris_id),
        "content_urls": content,
    }


def easter_sunday(year: int) -> date:
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7  # noqa: E741
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def austrian_public_holidays(year: int) -> set[date]:
    fixed = {(1, 1), (1, 6), (5, 1), (8, 15), (10, 26), (11, 1), (12, 8), (12, 25), (12, 26)}
    easter = easter_sunday(year)
    # Easter Monday, Ascension Day, Whit Monday, Corpus Christi
    movable = {easter + timedelta(days=n) for n in (1, 39, 50, 60)}
    return {date(year, m, d) for m, d in fixed} | movable


def in_bulk_window(moment: datetime) -> bool:
    """Bulk retrieval is allowed 20:00-05:00 Vienna time, and all day on weekends and public holidays."""
    local = moment.astimezone(VIENNA)
    if local.weekday() >= 5 or local.date() in austrian_public_holidays(local.year):
        return True
    return local.hour >= 20 or local.hour < 5


def next_window_opening(moment: datetime) -> datetime:
    local = moment.astimezone(VIENNA)
    if in_bulk_window(local):
        return local
    return local.replace(hour=20, minute=0, second=0, microsecond=0)


@dataclass
class SearchPage:
    hits: int
    records: list[dict]


@dataclass
class SearchResult:
    application: str
    params: dict
    hits: int
    records: list[dict]
    pages: int

    @property
    def unique_ids(self) -> set[str]:
        return {record_id(r) for r in self.records}

    @property
    def complete(self) -> bool:
        return len(self.unique_ids) == self.hits


class RisClient:
    """One connection, a pause of at least `min_pause` seconds between requests, requests only in the bulk window.

    With `stop_when_window_closes` (scheduled runs) a closed window raises WindowClosed instead of waiting."""

    def __init__(self, min_pause: float = MIN_PAUSE_SECONDS, contact: str | None = None,
                 restrict_to_window: bool = True, stop_when_window_closes: bool = False, timeout: float = 120.0,
                 retries: int = 5, page_size: int = 100, log=print):
        if page_size not in PAGE_SIZES:
            raise ValueError(f"page_size must be one of {sorted(PAGE_SIZES)}")
        if min_pause < MIN_PAUSE_SECONDS:
            raise ValueError(f"RIS allows at most 0.5 requests per second: min_pause must be >= {MIN_PAUSE_SECONDS}")
        self.min_pause = min_pause
        self.restrict_to_window = restrict_to_window
        self.stop_when_window_closes = stop_when_window_closes
        self.timeout, self.retries, self.page_size, self.log = timeout, retries, page_size, log
        self.requests_made = 0
        self._last_end = 0.0
        self.session = requests.Session()
        # RIS asks for a contact address in the User-Agent of bulk clients
        self.session.headers["User-Agent"] = f"{USER_AGENT}; contact: {contact}" if contact else USER_AGENT

    def window_closed(self) -> bool:
        return self.restrict_to_window and not in_bulk_window(datetime.now(VIENNA))

    def _wait_for_window(self) -> None:
        if not self.window_closed():
            return
        now = datetime.now(VIENNA)
        if self.stop_when_window_closes:
            raise WindowClosed(f"RIS bulk window closed at {now:%Y-%m-%d %H:%M}")
        opening = next_window_opening(now)
        self.log(f"  outside the RIS bulk window: waiting until {opening:%Y-%m-%d %H:%M} (Vienna time)")
        time.sleep(max(0.0, (opening - now).total_seconds()) + 1)

    def get(self, url: str, params: dict | None = None) -> requests.Response:
        """One GET. A connection lost or timed out, or a status of RETRY_STATUS, is tried again, up to `retries`
        times; every attempt keeps the pause, plus a backoff of 2, 4, 8, ... seconds or the server's Retry-After, and
        goes out only in the bulk window."""
        attempt, backoff = 0, 0.0
        while True:
            wait = self._last_end + self.min_pause + backoff - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._wait_for_window()
            self.requests_made += 1
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except (requests.ConnectionError, requests.Timeout, requests.exceptions.ChunkedEncodingError):
                if attempt == self.retries:
                    raise
                response = None
            finally:
                self._last_end = time.monotonic()
            if response is not None and (response.status_code not in RETRY_STATUS or attempt == self.retries):
                response.raise_for_status()
                return response
            after = response.headers.get("Retry-After", "") if response is not None else ""
            attempt += 1
            backoff = max(2.0 ** attempt, float(after) if after.isdigit() else 0.0)

    def search_page(self, application: str, params: dict, page: int = 1, page_size: int | None = None) -> SearchPage:
        query = {
            "Applikation": application,
            "Dokumenttyp.SucheInEntscheidungstexten": "true",
            "DokumenteProSeite": PAGE_SIZES[page_size or self.page_size],
            "Seitennummer": page,
            **params,
        }
        last_error: Exception | None = None
        for attempt in range(self.retries):
            try:
                result = self.get(API_URL, query).json()["OgdSearchResult"]
            except (ValueError, KeyError) as exc:                     # a malformed response (get retries failures)
                last_error = exc
                time.sleep(2 ** attempt)
                continue
            if "Error" in result:
                raise RisApiError(f"{query}: {result['Error'].get('Message')}")
            results = result["OgdDocumentResults"]
            return SearchPage(int(results["Hits"]["#text"]), as_list(results.get("OgdDocumentReference")))
        raise RisApiError(f"{query}: no valid response after {self.retries} attempts ({last_error})")

    def count(self, application: str, params: dict) -> int:
        return self.search_page(application, params, 1, page_size=10).hits

    def search(self, application: str, params: dict, first_page: SearchPage | None = None) -> SearchResult:
        """All records of a query, page by page."""
        first = first_page or self.search_page(application, params, 1)
        records = list(first.records)
        pages = max(1, math.ceil(first.hits / self.page_size))
        for page in range(2, pages + 1):
            records.extend(self.search_page(application, params, page).records)
        return SearchResult(application, params, first.hits, records, pages)
