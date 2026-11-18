"""The first half hour's option volume on the Magnificent 7 (owner, 11-18: add them). The study and its fixed lines are
in hero/research_flow_open.py; this only names the stocks. Record only. Run: python -m hero.research_flow_open_mag7
"""

from __future__ import annotations

import sys

from hero.research_flow_open import MAG7, main

if __name__ == "__main__":
    sys.exit(main(MAG7, "flow-open-mag7"))
