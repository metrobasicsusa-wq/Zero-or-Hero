"""The $5-wide spread study rerun with a $2.5 width (owner, 10-27: 「那500 呢 2.5 价差」): sets the width before the
study module reads it and runs the same code; results save as bottom-spread25."""
import os
import sys

os.environ.setdefault("RESEARCH_SPREAD_WIDTH", "2.5")

from hero.research_bottom_spread import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
