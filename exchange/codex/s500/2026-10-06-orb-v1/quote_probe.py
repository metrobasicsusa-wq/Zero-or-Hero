"""Bounded read-only quote feasibility probe at the first held-minute gap.

This does not repair or rerun the registered bar strategy. No order APIs.
"""
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from market_data import NoRedirect, atomic

ROOT = Path(__file__).resolve().parent


def main():
    parameters = {'start': '2026-01-06T14:49:00Z', 'end': '2026-01-06T14:51:00Z',
                  'limit': 10000, 'feed': 'sip', 'sort': 'asc'}
    base = 'https://data.alpaca.markets/v2/stocks/PRCT/quotes'
    folder = ROOT / 'raw-quote-probe'
    request_parameters = dict(parameters)
    rows = []
    evidence = []
    seen = set()
    complete = False
    status = None
    for page in range(5):
        request = urllib.request.Request(base + '?' + urllib.parse.urlencode(request_parameters),
            headers={'APCA-API-KEY-ID': os.environ['ALPACA_500_API_KEY'],
                     'APCA-API-SECRET-KEY': os.environ['ALPACA_500_SECRET_KEY']}, method='GET')
        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=40) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as error:
            status = 'http_' + str(error.code)
            break
        quotes = payload.get('quotes', []) or []
        path = folder / ('page-%04d.json' % page)
        atomic(path, {'retrieved_at': datetime.now(timezone.utc).isoformat(),
                     'parameters': request_parameters, 'response': payload})
        evidence.append({'name': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'bytes': path.stat().st_size, 'quote_count': len(quotes)})
        rows.extend(quotes)
        token = payload.get('next_page_token')
        if not token:
            complete = True
            status = 'http_200_pages_complete'
            break
        if token in seen:
            status = 'pagination_cycle'
            break
        seen.add(token)
        request_parameters['page_token'] = token
    if status is None:
        status = 'page_budget_reached'
    valid = [r for r in rows if r.get('bp', 0) > 0 and r.get('ap', 0) >= r['bp']]
    report = {'kind': 'post-failure quote-access feasibility; not simulated execution',
              'checked_at': datetime.now(timezone.utc).isoformat(), 'endpoint': base,
              'parameters': parameters, 'status': status, 'pages_complete': complete,
              'pages': evidence, 'quote_rows': len(rows), 'positive_noncrossed_quotes': len(valid),
              'earliest_quote': min((r['t'] for r in rows), default=None),
              'latest_quote': max((r['t'] for r in rows), default=None),
              'bid_price_range': [min(r['bp'] for r in valid), max(r['bp'] for r in valid)] if valid else None,
              'ask_price_range': [min(r['ap'] for r in valid), max(r['ap'] for r in valid)] if valid else None,
              'spread_range': [min(r['ap']-r['bp'] for r in valid), max(r['ap']-r['bp'] for r in valid)] if valid else None,
              'limitations': ['A quote response does not prove fills, absence of hidden gaps, current quote freshness or full-year quote coverage.',
                'Original bar-based paths remain incomplete. Changing to quote-based triggers requires a separately specified execution experiment.',
                'The query samples the first held-bar failure after observing it; it is a data-source feasibility diagnostic, not an unbiased strategy test.'],
              'broker_orders_sent': 0}
    atomic(ROOT / 'quote-probe.json', report)
    print(json.dumps({k: v for k, v in report.items() if k != 'pages'}, indent=2))


if __name__ == '__main__':
    main()
