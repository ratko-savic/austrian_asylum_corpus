"""Stage 02: which BVwG candidates of the inventory belong to the corpus (rules: config/filtering/asylum_filter.yaml).

From RIS metadata only. Reads interim/candidates/, writes interim/filtered/.

Examples:
    python scripts/02_filter.py
"""

import argparse
import sys

from asylum_corpus.filtering.stage import filtered_dir, run_filter


def main() -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    report = run_filter()
    v = report["verdicts"]
    print(f"Filter: {report['candidates']:,} candidates, included {v.get('include', 0):,}, "
          f"excluded {v.get('exclude', 0):,}")
    for rule in report["rules"]:
        print(f"  {rule['id']:36s} {rule['verdict']:8s} {rule['count']:>8,}")
    for flag in report["flags"]:
        print(f"  flag {flag['id']:31s} {flag['count']:>17,}")
    print(f"Report: {filtered_dir() / 'filter_report.md'}")
    unmatched = report["rules"][-1]["count"]
    if unmatched:
        print(f"{unmatched:,} candidates matched no rule ({report['rules'][-1]['id']})")
    return 1 if unmatched else 0


if __name__ == "__main__":
    sys.exit(main())
