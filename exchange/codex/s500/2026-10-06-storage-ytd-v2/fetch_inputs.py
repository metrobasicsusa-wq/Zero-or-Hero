"""Fetch versioned research inputs; credentials are never written or printed."""
import hashlib
import json
import os
from pathlib import Path
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ROOT = Path(__file__).parent


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('redirect_blocked')


def get(host, path, query):
    request = urllib.request.Request('https://' + host + path + '?' + urllib.parse.urlencode(query),
        headers={'APCA-API-KEY-ID': os.environ['ALPACA_500_API_KEY'],
                 'APCA-API-SECRET-KEY': os.environ['ALPACA_500_SECRET_KEY']})
    with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as r:
        return json.load(r)


def main():
    protocol = json.loads((ROOT / 'protocol.json').read_text())
    folder = ROOT / 'raw'
    folder.mkdir(exist_ok=True)
    manifest = {'retrieved_start': datetime.now(timezone.utc).isoformat(),
                'protocol_sha256': hashlib.sha256((ROOT / 'protocol.json').read_bytes()).hexdigest(),
                'source': 'Alpaca SIP raw historical 5-minute bars', 'files': []}
    def save(name, data):
        content = json.dumps(data, sort_keys=True).encode()
        (folder / name).write_bytes(content)
        manifest['files'].append({'name': name, 'sha256': hashlib.sha256(content).hexdigest(),
                                  'bytes': len(content)})
    calendar = get('paper-api.alpaca.markets', '/v2/calendar', {'start':protocol['input']['start'][:10],'end':protocol['evaluation_end']})
    save('calendar.json', calendar)
    for symbol in protocol['universe'] + protocol['comparators']:
        query = {k: protocol['input'][k] for k in ['timeframe','start','end','feed','adjustment']}
        query.update(symbols=symbol,limit=10000,sort='asc')
        bars=[]; pages=0; seen=set()
        while True:
            response=get('data.alpaca.markets','/v2/stocks/bars',query)
            bars.extend(response.get('bars',{}).get(symbol,[]));pages+=1
            token=response.get('next_page_token')
            if not token:break
            if token in seen:raise RuntimeError('pagination_loop')
            seen.add(token);query['page_token']=token
        save(symbol+'.json', {'symbol':symbol,'bars':bars,'pages':pages})
        print(json.dumps({'symbol':symbol,'bars':len(bars),'pages':pages}),flush=True)
    manifest['retrieved_end']=datetime.now(timezone.utc).isoformat()
    (ROOT/'input-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')


if __name__ == '__main__':main()
