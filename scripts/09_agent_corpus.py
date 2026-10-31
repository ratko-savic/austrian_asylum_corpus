"""Stage 09: the agent corpus (rules: config/agent_corpus.yaml) -> processed/agent_corpus/.

Examples:
    python scripts/09_agent_corpus.py
"""

import argparse
import sys

from asylum_corpus.agent_corpus import build
from asylum_corpus.common import default_workers, positive_int


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workers", type=positive_int, default=default_workers(),
                        help="worker processes (default: one less than the cores, at most 8)")
    return build(parser.parse_args().workers)


if __name__ == "__main__":
    sys.exit(main())
