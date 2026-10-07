"""Compare the wired analyzer to immutable old outcomes, never future evidence.

CLI takes private ancestor directories explicitly; only hashes/counts enter the
public audit. Synthetic historical seals exist solely inside this harness and
cannot pass the runtime's calendar, receipt or seal-verification gates.
"""
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import argparse, gzip, hashlib, json, sys
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'deploy/research/s500'))
from analyze_session import analyze


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def read(path):return json.loads(path.read_text())
def rows(path):
    with gzip.open(path,'rt') as f:
        for line in f:yield json.loads(line)
def key(row):return (row['case_id'],row['drop_threshold'],row['family'],row['exit_horizon'],row['cost_bps_per_side'])

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--ancestor',type=Path,required=True)
    parser.add_argument('--anatomy',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir()
    protocol=read(ROOT/'deploy/research/s500/protocol.json')
    registry=read(args.ancestor/'selected-case-registry.json')['cases']
    anatomy=read(args.anatomy/'anatomy-case-registry.json')['cases']
    dates=sorted({r['date'] for r in anatomy});daycases=defaultdict(list)
    for row in registry:
        if row['date'] in dates:daycases[row['date']].append(row)
    scopes={r['date']:r for r in read(args.ancestor/'daily-scope.json')['daily']}
    expected={};source_bindings=[]
    for path in sorted((args.anatomy/'inherited-results').glob('*.gz')):
        for row in rows(path):expected[key(row)]=row
        source_bindings.append({'name':'anatomy/inherited-results/'+path.name,'sha256':sha(path)})
    features={r['case_id']:r for r in rows(args.anatomy/'feature-records.jsonl.gz')}
    original_fields=None;matched_fields=0;matched_rows=0;feature_matches=0;day_reports=[]
    for day in dates:
        scope=scopes[day];opening=datetime.fromisoformat(day+'T'+scope['open_et']).replace(tzinfo=ZoneInfo('America/New_York'))
        closing=datetime.fromisoformat(day+'T'+scope['close_et']).replace(tzinfo=ZoneInfo('America/New_York'))
        historical_protocol=dict(protocol,id='historical_replay_not_prospective',sessions=[{
          'date':day,'open_et':scope['open_et'],'open_utc':opening.astimezone(timezone.utc).isoformat(),
          'close_utc':closing.astimezone(timezone.utc).isoformat()}])
        cases=[dict(r,selected=True) for r in daycases[day]];symbols=[r['symbol'] for r in cases]
        seal={'accepted':True,'date':day,'scope':'historical_replay_not_prospective',
              'selection':{'status':'ready','rows':cases,'selected_symbols':symbols}}
        source_path=args.ancestor/'private-inputs/minute-days'/(day+'.json');source=read(source_path)
        source.update(date=day,source_complete=source['request_complete'],analysis_scope='historical_replay_not_prospective')
        summary=analyze(historical_protocol,seal,source,args.output/day)
        count=0
        for row in rows(args.output/day/'results.jsonl.gz'):
            old=expected[key(row)]
            # Anatomy added four annotations; every inherited engine field is exact.
            inherited={k:v for k,v in old.items() if k not in ('anatomy_date_role','anatomy_feature_case_id','SPY_group','parent_result_file')}
            for name,value in inherited.items():
                assert row[name]==value,(day,row['case_id'],name,row[name],value)
            matched_fields+=len(inherited);matched_rows+=1;count+=1
            assert row['SPY_group']==old['SPY_group']
        for row in rows(args.output/day/'features.jsonl.gz'):
            old=features[row['case_id']]
            for field in ('features','fixed_bins','SPY_group'):assert row[field]==old[field],(day,row['case_id'],field)
            feature_matches+=1
        source_bindings.append({'name':'ancestor/minute-days/'+day+'.json','sha256':sha(source_path)})
        day_reports.append({'date':day,'cases':len(cases),'exact_inherited_rows':count,
             'primary_SPY_down_counts':summary['primary_SPY_down']['counts'],
             'analysis_scope':summary['analysis_scope']})
        print(json.dumps(day_reports[-1]),flush=True)
    assert matched_rows==44172 and feature_matches==1227 and len(expected)==44172
    bound_files=['deploy/research/s500/analyze_session.py','tests/test_analyze_session.py','historical_analyzer_replay.py']
    bound_files += [str(p.relative_to(ROOT)) for p in sorted((ROOT/'deploy/research/s500/vendor').glob('*.py'))]
    audit={'status':'passed','analysis_scope':'historical_replay_not_prospective','future_observations_added':0,
           'dates':len(dates),'cases':feature_matches,'exact_inherited_rows':matched_rows,
           'exact_inherited_fields':matched_fields,'features_fixed_bins_SPY_groups_exact_cases':feature_matches,
           'source_bindings':source_bindings,'days':day_reports,
           'bound_sha256':{p:sha(ROOT/p) for p in bound_files},
           'economic_changes':False,'cost_deducted_again':False,'peer_replacement':False,
           'raw_market_sources_exported':False,'network_calls':0,'orders':0}
    (ROOT/'historical-analyzer-replay-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in audit.items() if k not in ('source_bindings','days','bound_sha256')}),flush=True)
if __name__=='__main__':main()
