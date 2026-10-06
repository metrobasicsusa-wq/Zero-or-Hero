"""Single read-only historical news access diagnostic, never a complete archive."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request
from quote_data import NoRedirect, atomic

ROOT = Path(__file__).resolve().parent


def main():
    endpoint = 'https://data.alpaca.markets/v1beta1/news'
    params = {'symbols': 'ALT', 'start': '2026-01-02T21:00:00Z', 'end': '2026-01-05T14:30:00Z',
              'sort': 'asc', 'limit': 50, 'include_content': 'false'}
    req = urllib.request.Request(endpoint + '?' + urllib.parse.urlencode(params),
        headers={'APCA-API-KEY-ID': os.environ['ALPACA_500_API_KEY'],
                 'APCA-API-SECRET-KEY': os.environ['ALPACA_500_SECRET_KEY']}, method='GET')
    report = {'endpoint': endpoint, 'parameters': params, 'checked_at': datetime.now(timezone.utc).isoformat(),
              'purpose': 'Post-case historical news access and timestamp cross-reference diagnostic; not systematic candidate screening.'}
    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=40) as response:
            payload = json.load(response)
        path = ROOT / 'raw-news-probe' / 'response.json'
        atomic(path, payload)
        articles = payload.get('news') or []
        report.update({'status': 'http_200', 'page_complete': not payload.get('next_page_token'),
                       'article_count': len(articles), 'response_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                       'source_rows': [{k: a.get(k) for k in ['headline', 'created_at', 'updated_at', 'source', 'url', 'symbols']} for a in articles]})
    except urllib.error.HTTPError as error:
        report.update({'status': 'http_' + str(error.code), 'page_complete': False, 'article_count': None})
    except (TimeoutError, urllib.error.URLError):
        report.update({'status': 'network_or_timeout_failure', 'page_complete': False, 'article_count': None})
    report['limitations'] = ['One stock/window access result does not establish complete historical catalyst coverage or paid entitlement for other routes.',
        'Aggregator created_at and current updated_at/content are not a full historical version archive; primary source and event category still require verification.',
        'No article was used to change the frozen ALT signal result. No order or subscription change was made.']
    report['broker_orders_sent'] = 0
    atomic(ROOT / 'news-access-probe.json', report)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
