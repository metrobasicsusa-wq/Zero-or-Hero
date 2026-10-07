"""Independent post-result descriptive contribution check; no selection changes."""
from pathlib import Path
from collections import Counter,defaultdict
from fractions import Fraction
from decimal import Decimal,localcontext
from datetime import datetime,timezone
import gzip,hashlib,json
ROOT=Path(__file__).resolve().parent
STORAGE=['MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P']
F=lambda x:Fraction(Decimal(x))
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def equal(actual,expected):
    if expected is None:assert actual is None
    else:
        with localcontext() as c:
            c.prec=70;d=Decimal(expected.numerator)/Decimal(expected.denominator)
            assert abs(Decimal(actual)-d)<Decimal('1e-50'),(actual,str(d))
def mean(items):return sum(items,Fraction(0))/len(items) if items else None

def main():
    source=ROOT/'primary-concentration.json';output=json.loads(source.read_text());rows=[]
    for path in sorted((ROOT/'results').glob('*.gz')):
        with gzip.open(path,'rt') as f:
            for line in f:
                r=json.loads(line)
                if (r['target_role'],r['drop_threshold'],r['family'],r['exit_horizon'],r['cost_bps_per_side'])==('stock','0.02','REBOUND','60m',10):rows.append(r)
    signals=[r for r in rows if r['signal_status']=='signal'];pairs=[r for r in rows if r['matched_excess'] is not None];returns=[r for r in rows if r['net_return'] is not None]
    denom=Counter(r['date'] for r in pairs);assert dict(denom)==output['date_pair_denominators']
    assert len(rows)==19137 and len(pairs)==2203 and len(signals)==2681
    groups=defaultdict(list)
    for r in rows:groups[r['symbol']].append(r)
    def verify(item,subset):
        s=[r for r in subset if r['signal_status']=='signal'];ret=[r for r in subset if r['net_return'] is not None];p=[r for r in subset if r['matched_excess'] is not None]
        for field,data in [('selected',subset),('signal',s),('complete_return',ret),('complete_pair',p)]:
            rowkey={'selected':'selected_rows','signal':'signals','complete_return':'complete_returns','complete_pair':'complete_pairs'}[field]
            assert item[rowkey]==len(data)
            assert item[field+'_dates']==len({r['date'] for r in data})
        assert item['signal_status_counts']==dict(Counter(r['signal_status'] for r in subset))
        excess=[F(r['matched_excess']) for r in p]
        total=sum(excess,Fraction(0))
        equal(item['selected_date_fraction_of_191'],Fraction(len({r['date'] for r in subset}),191))
        equal(item['signal_rate_given_selected'],Fraction(len(s),len(subset)) if subset else None)
        equal(item['signal_fraction_of_all_signals'],Fraction(len(s),len(signals)))
        equal(item['complete_pair_fraction_of_all_pairs'],Fraction(len(p),len(pairs)))
        equal(item['mean_net_return_complete_returns'],mean([F(r['net_return']) for r in ret]))
        equal(item['mean_target_net_return_complete_pairs'],mean([F(r['net_return']) for r in p]))
        equal(item['mean_matched_control_net_return_complete_pairs'],mean([F(r['net_return'])-F(r['matched_excess']) for r in p]))
        equal(item['mean_matched_excess_complete_pairs'],mean(excess))
        equal(item['sum_matched_excess'],total)
        equal(item['pooled_matched_excess_contribution'],total/len(pairs))
        equal(item['date_equal_matched_excess_contribution'],sum((F(r['matched_excess'])/denom[r['date']] for r in p),Fraction(0))/191)
    assert {x['symbol'] for x in output['all_symbols']}==set(groups)
    for item in output['all_symbols']:verify(item,groups[item['symbol']])
    storage=output['fixed_storage_group'];assert storage['symbols']==STORAGE
    verify(storage,[r for r in rows if r['symbol'] in STORAGE])
    assert [r['symbol'] for r in storage['symbol_rows']]==STORAGE
    for item in storage['symbol_rows']:verify(item,groups.get(item['symbol'],[]))
    for mode,field in [('date_equal','date_equal_matched_excess_contribution'),('pooled','pooled_matched_excess_contribution')]:
        for side,sign in [('positive',1),('negative',-1)]:
            selected=[r for r in output['all_symbols'] if Decimal(r[field])*sign>0]
            expected=sorted(selected,key=lambda r:(-sign*Decimal(r[field]),r['symbol']))[:10]
            assert output['rankings'][mode][side+'_top10']==expected
    for name,field in [('signal_frequency_top10','signals'),('complete_pair_frequency_top10','complete_pairs')]:
        assert output['rankings'][name]==sorted(output['all_symbols'],key=lambda r:(-r[field],r['symbol']))[:10]
    result={'audit_version':'independent_concentration_audit_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,
        'all_symbol_rows':len(groups),'storage_symbols_retained':8,'primary_cases':len(rows),'complete_pairs':len(pairs),'source_sha256':sha(source),'audit_code_sha256':sha(Path(__file__)),
        'checks':'All per-symbol counts, means, exact-Fraction signed contributions, date denominators, positive/negative/frequency rankings and fixedstoragegroup recomputed from frozen primaryledger.',
        'interpretation':'Post-result descriptive contribution only; no new exclusion, strategy selection or causal/profit inference.'}
    (ROOT/'independent-concentration-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
