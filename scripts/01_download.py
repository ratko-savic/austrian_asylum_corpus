"""Stage 01: download the corpus from RIS (rules: config/ris/).

    inventory   the BVwG candidate decisions (metadata only)        -> interim/candidates/
    download    their XML decision texts and RIS metadata records   -> raw/bvwg/<partition>/<year>/, bookkeeping in raw/
    connected   the VwGH/VfGH pool: metadata, texts, local matching -> interim/connected/, raw/{vwgh,vfgh}/,
                                                                       bookkeeping in raw/connected/

Every request follows the RIS usage rules in config/ris/client.yaml. Until `contact` and `bulk_announced_on` are set
there, only small tests run (at most 50 texts with --limit, no inventory, pool or lookup query). Every run resumes where
the last one stopped.

    python scripts/01_download.py inventory
    python scripts/01_download.py download --exit-when-window-closes       # scheduled nightly run
    python scripts/01_download.py download --report-only                   # status, no requests
    python scripts/01_download.py connected --exit-when-window-closes      # after the BVwG download is complete
    python scripts/01_download.py connected --phases match                 # re-match locally, no requests
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import yaml

from asylum_corpus.common import CONFIG, data_dir, default_workers, positive_int
from asylum_corpus.ris import connected
from asylum_corpus.ris.client import RisClient, WindowClosed
from asylum_corpus.ris.download import Downloader, RunLock
from asylum_corpus.ris.inventory import build_inventory
from asylum_corpus.ris.queries import load_query_config

QUERIES = CONFIG / "ris" / "queries.yaml"


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def network_allowed(args, settings: dict, network: bool) -> bool:
    """Bulk runs need the announcement; outside the bulk window only small tests may run. A small test downloads at
    most `small_test_limit` texts and runs no query over the whole date range (inventory, pool, lookup)."""
    if not network:
        return True
    whole_range = args.command == "inventory" or bool(set(getattr(args, "phases", [])) & {"pool", "lookup"})
    small_test = args.limit is not None and args.limit <= int(settings["small_test_limit"]) and not whole_range
    if args.allow_office_hours and not small_test:
        print(f"--allow-office-hours is only for small tests (--limit {settings['small_test_limit']} or less).")
        return False
    if not small_test and not (settings.get("contact") and settings.get("bulk_announced_on")):
        print("Bulk retrieval refused: announce it to ris.it@bka.gv.at first and record that in "
              "config/ris/client.yaml (contact, bulk_announced_on).")
        return False
    return True


def make_client(args, settings: dict) -> RisClient:
    return RisClient(min_pause=float(settings["min_pause_seconds"]), contact=settings.get("contact"),
                     restrict_to_window=not args.allow_office_hours,
                     stop_when_window_closes=args.exit_when_window_closes)


def inventory(args, settings: dict, paths: dict) -> int:
    config = load_query_config(QUERIES)
    client = make_client(args, settings)
    print(f"Inventory {config.start_date} .. {config.end_date}: {len(config.indicators)} indicators in "
          f"{len(config.queries)} queries -> {paths['candidates']}")
    report = build_inventory(client, config, paths["candidates"])
    t = report["totals"]
    print(f"{t['candidates']:,} candidates, {len(t['incomplete_slices'])} incomplete slices, "
          f"{t['requests_this_run']:,} requests, {report['runtime_seconds']} s")
    return 1 if t["incomplete_slices"] else 0


def download(args, settings: dict, paths: dict) -> int:
    client = None if args.report_only else make_client(args, settings)
    downloader = Downloader(client, paths["candidates"], paths["raw"])
    rows = downloader.rows()
    print(f"{len(rows):,} candidates; metadata records written now: {downloader.write_metadata(rows):,}")
    try:
        if client:
            print(f"this run: {dict(downloader.download(rows, args.limit))}")
    finally:
        report = downloader.rebuild_manifest(rows)
    print(f"stored: {report['total'].get('ok', 0):,}, pending: {report['total'].get('pending', 0):,}, "
          f"failed: {report['total'].get('failed', 0):,}; report: {paths['raw'] / 'acquisition_report.md'}")
    return 0


def connected_step(args, settings: dict, paths: dict) -> int:
    bvwg = Downloader(None, paths["candidates"], paths["raw"])
    pending = len(bvwg.pending(bvwg.rows()))
    if pending:
        print(f"Connected step waits: {pending:,} BVwG decision texts are still pending.")
        return 0
    queries = load_yaml(QUERIES)
    pool = queries["connected_pool"]
    pool_query = {"params": dict(pool["params"]), "start": date.fromisoformat(str(pool["start_date"])),
                  "end": date.fromisoformat(str(pool["end_date"])) if pool.get("end_date") else date.today()}
    client = make_client(args, settings) if set(args.phases) & {"pool", "download", "lookup"} else None
    return connected.run(client, args.phases, paths["candidates"], paths["raw"], paths["connected"], pool_query,
                         int(queries["bvwg_inventory"]["max_hits_per_slice"]), args.limit,
                         match_partial=set(args.phases) != set(connected.PHASES), workers=args.workers)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    commands = {"inventory": "list the BVwG candidate decisions (metadata only)",
                "download": "download the XML decision texts of the inventory",
                "connected": "VwGH/VfGH decisions connected to the candidates (pool, download, match, lookup)"}
    for name, help_text in commands.items():
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--exit-when-window-closes", action="store_true",
                       help="stop when the bulk window closes instead of waiting (scheduled runs)")
        p.add_argument("--allow-office-hours", action="store_true",
                       help="ignore the bulk window; only for small tests comparable to manual use of RIS")
        if name != "inventory":
            p.add_argument("--limit", type=positive_int, help="download at most this many decisions in this run")
    sub.choices["inventory"].set_defaults(limit=None)
    sub.choices["download"].add_argument("--report-only", action="store_true",
                                         help="rebuild manifest and report, no requests")
    sub.choices["connected"].add_argument("--phases", nargs="+", choices=connected.PHASES, default=connected.PHASES,
                                          help="only these phases (lookup also runs match; match then also runs on a "
                                               "partial pool)")
    sub.choices["connected"].add_argument("--workers", type=positive_int, default=default_workers(),
                                          help="processes for reading texts in the match phase")
    args = parser.parse_args()

    settings = load_yaml(CONFIG / "ris" / "client.yaml")
    root = data_dir()
    paths = {"candidates": root / "interim" / "candidates", "connected": root / "interim" / "connected",
             "raw": root / "raw"}
    network = args.command == "inventory" or (args.command == "download" and not args.report_only) or (
        args.command == "connected" and bool(set(args.phases) & {"pool", "download", "lookup"}))
    if not network_allowed(args, settings, network):
        return 3
    step = {"inventory": inventory, "download": download, "connected": connected_step}[args.command]
    try:
        with RunLock(paths["raw"] / ".download.lock"):
            return step(args, settings, paths)
    except WindowClosed as exc:
        print(f"{exc}: stopping; the next run resumes")
        return 0


if __name__ == "__main__":
    sys.exit(main())
