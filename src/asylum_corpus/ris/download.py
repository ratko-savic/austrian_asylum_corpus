"""Download the decision texts of an inventory into raw/, with a retrieval log and a manifest.

For every decision of the inventory (inventory.jsonl + records.jsonl.gz):

    <RIS_ID>.json   the RIS metadata record, unchanged (from the inventory search; no request)
    <RIS_ID>.xml    the decision text exactly as RIS serves it

BVwG candidates go to raw/bvwg/<partition>/<year>/ with their bookkeeping in raw/; the VwGH/VfGH pool (`pool=True`)
goes to raw/{vwgh,vfgh}/<year>/ with its bookkeeping in raw/connected/. Every retrieval is appended to
retrieval_log.jsonl; manifest.jsonl and acquisition_report.md / .json are rebuilt from the log and the files at the
end of every run. A run resumes where the last one stopped: stored XML files are skipped.
"""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import time
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from ..common import require, utc_now, write_json, write_jsonl
from .client import RisClient

COURT_DIR = {"Bvwg": "bvwg", "Vwgh": "vwgh", "Vfgh": "vfgh"}
PARTITION_ORDER = ["main", "subsequent_applications_avg68", "new_law_2026"]


def write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(path)


def well_formed_ris_xml(data: bytes) -> bool:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return False
    return root.tag.endswith("risdok")


class Downloader:
    def __init__(self, client: RisClient | None, inventory: Path, raw: Path, pool: bool = False, log=print):
        self.client, self.inventory, self.raw, self.pool, self.log = client, inventory, raw, pool, log
        self.books = raw / "connected" if pool else raw
        self.log_file = self.books / "retrieval_log.jsonl"

    def rows(self) -> list[dict]:
        """The decisions of the inventory, main partition first, then by RIS ID."""
        with require(self.inventory / "inventory.jsonl", "stage 01 inventory").open(encoding="utf-8") as fh:
            rows = [json.loads(line) for line in fh]
        order = {p: i for i, p in enumerate(PARTITION_ORDER)}
        return sorted(rows, key=lambda r: (order.get(r["partition"], len(order)), r["ris_id"]))

    def base(self, row: dict) -> Path:
        court = self.raw / COURT_DIR[row["application"]]
        folder = court if self.pool else court / row["partition"]
        return folder / row["decision_date"][:4] / row["ris_id"]

    def rel(self, path: Path) -> str:
        return str(path.relative_to(self.raw))

    def write_metadata(self, rows: list[dict]) -> int:
        """Store the unchanged RIS record of every decision from the inventory (no requests)."""
        bases = {r["ris_id"]: self.base(r) for r in rows}
        written = 0
        with gzip.open(self.inventory / "records.jsonl.gz", "rt", encoding="utf-8") as fh:
            for line in fh:
                item = json.loads(line)
                base = bases.get(item["ris_id"])
                if base is None or base.with_suffix(".json").exists():
                    continue
                write_bytes_atomic(base.with_suffix(".json"),
                                   (json.dumps(item["record"], ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                written += 1
        return written

    def pending(self, rows: list[dict]) -> list[dict]:
        return [r for r in rows if not self.base(r).with_suffix(".xml").exists()]

    def download(self, rows: list[dict], limit: int | None = None) -> Counter:
        """Download the pending texts; a scheduled run (client.stop_when_window_closes) stops when the window closes."""
        pending = self.pending(rows)
        todo = pending if limit is None else pending[:limit]
        stats: Counter = Counter()
        started = time.monotonic()
        self.log(f"{len(todo):,} decision texts to download ({len(rows) - len(pending):,} already stored)")
        self.books.mkdir(parents=True, exist_ok=True)
        with self.log_file.open("a", encoding="utf-8") as log_fh:
            for n, row in enumerate(todo, 1):
                if self.client.stop_when_window_closes and self.client.window_closed():
                    self.log(f"RIS bulk window closed: stopping ({n - 1:,} done)")
                    stats["stopped_window_closed"] = 1
                    break
                entry = self.fetch_one(row)
                log_fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                log_fh.flush()
                stats[entry["status"]] += 1
                if n % 100 == 0 or n == len(todo):
                    rate = n / max(1.0, time.monotonic() - started)
                    self.log(f"  {n:,}/{len(todo):,} ({stats['ok']:,} ok, {stats['failed']:,} failed), "
                             f"{rate * 3600:,.0f} per hour")
        return stats

    def fetch_one(self, row: dict) -> dict:
        url = row["content_urls"].get("xml")
        target = self.base(row).with_suffix(".xml")
        entry = {"ris_id": row["ris_id"], "court": COURT_DIR[row["application"]], "partition": row["partition"],
                 "file": self.rel(target), "source_url": url, "retrieved_at": utc_now()}
        if not url:
            return entry | {"status": "failed", "error": "no XML content URL in the RIS record"}
        try:
            data = self.client.get(url).content
        except Exception as exc:  # recorded, and retried on the next run
            return entry | {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if not well_formed_ris_xml(data):
            return entry | {"status": "failed", "error": f"not a well-formed RIS XML document ({len(data)} bytes)"}
        write_bytes_atomic(target, data)
        return entry | {"status": "ok", "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}

    def rebuild_manifest(self, rows: list[dict]) -> dict:
        """manifest.jsonl and the report from the retrieval log and the stored files (no requests)."""
        latest: dict[str, dict] = {}
        failures: dict[str, dict] = {}
        if self.log_file.exists():
            with self.log_file.open(encoding="utf-8") as fh:
                for line in fh:
                    e = json.loads(line)
                    if e["status"] == "ok":
                        latest[e["ris_id"]] = e
                        failures.pop(e["ris_id"], None)
                    elif e["ris_id"] not in latest:
                        failures[e["ris_id"]] = e
        queries = "pool queries in interim/connected/pool/queries.jsonl" if self.pool else \
            "queries in interim/candidates/queries.jsonl"
        manifest = []
        for row in rows:
            base = self.base(row)
            xml, meta = base.with_suffix(".xml"), base.with_suffix(".json")
            got = latest.get(row["ris_id"]) if xml.exists() else None
            status = "ok" if got else ("failed" if row["ris_id"] in failures else "pending")
            entry = {
                "ris_id": row["ris_id"], "court": COURT_DIR[row["application"]], "partition": row["partition"],
                "decision_date": row["decision_date"], "case_numbers": row["case_numbers"], "status": status,
                "xml_file": self.rel(xml) if got else None, "xml_retrieved_at": got and got["retrieved_at"],
                "xml_bytes": got and got["bytes"], "xml_sha256": got and got["sha256"],
                "xml_source_url": row["content_urls"].get("xml"),
                "metadata_file": self.rel(meta) if meta.exists() else None,
                "metadata_retrieved_at": row.get("first_retrieved_at"),
                "metadata_source": f"RIS OGD API v2.6 Judikatur search; {queries}",
            }
            if not self.pool:
                entry["found_by_queries"] = row.get("queries", [])
            entry["document_url"] = row["document_url"]
            entry["error"] = failures.get(row["ris_id"], {}).get("error") if status == "failed" else None
            manifest.append(entry)
        self.books.mkdir(parents=True, exist_ok=True)
        write_jsonl(self.books / "manifest.jsonl", manifest)
        return self.report(manifest)

    def report(self, manifest: list[dict]) -> dict:
        group = "court" if self.pool else "partition"
        by_group: dict[str, Counter] = defaultdict(Counter)
        by_year: dict[str, Counter] = defaultdict(Counter)
        for m in manifest:
            by_group[m[group]][m["status"]] += 1
            by_year[m["decision_date"][:4]][m["status"]] += 1
        total = Counter(m["status"] for m in manifest)
        size = sum(m["xml_bytes"] or 0 for m in manifest)
        report = {"generated_at": utc_now(), "total": dict(total), "xml_bytes": size,
                  f"by_{group}": {k: dict(v) for k, v in sorted(by_group.items())},
                  "by_year": {k: dict(v) for k, v in sorted(by_year.items())},
                  "failed": [{"ris_id": m["ris_id"], "error": m["error"]} for m in manifest if m["status"] == "failed"]}
        write_json(self.books / "acquisition_report.json", report)
        what = "VwGH/VfGH pool decisions" if self.pool else "BVwG decisions"
        files = "`vwgh|vfgh/<year>/<RIS_ID>.{json,xml}`" if self.pool else \
            "`bvwg/<partition>/<year>/<RIS_ID>.{json,xml}`"
        lines = [f"# Raw {what}: acquisition status", "",
                 f"Generated {report['generated_at']}. Source: RIS OGD (XML decision texts; metadata records from the "
                 f"search). Files: {files} in `raw/`; details per decision in `manifest.jsonl`.", "",
                 f"- Downloaded: {total['ok']:,} of {len(manifest):,} ({size / 1e9:.2f} GB XML)",
                 f"- Pending: {total['pending']:,}",
                 f"- Failed (retried on the next run): {total['failed']:,}", "",
                 f"| {group.capitalize()} | Downloaded | Pending | Failed |", "|---|---|---|---|"]
        for g, c in report[f"by_{group}"].items():
            lines.append(f"| `{g}` | {c.get('ok', 0):,} | {c.get('pending', 0):,} | {c.get('failed', 0):,} |")
        lines += ["", "| Year | Downloaded | Pending | Failed |", "|---|---|---|---|"]
        for y, c in report["by_year"].items():
            lines.append(f"| {y} | {c.get('ok', 0):,} | {c.get('pending', 0):,} | {c.get('failed', 0):,} |")
        if report["failed"]:
            lines += ["", "## Failed", ""] + [f"- {f['ris_id']}: {f['error']}" for f in report["failed"][:200]]
        (self.books / "acquisition_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report


class RunLock:
    """Only one download run at a time (a manual run and a scheduled one must not overlap: one connection)."""

    def __init__(self, path: Path):
        self.path = path
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = self.path.open("w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit(f"another download run holds {self.path}")
        self.fh.write(f"{utc_now()}\n")
        self.fh.flush()
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh, fcntl.LOCK_UN)
        self.fh.close()
