"""Fetch a bounded official calendar source set through inherited proxy/TLS.

No credentials, account API, prices or order routes. Cumulative maximum 20 GETs.
Redirects are recorded and count toward the cap; hosts are paced at <= 1 GET/s.
"""
import argparse
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Readable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.links = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.skip += 1
        if tag in {"p", "div", "tr", "td", "th", "h1", "h2", "h3", "h4", "li", "br"}:
            self.text.append("\n")
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.skip = max(0, self.skip - 1)
        if tag in {"p", "div", "tr", "td", "th", "h1", "h2", "h3", "h4", "li"}:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.text.append(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    private = root / "private-macro"
    private.mkdir(exist_ok=True)
    manifest_path = root / "macro-source-manifest.json"
    entries = json.loads(manifest_path.read_text())["requests"] if manifest_path.exists() else []
    assert not any(row["document_id"] == args.id for row in entries), "Existing document ID will not be overwritten"
    allowed_hosts = {"www.bls.gov", "bls.gov", "www.bea.gov", "bea.gov", "apps.bea.gov", "www.federalreserve.gov", "federalreserve.gov"}
    current = args.url
    opener = build_opener(NoRedirect)
    for hop in range(4):
        assert len(entries) < 20, "20 GET cumulative limit"
        host = urlsplit(current).hostname
        assert urlsplit(current).scheme == "https" and host in allowed_hosts
        assert not any(row["host"] == host and row["status"] in {401, 403} for row in entries), "Host has prior access denial; no retry"
        past = [datetime.fromisoformat(row["started_at_utc"]).timestamp() for row in entries if row["host"] == host]
        if past:
            time.sleep(max(0, 1.05 - (time.time() - max(past))))
        started = datetime.now(timezone.utc).isoformat()
        request = Request(current, headers={"User-Agent": "HistoricalCalendarResearch/1.0", "Accept": "text/html,application/xhtml+xml,text/calendar;q=0.8,*/*;q=0.5"})
        error = None
        try:
            response = opener.open(request, timeout=40)
            body = response.read()
            status, headers, response_url = response.status, response.headers, response.geturl()
        except HTTPError as exc:
            status, headers, response_url = exc.code, exc.headers, exc.geturl()
            body = exc.read()
            error = f"HTTP {status}"
        except URLError as exc:
            status, headers, response_url, body = None, {}, current, b""
            error = type(exc.reason).__name__
        filename = f"{args.id}-{hop}.body"
        (private / filename).write_bytes(body)
        readable = Readable()
        readable.feed(body.decode("utf-8", errors="replace"))
        plain = "\n".join(line.strip() for line in "".join(readable.text).splitlines() if line.strip())
        (private / filename.replace(".body", ".txt")).write_text(plain)
        links = sorted({urljoin(response_url, href) for href in readable.links})
        (private / filename.replace(".body", ".links.json")).write_text(json.dumps(links, indent=2) + "\n")
        location = headers.get("Location")
        row = {"document_id": args.id, "redirect_hop": hop, "host": host, "requested_url": current, "initial_url": args.url, "final_url": response_url, "started_at_utc": started, "retrieved_at_utc": datetime.now(timezone.utc).isoformat(), "status": status, "content_type": headers.get("Content-Type"), "body_bytes": len(body), "body_sha256": hashlib.sha256(body).hexdigest(), "raw_relative_path": "private-macro/" + filename, "redirect_location": urljoin(response_url, location) if location else None, "error": error}
        entries.append(row)
        manifest = {"purpose": "Limited retrospective official macro calendar review; no market data or accounts", "maximum_gets": 20, "requests": entries}
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        print(json.dumps({key: row[key] for key in ["document_id", "redirect_hop", "status", "final_url", "body_bytes", "redirect_location"]}))
        if status in {301, 302, 303, 307, 308} and location:
            current = row["redirect_location"]
        else:
            break


if __name__ == "__main__":
    main()
