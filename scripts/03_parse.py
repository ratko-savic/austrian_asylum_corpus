"""Stage 03: RIS XML and metadata -> documents in processed/documents/<court>/.

Examples:
    python scripts/03_parse.py                      # BVwG
    python scripts/03_parse.py --court vwgh         # or vfgh, after 01 connected
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.parsing.stage import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--court", choices=["bvwg", "vwgh", "vfgh"], default="bvwg", help="default: bvwg")
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run(args.court, args.workers)


if __name__ == "__main__":
    sys.exit(main())
