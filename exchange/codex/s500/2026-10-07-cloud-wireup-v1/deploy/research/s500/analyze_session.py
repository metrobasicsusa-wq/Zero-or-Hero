"""Offline economics for a sealed session; no HTTP, credentials or account APIs.

Receipt/time/raw-source verification belongs to the caller. The registered
vendor economics, costs and peers are preserved. RFC3339 t in source.bars is
normalized exactly once to the regular session open. Every selected case survives.
"""
from pathlib import Path
from collections import Counter, defaultdict
from decimal import Decimal, localcontext
from fractions import Fraction
from datetime import datetime
import gzip, hashlib, io, json, os, shutil, tempfile
from vendor.flush_engine import prepare_bars, evaluate_prefix, evaluate_day, evaluate_window
from vendor.normalize_bars import normalize_provider_bars
from vendor.prefix_features import first30_features
from vendor.classify_relative import classify_case
from vendor.return_distribution import compute_distribution, render

D = Decimal
VARIANTS = tuple((d,f,h,c) for d in ['0.02','0.03'] for f in ['FIXED','REBOUND']
                 for h in ['30m','60m','close_minus_10m'] for c in [5,10,25])
PRIMARY = ('0.02','REBOUND','60m',10)
GROUPS = ('market_down','market_not_down','unknown')
BIN_LABELS = {'gap': ['le_minus_2pct','minus_2pct_to_below_zero','zero_or_positive','unknown'],
              'close29': ['le_minus_2pct','minus_2pct_to_below_zero','zero_or_positive','unknown'],
              'trough': ['minute0_9','minute10_19','minute20_29','unknown']}
# Following three functions are copied verbatim from the original run_flush.py.
ECONOMIC_SOURCE_SHA256 = "026931d7bc2f84ace2f597d821a33ed92083ce269a85a58507f052577251faa0"
def peer_selection(target,cases,prefixes,threshold):
 """Uses only prior liquidity and completed first30 prefix, never exit availability."""
 if target['symbol'] in ('SPY','QQQ') or not target['stock_eligible']:return []
 liq=D(target['selection_metrics']['median_prior20_daily_close_times_volume']);ranked=[]
 with localcontext() as ctx:
  ctx.prec=42
  for c in cases:
   s=c['symbol']
   if s==target['symbol'] or s in ('SPY','QQQ') or not c['stock_eligible']:continue
   if prefixes[s][threshold]['is_flush'] is not False:continue
   other=D(c['selection_metrics']['median_prior20_daily_close_times_volume'])
   distance=abs((other/liq).ln());ranked.append((distance,s))
 return [{'symbol':s,'absolute_log_liquidity_distance':str(distance)} for distance,s in sorted(ranked)[:3]]
def canonical_signal(status):
 return {'signal':'signal','no_flush':'no_flush','valid_no_rebound':'no_rebound'}.get(status,'unknown')
def evaluate_case(c,all_cases,prepared,prefixes,session_minutes,normalization):
 symbol=c['symbol'];stock=symbol not in ('SPY','QQQ');p=prepared[symbol]
 peers={t:peer_selection(c,all_cases,prefixes,t) for t in ['0.02','0.03']}
 decisions={};rows=[];cache={}
 def window(s,entry,exit,cost):
  key=(s,entry,exit,cost)
  if key not in cache:cache[key]=evaluate_window(prepared[s],entry,exit,cost,session_minutes=session_minutes)
  return cache[key]
 for e in evaluate_day(p,session_minutes=session_minutes):
  t=e['threshold'];decision=e['decision'];signal=canonical_signal(decision['status']);entry=e['entry_minute'];exit=e['exit_minute'];cost=int(e['per_side_cost_bps']);value=e['net_return']
  decisions[t+'|'+e['family']]=decision
  controls=[];benchmarks={};excess=None;paired_status='not_applicable'
  if signal=='signal':
   if stock:
    for selection in peers[t]:
     cw=window(selection['symbol'],entry,exit,cost)
     controls.append({**selection,'status':'complete' if cw['status']=='ok' else cw['status'],
       'net_return':cw['net_return'],'entry_minute':entry,'exit_minute':exit,
       'entry_open':cw['entry_open'],'exit_open':cw['exit_open'],
       'missing_minutes':(cw.get('coverage') or {}).get('missing_minutes',[]),
       'invalid_minutes':(cw.get('coverage') or {}).get('invalid_minutes',{})})
    paired_status='insufficient_candidates' if len(controls)!=3 else 'missing_return'
    if len(controls)==3 and value is not None and all(q['status']=='complete' and q['net_return'] is not None for q in controls):
     with localcontext() as ctx:
      ctx.prec=42;excess=str(D(value)-sum(D(q['net_return']) for q in controls)/D(3))
     paired_status='complete_three'
   for s in ['SPY','QQQ']:
    bw=window(s,entry,exit,cost);br=bw['net_return'];be=None
    if value is not None and br is not None:
     with localcontext() as ctx:ctx.prec=42;be=str(D(value)-D(br))
    benchmarks[s]={'status':'complete' if bw['status']=='ok' else bw['status'],'net_return':br,'excess_return':be,
      'entry_minute':entry,'exit_minute':exit,'entry_open':bw['entry_open'],'exit_open':bw['exit_open']}
  prefix=prefixes[symbol][t];window_result=e['window'];coverage=(window_result or {}).get('coverage') or {}
  missing_stage='signal' if signal=='unknown' else ('return' if signal=='signal' and value is None else None)
  reason=decision['status'] if missing_stage=='signal' else (e['status'] if missing_stage=='return' else None)
  rows.append({'case_id':c['case_id'],'date':c['date'],'symbol':symbol,'target_role':'stock' if stock else 'benchmark',
    'current_etf_classification':c['current_etf_classification'],'sample_roles':c['roles'],
    'drop_threshold':t,'family':e['family'],'exit_horizon':e['horizon'],'cost_bps_per_side':cost,
    'risk_set_eligible':bool(c['stock_eligible'] and prefix['is_flush'] is not None),
    'flush_detected':prefix['is_flush'],'signal_status':signal,'engine_signal_status':decision['status'],
    'signal_minute':decision['signal_minute'],'decision_minute':decision['decision_minute'],
    'entry_minute':entry,'exit_minute':exit,'net_return':value,'gross_return':e['gross_return'],
    'entry_open':(window_result or {}).get('entry_open'),'exit_open':(window_result or {}).get('exit_open'),
    'window_status':(window_result or {}).get('status'),'missing_stage':missing_stage,'missing_reason':reason,
    'missing_holding_minutes':coverage.get('missing_minutes',[]),'invalid_holding_minutes':coverage.get('invalid_minutes',{}),
    'paired_controls':controls,'paired_control_status':paired_status,'matched_excess':excess,'benchmarks':benchmarks,
    'actual_fill':False,'reference_prices':'retrospective_SIP_minute_open_proxies','wealth_path':False})
 diagnostics={'case_id':c['case_id'],'date':c['date'],'symbol':symbol,'target_role':'stock' if stock else 'benchmark',
  'prior_liquidity':(c['selection_metrics'] or {}).get('median_prior20_daily_close_times_volume'),
  'normalization_issues':normalization.get('issues',[]),'prefixes':prefixes[symbol],'family_decisions':decisions,
  'preselected_peers':peers,'no_peer_replacement_after_future_data_check':True}
 return rows,diagnostics

def variant(row):
    return (row['drop_threshold'], row['family'], row['exit_horizon'], row['cost_bps_per_side'])


def price_bin(numerator, denominator):
    if numerator is None or denominator is None:
        return 'unknown'
    n, d = Fraction(D(numerator)), Fraction(D(denominator))
    if n <= 0 or d <= 0:
        raise ValueError('price_bin_requires_positive_prices')
    value = n / d - 1
    return ('le_minus_2pct' if value <= Fraction(-2,100) else
            'minus_2pct_to_below_zero' if value < 0 else 'zero_or_positive')


def fixed_bins(feature):
    if not feature['prefix_valid']:
        return dict.fromkeys(BIN_LABELS, 'unknown')
    return {'gap': price_bin(feature['open0'], feature['previous_close'])
                   if feature['previous_close_valid'] else 'unknown',
            'close29': price_bin(feature['close29'], feature['open0']),
            'trough': BIN_LABELS['trough'][feature['t_min'] // 10]}


def describe(values):
    values = [Fraction(D(x)) for x in values if x is not None]
    return {'n': len(values), 'mean': render(sum(values,Fraction()) / len(values)) if values else None,
            'minimum': render(min(values)) if values else None,
            'maximum': render(max(values)) if values else None}


def summarize(rows):
    counts = dict.fromkeys(('selected_cases','signals','unknown_signals','no_flush','no_rebound',
                           'complete_net','complete_pairs','signal_return_missing'),0)
    statuses, paired = Counter(), Counter()
    net, excess = [], []
    daily = defaultdict(lambda: {'net': [], 'excess': []})
    for row in rows:
        counts['selected_cases'] += 1
        status = row['signal_status']
        statuses[status] += 1
        paired[row['paired_control_status']] += 1
        if status == 'signal':
            counts['signals'] += 1
            counts['signal_return_missing'] += row['net_return'] is None
        elif status == 'unknown':
            counts['unknown_signals'] += 1
        else:
            counts[status] += 1
        if row['net_return'] is not None:
            net.append({'case_id': row['case_id'], 'net_return': row['net_return']})
            daily[row['date']]['net'].append(row['net_return'])
        if row['matched_excess'] is not None:
            excess.append(row['matched_excess'])
            daily[row['date']]['excess'].append(row['matched_excess'])
    counts['complete_net'], counts['complete_pairs'] = len(net), len(excess)
    return {'counts': counts, 'signal_status_counts': dict(statuses),
            'paired_control_status_counts': dict(paired),
            'target_net_distribution': compute_distribution(net),
            'matched_excess': {**describe(excess), 'scope': 'relative_performance_not_trading_profit'},
            'date_equal_net': describe(describe(v['net'])['mean'] for v in daily.values()),
            'date_equal_excess': describe(describe(v['excess'])['mean'] for v in daily.values())}


def _selected_cases(protocol, seal, source):
    if seal.get('accepted') is not True:
        raise ValueError('accepted_preopen_seal_required')
    day = seal.get('date')
    if source.get('date') != day:
        raise ValueError('source_date_differs_from_seal')
    sessions = [s for s in protocol['sessions'] if s['date'] == day]
    if len(sessions) != 1:
        raise ValueError('date_not_in_protocol')
    if tuple(map(tuple,protocol['inherited_variants'])) != VARIANTS or tuple(protocol['primary_variant']) != PRIMARY:
        raise ValueError('registered_economic_variants_changed')
    selection = seal.get('selection') or {}
    if selection.get('status') != 'ready':
        raise ValueError('ready_sealed_selection_required')
    cases = []
    for row in selection.get('rows',[]):
        if row.get('selected') is True:
            case = dict(row)
            # Only schema adaptation: old economic function consumes this name.
            case['current_etf_classification'] = case.get('current_etf_classification',case.get('etf_classification','unknown'))
            if case.get('date') != day or case.get('case_id') != day + '__' + case.get('symbol',''):
                raise ValueError('selected_case_identity_mismatch')
            cases.append(case)
    symbols = [c['symbol'] for c in cases]
    requested = source.get('requested_symbols',[])
    if len(symbols) != len(set(symbols)) or not {'SPY','QQQ'} <= set(symbols):
        raise ValueError('duplicate_or_missing_control_cases')
    if len(selection.get('selected_symbols',[])) != len(symbols) or set(selection['selected_symbols']) != set(symbols):
        raise ValueError('seal_selected_symbols_mismatch')
    if len(requested) != len(set(requested)) or set(requested) != set(symbols):
        raise ValueError('source_requested_symbols_mismatch')
    if not isinstance(source.get('bars'),dict) or not set(source['bars']) <= set(symbols):
        raise ValueError('source_bars_not_selected_symbol_mapping')
    for case in cases:
        if case.get('stock_eligible') is True and case['symbol'] not in ('SPY','QQQ'):
            liq = D((case.get('selection_metrics') or {})['median_prior20_daily_close_times_volume'])
            if not liq.is_finite() or liq <= 0:
                raise ValueError('invalid_sealed_prior_liquidity')
    session = sessions[0]
    start = datetime.fromisoformat(session['open_utc'])
    end = datetime.fromisoformat(session['close_utc'])
    minutes = int((end-start).total_seconds() // 60)
    if minutes <= 30 or (end-start).total_seconds() != minutes*60:
        raise ValueError('invalid_session_duration')
    return cases, minutes, session


def _json_bytes(value):
    return (json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode()


def _gzip_rows(path, rows):
    with path.open('xb') as raw:
        with gzip.GzipFile(filename='',mode='wb',fileobj=raw,mtime=0) as handle:
            for row in rows:
                handle.write(_json_bytes(row))


def _report(summary):
    primary = summary['primary_SPY_down']
    count = primary['counts']
    lines = ['# '+summary['date']+' 封存样本日终研究', '',
             '研究范围：'+summary['analysis_scope']+'。日终 SIP 股票参考价格，非实际成交、非期权收益或资金路径。', '',
             f"保留 {summary['counts']['selected_cases']} 个封存样本、{summary['counts']['result_rows']} 条方案结果；源完成状态：{summary['source']['source_complete']}。", '',
             f"主方案 SPY 同跌股票：入组 {count['selected_cases']}，信号 {count['signals']}，完整净收益 {count['complete_net']}，完整三同业超额 {count['complete_pairs']}，未知信号 {count['unknown_signals']}，信号后缺收益 {count['signal_return_missing']}。", '',
             '成本已在原引擎按每侧5/10/25bp扣除一次。未触发与未知不作为零收益；SPY/QQQ控制样本不计入股票统计。', '',
             '所有正负尾部保留，不设40%胜率门槛，不挑选表现最好分组晋级。首30分钟特征按10:00事件时间描述，日终取数不能证明当时已收到。', '',
             '## 固定特征分组（主方案 SPY 同跌股票）', '',
             '|轴|分组|入组|信号|完整净收益|完整配对|平均净收益|',
             '|---|---|---:|---:|---:|---:|---:|']
    def pct(value):
        return 'unknown' if value is None else format(D(value)*100,'.6f')+'%'
    for row in summary['fixed_feature_bins']:
        table=row['summary'];c=table['counts']
        lines.append(f"|{row['axis']}|{row['bin']}|{c['selected_cases']}|{c['signals']}|{c['complete_net']}|{c['complete_pairs']}|{pct(table['target_net_distribution']['mean'])}|")
    lines += ['', '## 全部固定方案与市场分组', '',
              '|方案|基准|市场分组|入组|信号|未知信号|完整净收益|完整配对|平均净收益|',
              '|---|---|---|---:|---:|---:|---:|---:|---:|']
    for row in summary['variant_group_summaries']:
        table=row['summary'];c=table['counts'];v='/'.join(map(str,row['variant']))
        lines.append(f"|{v}|{row['benchmark']}|{row['group']}|{c['selected_cases']}|{c['signals']}|{c['unknown_signals']}|{c['complete_net']}|{c['complete_pairs']}|{pct(table['target_net_distribution']['mean'])}|")
    lines += ['', '单日不计算新的置信区间；累计复盘遵照原登记检查点。完整分母、盈亏分布和缺失原因见JSON及逐例结果。', '']
    return '\n'.join(lines).encode()


def analyze(protocol, seal, source, out_dir):
    """Calculate a day from accepted sealed rows and provider RFC3339 bars.

    No network or clock gate is performed here. Caller must validate the seal,
    receipt times, source hashes and source version before this economic step.
    Source incompleteness preserves every case*36 with unknown outcomes.
    Returns the summary after publishing the output directory in one rename.
    Existing destination/lock is rejected; no files are replaced.
    """
    cases, session_minutes, session = _selected_cases(protocol,seal,source)
    complete = source.get('source_complete') is True
    prepared, prefixes, features, diagnostics, results = {}, {}, [], [], []
    normalized = {case['symbol']: normalize_provider_bars(source['bars'].get(case['symbol'],[]),
                  seal['date'],session_open_et=session.get('open_et','09:30')) for case in cases}
    for case in cases:
        symbol=case['symbol'];bars=normalized[symbol]['bars']
        prepared[symbol]=prepare_bars(bars,source_complete=complete)
        prefixes[symbol]={t:evaluate_prefix(prepared[symbol],t) for t in ('0.02','0.03')}
        previous=(case.get('selection_metrics') or {}).get('prior_close')
        feature=first30_features(bars,previous,source_complete=complete)
        classifications={b: classify_case(bars,normalized[b]['bars'],source_complete=complete)
                         for b in ('SPY','QQQ')}
        features.append({'case_id':case['case_id'],'date':case['date'],'symbol':symbol,
                         'target_role':'benchmark' if symbol in ('SPY','QQQ') else 'stock',
                         'features':feature,'fixed_bins':fixed_bins(feature),
                         'market_classification':classifications,
                         'SPY_group': 'not_stock_target' if symbol in ('SPY','QQQ') else classifications['SPY']['classification'] or 'unknown',
                         'QQQ_group': 'not_stock_target' if symbol in ('SPY','QQQ') else classifications['QQQ']['classification'] or 'unknown',
                         'event_time_available_at_ET':'10:00',
                         'live_received_intraday':False,'actual_fill':False})
    feature_map={f['case_id']: f for f in features}
    for case in cases:
        rows, diagnostic=evaluate_case(case,cases,prepared,prefixes,session_minutes,normalized[case['symbol']])
        if len(rows) != 36 or set(map(variant,rows)) != set(VARIANTS):
            raise AssertionError('inherited_engine_variant_mismatch')
        feature=feature_map[case['case_id']]
        for row in rows:
            row.update(SPY_group=feature['SPY_group'],QQQ_group=feature['QQQ_group'],
                       fixed_bins=feature['fixed_bins'],live_received_intraday=False,
                       stock_eligible=case['stock_eligible'])
        results.extend(rows);diagnostics.append(diagnostic)
    stock=[r for r in results if r['target_role']=='stock' and r['stock_eligible'] is True]
    grouped=defaultdict(list)
    for row in stock:
        for benchmark in ('SPY','QQQ'):
            grouped[(variant(row),benchmark,row[benchmark+'_group'])].append(row)
    tables=[{'variant':list(v),'benchmark':benchmark,'group':g,
             'summary':summarize(grouped[(v,benchmark,g)])}
            for v in VARIANTS for benchmark in ('SPY','QQQ') for g in GROUPS]
    main=grouped[(PRIMARY,'SPY','market_down')]
    bins=[{'axis':axis,'bin':label,
           'summary':summarize([r for r in main if r['fixed_bins'][axis]==label])}
          for axis,labels in BIN_LABELS.items() for label in labels]
    summary={'protocol_id':protocol['id'],'date':seal['date'],
             'analysis_scope':source.get('analysis_scope','prospective_sealed_postclose_stock_proxy'),
             'source':{'source_complete':complete,'status':source.get('status','not_supplied')},
             'counts':{'selected_cases':len(cases),'stock_cases':len(cases)-2,'benchmark_cases':2,
                       'result_rows':len(results),'variants':36,'variant_group_summaries':len(tables),
                       'prefix_complete':sum(f['features']['prefix_valid'] for f in features)},
             'primary_SPY_down':summarize(main),'variant_group_summaries':tables,
             'fixed_feature_bins':bins,'actual_fill':False,'wealth_path':False,
             'option_return_claim':False,'costs_already_net_not_deducted_twice':True,
             'economic_source_sha256':ECONOMIC_SOURCE_SHA256}
    target=Path(out_dir);target.parent.mkdir(parents=True,exist_ok=True)
    lock=target.with_name(target.name+'.analysis-lock')
    fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    temporary=None
    try:
        if target.exists():
            raise FileExistsError(str(target))
        temporary=Path(tempfile.mkdtemp(prefix='.'+target.name+'-',dir=target.parent))
        _gzip_rows(temporary/'results.jsonl.gz',results)
        _gzip_rows(temporary/'features.jsonl.gz',features)
        _gzip_rows(temporary/'case-diagnostics.jsonl.gz',diagnostics)
        (temporary/'summary.json').write_bytes(_json_bytes(summary))
        (temporary/'REPORT.zh-CN.md').write_bytes(_report(summary))
        manifest={'date':seal['date'],'analysis_scope':summary['analysis_scope'],
                  'files':[{'name':p.name,'bytes':p.stat().st_size,
                            'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
                           for p in sorted(temporary.iterdir())]}
        (temporary/'analysis-manifest.json').write_bytes(_json_bytes(manifest))
        if target.exists():
            raise FileExistsError(str(target))
        os.rename(temporary,target);temporary=None
    finally:
        if temporary is not None:
            shutil.rmtree(temporary)
        lock.unlink()
    return summary
