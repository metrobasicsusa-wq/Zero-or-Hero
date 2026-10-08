"""Re-send today's (ET) pushable events to Discord as a preview of the format; the cursor is not moved.
Run from research.yml as study notify_replay."""

import sys

from hero.notify import main

if __name__ == "__main__":
    sys.exit(main(["--replay"]))
