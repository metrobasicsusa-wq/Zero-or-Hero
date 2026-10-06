"""Offline deterministic replay of all derived budget rows; no network or credentials."""
from pathlib import Path
import json,gzip
from option_feasibility import evaluate_quote,evaluate_trade_price_proxy
R=Path(__file__).resolve().parent
def read(name):
 p=R/name
 return json.loads(p.read_bytes() if p.exists() else gzip.decompress(Path(str(p)+'.gz').read_bytes()))
policy={'budget':'500','roundtrip_reserves_per_contract':['0','0.10','1'],'max_age_seconds':'5','max_relative_spread_mid':'0.10','study_cutoff_date':'2026-10-05'}
selected=read('selected-contracts.json');contracts={(r['case_id'],r['type']):r.get('contract') or {} for group in selected.values() if isinstance(group,list) for r in group}
count=0
for r in read('historical-reference-results.json')['rows']:
 t=r['source_event_evidence'].get('first_trade') or {}
 result=evaluate_trade_price_proxy(contracts[(r['case_id'],r['type'])],t.get('p'),{'received_at':None,'feed':'historical_trade_api_documented_default'},policy)
 assert result==r['analysis'],(r['case_id'],r['type']);count+=1
for r in read('current-indicative-results.json')['rows']:
 result=evaluate_quote(contracts[(r['case_id'],r['type'])],r['source_quote_evidence'].get('quote'),r['observation_assumptions'],policy)
 assert result==r['analysis'],(r['case_id'],r['type']);count+=1
assert count==880
print(json.dumps({'rows_exactly_replayed':count,'budget_scenarios':count*3,'network_requests':0,'executable_quotes_verified':0,'strategy_returns_computed':False}))
