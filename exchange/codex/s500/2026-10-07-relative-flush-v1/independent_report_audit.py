"""Final prose/CSV numerical binding for the independently checked exploratory study."""
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
import csv,hashlib,json,math,re,statistics
ROOT=Path(__file__).resolve().parent
read=lambda name:json.loads((ROOT/name).read_text())
sha=lambda name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
LABEL={'market_down':'基准同跌≥0.5%','market_not_down':'基准未跌到0.5%'}
def pct(x):return '—' if x is None else f'{float(x)*100:+.4f}%'
def win(x):return '—' if x is None else f'{float(x)*100:.2f}%'
def ratio(x):return '—' if x is None else f'{float(x):.4f}'
def ci(x):return '['+pct(x['lower'])+', '+pct(x['upper'])+']'
def tableline(fields):return '| '+' | '.join(map(str,fields))+' |'
def main():
    report=(ROOT/'REPORT.zh-CN.md').read_text();summary=read('summary.json');primary=read('primary-results.json');decision=read('stage-decision.json');peer=read('peer-exchange.json')
    lines=set(report.splitlines());checked=0
    for g in primary['groups']:
        if g['group']=='unknown':continue
        b=g['benchmark'];label=LABEL[g['group']];a=g['all'];d=a['target_net_distribution'];i=g['primary_inference']
        rows=[
            [b,label,d['n'],i['net_date_bootstrap']['n_dates'],win(d['win_rate_all_n']),pct(d['conditional_average_win']),pct(d['conditional_average_absolute_loss']),ratio(d['payoff_ratio_average_win_to_average_absolute_loss']),ratio(d['profit_factor_total_positive_to_total_absolute_loss']),pct(d['mean'])],
            [b,label,pct(i['net_date_bootstrap']['point_estimate']),ci(i['net_date_bootstrap']),pct(i['matched_excess_date_bootstrap']['point_estimate']),i['matched_excess_date_bootstrap']['n_dates'],ci(i['matched_excess_date_bootstrap'])],
            [b,label,a['counts'].get('selected_cases',0),a['signal_status_counts'].get('no_flush',0),a['signal_status_counts'].get('signal',0),a['signal_status_counts'].get('no_rebound',0),a['signal_status_counts'].get('unknown',0),a['counts'].get('signal_return_missing',0),a['counts']['complete_matched_excess']]
        ]
        for q in ['1','5']:
            t=d['top_positive_tails']['top_'+q+'_percent_of_all_n'];rows.append([b,label,q+'%',t['actual_removed_count'],win(t['share_of_total_positive_return']),pct(t['mean_without_selected_observations'])])
        for period,s in {**g['months'],**g['periods']}.items():
            dist=s['target_net_distribution'];rows.append([b,label,period,dist['n'],win(dist['win_rate_all_n']),pct(dist['mean']),pct(s['date_equal_target_net']['mean'])])
        for row in rows:
            expected=tableline(row);assert expected in lines,expected;checked+=1
    for c in primary['contrasts']:
        s=c['date_status_counts'];line=tableline([c['benchmark'],s.get('both_groups',0),s.get('down_only',0),s.get('not_down_only',0),s.get('neither_group',0),pct(c['bootstrap']['point_estimate']),ci(c['bootstrap'])]);assert line in lines;checked+=1
    for c in decision['all_cost_sensitivity']:
        chosen=[g for g in summary['groups'] if g['benchmark']==c['benchmark'] and g['group']==c['group'] and g['variant'][-1]==c['cost_bps_per_side']]
        assert len(chosen)==c['structural_variants']==12
        ep=sum(g['all']['target_net_distribution']['mean'] is not None and Decimal(g['all']['target_net_distribution']['mean'])>0 for g in chosen)
        dp=sum(g['all']['date_equal_target_net']['mean'] is not None and g['all']['date_equal_target_net']['mean']>0 for g in chosen)
        assert ep==c['positive_event_mean'] and dp==c['positive_date_mean']
        assert tableline([c['benchmark'],LABEL[c['group']],c['cost_bps_per_side'],str(ep)+'/12',str(dp)+'/12']) in lines;checked+=1
    assert decision['primary_groups']==[g for g in primary['groups'] if g['group']!='unknown'] and decision['primary_contrasts']==primary['contrasts']
    assert all(g['all']['target_net_distribution']['n']==0 for g in primary['groups'] if g['group']=='unknown')
    assert decision['no_minimum_win_rate'] is True and decision['user_40percent_estimate_verified'] is False
    assert decision['next_research']['status']=='proposed_not_registered_or_run'
    for k in ['new_market_requests','orders_sent','account_reads']:assert decision[k]==0
    assert decision['new_scheduler'] is False and decision['vendor_purchase'] is False
    for name,digest in decision['bound_evidence'].items():assert sha(name)==digest
    groups={(g['benchmark'],g['group'],tuple(g['variant'])):g for g in summary['groups']}
    with (ROOT/'all-variant-table.csv').open(newline='') as f:csvrows=list(csv.DictReader(f))
    assert len(csvrows)==216;seen=set()
    for r in csvrows:
        key=(r['benchmark'],r['group'],(r['drop'],r['family'],r['exit'],int(r['cost_bps_per_side'])));assert key not in seen;seen.add(key);g=groups[key];a=g['all'];d=a['target_net_distribution']
        for field,value in [('selected_cases',a['counts'].get('selected_cases',0)),('signals',a['signal_status_counts'].get('signal',0)),('complete_returns',d['n']),('complete_pairs',a['counts'].get('complete_matched_excess',0))]:assert int(r[field])==value
        expected={'win_rate':d['win_rate_all_n'],'average_win':d['conditional_average_win'],'average_absolute_loss':d['conditional_average_absolute_loss'],'payoff_ratio':d['payoff_ratio_average_win_to_average_absolute_loss'],'profit_factor':d['profit_factor_total_positive_to_total_absolute_loss'],'event_mean_net':d['mean'],'date_mean_net':a['date_equal_target_net']['mean'],'date_mean_matched_excess':a['date_equal_matched_excess']['mean']}
        for k,v in expected.items():
            if v is None:assert r[k]==''
            else:assert Decimal(r[k])==Decimal(str(v))
    assert seen==set(groups)
    spy=next(g for g in primary['groups'] if (g['benchmark'],g['group'])==('SPY','market_down'))
    assert spy['periods']['H2_to_cutoff']['counts'].get('selected_cases',0)==0
    assert spy['periods']['H2_to_cutoff']['target_net_distribution']['n']==0 and spy['periods']['H2_to_cutoff']['target_net_distribution']['mean'] is None
    assert all(date<'2026-07-01' for date in spy['all']['date_means'])
    pr=peer['result_summary'];d=spy['all']['target_net_distribution']
    assert pr['SPY_down_primary_complete_events']==d['n'] and pr['SPY_down_outcome_dates']==11
    assert pr['SPY_down_event_mean_net']==d['mean'] and pr['SPY_down_payoff_ratio']==d['payoff_ratio_average_win_to_average_absolute_loss'] and pr['SPY_down_profit_factor']==d['profit_factor_total_positive_to_total_absolute_loss']
    for c in primary['contrasts']:assert pr[c['benchmark']+'_same_date_primary_contrast']==c['bootstrap']
    assert 'SPY_down_period_scope' in pr
    assert peer['next']==decision['next_research']
    required=['没有 40% 胜率筛选线','不是期权胜率','全部位于上半年','下半年没有该组样本','这是无样本','不同日期、权重和比较对象','这本身不是否决低胜率大赔率策略的理由','不是交易连败概率','未校正跨日相关或多重探索','尚未登记或运行','不能把本轮股票收益乘个杠杆当期权收益','11:12:35 EDT']
    for text in required:assert text in report,text
    payoff=(ROOT/'PAYOFF_INTERPRETATION.zh-CN.md').read_text()
    assert '未知与未触发样本另列' in payoff and '未知或未成交事件另列' not in payoff
    # Extra explanatory values are shared-date subgroup means, not whole-period values.
    common=[r for r in primary['contrasts'][0]['dates'] if r['status']=='both_groups']
    common_down=statistics.fmean(r['down_mean'] for r in common)*100;common_notdown=statistics.fmean(r['not_down_mean'] for r in common)*100
    assert f'{common_down:+.5f}%' in payoff and f'{common_notdown:+.5f}%' in payoff
    assert '0.3 × 3R − 0.7 × 1R = +0.2R' in payoff
    reproduction=(ROOT/'REPRODUCE.md').read_text()
    for text in ['107 tests passed','are not republished','private files','standard library','not an option backtest'] :assert text in reproduction,text
    names=['REPORT.zh-CN.md','stage-decision.json','peer-exchange.json','all-variant-table.csv','REPRODUCE.md','PAYOFF_INTERPRETATION.zh-CN.md']
    result={'audit_version':'independent_relative_report_v1','completed_at_utc':datetime.now(timezone.utc).isoformat(),'passed':True,'checked_markdown_numeric_rows':checked,'checked_csv_variants':216,'checks':['Allprimarypayoff/dateinference/contrast/coverage/tail/cost/month/half tablesmatch auditedresults','All216CSVvariants exactnumericsandnulls match fullsummary','Decision andpeer copies/numerics match frozenprimaryresults; parentboundevidenceunchanged','SPYdown allH1 and noH2 selection/returns verified; zeroobservations distinctfromzero return','Lowwinrate acceptance preserved, taildependency not automaticrejection, nooptionorcapitalinference','Shared-datecondition andtrough-timeconfounding/small-nexplained; nextstage onlyunrun proposal','Hypotheticalpayoff arithmetic andshared-date explanatorymeans verified','NewYork user-facing registrationtime and reproductionlimits explicit'],
      'bound_documents':{n:sha(n) for n in names},'auditor_code_sha256':sha('independent_report_audit.py'),'remote_publication_status':'Localreportapproved;remotecommitandreadbacknotclaimed.'}
    (ROOT/'independent-report-audit.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
if __name__=='__main__':main()
