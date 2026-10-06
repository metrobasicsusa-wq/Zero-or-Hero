"""Offline registered EP news/volume/breakout case gates; no exit or return model."""
import hashlib
import importlib.util
import json
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'s500-orb-20261006'
NY=ZoneInfo('America/New_York')


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def valid_bar(bar):
    if not isinstance(bar,dict):return False
    def finite(x):return isinstance(x,(int,float)) and not isinstance(x,bool) and math.isfinite(x)
    return (all(finite(bar.get(k)) and bar[k]>0 for k in ('o','h','l','c'))
        and finite(bar.get('v')) and bar['v']>=0
        and bar['l']<=min(bar['o'],bar['c'])<=max(bar['o'],bar['c'])<=bar['h'])


def causal_signal(series, or_high):
    """First completed breakout, preserving every observed predecessor and any gap."""
    scanned=[]
    for minute in range(30,90):
        bar=series.get(minute)
        if not valid_bar(bar):
            return {'status':'unknown_signal_gap','first_missing_or_invalid_offset':minute,'observed_bars':scanned}
        passed=bar['c']>or_high
        scanned.append({'minute_offset':minute,'close':bar['c'],'above_or30_high':passed})
        if passed:
            return {'status':'first_breakout_confirmed','signal_minute_offset':minute,
                    'planned_entry_offset':minute+2,'observed_bars':scanned}
    return {'status':'no_breakout_before_11','observed_bars':scanned}


def self_check():
    base={m:{'o':10,'h':11,'l':9,'c':10,'v':100} for m in range(30,90)}
    assert causal_signal(base,11)['status']=='no_breakout_before_11'
    base[31]={'o':10,'h':12,'l':9,'c':12,'v':100}
    assert causal_signal(base,11)['signal_minute_offset']==31
    assert causal_signal(base,11)['planned_entry_offset']==33
    base[50]=None
    assert causal_signal(base,11)['signal_minute_offset']==31  # future gap cannot remove first signal
    base[30]=None
    assert causal_signal(base,11)['status']=='unknown_signal_gap'  # never choose later winner
    base={m:{'o':10,'h':11,'l':9,'c':11,'v':100} for m in range(30,90)}
    assert causal_signal(base,11)['status']=='no_breakout_before_11'  # equality is not breakout
    base[89]={'o':11,'h':12,'l':10,'c':12,'v':100}
    assert causal_signal(base,11)['planned_entry_offset']==91  # last signal bar completes exactly11:00


def main():
    self_check()
    design=read(ROOT/'ep-gate-design.json')
    assert sha(OLD/'registry.json')==design['rule_source_registry_sha256']
    inventory=read(OLD/'ep-candidate-inventory.json')
    followup=read(ROOT/'ep-followup.json')
    candidate=next(r for r in inventory['candidates'] if r['date']=='2026-01-05' and r['symbol']=='ALT')
    news=next(r for r in followup['candidate_reviews'] if r['date']==candidate['date'] and r['symbol']==candidate['symbol'])
    assert news['pass_registered_news_gate'] is True and not candidate['applied_splits']
    calendar=next(r for r in read(OLD/'prepare.json')['calendar'] if r['date']==candidate['date'])
    spec=importlib.util.spec_from_file_location('prior_bar_loader',OLD/'market_data.py')
    loader=importlib.util.module_from_spec(spec);spec.loader.exec_module(loader)
    folder=ROOT/'raw-ep/market/ALT-2026-01-05-full'
    meta=read(folder/'complete.json')
    bars=loader.load_window(folder)['ALT']  # local hash-verified cache read; no network method called
    start=datetime.fromisoformat(candidate['date']+'T'+calendar['open']+':00').replace(tzinfo=NY)
    close=datetime.fromisoformat(candidate['date']+'T'+calendar['close']+':00').replace(tzinfo=NY)
    expected=int((close-start).total_seconds()/60)
    series={};outside=[];duplicates=[]
    for bar in bars:
        moment=datetime.fromisoformat(bar['t'].replace('Z','+00:00')).astimezone(NY)
        offset=(moment-start).total_seconds()/60
        if offset!=int(offset):raise ValueError('noncanonical_minute_timestamp')
        offset=int(offset)
        if not 0<=offset<expected:
            outside.append({'timestamp':bar['t'],'offset':offset,'reason':'outside_regular_session_excluded'})
            continue
        if offset in series:duplicates.append(offset)
        series[offset]=bar
    if duplicates:raise ValueError('duplicate_regular_minutes')
    missing=[m for m in range(expected) if not valid_bar(series.get(m))]
    source_pages=[]
    for page in meta['pages']:
        stored=read(folder/page['name']);assert sha(folder/page['name'])==page['sha256']
        source_pages.append({**page,'retrieved_at':stored['retrieved_at'],
                             'next_page_token_is_null':stored['response'].get('next_page_token') is None})
    assert source_pages[-1]['next_page_token_is_null']
    result={'schema':'s500.ep-isolated-candidate-gates.v1','created_at':datetime.now(timezone.utc).isoformat(),
        'date':candidate['date'],'symbol':'ALT','research_type':'isolated candidate causal gate and entry-reference diagnostic, not a portfolio winner or return estimate',
        'frozen_design_id':design['id'],'frozen_design_recorded_at':design['recorded_at'],
        'source_hashes':{'ep-gate-design.json':sha(ROOT/'ep-gate-design.json'),
            'registry.json':sha(OLD/'registry.json'),'ep-candidate-inventory.json':sha(OLD/'ep-candidate-inventory.json'),
            'ep-followup.json':sha(ROOT/'ep-followup.json'),'prepare.json':sha(OLD/'prepare.json'),
            'market_data.py':sha(OLD/'market_data.py'),'ep_case.py':sha(ROOT/'ep_case.py'),
            'full_session_complete.json':sha(folder/'complete.json')},
        'market_source':{'url':'https://data.alpaca.markets/v2/stocks/bars','parameters':meta['parameters'],
            'request_sha256':meta['request_sha256'],'pages':source_pages},
        'news_gate':{'pass':True,'event_category':news['event_category'],
            'timestamp_evidence':news['timestamp_evidence'],'source_keys':news['source_keys'],
            'news_window':news['news_window'],'classification':'regulatory announcement; FDA designation, not marketing approval'},
        'coverage':{'regular_minutes_expected':expected,'api_bars_received':len(bars),
            'regular_minutes_present':len(series),'regular_valid_ohlcv_minutes':expected-len(missing),
            'missing_or_invalid_regular_offsets':missing,'outside_regular_session_bars':outside,
            'duplicate_offsets':duplicates,'first30_expected':30,
            'first30_valid':sum(valid_bar(series.get(m)) for m in range(30)),
            'full_session_coverage_role':'audit only; future bar values are not used in the earlier gate or signal'},
        'status':'unresolved','quote_point':None,'strategy_returns_computed':False,'broker_orders_sent':0}
    if any(m<30 for m in missing):
        result['status']='incomplete_opening30';result['opening30_missing_offsets']=[m for m in missing if m<30]
    else:
        opening=[series[m] for m in range(30)]
        volume=sum(b['v'] for b in opening)
        prior=candidate['prior20_mean_adjusted_daily_volume']
        gap=opening[0]['o']/candidate['prior_close_adjusted']-1
        or_high=max(b['h'] for b in opening);or_low=min(b['l'] for b in opening)
        assert volume==7120536 and math.isclose(prior,4851779.65)
        assert math.isclose(opening[0]['o'],candidate['daily_open_reference'])
        result['opening30']={'available_at':(start+timedelta(minutes=30)).isoformat(),
            'regular_open':opening[0]['o'],'daily_open_reference':candidate['daily_open_reference'],
            'regular_open_matches_daily_reference':True,'prior_close_adjusted':candidate['prior_close_adjusted'],
            'applied_splits':candidate['applied_splits'],'gap_fraction':gap,'gap_gate_pass':gap>=.1,
            'bar_count':30,'volume':volume,'prior20_mean_adjusted_full_session_volume':prior,
            'volume_ratio':volume/prior,'volume_gate_pass':volume>=prior,
            'or_high':or_high,'or_low':or_low,'or_open':opening[0]['o'],'or_close':opening[-1]['c'],
            'opening_bar_source_timestamps':[b['t'] for b in opening]}
        if gap<.1 or volume<prior:
            result['status']='opening_gap_or_volume_gate_failed'
        else:
            scan=causal_signal(series,or_high)
            result['signal_scan']=scan
            result['status']=scan['status']
            if scan['status']=='first_breakout_confirmed':
                m=scan['signal_minute_offset'];entry=scan['planned_entry_offset']
                signal_complete=start+timedelta(minutes=m+1);entry_time=start+timedelta(minutes=entry)
                reference=series.get(entry)
                result['signal']={'minute_offset':m,'minute_start_et':(start+timedelta(minutes=m)).isoformat(),
                    'completed_at_et':signal_complete.isoformat(),'completed_at_utc':signal_complete.astimezone(timezone.utc).isoformat(),
                    'close':series[m]['c'],'or30_high':or_high,'prior_signal_bars_observed':len(scan['observed_bars'])-1,
                    'all_prior_signal_closes_at_or_below_or_high':all(x['close']<=or_high for x in scan['observed_bars'][:-1]),
                    'entry_latency':'One full minute after the signal bar completes; planned entry uses minute-start offset+2.'}
                if not isinstance(reference,dict) or not isinstance(reference.get('o'),(int,float)) or not math.isfinite(reference['o']) or reference['o']<=0:
                    result['status']='incomplete_selected_entry_reference'
                    result['pending_entry']={'entry_offset':entry,'time':entry_time.isoformat(),'known_stop_or30_low':or_low}
                else:
                    entry_open=reference['o'];clear=entry_open>or_low
                    result['entry_reference']={'minute_offset':entry,'time_et':entry_time.isoformat(),
                        'time_utc':entry_time.astimezone(timezone.utc).isoformat(),'minute_open':entry_open,
                        'known_stop_or30_low':or_low,'distance_to_stop':entry_open-or_low,
                        'above_stop':clear,'modeled_fill_claimed':False,
                        'exposure_status':'reference_only_no_order' if clear else 'unresolved_gap_through_entry_stop'}
                    result['status']='signal_and_entry_reference_ready_for_quote_check' if clear else 'incomplete_gap_through_entry_stop'
                    decision=entry_time.astimezone(timezone.utc)
                    result['quote_point']={'id':'EP__'+candidate['date']+'__ALT__'+decision.strftime('%H%M%SZ'),
                        'date':candidate['date'],'symbol':'ALT','decision_time':decision.isoformat(),
                        'request_start':(decision-timedelta(seconds=60)).isoformat(),'request_end':decision.isoformat(),
                        'cohorts':['EP_first10_chronological_news_verified_isolated_case'],
                        'metadata':[{'known_stop_or30_low':or_low,'or30_high':or_high,'entry_reference_open':entry_open,
                            'entry_reference_offset':entry,'signal_completed_at':signal_complete.astimezone(timezone.utc).isoformat(),
                            'metric_policy':'Compare spread with quote ask minus OR30 low, not ORB ATR stop.',
                            'gap_through_entry_stop':not clear}]}
    result['limitations']=['One verified candidate is not a broad-universe winner: 1069 news candidates remain unreviewed, and unknown news is not false.',
        'This diagnostic was frozen after ALT first30 volume was known; it is not untouched validation.',
        'All input bars are historical SIP source observations, not a guarantee of tradable quotes, depth or fills.',
        'Any market quote must be timestamped no later than the planned entry reference; do not select a later favorable quote.',
        'No exits, PnL, cumulative wealth, compounding or target achievement are computed.']
    (ROOT/'ep-case.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    op=result.get('opening30',{});signal=result.get('signal');entry=result.get('entry_reference')
    lines=['# ALT 2026-01-05：EP 单候选门槛','',
        '这是按时间顺序核查前 10 个候选中唯一新闻通过者的孤立案例，不是完整股票池选出的交易赢家。未计算退出、收益或财富。','',
        f'状态：`{result["status"]}`。',
        f'常规时段 {len(series)}/{expected} 个分钟条；API 另有 {len(outside)} 条时段外记录，被排除。',
        '新闻：FDA 突破性疗法资格，保守可用时间 07:31:02 EST；不是批准上市。','']
    if op:
        lines += [f'首 30 分钟量 {op["volume"]:,}，前 20 日平均全天量 {prior:,.2f}，比值 {volume/prior:.6f}；开盘 {op["regular_open"]}，前收 {op["prior_close_adjusted"]}，跳空 {gap:.2%}。',
                  f'10:00 时已知的开盘 30 分钟高/低为 {or_high} / {or_low}。','']
    if signal:
        lines += [f'首次突破信号：{signal["minute_start_et"]} 的分钟条，收盘 {signal["close"]}，于 {signal["completed_at_et"]} 完成；此前信号分钟没有缺口或突破。']
    if entry:
        lines += [f'等待完整一分钟后的入场参考：{entry["time_et"]}，分钟开盘 {entry["minute_open"]}；预先已知止损线 {entry["known_stop_or30_low"]}。这里只标记参考价，没有成交声明。']
    if result['quote_point']:
        q=result['quote_point'];lines += ['',f'后续报价窗口：{q["request_start"]} 至 {q["request_end"]}，只选不晚于决策时间的报价。应比较价差与 ask 减 OR30 低点，而非 ORB 的 ATR 止损。']
    lines += ['', '全部门槛、逐分钟信号扫描、来源参数、哈希和引用时间见 ep-case.json。缺失消息的其他候选仍未知，不能视为没有催化。']
    (ROOT/'ep-case.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:result.get(k) for k in ('status','coverage','opening30','signal','entry_reference','quote_point')},indent=2))


if __name__=='__main__':main()
