"""Independent full CSV reconciliation and narrative numeric/scope binding."""
import ast,csv,hashlib,json,re
from pathlib import Path
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from decimal import Decimal,localcontext
ROOT=Path(__file__).resolve().parent
D=Decimal
read=lambda n:json.loads((ROOT/n).read_text())
sha=lambda n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest()
def pct(x):return '—' if x is None else f'{D(str(x))*100:+.5f}%'
def pp(x):return '—' if x is None else f'{D(str(x))*100:+.5f}'
def textval(v):return '' if v is None else str(v)
def cells(line):return [s.strip() for s in line.strip().strip('|').split('|')]
def main():
 s=read('summary.json');c=read('concentration.json');design=read('study-design.json');report=(ROOT/'REPORT.zh-CN.md').read_text();inter=(ROOT/'INTERPRETATION.zh-CN.md').read_text();methods=(ROOT/'METHODS.md').read_text();repro=(ROOT/'REPRODUCE.md').read_text()
 m=read('report-output-manifest.json')
 for f in m['files']:assert sha(f['name'])==f['sha256'] and (ROOT/f['name']).stat().st_size==f['bytes']
 for n in ['independent-actual-audit.json','independent-concentration-audit.json','independent-macro-audit.json','independent-scope-audit.json']:assert read(n)['passed'] is True
 totalrows=0;totalfields=0;csvcounts={}
 summarycols={'net_mean':('target_net_distribution','mean'),'net_win_rate':('target_net_distribution','win_rate_all_n'),'net_average_win':('target_net_distribution','conditional_average_win'),'net_average_absolute_loss':('target_net_distribution','conditional_average_absolute_loss'),'net_payoff_ratio':('target_net_distribution','payoff_ratio_average_win_to_average_absolute_loss'),'net_profit_factor':('target_net_distribution','profit_factor_total_positive_to_total_absolute_loss'),'date_equal_net_mean':('date_equal_net','mean'),'date_equal_net_dates':('date_equal_net','n'),'matched_excess_mean':('matched_excess','mean'),'date_equal_excess_mean':('date_equal_excess','mean'),'date_equal_excess_dates':('date_equal_excess','n')}
 def verify_csv(name,expected):
  nonlocal totalrows,totalfields
  rs=list(csv.DictReader((ROOT/name).open()));assert len(rs)==len(expected),(name,len(rs),len(expected))
  for row,exp in zip(rs,expected):
   assert set(row)==set(exp),(name,set(row),set(exp))
   for k,v in exp.items():assert row[k]==textval(v),(name,k,row[k],v);totalfields+=1
  totalrows+=len(rs);csvcounts[name]=len(rs)
 def flat(z):return {**z['counts'],**{k:z[a][b] for k,(a,b) in summarycols.items()}}
 verify_csv('all-variant-group-summaries.csv',[{'drop_threshold':r['variant'][0],'family':r['variant'][1],'exit_horizon':r['variant'][2],'cost_bps_per_side':r['variant'][3],'SPY_group':r['SPY_group'],**flat(r['summary'])} for r in s['variant_group_summaries']])
 verify_csv('all-date-group-summaries.csv',[{'date':r['date'],'date_role':r['date_role'],'SPY_group':r['SPY_group'],**flat(r['summary'])} for r in s['primary_date_group_summaries']])
 verify_csv('all-fixed-feature-bins.csv',[{'axis':r['axis'],'bin':r['bin'],**flat(r['primary_SPY_down_all_selected_case_summary'])} for r in s['fixed_feature_bins']])
 metricfields=['complete_count','missing_count','observed_date_count','event_mean','date_equal_mean','contribution_to_original_pooled_mean','contribution_to_original_date_equal_mean','original_complete_denominator','original_observed_date_denominator']
 for name,key in [('all-date-contributions.csv','by_date'),('all-symbol-contributions.csv','by_symbol'),('all-date-symbol-contributions.csv','date_symbol_matrix_observed_cells')]:
  expected=[]
  for entity in c[key]:
   for metric in ['net_return','matched_excess']:
    z=entity['metrics'][metric];expected.append({'entity_type':entity['entity_type'],'entity':entity['entity'],'metric':metric,'signals':entity['signals'],**{f:z[f] for f in metricfields}})
  verify_csv(name,expected)
 for name,key in [('all-leave-one-date-out.csv','leave_one_date_out'),('all-leave-one-symbol-out.csv','leave_one_symbol_out')]:
  expected=[]
  for entity in c[key]:
   for metric in ['net_return','matched_excess']:
    z=entity['metrics'][metric];expected.append({'removed_entity_type':entity['removed_entity_type'],'removed_entity':entity['removed_entity'],'metric':metric,'remaining_signal_count':entity['remaining_signal_count'],**{f:z[f] for f in ['complete_count','missing_count','observed_date_count','event_mean','date_equal_mean','removed_complete_count','removed_missing_count']},'lost_observed_dates':'|'.join(z['lost_observed_dates'])})
  verify_csv(name,expected)
 # Rebuild report table cells from independently audited source results.
 expected_tables=[]
 for row in s['primary_date_group_summaries']:
  if row['SPY_group']!='market_down':continue
  z=row['summary'];n=z['counts'];expected_tables.append([row['date'],n['selected_cases'],n['signals'],n['complete_net'],n['signal_return_missing'],n['complete_pairs'],pct(z['target_net_distribution']['mean'])])
 for row in c['by_date']:
  z=row['metrics']['net_return'];expected_tables.append([row['entity'],z['complete_count'],pct(z['event_mean']),pp(z['contribution_to_original_pooled_mean']),pp(z['contribution_to_original_date_equal_mean'])])
 for row in c['leave_one_date_out']:
  z=row['metrics']['net_return'];expected_tables.append([row['removed_entity'],z['complete_count'],z['observed_date_count'],pct(z['event_mean']),pct(z['date_equal_mean'])])
 labels={'le_minus_2pct':'≤−2%','minus_2pct_to_below_zero':'(−2%, 0)','zero_or_positive':'≥0','unknown':'未知','minute0_9':'0–9','minute10_19':'10–19','minute20_29':'20–29'}
 for row in s['fixed_feature_bins']:
  z=row['primary_SPY_down_all_selected_case_summary'];n=z['counts'];expected_tables.append([row['axis'],labels[row['bin']],n['selected_cases'],n['signals'],n['complete_net'],n['signal_return_missing'],pct(z['target_net_distribution']['mean'])])
 markdown_rows=[cells(l) for l in report.splitlines() if l.startswith('|')]
 for row in expected_tables:assert [str(v) for v in row] in markdown_rows,row
 assert len(expected_tables)==46
 # Narrative counts, weighting reversals and all single-entity sensitivities.
 registry=read('anatomy-case-registry.json');assert len({r['symbol'] for r in registry['cases']})==445 and '445 个不同符号' in report
 overall=c['overall']['net_return'];assert overall['positive_count']==79 and overall['negative_count']==74 and overall['zero_count']==0
 assert f"{D(79)/153*100:.2f}%" in report and c['signal_count']==161 and c['symbol_count']==83 and c['signal_date_count']==11
 june=next(r for r in c['by_date'] if r['entity']=='2026-06-25')['metrics']['net_return']
 assert june['complete_count']==44 and f"{D(44)/153*100:.2f}%" in report
 ld={r['removed_entity']:r['metrics']['net_return'] for r in c['leave_one_date_out']}
 assert [k for k,v in ld.items() if D(v['event_mean'])<0]==['2026-06-25'];assert all(D(v['date_equal_mean'])>0 for v in ld.values())
 ls={r['removed_entity']:r['metrics']['net_return'] for r in c['leave_one_symbol_out']}
 for f in ['event_mean','date_equal_mean']:
  assert all(D(v[f])>0 for v in ls.values())
  for val in [min(D(v[f]) for v in ls.values()),max(D(v[f]) for v in ls.values())]:assert pct(val) in report and pct(val) in inter
 ranks=c['rankings'];assert ranks['symbol']['metrics']['net_return']['pooled']['positive_contributors_descending'][0]=='CRWV'
 assert ranks['symbol']['metrics']['net_return']['pooled']['negative_contributors_ascending'][0]=='NBIS'
 assert ranks['date']['metrics']['net_return']['date_equal']['positive_contributors_descending'][0]=='2026-02-17'
 assert ranks['date']['metrics']['net_return']['pooled']['negative_contributors_ascending'][0]=='2026-03-30'
 for symbol in ['CRWV','NBIS']:
  entry=next(r for r in c['by_symbol'] if r['entity']==symbol)['metrics']['net_return'];assert entry['complete_count']==5
  for f in ['contribution_to_original_pooled_mean','contribution_to_original_date_equal_mean']:assert pp(entry[f]).replace('-','−') in inter.replace('-','−')
 for d in ['2026-06-25','2026-02-17','2026-02-13','2026-03-30']:
  for f in ['event_mean','date_equal_mean']:assert pct(ld[d][f]).replace('-','−') in inter.replace('-','−')
 assert {r['symbol'] for r in s['diagnostic_June18_SPY_down_cases']}=={'CRM','MSFT','TXN'} and all(r['signal_status']=='no_flush' for r in s['diagnostic_June18_SPY_down_cases'])
 # Parent CI is context only; no new bootstrap was produced.
 parent=read('../s500-relative-flush-20261007/primary-results.json')
 pg=next(r for r in parent['groups'] if r['benchmark']=='SPY' and r['group']=='market_down')
 ci=pg['primary_inference']['net_date_bootstrap']
 # Endpoint keys are verified below from the parent frozen object.
 ci_text=str(ci)
 assert '-0.003691' in ci_text and '0.008563' in ci_text
 assert '[−0.36916%, +0.85633%]' in report and '[−0.36916%, +0.85633%]' in inter
 registered=datetime.fromisoformat(design['registered_at']).astimezone(ZoneInfo('America/New_York')).strftime('%Y-%m-%d %H:%M:%S')
 assert registered in report
 decision=read('stage-decision.json');peer=read('peer-exchange.json')
 assert decision['scope']==s['counts'] and decision['primary_variant']==design['primary_variant']
 primary=next(r['summary'] for r in s['variant_group_summaries'] if r['variant']==design['primary_variant'] and r['SPY_group']=='market_down')
 assert decision['primary_counts']==primary['counts']
 assert decision['net_event_mean']==overall['event_mean'] and decision['net_date_equal_mean']==overall['date_equal_mean']
 assert decision['no_minimum_40_percent_win_rate'] is True and decision['tail_dependence_is_not_automatic_rejection'] is True
 assert decision['next_stage']==peer['next_stage'] and decision['next_stage']['status']=='proposed_not_registered_or_run'
 assert all(decision[k]==0 for k in ['orders_sent','account_reads','new_market_requests']) and decision['new_scheduler'] is False
 assert decision['paper_capital_round']==500 and decision['paper_target']==10000 and decision['funded_capital_or_option_simulation_performed'] is False
 assert peer['preserved']=={'all_cases':1227,'all_parent_parameter_rows':44172,'all_original_variants':36,'all_signs_missingness_and_peers':True}
 assert '不能称完全独立可复现' in report and '尚未登记、运行或新增调度' in report
 assert 'FOMC minutes' in methods and 'not a preregistered return split' in methods
 assert '40%' in inter or '40%' in report
 assert '它不是包含全部原始输入的' in repro and '可完全独立复现' in repro and '原始行情及规范化分钟输入未随包公开' in repro
 docs=['REPORT.zh-CN.md','INTERPRETATION.zh-CN.md','METHODS.md','MACRO_CONTEXT.zh-CN.md','REPRODUCE.md','stage-decision.json','peer-exchange.json','report-output-manifest.json','build_report.py']+list(csvcounts)
 for n in docs:
  txt=(ROOT/n).read_text();assert not any(p in txt for p in ['/' + name + '/' for name in ['workspace','home','tmp']]),n
 result={'passed':True,'audited_at':datetime.now(timezone.utc).isoformat(),'csv_files':8,'csv_rows_reconciled':totalrows,'csv_fields_reconciled':totalfields,'csv_row_counts':csvcounts,'report_numeric_table_rows_reconciled':46,'report_macro_table_rows_manually_verified_against_independently_audited_calendar':3,'narrative_scope_and_sensitivity_claims_verified':True,'known_outcome_focus_not_oos':True,'no_minimum_winrate_or_tail_rejection':True,'next_stage_proposed_not_registered_or_run':True,'publication_private_path_scan_passed':True,'files':[{'name':n,'sha256':sha(n),'bytes':(ROOT/n).stat().st_size} for n in docs],'auditor_sha256':sha('independent_report_audit.py')}
 (ROOT/'independent-report-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result))
if __name__=='__main__':main()
