import concurrent.futures, datetime, hashlib, json, os, pathlib, sys, urllib.request, urllib.error
from html.parser import HTMLParser

BASE = pathlib.Path(__file__).resolve().parent
class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.hidden = 0; self.parts=[]; self.links=[]
    def handle_starttag(self, tag, attrs):
        if tag in ('script','style'): self.hidden += 1
        if tag in ('p','div','h1','h2','h3','h4','br','li','tr','td','th'): self.parts.append('\n')
        if tag=='a':
            href=dict(attrs).get('href')
            if href: self.links.append(href)
    def handle_endtag(self, tag):
        if tag in ('script','style') and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)

def fetch(item):
    key,url=item
    now=datetime.datetime.now(datetime.timezone.utc).isoformat().replace('+00:00','Z')
    result={'id':key,'requested_url':url,'retrieved_at_utc':now}
    req=urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0 (compatible; public-documentation-review/1.0)'})
    try:
        with urllib.request.urlopen(req,timeout=45) as r:
            body=r.read(); result.update(final_url=r.url,http_status=r.status,content_type=r.headers.get('Content-Type'))
    except urllib.error.HTTPError as e:
        body=e.read();result.update(final_url=e.url,http_status=e.code,content_type=e.headers.get('Content-Type'),error='HTTPError')
    except Exception as e:
        body=b'';result.update(error=type(e).__name__,error_message=str(e))
    result['body_sha256']=hashlib.sha256(body).hexdigest(); result['body_bytes']=len(body)
    path=BASE/'private'/'raw'/(key+'.body')
    with open(path,'xb') as f: f.write(body)
    os.chmod(path,0o600)
    result['private_raw_relative_path']='private/raw/'+key+'.body'
    parser=Text(); parser.feed(body.decode('utf-8','replace'))
    text='\n'.join(s.strip() for s in ''.join(parser.parts).splitlines() if s.strip())
    textpath=BASE/'private'/(key+'.txt');textpath.write_text(text);os.chmod(textpath,0o600)
    linkpath=BASE/'private'/(key+'.links.json');linkpath.write_text(json.dumps(parser.links,indent=2));os.chmod(linkpath,0o600)
    result['readable_text_characters']=len(text)
    return result

if __name__=='__main__':
    entries=json.loads(sys.argv[1])
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool: results=list(pool.map(fetch,entries))
    manifest=BASE/'manifest.json';prior=json.loads(manifest.read_text()) if manifest.exists() else []
    manifest.write_text(json.dumps(prior+results,indent=2)+'\n')
    for result in results: print(json.dumps(result))
