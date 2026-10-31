"""Stage 06: the citations of the decisions (rules: config/citations/) -> processed/citations/<court>/.

Examples:
    python scripts/06_citations.py                 # BVwG first
    python scripts/06_citations.py --court vwgh    # or vfgh
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.citations.stage import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--court", choices=["bvwg", "vwgh", "vfgh"], default="bvwg", help="default: bvwg")
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run(args.court, args.workers)


if __name__ == "__main__":
    sys.exit(main())
