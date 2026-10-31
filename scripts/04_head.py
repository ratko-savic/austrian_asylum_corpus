"""Stage 04: the head labels and the case history of the BVwG decisions (rules: config/head/).

Reads the coarse structure (stage 05 segment) and writes processed/head/bvwg/.

Examples:
    python scripts/04_head.py
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.head.stage import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run(args.workers)


if __name__ == "__main__":
    sys.exit(main())
