"""Post a written page to one of the owner's Discord channels through its webhook (one-way: nothing is read back).

Owner, 10-19: besides the trade feed (hero.notify, DC_WEBHOOK), the daily follow card, the earnings forecast, the
midday / closing summaries and research notes go to their own channels, so people other than claude-b and claude-c
can read them. Claude writes the page as posts/<channel>/YYYY-MM-DD-<slot>.md and runs the post workflow
(.github/workflows/post.yml), which holds the webhook addresses as secrets:

    python -m hero.discord_post <channel> posts/<channel>/<file>.md

The first "# " line is the title; each "## " section becomes its own card; markdown tables (which Discord does not
draw) become "a · b · c" lines. Every card carries the paper-account footer. A page is refused when it holds
anything that looks like a key, an order / account id or a webhook address. A channel whose secret is not set is
skipped without an error, so the routines can post before the owner has made every channel.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

from hero.notify import GREY, chunked, send

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
CHANNELS = {"follow": "DC_WEBHOOK_FOLLOW", "earnings": "DC_WEBHOOK_EARNINGS", "daily": "DC_WEBHOOK_DAILY",
            "research": "DC_WEBHOOK_RESEARCH", "trades": "DC_WEBHOOK"}
COLOR = {"follow": 0x2E86DE, "earnings": 0x8E44AD, "daily": 0x16A085, "research": 0xD35400}
FOOTER = "Zero-or-Hero · Alpaca 模拟盘，参考记录，不是投资建议"
NOT_OPTION = r"\b(?![A-Z]{1,6}\d{6}[CP]\d{8}\b)"   # option symbols (PANW261023C00200000) are fine to show
UNSAFE = [
    (re.compile(r"discord(?:app)?\.com/api/webhooks", re.I), "webhook 地址"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I), "订单 / 账户 ID"),
    (re.compile(NOT_OPTION + r"(?:PK|AK|CK)[A-Z0-9]{18}\b"), "Alpaca 钥匙"),
    (re.compile(r"\b(?:sk-ant-|ghp_|github_pat_|xox[bp]-)[\w-]{10,}"), "钥匙 / 令牌"),
    (re.compile(NOT_OPTION + r"PA[0-9A-Z]{8,}\b"), "账户号"),
]


def unsafe(text: str) -> str | None:
    """What in the page must not go out, if anything."""
    for pat, what in UNSAFE:
        if pat.search(text):
            return what
    return None


def plain(lines: list[str]) -> list[str]:
    """Markdown tables to plain lines; the header row in bold, the |---| rule dropped."""
    out, header = [], True
    for line in lines:
        s = line.strip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c):
                continue
            row = " · ".join(c for c in cells if c)
            out.append(f"**{row}**" if header else row)
            header = False
        else:
            out.append(line)
            header = True
    return out


def cards(text: str, color: int = GREY) -> list[dict]:
    lines = text.strip().splitlines()
    title = lines[0].lstrip("# ").strip() if lines and lines[0].startswith("# ") else "Zero-or-Hero"
    body = lines[1:] if lines and lines[0].startswith("# ") else lines
    sections, cur_title, cur = [], title, []
    for line in body:
        if line.startswith("## "):
            if any(x.strip() for x in cur):
                sections.append((cur_title, cur))
            cur_title, cur = line[3:].strip(), []
        else:
            cur.append(line)
    if any(x.strip() for x in cur) or not sections:
        sections.append((cur_title, cur))
    out = []
    for t, ls in sections:
        rows = plain(ls)
        while rows and not rows[0].strip():
            rows.pop(0)
        out += chunked(t[:250], rows or ["—"], color, FOOTER)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[0] not in CHANNELS:
        print(f"usage: python -m hero.discord_post <{'|'.join(CHANNELS)}> posts/<channel>/<file>.md")
        return 2
    channel, rel = argv
    path = (ROOT / rel).resolve()
    if POSTS.resolve() not in path.parents or path.suffix != ".md" or not path.exists():
        print(f"discord_post: only existing posts/**/*.md pages can be sent, not {rel}")
        return 2
    text = path.read_text()
    bad = unsafe(text)
    if bad:
        print(f"discord_post: refused, the page looks like it holds a {bad}")
        return 1
    url = os.environ.get(CHANNELS[channel], "").strip()
    if not url:
        print(f"discord_post: {CHANNELS[channel]} is not set; {channel} skipped")
        return 0
    ok = send(url, cards(text, COLOR.get(channel, GREY)))
    print(f"discord_post: {rel} -> {channel}: {'sent' if ok else 'failed'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
