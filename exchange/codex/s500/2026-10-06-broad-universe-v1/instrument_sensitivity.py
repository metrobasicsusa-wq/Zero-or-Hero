import hashlib
import json
from collections import defaultdict
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from research import run_path

ROOT=Path(__file__).parent


def main():
    protocol=json.loads((ROOT/'protocol.json').read_text())
    official=json.loads((ROOT/'current-instrument-classification.json').read_text())
    etfs={x['symbol'] for x in official['rows'] if x['etf']=='Y'}
    dates=[x['date'] for x in json.loads((ROOT/'raw/calendar.json').read_text()) if protocol['evaluation_start']<=x['date']<=protocol['evaluation_end']]
    candidates=json.loads((ROOT/'candidates.json').read_text())
    data={}
    for f in sorted((ROOT/'raw').glob('batch-*.json')):
        for symbol,bars in json.loads(f.read_text())['bars'].items():
            data[symbol]={datetime.fromisoformat(b['t'].replace('Z','+00:00')).astimezone(ZoneInfo('America/New_York')).date().isoformat():b for b in bars}
    signals={date:{'breakout20':[],'reversal':[]} for date in dates}
    removed=[]
    for row in candidates:
        if row['symbol'] in etfs:
            removed.append({'date':row['signal_date'],'symbol':row['symbol'],'rank':row['rank'],'triggered':row['breakout20'] or row['reversal']});continue
        if row['execution_date'] in signals:
            for rule in signals[row['execution_date']]:
                if row[rule]:signals[row['execution_date']][rule].append(row)
    paths=[run_path(dates,signals,data,rule,cost) for rule in protocol['signal_rules'] for cost in protocol['screen']['stock_costs_bps_each_side']]
    result={'status':'post_hoc_instrument_classification_sensitivity','not_independent_validation':True,
            'current_official_etfs_excluded':sorted({x['symbol'] for x in removed}),
            'removed_candidate_rows':len(removed),'paths':paths}
    checks=0
    for p in paths:
        for row in p['ledger']:
            if row['status']=='historical_model_trade':
                assert row['symbol'] not in etfs
                assert row['notional']<=row['cash_before']+1e-8
                assert abs(row['cash_after']-row['cash_before']-row['pnl'])<1e-8
                checks+=1
        if p['incomplete'] is None:assert abs(p['ending_equity']-500-sum(x.get('pnl',0) for x in p['ledger']))<1e-8
    result['accounting_checks']=checks
    (ROOT/'instrument-sensitivity.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({**{k:v for k,v in result.items() if k!='paths'},'paths':[{k:v for k,v in p.items() if k!='ledger'} for p in paths]},indent=2))


if __name__=='__main__':main()
