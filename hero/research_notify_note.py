"""Send notes/discord.md to Discord as one card. Run from research.yml as study notify_note."""

import sys

from hero.notify import main

if __name__ == "__main__":
    sys.exit(main(["--note"]))
