"""EP-NEWS initial inventory, never a trade/return simulation or event inference.

Only decision-day opening price is accessed; daily high/low/close/volume must not
enter the screen. Prior daily bars and retrospective action/directory coverage
are explicitly imperfect proxies. Reproduce using the supplied cached inputs.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date, datetime
import hashlib
import json
import math
from pathlib import Path
import statistics
from zoneinfo import ZoneInfo

NY = ZoneInfo('America/New_York')
ROOT = Path(__file__).parent

def positive(v):
    return isinstance(v, (int, float)) and math.isfinite(v) and v > 0

def validdate(v):
    try: return isinstance(v,str) and date.fromisoformat(v).isoformat() == v
    except ValueError: return False

def ge(a,b):
    return a >= b or math.isclose(a,b,rel_tol=0,abs_tol=1e-12)

def action_index(rows):
    index=defaultdict(list); issues=Counter()
    for n,r in enumerate(rows):
        if r.get('type') not in ('forward_split','reverse_split'): continue
        a=dict(r); a['source_row_index']=n; flags=[]
        if not a.get('symbol'): flags.append('unmapped_old_symbol')
        if 'new_symbol' in a and not a['new_symbol']: flags.append('unmapped_new_symbol')
        if a.get('new_symbol') and a['new_symbol'] != a.get('symbol'): flags.append('unresolved_symbol_change')
        if not validdate(a.get('ex_date')): flags.append('invalid_ex_date')
        if not all(positive(a.get(k)) for k in ('old_rate','new_rate')): flags.append('invalid_rate')
        if a.get('semantic_duplicate_group'): flags.append('semantic_duplicate')
        a['screen_date']=a.get('ex_date') if validdate(a.get('ex_date')) else a.get('process_date') if validdate(a.get('process_date')) else None
        a['issues']=flags
        syms={s for s in (a.get('symbol'),a.get('new_symbol')) if s}
        if not syms: issues['split_rows_without_any_mappable_symbol']+=1
        if not a['screen_date']: issues['split_rows_without_placeable_date']+=1
        issues.update(flags)
        for s in syms:index[s].append(a)
    for syms,actions in index.items():
        dates=Counter(a.get('ex_date') for a in actions if validdate(a.get('ex_date')))
        for a in actions:
            if dates[a.get('ex_date')]>1:a['issues']=sorted(set(a['issues']+['multiple_split_rows_same_symbol_date']))
    return index,dict(issues)

def screen(prior_dates, series, today, splits):
    if len(prior_dates)!=20:raise ValueError('twenty prior calendar sessions required')
    if any(d not in series or not positive(series[d].get('c')) or not isinstance(series[d].get('v'),(int,float)) or not math.isfinite(series[d]['v']) or series[d]['v']<0 for d in prior_dates):
        return None,'prior_history_missing_or_invalid',[]
    # Copy ONLY prior completed close/volume. No current-day close/high/low/volume.
    hist=[{'c':series[d]['c'],'v':series[d]['v']} for d in prior_dates]
    applied=[];bad=[]
    for a in splits:
        ex=a.get('screen_date')
        if not ex or not prior_dates[0]<ex<=today: continue
        if a['issues']:bad.extend(a['issues']);continue
        factor=a['old_rate']/a['new_rate']
        for d,b in zip(prior_dates,hist):
            if d<ex:b['c']*=factor;b['v']/=factor
        applied.append({'type':a['type'],'ex_date':ex,'old_rate':a['old_rate'],'new_rate':a['new_rate'],'source_row_index':a['source_row_index']})
    if bad:return None,'unresolved_split_window',sorted(set(bad))
    priorclose=hist[-1]['c'];liquidity=statistics.median(b['c']*b['v'] for b in hist)
    if not ge(priorclose,2):return None,'prior_close_below_2',[]
    if not ge(liquidity,10_000_000):return None,'prior20_median_dollar_volume_below_10m',[]
    op=series.get(today,{}).get('o')
    if not positive(op):return None,'missing_or_invalid_current_open',[]
    gap=op/priorclose-1
    if not ge(gap,.1):return None,'opening_gap_below_10pct',[]
    return {'prior_close_adjusted':priorclose,'daily_open_reference':op,'opening_gap_fraction':gap,'prior20_median_dollar_volume':liquidity,'prior20_mean_adjusted_daily_volume':statistics.mean(b['v'] for b in hist),'applied_splits':applied},'candidate',[]

def build(source, actions_path, registry_path):
    hashes={};market={};raw_count=0
    files=[source/'raw/calendar.json',source/'universe.json',source/'current-instrument-classification.json',actions_path,registry_path]
    for p in files:hashes[p.name]=hashlib.sha256(p.read_bytes()).hexdigest()
    for p in sorted((source/'raw').glob('batch-*.json')):
        b=p.read_bytes();hashes['raw/'+p.name]=hashlib.sha256(b).hexdigest()
        for symbol,bars in json.loads(b)['bars'].items():
            if symbol in market:raise ValueError('duplicate batch symbol')
            series={}
            for bar in bars:
                d=datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(NY).date().isoformat()
                if d in series:raise ValueError('duplicate daily date')
                series[d]=bar;raw_count+=1
            market[symbol]=series
    universe={r['symbol']:r for r in json.loads((source/'universe.json').read_text())}
    if set(universe)!=set(market):raise ValueError('market/universe mismatch')
    calendar=json.loads((source/'raw/calendar.json').read_text());days=[r['date'] for r in calendar]
    if days!=sorted(set(days)):raise ValueError('calendar duplicate/order')
    klass=defaultdict(list)
    for r in json.loads((source/'current-instrument-classification.json').read_text())['rows']:klass[r['symbol']].append(r)
    index,action_diag=action_index(json.loads(actions_path.read_text())['rows'])
    result=[];daily=[];exclusions=[];total=Counter()
    for i,d in enumerate(days):
        if not '2026-01-02'<=d<='2026-10-05':continue
        prev=days[i-20:i];stats=Counter()
        for symbol in sorted(universe):
            row,reason,issues=screen(prev,market[symbol],d,index.get(symbol,[]));stats[reason]+=1
            if issues:exclusions.append({'date':d,'symbol':symbol,'reason':reason,'issues':issues})
            if row is None:continue
            classes=klass[symbol]
            excluded=any(r.get('etf')=='Y' or r.get('test_issue')=='Y' for r in classes)
            unknown=not classes or not all(r.get('etf')=='N' and r.get('test_issue')=='N' for r in classes)
            row={'date':d,'symbol':symbol,'name_current_directory':universe[symbol].get('name'),'prior_session':days[i-1],**row,'current_classified_etf_or_test_issue':excluded,'current_classification_unknown':unknown,'stock_research_eligible_initial':not excluded,'news_status':'missing','news_evidence':[],'regular_open_verification':'pending_minute_data','opening30minute_volume_status':'not_evaluated','outcome_status':'not_computed'}
            result.append(row)
        stats['stock_candidate_count']=sum(r['date']==d and r['stock_research_eligible_initial'] for r in result)
        daily.append({'date':d,'counts':dict(stats)});total.update({k:v for k,v in stats.items() if k!='stock_candidate_count'})
    stock=[r for r in result if r['stock_research_eligible_initial']]
    return {'schema':'s500.ep-initial-candidates.v1','created_at':datetime.now().astimezone().isoformat(),'strategy':'EP-NEWS-500-v1','status':'data_readiness_inventory_only_no_strategy_returns','period':{'start':'2026-01-02','end':'2026-10-05','sessions':len(daily)},'rules':{'universe':'all 6633 cached candidate codes, no top500/top100 cap or storage-only selection; ETFs/test issues retained and flagged outside stock research set','history':'all previous20 calendar sessions required; priorclose split adjusted through opening date>=2; median prior20 close*volume>=10000000','gap':'daily_open/(split-adjusted priorclose)-1>=0.10; opening reference must be verified with regular-session minutes','no_current_volume_filter':True,'news':'all missing initially; verify primary public event and timestamp after previous regular close and before09:30; missing is not negative evidence','sampling':'first three stock-eligible rows ordered ascending date then symbol; never sort by subsequent outcomes'},'coverage':{'universe_codes':len(universe),'raw_daily_bars':raw_count,'initial_gap_candidates':len(result),'initial_stock_candidates':len(stock),'unique_stock_candidate_symbols':len({r['symbol'] for r in stock}),'excluded_current_etf_or_test_issue_candidates':len(result)-len(stock),'unknown_classification_stock_candidates':sum(r['current_classification_unknown'] for r in stock),'monthly_stock_candidates':dict(sorted(Counter(r['date'][:7] for r in stock).items())),'news_status_counts':dict(Counter(r['news_status'] for r in stock)),'screen_reason_counts':dict(total)},'split_source_diagnostics':action_diag,'split_window_exclusions':exclusions,'daily_screen_counts':daily,'candidates':result,'chronological_news_audit_sample':[{'date':r['date'],'symbol':r['symbol']} for r in stock[:3]],'inputs_sha256':hashes,'limitations':['Current active/inactive directory and current official classifications are not point-in-time; omitted historical listings and symbol reuse remain possible.','Daily opening/closing values and volumes have not been reconciled to regular-session minute bars; this is a preliminary candidate index, not a tradable opening screen.','Corporate action process-date range may omit late-processed historical events; unmapped actions and timestamp availability remain unresolved.','No same-day volume, high, low, close or later returns used to accept/rank candidates. No 10:00 volume gate, breakout, fills or EP12 portfolio returns have been evaluated.','All candidate event statuses initialize missing. Missing primary news evidence does not mean there was no catalyst.','The already inspected2026 period is exploratory; no untouched holdout claim.']}

def incorporate_event_audit(x, audit_path):
    if not audit_path.exists(): return
    audit=json.loads(audit_path.read_text())
    allowed={(r['date'],r['symbol']) for r in x['chronological_news_audit_sample']}
    reviews={(r['date'],r['symbol']):r for r in audit['candidate_reviews']}
    if set(reviews)-allowed: raise ValueError('event audit outside frozen chronological sample')
    for row in x['candidates']:
        row['initial_news_status']='missing'
        key=(row['date'],row['symbol'])
        if key in reviews:
            rev=reviews[key];row['news_status']=rev['news_status'];row['news_evidence']=[{'audit_file':audit_path.name,'source_keys':rev['source_keys'],'pass_registered_news_gate':rev['pass_registered_news_gate'],'reason':rev['reason']}]
    stock=[r for r in x['candidates'] if r['stock_research_eligible_initial']]
    x['coverage']['initial_news_status_counts']={'missing':len(stock)}
    x['coverage']['news_status_counts']=dict(Counter(r['news_status'] for r in stock))
    x['coverage']['news_audited_stock_candidates']=len(reviews)
    x['coverage']['confirmed_registered_news_gate']=sum(r['pass_registered_news_gate'] for r in reviews.values())
    x['event_audit']={'file':audit_path.name,'sha256':hashlib.sha256(audit_path.read_bytes()).hexdigest(),'sampling':audit['sampling'],'reviews':audit['candidate_reviews']}

def write_report(x,path):
    c=x['coverage'];lines=['# EP全年初始候选清单','','这是 EP-NEWS-500-v1 的数据准备，不是12组策略收益回测。','','- 期间：2026-01-02 至2026-10-05，共 '+str(x['period']['sessions'])+' 个交易日。','- 全量扫描 '+str(c['universe_codes'])+' 个原始代码，未截取流动性前500或存储8股。','- 初始跳空候选 '+str(c['initial_gap_candidates'])+' 条；剔除当前已知ETF/测试标记后股票候选 '+str(c['initial_stock_candidates'])+' 条、'+str(c['unique_stock_candidate_symbols'])+' 个代码。','- 全部事件状态初始为 missing；不把缺资料当无催化。','- 仅前20个完整交易日成交额/前收盘、拆股和当天日线开盘引用价用于筛选；没有使用当天最终成交量或后续涨跌。','','规则：前20日中位日成交额至少1000万美元、拆股调整前收盘至少2美元、开盘跳空至少10%。开盘价及前日量仍须与分钟常规时段核验，10:00 的30分钟量门槛还未评估。','','## 按时间选定的首3例新闻核验样本','','| 日期 | 代码 | 当前事件状态 |','|---|---|---|']
    for r in x['chronological_news_audit_sample']:
        status=next(q['news_status'] for q in x['candidates'] if q['date']==r['date'] and q['symbol']==r['symbol'])
        lines.append('| '+r['date']+' | '+r['symbol']+' | '+status+' |')
    if x.get('event_audit'):
        lines+=['','三例有限核验后的状态：'+json.dumps(c['news_status_counts'],ensure_ascii=False)+'；通过注册新闻门槛 '+str(c['confirmed_registered_news_gate'])+' 例。详细时间冲突、事件分类与失败访问见 ep-event-audit.json。BIDU 拆分上市计划、SMX 产品市场扩展不自动改写为财报、合同或监管批准；SLS 缺证据不是无催化。']
    lines+=['','## 覆盖限制','']+['- '+s for s in x['limitations']]
    path.write_text('\n'.join(lines)+'\n')

def self_test():
    days=['2026-01-'+str(i).zfill(2) for i in range(1,21)];series={d:{'c':10.,'v':2_000_000} for d in days};series['2026-01-21']={'o':11.}
    a=screen(days,series,'2026-01-21',[])
    assert a[1]=='candidate'
    series['2026-01-21'].update(c=0,h=float('nan'),l=-100,v=0)
    assert a==screen(days,series,'2026-01-21',[])
    index,_=action_index([{'type':'forward_split','symbol':'X','ex_date':'2026-01-21','old_rate':1,'new_rate':2}]);series['2026-01-21']['o']=5.5
    b=screen(days,series,'2026-01-21',index['X']);assert b[1]=='candidate' and b[0]['prior_close_adjusted']==5
    series['2026-01-21']['o']=5.49;assert screen(days,series,'2026-01-21',index['X'])[1]=='opening_gap_below_10pct'
    del series[days[0]];assert screen(days,series,'2026-01-21',[])[1]=='prior_history_missing_or_invalid'
    return {'checks':5,'result':'passed','topics':['only_current_open','10pct_boundary','effective_split','below_gap_boundary','missing_history']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,default=ROOT.parent/'s500-broad-20261006');p.add_argument('--actions',type=Path,default=ROOT.parent/'s500-aggressive-20261006/company-actions.json');p.add_argument('--registry',type=Path,default=ROOT.parent/'s500-web-research-20261006/experiment-registry.json');p.add_argument('--output',type=Path,default=ROOT/'ep-candidate-inventory.json');args=p.parse_args()
    tests=self_test();x=build(args.source,args.actions,args.registry);tests.update(coverage_partition_symbol_days=sum(x['coverage']['screen_reason_counts'].values()),all_candidate_keys_unique=len({(r['date'],r['symbol']) for r in x['candidates']})==len(x['candidates']),code_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest());assert tests['coverage_partition_symbol_days']==len(x['daily_screen_counts'])*x['coverage']['universe_codes'];x['verification']=tests;incorporate_event_audit(x,ROOT/'ep-event-audit.json');args.output.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n');write_report(x,args.output.with_suffix('.md'));print(json.dumps({'coverage':x['coverage'],'first_three':x['chronological_news_audit_sample'],'verification':tests},ensure_ascii=False))
if __name__=='__main__':main()
