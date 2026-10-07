"""Post-result descriptive concentration; no model changes or exclusion optimization."""
import gzip,hashlib,json
from pathlib import Path
from collections import Counter,defaultdict
from decimal import Decimal,getcontext
from datetime import datetime,timezone
ROOT=Path(__file__).parent;D=Decimal;getcontext().prec=60;SESSIONS=191
STORAGE=['MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P']
EXPECTED_DATE_EQUAL=D('0.0005955449036716643')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def total(xs):return sum(xs,D(0))
def mean(xs):return total(xs)/len(xs) if xs else None
def textdec(x):return str(x) if x is not None else None
def is_primary(r):return r['target_role']=='stock' and D(r['drop_threshold'])==D('.02') and r['family']=='REBOUND' and r['exit_horizon']=='60m' and r['cost_bps_per_side']==10
def subset_metrics(rows):
 return {'rows':len(rows),'unique_dates':len({r['date'] for r in rows}),'unique_symbols':len({r['symbol'] for r in rows})}
def main():
 files=sorted((ROOT/'results').glob('*.jsonl.gz'));assert len(files)==10
 source=[];rows=[]
 for f in files:
  source.append({'name':'results/'+f.name,'sha256':sha(f),'bytes':f.stat().st_size})
  with gzip.open(f,'rt') as stream:
   for line in stream:
    r=json.loads(line)
    if is_primary(r):rows.append(r)
 assert len(rows)==19137 and len({r['case_id'] for r in rows})==len(rows)
 assert len({r['date'] for r in rows})==SESSIONS
 signals=[r for r in rows if r['signal_status']=='signal'];returns=[r for r in signals if r['net_return'] is not None];pairs=[r for r in signals if r['matched_excess'] is not None]
 assert all(r['paired_control_status']=='complete_three' and r['net_return'] is not None for r in pairs)
 day_n=Counter(r['date'] for r in pairs);assert len(day_n)==SESSIONS
 N=len(pairs);by_symbol=defaultdict(list)
 for r in rows:by_symbol[r['symbol']].append(r)
 def summarize(symbol,subset):
  sig=[r for r in subset if r['signal_status']=='signal'];ret=[r for r in sig if r['net_return'] is not None];pair=[r for r in sig if r['matched_excess'] is not None]
  returns_d=[D(r['net_return']) for r in ret];excess=[D(r['matched_excess']) for r in pair];pair_target=[D(r['net_return']) for r in pair];pair_control=[D(r['net_return'])-D(r['matched_excess']) for r in pair]
  pooled_contribution=total(excess)/N
  dateequal_contribution=total(D(r['matched_excess'])/day_n[r['date']] for r in pair)/SESSIONS
  return {'symbol':symbol,'selected_rows':len(subset),'selected_dates':len({r['date'] for r in subset}),'selected_date_fraction_of_191':textdec(D(len({r['date'] for r in subset}))/SESSIONS),'signals':len(sig),'signal_dates':len({r['date'] for r in sig}),'complete_returns':len(ret),'complete_return_dates':len({r['date'] for r in ret}),'complete_pairs':len(pair),'complete_pair_dates':len({r['date'] for r in pair}),'signal_status_counts':dict(sorted(Counter(r['signal_status'] for r in subset).items())),'signal_rate_given_selected':textdec(D(len(sig))/len(subset)) if subset else None,'signal_fraction_of_all_signals':textdec(D(len(sig))/len(signals)),'complete_pair_fraction_of_all_pairs':textdec(D(len(pair))/N),'mean_net_return_complete_returns':textdec(mean(returns_d)),'mean_target_net_return_complete_pairs':textdec(mean(pair_target)),'mean_matched_control_net_return_complete_pairs':textdec(mean(pair_control)),'mean_matched_excess_complete_pairs':textdec(mean(excess)),'sum_matched_excess':textdec(total(excess)),'pooled_matched_excess_contribution':textdec(pooled_contribution),'date_equal_matched_excess_contribution':textdec(dateequal_contribution),'storage_overlay_member':symbol in STORAGE,'storage_classification_verified':False if symbol=='P' else None}
 symbols=[summarize(s,by_symbol[s]) for s in sorted(by_symbol)]
 pooled=mean([D(r['matched_excess']) for r in pairs]);dateequal=total(total(D(r['matched_excess']) for r in pairs if r['date']==day)/day_n[day] for day in sorted(day_n))/SESSIONS
 bysymbol_pooled=total(D(r['pooled_matched_excess_contribution']) for r in symbols);bysymbol_dateequal=total(D(r['date_equal_matched_excess_contribution']) for r in symbols)
 auditfile=ROOT/'independent-actual-audit.json';audit=json.loads(auditfile.read_text());a=audit['primary_independent_summary'];auditvalue=D(str(a['equally_weighted_date_matched_excess_mean']));auditpooled=D(str(a['pooled_matched_excess_mean']))
 assert len(signals)==a['primary_signal_count']==2681 and len(returns)==a['primary_completed_return_count']==2542 and len(pairs)==a['complete_pairs']==2203
 assert abs(pooled-bysymbol_pooled)<D('1e-50') and abs(dateequal-bysymbol_dateequal)<D('1e-50')
 assert abs(dateequal-EXPECTED_DATE_EQUAL)<D('1e-15') and abs(dateequal-auditvalue)<D('1e-15') and abs(pooled-auditpooled)<D('1e-15')
 def rankings(field):
  positive=sorted((s for s in symbols if D(s[field])>0),key=lambda s:(-D(s[field]),s['symbol']))[:10]
  negative=sorted((s for s in symbols if D(s[field])<0),key=lambda s:(D(s[field]),s['symbol']))[:10]
  return {'positive_top10':positive,'negative_top10':negative}
 storage_subset=[r for r in rows if r['symbol'] in STORAGE];storage=summarize('fixed_storage_group_aggregate',storage_subset);storage['symbols']=STORAGE;storage['storage_overlay_member']=None;storage['is_fixed_storage_group']=True;storage['selected_unique_symbols']=len({r['symbol'] for r in storage_subset});storage['unique_complete_return_symbols']=len({r['symbol'] for r in storage_subset if r['signal_status']=='signal' and r['net_return'] is not None});storage['classification_caveat']='P is only a predesignated overlay label; its storage-industry classification remains unverified.';storage['symbol_rows']=[summarize(s,by_symbol.get(s,[])) for s in STORAGE];storage['unique_signal_symbols']=len({r['symbol'] for r in storage_subset if r['signal_status']=='signal'});storage['unique_complete_pair_symbols']=len({r['symbol'] for r in storage_subset if r['matched_excess'] is not None})
 output={'id':'s500_flush_primary_descriptive_concentration_v1','created_at':datetime.now(timezone.utc).isoformat(),'analysis_timing':'post_result_descriptive_only','filter':{'target_role':'stock','drop_threshold':'0.02','family':'REBOUND','exit_horizon':'60m','cost_bps_per_side':10},'calendar_sessions':SESSIONS,'counts':{'all_selected':subset_metrics(rows),'all_signals':subset_metrics(signals),'complete_returns':subset_metrics(returns),'complete_pairs':subset_metrics(pairs)},'primary_point':{'pooled_matched_excess_mean':textdec(pooled),'date_equal_matched_excess_mean':textdec(dateequal),'mean_net_return_complete_returns':textdec(mean([D(r['net_return']) for r in returns]))},'formulas':{'pooled_symbol_contribution':'sum(symbol complete-pair matched excess) / all 2203 complete pairs','date_equal_symbol_contribution':'sum(symbol matched excess / complete-pair count on its date) / all 191 calendar dates','symbol_means':'Complete observations only; missing outcomes remain missing, zero-count means are null.','frequency':'Observed participation in this frozen sample, not inferred population probability.'},'all_symbols':symbols,'rankings':{'date_equal':rankings('date_equal_matched_excess_contribution'),'pooled':rankings('pooled_matched_excess_contribution'),'signal_frequency_top10':sorted(symbols,key=lambda s:(-s['signals'],s['symbol']))[:10],'complete_pair_frequency_top10':sorted(symbols,key=lambda s:(-s['complete_pairs'],s['symbol']))[:10]},'fixed_storage_group':storage,'date_pair_denominators':dict(sorted(day_n.items())),'input_files':source,'independent_reference':{'name':auditfile.name,'sha256':sha(auditfile),'date_equal_primary':str(auditvalue),'pooled_primary':str(auditpooled)},'method_sha256':sha(Path(__file__)),'limitations':['This is post-result concentration description, not a new selection rule, robustness exclusion, or optimization. No symbol is removed and no engine or return record is changed.','Rankings are selected after observing historical outcomes and have no predictive or significance interpretation.','Survivorship, present-day directory classification, missing returns/control windows, matched-control limitations, retrospective SIP bar proxies, and repeated-research limitations remain.','Date-equal contribution divides each paired excess by its actual all-symbol same-day pair count and by all 191 dates; it is neither a standalone symbol strategy return nor compounded wealth.','P storage-industry classification remains unverified. All eight predesignated storage labels are retained even if absent from selected results.','Positive and negative signed contributions can offset; a contribution divided by the small aggregate point is not an investment weight.']}
 resultfile=ROOT/'primary-concentration.json';resultfile.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
 checks={'unique_primary_case_ids':True,'all_191_dates_represented':True,'all_191_dates_have_complete_pair':True,'counts_match_independent_audit':True,'all_symbol_pooled_contributions_sum_to_primary':True,'all_symbol_dateequal_contributions_sum_to_primary':True,'primary_matches_independent_audit_and_requested_value':True,'all_fixed_storage_labels_retained':len(storage['symbol_rows'])==8}
 validation={'checked_at':datetime.now(timezone.utc).isoformat(),'passed':all(checks.values()),'checks':checks,'checks_count':len(checks),'failed_checks':0,'source_files_checked':len(source),'source_file_hashes':source,'method_sha256':sha(Path(__file__)),'output_sha256':sha(resultfile),'primary_reference_audit_sha256':sha(auditfile),'dateequal_contribution_sum':str(bysymbol_dateequal),'dateequal_direct_calculation':str(dateequal),'dateequal_audit_reference':str(auditvalue),'absolute_difference_vs_audit':str(abs(dateequal-auditvalue)),'pooled_contribution_sum':str(bysymbol_pooled),'pooled_direct_calculation':str(pooled),'all_symbol_rows':len(symbols),'no_raw_market_API_used':True,'no_production_result_or_rule_modification':True}
 (ROOT/'primary-concentration-validation.json').write_text(json.dumps(validation,indent=2)+'\n')
 print(json.dumps({'counts':output['counts'],'primary_point':output['primary_point'],'positive_top10':[(s['symbol'],s['signals'],s['complete_pairs'],s['date_equal_matched_excess_contribution']) for s in output['rankings']['date_equal']['positive_top10']],'negative_top10':[(s['symbol'],s['signals'],s['complete_pairs'],s['date_equal_matched_excess_contribution']) for s in output['rankings']['date_equal']['negative_top10']],'storage':{k:v for k,v in storage.items() if k not in ('symbol_rows','signal_status_counts')},'validation_passed':validation['passed']}),flush=True)
if __name__=='__main__':main()
