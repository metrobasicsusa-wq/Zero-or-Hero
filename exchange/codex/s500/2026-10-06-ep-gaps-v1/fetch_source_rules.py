#!/usr/bin/env python3
"""Fetch public documentation only; no credentials, raw bodies remain private."""
import concurrent.futures
import datetime as dt
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
SOURCES = {
    "alpaca_faq": "https://docs.alpaca.markets/docs/market-data-faq",
    "alpaca_stocks": "https://docs.alpaca.markets/docs/historical-stock-data-1",
    "alpaca_trades": "https://docs.alpaca.markets/reference/stocktrades-1",
    "alpaca_quotes": "https://docs.alpaca.markets/reference/stockquotes-1",
    "alpaca_trades_markdown": "https://docs.alpaca.markets/us/reference/stocktrades-1.md",
    "alpaca_quotes_markdown": "https://docs.alpaca.markets/us/reference/stockquotes-1.md",
    "alpaca_conditions": "https://docs.alpaca.markets/reference/stockconditions-1",
    "alpaca_conditions_reference": "https://docs.alpaca.markets/us/reference/stockmetaconditions-1.md",
    "alpaca_minute_article": "https://alpaca.markets/learn/stock-minute-bars/",
    "alpaca_stream": "https://docs.alpaca.markets/docs/real-time-stock-pricing-data",
    "nasdaq_halt_history": "https://www.nasdaqtrader.com/trader.aspx?id=TradingHaltHistory",
    "nasdaq_halt_search": "https://www.nasdaqtrader.com/Trader.aspx?id=TradingHaltSearch",
    "nasdaq_halt_codes": "https://www.nasdaqtrader.com/trader.aspx?id=TradeHaltCodes",
    "nyse_halts": "https://www.nyse.com/trade-halt",
    "alpaca_stocks_obsolete_path": "https://docs.alpaca.markets/docs/stock-pricing-data",
    "alpaca_trades_obsolete_path": "https://docs.alpaca.markets/reference/stocktrades",
    "alpaca_quotes_obsolete_path": "https://docs.alpaca.markets/reference/stockquotes",
    "alpaca_conditions_obsolete_path": "https://docs.alpaca.markets/reference/stockconditions",
}

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.skip=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.skip += 1
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'li', 'tr', 'br'): self.parts.append('\n')
    def handle_endtag(self, tag):
        if tag in ('script', 'style') and self.skip: self.skip -= 1
        if tag in ('p', 'div', 'h1', 'h2', 'h3', 'li', 'tr'): self.parts.append('\n')
    def handle_data(self, data):
        if not self.skip: self.parts.append(data)

def fetch(item):
    key, url = item
    record = {'key': key, 'url': url, 'retrieved_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'method':'GET', 'authorization_headers_sent':False, 'tls_verification':True}
    try:
        request = urllib.request.Request(url, headers={'User-Agent':'Research public documentation (Python urllib)'})
        with urllib.request.urlopen(request, timeout=45) as response:
            body = response.read()
            record.update(status=response.status, final_url=response.url,
                          content_type=response.headers.get('Content-Type'),
                          body_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
        (ROOT/'raw-docs'/f'{key}.raw').write_bytes(body)
        parser=Text(); parser.feed(body.decode('utf-8',errors='replace'))
        text='\n'.join(x.strip() for x in ''.join(parser.parts).splitlines() if x.strip())
        (ROOT/'raw-docs'/f'{key}.txt').write_text(text)
    except urllib.error.HTTPError as e:
        body=e.read()
        record.update(status=e.code, error='HTTPError', body_sha256=hashlib.sha256(body).hexdigest(), bytes=len(body))
        (ROOT/'raw-docs'/f'{key}.raw').write_bytes(body)
    except Exception as e:
        record.update(status=None, error=type(e).__name__)
    (ROOT/'raw-docs'/f'{key}.meta.json').write_text(json.dumps(record, indent=2)+'\n')
    return record

if __name__=='__main__':
    (ROOT/'raw-docs').mkdir(parents=True,exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        records=list(pool.map(fetch, SOURCES.items()))
    (ROOT/'source-doc-manifest.json').write_text(json.dumps({'sources':records},indent=2)+'\n')
    print(json.dumps([{'key':r['key'],'status':r['status'],'bytes':r.get('bytes')} for r in records]))
