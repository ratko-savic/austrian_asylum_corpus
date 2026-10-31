"""Stage 05: the structure of the BVwG decisions (rules: config/segmentation/).

Examples:
    python scripts/05_segment.py segment     # the coarse sections -> processed/sections/bvwg/
    python scripts/05_segment.py fine        # the fine sections, after segment -> processed/fine_sections/bvwg/
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.segmentation.stage import run_fine, run_segment


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["segment", "fine"], help="segment: coarse sections; fine: fine sections")
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run_segment(args.workers) if args.command == "segment" else run_fine(args.workers)


if __name__ == "__main__":
    sys.exit(main())
