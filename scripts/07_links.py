"""Stage 07: the relations between BVwG, VwGH and VfGH decisions (rules: config/links/) -> processed/links/.

Examples:
    python scripts/07_links.py
"""

import argparse
import sys

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.links.stage import run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    return run(args.workers)


if __name__ == "__main__":
    sys.exit(main())
