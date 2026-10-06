"""Bounded credential-free public-source completion, preserving all prior attempts."""
from pathlib import Path
from datetime import datetime, timezone
from html.parser import HTMLParser
from concurrent.futures import ThreadPoolExecutor
import hashlib
import html
import json
import urllib.request
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parent


class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts=[]; self.links=[]; self.hidden=0
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden += 1
        if tag in ('div','p','br','h1','h2','li','tr'): self.parts.append('\n')
        if tag=='a' and dict(attrs).get('href'): self.links.append(dict(attrs)['href'])
    def handle_endtag(self,tag):
        if tag in ('script','style'): self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden: self.parts.append(data)


def prior_count(group,symbol):
    folder=ROOT/f'raw-source-group-{group}'
    if group==4:return len(list(folder.glob(symbol+'__*-meta.json')))
    return len(list((folder/symbol).glob('*-meta.json')))+len(list((folder/'sub-six'/symbol).glob('*-meta.json')))


def fetch(task):
    group,symbol,key,url=task
    folder=ROOT/'raw-primary-root'/f'group-{group}'/symbol
    path=folder/(key+'-meta.json')
    if path.exists():
        value=json.loads(path.read_text());assert value['url']==url;return value
    count=prior_count(group,symbol)+len(list(folder.glob('*-meta.json')))
    if count>=8:return {'symbol':symbol,'url':url,'status':'budget_exhausted_preserved_unknown','requests_already':count}
    assert url.startswith('https://') and not any(x in url for x in ['alpaca.markets','api.github.com'])
    folder.mkdir(parents=True,exist_ok=True)
    meta={'group':group,'symbol':symbol,'key':key,'url':url,'retrieved_at':datetime.now(timezone.utc).isoformat(),'case_request_index':count+1}
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'Codex Research Assistant public source verification'}),timeout=30) as response:
            body=response.read();meta.update(status=response.status,final_url=response.url)
    except urllib.error.HTTPError as error:
        body=error.read();meta['status']=error.code
    except (urllib.error.URLError,TimeoutError):
        body=b'';meta['status']='transport_error'
    meta.update(bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
    (folder/(key+'.raw')).write_bytes(body)
    parser=Text();parser.feed(body.decode('utf8','replace'))
    (folder/(key+'.txt')).write_text('\n'.join(x.strip() for x in ''.join(parser.parts).splitlines() if x.strip()))
    (folder/(key+'-links.json')).write_text(json.dumps(parser.links))
    path.write_text(json.dumps(meta,indent=2)+'\n')
    return meta


if __name__=='__main__':
    tasks=[(1,'RNG','wire','https://www.businesswire.com/news/home/20260219280807/en/'),
           (2,'MXL','wire','https://www.businesswire.com/news/home/20260423626027/en/')]
    queries={
        (1,'ICHR'):'Ichor Holdings February 9 2026 fourth quarter results press release',
        (1,'PDYN'):'Palladyne AI March 5 2026 fourth quarter results press release',
        (1,'MRVL'):'Marvell March 5 2026 fourth quarter results press release',
        (1,'ZD'):'Ziff Davis March 2 2026 connectivity division Accenture definitive agreement',
        (2,'FLNC'):'Fluence Energy May 7 2026 second quarter results press release'}
    for (group,symbol),query in queries.items():
        tasks.append((group,symbol,'ddg','https://html.duckduckgo.com/html/?q='+urllib.parse.quote(query)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        for result in pool.map(fetch,tasks):
            print(json.dumps({k:result.get(k) for k in ['group','symbol','key','status','case_request_index']}),flush=True)
