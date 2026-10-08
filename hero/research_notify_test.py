"""Send one check message to the Discord webhook (DC_WEBHOOK). Run from research.yml as study notify_test,
since a new workflow file cannot be dispatched until it is on the default branch."""

import sys

from hero.notify import main

if __name__ == "__main__":
    sys.exit(main(["--test"]))
