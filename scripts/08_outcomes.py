"""Stage 08: the outcomes of the decisions (rules: config/outcomes/) -> processed/outcomes/<court>/.

Examples:
    python scripts/08_outcomes.py                  # BVwG: one outcome per outcome unit
    python scripts/08_outcomes.py --court vwgh     # or vfgh: one outcome per decision
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.outcomes.stage import run_bvwg, run_higher


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--court", choices=["bvwg", "vwgh", "vfgh"], default="bvwg", help="default: bvwg")
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes for the BVwG (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run_bvwg(args.workers) if args.court == "bvwg" else run_higher(args.court)


if __name__ == "__main__":
    sys.exit(main())
