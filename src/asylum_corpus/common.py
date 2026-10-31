"""Paths and file helpers shared by the stages."""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / "config"
SCHEMAS = ROOT / "schemas"
if not (CONFIG.is_dir() and SCHEMAS.is_dir()):
    raise SystemExit(f"config/ and schemas/ not found in {ROOT}: install the code in place, with pip install -e . in a "
                     "checkout of the repository")


def data_dir() -> Path:
    """The data folder: ASYLUM_CORPUS_DATA if set, else data/ next to the code."""
    return Path(os.environ.get("ASYLUM_CORPUS_DATA", ROOT / "data"))


def default_workers() -> int:
    """Worker processes: one less than the cores, at most 8. The outputs do not depend on it."""
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def positive_int(text: str) -> int:
    """An argparse type for --workers and --limit: a whole number of at least 1."""
    n = int(text)
    if n < 1:
        raise argparse.ArgumentTypeError(f"{text} is less than 1")
    return n


def require(path: Path, after: str) -> Path:
    """An input of a stage; stops with a message where it is missing."""
    if not Path(path).exists():
        raise SystemExit(f"{path} missing: run {after} first")
    return Path(path)


def year_files(folder: Path, after: str) -> list[str]:
    """The year files of a stage's output, sorted; stops with a message where there are none."""
    files = sorted(str(p) for p in Path(folder).glob("*.jsonl.gz"))
    if not files:
        raise SystemExit(f"no year files in {folder}: run {after} first")
    return files


@lru_cache(maxsize=None)
def schema_validator(name: str) -> Draft202012Validator:
    """The validator of schemas/<name>.schema.json."""
    return Draft202012Validator(json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8")))


def remove_other_year_files(folder: Path, written: Iterable[str]) -> None:
    """Delete the year files of an earlier run that this run did not write (a year without decisions now), so that the
    next stage does not read them."""
    keep = set(written)
    for f in Path(folder).glob("*.jsonl.gz"):
        if f.name not in keep:
            f.unlink()


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f} %" if d else "–"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def read_jsonl(path: Path) -> Iterator[dict]:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, data) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_jsonl(path: Path, records: Iterable[dict]) -> None:
    """JSON lines via a temporary file."""
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        for record in records:
            fh.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))
    tmp.replace(path)


def read_csv(path: Path) -> list[dict]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


class GzipLines:
    """A gzip text file written via a temporary file. The header holds no file name and no time, so the same lines
    always give the same bytes."""

    def __init__(self, path: Path):
        self.path, self.tmp = Path(path), Path(path).with_name(Path(path).name + ".tmp")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.raw = self.tmp.open("wb")
        self.gz = gzip.GzipFile(filename="", mode="wb", fileobj=self.raw, mtime=0)
        self.lines = 0

    def write(self, line: str) -> None:
        self.gz.write(line.encode("utf-8"))
        self.lines += 1

    def close(self) -> None:
        self.gz.close()
        self.raw.close()
        self.tmp.replace(self.path)


def write_lines_gz(path: Path, lines: Iterable[str]) -> int:
    out = GzipLines(path)
    for line in lines:
        out.write(line)
    out.close()
    return out.lines


def write_jsonl_gz(path: Path, records: Iterable[dict]) -> int:
    return write_lines_gz(path, (json.dumps(r, ensure_ascii=False) + "\n" for r in records))


def text_fingerprint(retrieval_log: Path, ris_ids: Iterable[str]) -> dict:
    """The decision texts used: their number, and SHA-256 over the lines "<RIS ID> <SHA-256 of the XML>" (the XML
    hashes come from the retrieval log written at download time)."""
    hashes: dict[str, str] = {}
    if retrieval_log.exists():
        for entry in read_jsonl(retrieval_log):
            if entry.get("status") == "ok":
                hashes[entry["ris_id"]] = entry["sha256"]
    ids = sorted(set(ris_ids))
    digest, missing = hashlib.sha256(), 0
    for rid in ids:
        missing += rid not in hashes
        digest.update(f"{rid} {hashes.get(rid, 'unknown')}\n".encode())
    return {"documents": len(ids), "sha256": digest.hexdigest(), "without_hash": missing}
