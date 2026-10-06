#!/usr/bin/env python3
"""Record the actual bounded query failures; never infer no halt from silence."""
import datetime as dt
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent

def main():
    attempts=json.loads((ROOT/'halt-query-attempts.json').read_text())['attempts']
    assert len(attempts)==2 and {r['symbol'] for r in attempts}=={'REPL','SPCE'}
    for row in attempts:
        body=(ROOT/'raw-halts'/(row['symbol']+'__attempt1.raw')).read_bytes()
        assert hashlib.sha256(body).hexdigest()==row['body_sha256']
        assert row['http_status']==200 and b'_Incapsula_Resource' in body
        row.update(result_status='challenge_page_not_event_data',parsed_event_records=None,
            halt_at_target=None,absence_of_halt_established=False,
            result_explanation='HTTP 200 body is an Incapsula JavaScript challenge page, not a halt result table/JSON. No challenge bypass or retry attempted.')
    supporting=[]
    for key in ['nasdaq_halt_search','nasdaq_rpcclient','nyse_halts']:
        record=json.loads((ROOT/'raw-docs'/(key+'.meta.json')).read_text())
        assert hashlib.sha256((ROOT/'raw-docs'/(key+'.raw')).read_bytes()).hexdigest()==record['body_sha256']
        supporting.append(record)
    result={'schema_version':1,'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
        'scope':'Bounded event-specific halt checks for only the two no-trade/no-quote target minutes.',
        'max_event_queries_per_symbol':2,'event_queries_per_symbol':{'REPL':1,'SPCE':1},
        'attempts':attempts,'supporting_source_captures':supporting,'nyse_event_query_attempts':0,
        'nyse_status':'Dynamic historical component observed; no confirmed historical query endpoint or parameters in captured page. No guessed NYSE request.',
        'source_method_status':'Nasdaq page supplied read-only SearchTradeHaltsNEW method and seven-argument ordering. Its linked rpcclient.axd returned Request is not valid; RPCHandler.axd transport route remained an unverified conventional candidate.',
        'classification':{'REPL':'unknown','SPCE':'unknown'},'halt_explanations_confirmed':0,
        'no_halt_claims':0,'raw_bodies_public':False,
        'code_sha256':hashlib.sha256((ROOT/'query_halts.py').read_bytes()).hexdigest(),
        'summarizer_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    (ROOT/'halt-query-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'REPL':'unknown','SPCE':'unknown','event_queries':2,'challenge_pages':2}))

if __name__=='__main__':main()
