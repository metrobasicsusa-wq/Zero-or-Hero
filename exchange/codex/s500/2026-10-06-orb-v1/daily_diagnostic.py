"""Post-failure, independent one-session diagnostics; never a capital path."""
import hashlib
import json
import math
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

from engine import run_path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def write(name, value):
    path = ROOT / name
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, separators=(',', ':'), allow_nan=False) + '\n')
    temp.replace(path)


def close(a, b):
    assert math.isclose(a, b, rel_tol=1e-10, abs_tol=1e-7), (a, b)


def raw_bar(minutes, day, symbol, offset):
    series = minutes[day][symbol]
    return series.get(str(offset), series.get(offset))


def reconcile(result, minutes, calendar_row):
    """Check episode funding and source-price evidence independently of engine helpers."""
    day = calendar_row['date']; rate = result['parameters']['cost_bps_each_side'] / 10000
    assert result['initial_funding'] == 500 and result['additional_funding'] == 0
    assert result['restart_count'] == 0 and len(result['daily']) == 1
    d = result['daily'][0]
    close(d['settled_cash_before_signals'], 500)
    close(d['settlement_cash_paid'], 0)
    assert len(result['entries']) <= 1 and len(result['trades']) <= 1
    assert len(result['trades']) <= len(result['entries'])
    debit = 0
    for e in result['entries']:
        assert e['entry_offset'] == e['signal_minute_offset'] + 2
        signal = raw_bar(minutes, day, e['symbol'], e['signal_minute_offset'])
        entry = raw_bar(minutes, day, e['symbol'], e['entry_offset'])
        close(signal['c'], e['signal_close']); close(entry['o'], e['entry_price'])
        assert isinstance(e['qty'], int) and e['qty'] >= 1
        unit = entry['o'] * (1 + rate)
        budget = 500 * result['parameters']['capital_fraction']
        assert e['qty'] * unit <= budget + 1e-8
        assert (e['qty'] + 1) * unit > budget - 1e-8
        debit += e['qty'] * unit
        close(e['entry_debit'], debit)
    close(result['settled_cash'], 500 - debit)
    assert result['settled_cash'] >= -1e-8
    credit = 0
    for t in result['trades']:
        b = raw_bar(minutes, day, t['symbol'], t['exit_offset'])
        if t['exit_reason'] in ('scheduled_last_minute_open', 'stop_gap_open'):
            close(t['exit_price'], b['o'])
        else:
            assert t['exit_reason'] == 'stop_touch_in_minute'
            assert b['l'] <= t['stop_price'] < b['o']
            close(t['exit_price'], t['stop_price'])
        credit += t['qty'] * t['exit_price'] * (1 - rate)
        close(t['exit_credit'], credit); close(t['pnl'], credit - debit)
        assert t['settlement_date'] == calendar_row['settlement_date'] > day
    close(result['unsettled_cash'], credit)
    close(sum(r['amount'] for r in result['terminal_receivables']), credit)
    for receivable in result['terminal_receivables']:
        assert receivable['settlement_date'] == calendar_row['settlement_date']
    if result['status'] == 'incomplete':
        assert result['final_equity'] is None and d['equity'] is None
        assert result['incomplete'] and d['incomplete']
        if result['entries']:
            assert result['open_position'] is not None and not result['trades']
            pos = result['open_position']
            close(result['last_observed_equity'], 500 - debit + pos['qty'] * pos['last_observed_price'] * (1-rate))
        else:
            close(result['settled_cash'], 500)
    else:
        assert result['open_position'] is None and result['pending_entry'] is None
        close(result['final_equity'], 500 - debit + credit)
        close(d['equity'], result['final_equity'])
        if d['status'] == 'skipped_data_gap':
            assert not result['entries'] and not result['trades']
            assert len(result['aborted_sessions']) == 1 and d['aborted_decision']
            close(result['final_equity'], 500)
    return {'funding_checked': True, 'source_entries_checked': len(result['entries']),
            'source_exits_checked': len(result['trades']),
            'incomplete_exposure_retained': result['status'] == 'incomplete' and bool(result['open_position'])}


def distribution(values):
    if not values:
        return {'count': 0, 'mean': None, 'median': None, 'minimum': None, 'maximum': None,
                'p10': None, 'p90': None, 'positive': 0, 'negative': 0, 'zero': 0}
    s = sorted(values)
    def quantile(q):
        index = (len(s)-1)*q; lower = int(index); fraction = index-lower
        return s[lower] + (s[min(lower+1,len(s)-1)]-s[lower])*fraction
    return {'count':len(s),'mean':mean(s),'median':median(s),'minimum':s[0],'maximum':s[-1],
            'p10':quantile(.1),'p90':quantile(.9),'positive':sum(x>0 for x in s),
            'negative':sum(x<0 for x in s),'zero':sum(x==0 for x in s)}


def main():
    names = ['daily_diagnostic.py','engine.py','prepare.json','ranked.json',
             'bounded-coverage.json','minute-market.json','results.json']
    hashes = {n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    design = {'created_at':datetime.now(timezone.utc).isoformat(),
        'id':'ORB2026-YTD-postfailure-independent-session-diagnostics-v1',
        'design_timing':'Frozen after observing the original 12 capital paths fail, before this diagnostic executes.',
        'known_failures_before_design':['Original six paths stop on 2026-01-02 ranking coverage.',
            'Guarded six paths stop on 2026-01-06 with PRCT held at the 09:49 minute gap.'],
        'purpose':'Locate later YTD episodes that can be evaluated without inventing wealth after an unresolved holding.',
        'registered_allocations':[.5,1.],'registered_cost_bps_each_side':[25,50,100],
        'sessions_expected':190,'episodes_expected':1140,'period':['2026-01-02','2026-10-05'],
        'per_episode_initial_cash':500,'per_episode_sessions':1,
        'pre_entry_gap_policy':'skip_session','coverage_source':'bounded-coverage.json',
        'reset_semantics':'Statistical resampling of each session with a fresh hypothetical $500. These are not funded strategy restarts, contributions, or a portfolio.',
        'forbidden_interpretations':['No compounding across days.','Never compute 500 plus summed episode PnL as wealth.',
            'No portfolio $10,000 milestone claim.','No annual return, CAGR or strategy Sharpe from these disconnected episodes.',
            'No claim that 1140 episodes are independent; six parameter variants share the same 190 sessions.',
            'No hindsight selection of a winning parameter as evidence of an edge.'],
        'missingness':'Completed-episode PnL is conditional on evaluable data and may be selection biased. Every skipped and incomplete episode is retained.',
        'settlement':'One-day episodes retain sale proceeds as terminal T+1 receivables; they cannot fund another episode.',
        'source_sha256':hashes,'broker_orders_sent':0}
    design_path = ROOT/'daily-diagnostics-design.json'
    if design_path.exists():
        previous = read(design_path.name)
        assert previous['source_sha256'] == hashes, 'Frozen design inputs changed; preserve this version and create a successor.'
        design = previous
    else:
        write(design_path.name,design)
    prepared=read('prepare.json');calendar=prepared['calendar'];del prepared
    ranked=read('ranked.json');coverage=read('bounded-coverage.json')['coverage'];minutes=read('minute-market.json')
    dates=[x['date'] for x in calendar]
    assert len(dates)==190 and dates[0]=='2026-01-02' and dates[-1]=='2026-10-05'
    assert set(dates)==set(ranked)==set(coverage)
    for rows in ranked.values():
        for r in rows:assert all(k in r for k in ('opening_volume','mean_opening_volume14','rv'))
    records=[];summaries=[]
    for allocation in design['registered_allocations']:
        for bps in design['registered_cost_bps_each_side']:
            group=[]
            for session in calendar:
                day=session['date']
                episode=run_path({day:ranked[day]},[session],{day:minutes.get(day,{})},allocation,bps,
                    coverage={day:coverage[day]},pre_entry_gap_policy='skip_session')
                episode['engine_result_id']=episode['id']
                episode['id']=f'POSTFAILURE-SESSION-DIAGNOSTIC__{day}__a{allocation:g}__c{bps}'
                episode['research_type']='independent_one_session_statistical_reset_not_capital_path'
                episode['episode_date']=day
                episode['reconciliation']=reconcile(episode,minutes,session)
                records.append(episode);group.append(episode)
            complete=[r for r in group if r['status']!='incomplete']
            traded=[r for r in complete if r['trades']]
            incomplete=[r for r in group if r['status']=='incomplete']
            summaries.append({'capital_fraction':allocation,'cost_bps_each_side':bps,'episodes':len(group),
                'day_status_counts':dict(Counter(r['daily'][0]['status'] for r in group)),
                'complete_episodes_including_skips':len(complete),
                'completed_trading_episodes':len(traded),
                'completed_no_entry_without_data_skip':sum(not r['trades'] and r['daily'][0]['status']!='skipped_data_gap' for r in complete),
                'skipped_data_gap_episodes':sum(r['daily'][0]['status']=='skipped_data_gap' for r in group),
                'incomplete_episodes':len(incomplete),
                'incomplete_with_held_exposure':sum(r['open_position'] is not None for r in incomplete),
                'incomplete_reasons':dict(Counter(r['incomplete']['reason'] for r in incomplete)),
                'skip_reasons':dict(Counter(a['reason'] for r in group for a in r['aborted_sessions'])),
                'completed_trade_pnl_dollars_distribution':distribution([t['pnl'] for r in traded for t in r['trades']]),
                'completed_episode_pnl_including_cash_skips_distribution':distribution([r['final_equity']-500 for r in complete]),
                'completed_trade_exit_reasons':dict(Counter(t['exit_reason'] for r in traded for t in r['trades'])),
                'completed_trade_dates':[r['episode_date'] for r in traded],
                'incomplete_details':[{'date':r['episode_date'],'gap':r['incomplete'],'open_position':r['open_position']} for r in incomplete]})
    assert len(records)==1140 and len({r['id'] for r in records})==1140
    payload={'design_id':design['id'],'design_sha256':hashlib.sha256(design_path.read_bytes()).hexdigest(),
             'source_sha256':hashes,'record_count':len(records),'episodes':records,
             'wealth_series_generated':False,'broker_orders_sent':0}
    write('daily-diagnostics.json',payload)
    summary={'created_at':datetime.now(timezone.utc).isoformat(),'design_id':design['id'],
        'research_type':'postfailure diagnostic; not preregistered strategy advantage',
        'period':design['period'],'unique_sessions':190,'parameter_variants':6,'episode_records':len(records),
        'episode_records_sha256':hashlib.sha256((ROOT/'daily-diagnostics.json').read_bytes()).hexdigest(),
        'episode_records_bytes':(ROOT/'daily-diagnostics.json').stat().st_size,
        'reset_semantics':design['reset_semantics'],'missingness':design['missingness'],
        'cross_variant_dependence':'All six variants share dates and market inputs. Counts do not establish independent observations.',
        'not_a_wealth_path':True,'no_cumulative_pnl_or_portfolio_return_calculated':True,
        'reconciliation':{'episodes_checked':len(records),
            'source_entries_checked':sum(r['reconciliation']['source_entries_checked'] for r in records),
            'source_exits_checked':sum(r['reconciliation']['source_exits_checked'] for r in records),
            'incomplete_held_episodes_retained':sum(r['reconciliation']['incomplete_exposure_retained'] for r in records)},
        'parameter_groups':summaries,'broker_orders_sent':0}
    write('daily-diagnostics-summary.json',summary)
    print(json.dumps({'episodes':len(records),'bytes':summary['episode_records_bytes'],
        'reconciliation':summary['reconciliation'],
        'groups':[{k:r[k] for k in ('capital_fraction','cost_bps_each_side','completed_trading_episodes',
            'skipped_data_gap_episodes','incomplete_episodes','completed_trade_pnl_dollars_distribution')} for r in summaries]},indent=2))


if __name__=='__main__':
    main()
