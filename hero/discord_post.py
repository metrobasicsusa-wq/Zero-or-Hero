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

Owner, 10-19: a webhook can edit and delete the messages it sent, so each send keeps the message ids in
posts/.sent/<channel>/<file>.json (ids are not secret; the webhook address stays a secret). Sending the same page
again edits those messages in place (a longer page adds messages, a shorter one deletes the extra);
`--delete <channel> <file>` removes them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from hero.notify import EMBEDS_PER_MESSAGE, GREY, chunked

ROOT = Path(__file__).resolve().parent.parent
POSTS = ROOT / "posts"
SENT = ".sent"   # posts/.sent/<channel>/<file>.json: the ids of the messages a page became
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



# 10-21: Discord also caps a message by bytes. A page of Chinese text (3 bytes a character) under the 6,000-character
# rule still came back "413 Message size exceeds maximum allowed" at about 10.5 KB, and a message near 8.3 KB went
# through, so each card is kept under CARD_BYTES and each message under MESSAGE_BYTES of UTF-8 JSON.
CARD_BYTES, MESSAGE_BYTES = 5000, 7000


def nbytes(obj) -> int:
    return len(json.dumps(obj, ensure_ascii=False).encode())


def split_card(card: dict) -> list[dict]:
    """One card as several (title「（续）」) when its text is too many bytes; lines are kept whole."""
    if nbytes(card) <= CARD_BYTES:
        return [card]
    base = card["title"].removesuffix("（续）")
    out, cur = [], []
    for line in card.get("description", "").split("\n"):
        trial = {**card, "description": "\n".join(cur + [line])}
        if cur and nbytes(trial) > CARD_BYTES:
            out.append(cur)
            cur = []
        cur.append(line)
    if cur:
        out.append(cur)
    return [{**card, "title": card["title"] if i == 0 else f"{base}（续）", "description": "\n".join(ls)}
            for i, ls in enumerate(out)]


def messages(cards_: list[dict]) -> list[dict]:
    """Cards grouped into messages: at most 10 cards and MESSAGE_BYTES each."""
    out, cur = [], []
    for c in (x for card in cards_ for x in split_card(card)):
        if cur and (len(cur) >= EMBEDS_PER_MESSAGE or nbytes({"embeds": cur + [c]}) > MESSAGE_BYTES):
            out.append({"embeds": cur})
            cur = []
        cur.append(c)
    if cur:
        out.append({"embeds": cur})
    return out


def _url(base: str, mid: str | None = None, **query) -> str:
    """The webhook address, or one of its messages, with extra query parameters (thread_id is kept)."""
    u = urlsplit(base)
    path = u.path.rstrip("/") + (f"/messages/{mid}" if mid else "")
    q = dict(parse_qsl(u.query))
    q.update({k: v for k, v in query.items() if v is not None})
    return urlunsplit((u.scheme, u.netloc, path, urlencode(q), ""))


def _call(method: str, url: str, what: str, **kw) -> requests.Response | None:
    """One request to Discord. A 429 (rate limit) waits the retry_after Discord gives (at most 10 s) and tries
    again, a 5xx tries once more after 2 s; any other failure prints its HTTP status and Discord's message (never
    the address)."""
    r, server_errors = None, 0
    for _ in range(4):
        try:
            r = requests.request(method, url, timeout=15, **kw)
        except Exception as e:
            print(f"discord_post: {what} failed: {type(e).__name__}")
            return None
        if r.status_code >= 500 and server_errors == 0:      # Discord's own error: one more try after 2 s
            server_errors += 1
            time.sleep(2)
            continue
        if r.status_code != 429:
            break
        try:
            wait = float(r.json().get("retry_after", 1))
        except Exception:
            wait = float(r.headers.get("Retry-After", 1) or 1)
        time.sleep(min(max(wait, 0.5), 10))
    if r is not None and r.status_code >= 300 and r.status_code != 404:
        try:
            msg = str(r.json().get("message", ""))[:200]
        except Exception:
            msg = ""
        print(f"discord_post: {what} -> HTTP {r.status_code} {msg}".rstrip())
    return r


def create(url: str, payload: dict) -> str | None:
    """Post one message and return its id (wait=true makes Discord answer with the message)."""
    r = _call("POST", _url(url, wait="true"), "create", json=payload)
    try:
        return str(r.json()["id"]) if r is not None and r.status_code < 300 else None
    except Exception:
        return None


def edit(url: str, mid: str, payload: dict) -> bool | None:
    """Replace a message; None when Discord no longer has it (deleted by hand)."""
    r = _call("PATCH", _url(url, mid), f"edit {mid}", json={"content": None, **payload})
    if r is None:
        return False
    return None if r.status_code == 404 else r.status_code < 300


def delete(url: str, mid: str) -> bool:
    r = _call("DELETE", _url(url, mid), f"delete {mid}")
    return r is not None and (r.status_code < 300 or r.status_code == 404)


def record_path(path: Path) -> Path:
    return POSTS / SENT / path.relative_to(POSTS.resolve()).with_suffix(path.suffix + ".json")


def sync(url: str, payloads: list[dict], ids: list[str]) -> tuple[list[str], bool]:
    """Make the channel show `payloads`: edit the messages already sent, add or delete the difference."""
    out, ok = [], True
    for i, pl in enumerate(payloads):
        mid = ids[i] if i < len(ids) else None
        done = edit(url, mid, pl) if mid else None
        if done:
            out.append(mid)
            continue
        if done is False:
            ok = False
            out.append(mid)
            continue
        new = create(url, pl)          # never sent, or deleted by hand: post it again
        if new:
            out.append(new)
        else:
            ok = False
    for mid in ids[len(payloads):]:
        ok = delete(url, mid) and ok
    return out, ok


def main(argv: list[str]) -> int:
    remove = "--delete" in argv
    argv = [a for a in argv if a != "--delete"]
    if len(argv) != 2 or argv[0] not in CHANNELS:
        print(f"usage: python -m hero.discord_post [--delete] <{'|'.join(CHANNELS)}> posts/<channel>/<file>.md")
        return 2
    channel, rel = argv
    path = (ROOT / rel).resolve()
    if POSTS.resolve() not in path.parents or path.suffix != ".md" or SENT in path.parts or not (path.exists() or remove):
        print(f"discord_post: only posts/**/*.md pages can be sent, not {rel}")
        return 2
    url = os.environ.get(CHANNELS[channel], "").strip()
    if not url:
        print(f"discord_post: {CHANNELS[channel]} is not set; {channel} skipped")
        return 0
    rec_path = record_path(path)
    rec = json.loads(rec_path.read_text()) if rec_path.exists() else {}
    ids = rec.get("ids", []) if rec.get("channel") == channel else []
    if remove:
        ok = all([delete(url, mid) for mid in ids])
        if ok and rec_path.exists():
            rec_path.unlink()
        print(f"discord_post: {rel} <- {channel}: {len(ids)} message(s) {'deleted' if ok else 'not all deleted'}")
        return 0 if ok else 1
    text = path.read_text()
    bad = unsafe(text)
    if bad:
        print(f"discord_post: refused, the page looks like it holds a {bad}")
        return 1
    payloads = messages(cards(text, COLOR.get(channel, GREY)))
    new_ids, ok = sync(url, payloads, ids)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if new_ids:
        rec_path.parent.mkdir(parents=True, exist_ok=True)
        rec_path.write_text(json.dumps({"channel": channel, "page": rel, "ids": new_ids,
                                        "sent": rec.get("sent") or now, **({"edited": now} if ids else {})}, indent=1) + "\n")
    print(f"discord_post: {rel} -> {channel}: {'edited' if ids else 'sent'} {len(new_ids)} message(s)"
          + ("" if ok else " (some failed)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
