"""Stage 10: the data package (rules: config/package/package.yaml).

One JSON record per decision with everything stages 02 to 09 found in it, checked against the record schema; a table
with one row per record; the download candidates with the filter's verdict; examples; the record schema. It is built
into a new or empty folder. Needs ASYLUM_CORPUS_JUDGE_SALT, the salt of the judge IDs.

The README, LICENSE and CITATION.cff of the data are written by hand and added to the folder; then --checksums writes
SHA256SUMS over all its files. --agent-corpus writes the agent corpus of stage 09, published on its own: its records by
split and year and a table of them; its dataset card is written by hand.

Examples:
    python scripts/10_package.py --out /path/to/package
    python scripts/10_package.py --checksums /path/to/package
    python scripts/10_package.py --agent-corpus /path/to/agent_corpus
"""

import argparse
import sys
from pathlib import Path

from asylum_corpus.common import default_workers, positive_int
from asylum_corpus.package import agent_corpus, build, checksums


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--out", type=Path, help="a new or empty folder (default: package/ in the data folder)")
    target.add_argument("--checksums", type=Path, metavar="FOLDER",
                        help="only write SHA256SUMS for this package folder")
    target.add_argument("--agent-corpus", type=Path, metavar="FOLDER",
                        help="only the agent corpus, published on its own, into this new or empty folder")
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    args = parser.parse_args()
    if args.checksums:
        checksums(args.checksums)
    elif args.agent_corpus:
        agent_corpus(args.agent_corpus, args.workers)
    else:
        build(args.out, args.workers)
    return 0


if __name__ == "__main__":
    sys.exit(main())
