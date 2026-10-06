"""Download bounded stages, persist each completed page, never submit orders."""
import argparse,json,time,hashlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
from market_data import ROOT,fetch_window,load_window,local_time,atomic

def main():
    ap=argparse.ArgumentParser();ap.add_argument('stage',choices=['openings','intraday']);ap.add_argument('--workers',type=int,default=4);args=ap.parse_args()
    if not 1<=args.workers<=4:raise ValueError('workers_exceed_budget')
    prep=json.loads((ROOT/'prepare.json').read_text());stage=args.stage;tasks=[]
    if stage=='openings':
        for day,symbols in sorted(prep['opening_requests'].items()):
            symbols=sorted(set(symbols))
            for offset in range(0,len(symbols),200):tasks.append((day,symbols[offset:offset+200],'09:30','09:35','5Min',ROOT/'raw-openings'/day/f'batch-{offset:04d}'))
    else:
        ranked=json.loads((ROOT/'ranked.json').read_text());calendar={d['date']:d for d in prep['calendar']}
        for day,rows in sorted(ranked.items()):
            symbols=sorted(set(r['symbol'] for r in rows))
            if symbols:tasks.append((day,symbols,'09:30',calendar[day]['close'],'1Min',ROOT/'raw-intraday'/day))
    completed=0;start=time.monotonic();failures=[]
    def one(task):
        day,symbols,a,b,frame,folder=task
        result=fetch_window(symbols,day,a,b,frame,folder)
        return {'date':day,'directory':folder.relative_to(ROOT).as_posix(),'requested':len(symbols),'bar_count':sum(p['bar_count'] for p in result['pages']),'request_sha256':result['request_sha256'],'pages':result['pages']}
    print(json.dumps({'stage':stage,'request_chunks':len(tasks),'date_count':len(set(t[0] for t in tasks)),'symbol_days':sum(len(t[1]) for t in tasks)}),flush=True)
    records=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(one,t):t for t in tasks}
        for future in as_completed(futures):
            t=futures[future]
            try:records.append(future.result())
            except Exception as e:failures.append({'date':t[0],'directory':t[-1].relative_to(ROOT).as_posix(),'error':str(e)[:120]})
            completed+=1
            if completed%50==0 or completed==len(tasks):
                print(json.dumps({'stage':stage,'chunks_completed':completed,'total':len(tasks),'failures':len(failures),'seconds':round(time.monotonic()-start)}),flush=True)
                atomic(ROOT/(stage+'-progress.json'),{'completed_chunks':completed,'total_chunks':len(tasks),'failures':failures,'updated_at':datetime.now(timezone.utc).isoformat()})
    manifest={'stage':stage,'completed_at':datetime.now(timezone.utc).isoformat(),'request_chunks':len(tasks),'records':sorted(records,key=lambda r:r['directory']),'failures':failures,'prepare_sha256':hashlib.sha256((ROOT/'prepare.json').read_bytes()).hexdigest(),'source':'Alpaca SIP RAW market-data GET only; endpoints/requests stored in private raw directories','all_pages_complete':not failures}
    atomic(ROOT/(stage+'-manifest.json'),manifest)
    if failures:raise RuntimeError('input_chunks_incomplete_see_manifest')
    market={};validation=[]
    for record in records:
        day=record['date'];market.setdefault(day,{})
        for symbol,bars in load_window(ROOT/record['directory']).items():
            if symbol in market[day]:raise RuntimeError('duplicate_symbol_day')
            if stage=='openings':
                first=[b for b in bars if local_time(b).date().isoformat()==day and local_time(b).strftime('%H:%M')=='09:30']
                if len(first)>1:raise RuntimeError('duplicate_opening_bar')
                if first:market[day][symbol]=first[0]
            else:
                daymap={}
                for b in bars:
                    stamp=local_time(b);offset=stamp.hour*60+stamp.minute-570
                    if stamp.date().isoformat()==day and 0<=offset<390:
                        if offset in daymap:raise RuntimeError('duplicate_minute')
                        daymap[offset]=b
                market[day][symbol]=daymap
    target='opening-market.json' if stage=='openings' else 'minute-market.json';atomic(ROOT/target,market)
    print(json.dumps({'stage':stage,'output':target,'dates':len(market),'symbols_days_returned':sum(len(rows) for rows in market.values()),'all_pages_complete':True,'seconds':round(time.monotonic()-start)}),flush=True)
if __name__=='__main__':main()
