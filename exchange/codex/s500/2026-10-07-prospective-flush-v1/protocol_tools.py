"""Receipt and scope guards for the preregistered future cohort. No network."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from collections import Counter
import hashlib,json
from prospect_selection import selection
ET=ZoneInfo('America/New_York')
def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def timestamp(text):
    if not isinstance(text,str):raise ValueError('timestamp_must_be_text')
    value=datetime.fromisoformat(text.replace('Z','+00:00'))
    if value.tzinfo is None:raise ValueError('timestamp_must_have_timezone')
    return value.astimezone(timezone.utc)
def session(protocol,day):
    matches=[s for s in protocol['sessions'] if s['date']==day]
    if len(matches)!=1:raise ValueError('date_not_in_registered_calendar')
    return matches[0]
def check_clock(protocol,day,now,phase):
    s=session(protocol,day);at=timestamp(now);local=at.astimezone(ET)
    if local.date().isoformat()!=day:raise ValueError('capture_must_run_on_target_date')
    if protocol.get('status')!='registered':raise ValueError('protocol_not_registered')
    if timestamp(protocol['registered_at'])>=timestamp(s['open_utc']):raise ValueError('registration_not_before_session')
    if at<timestamp(protocol['registered_at']):raise ValueError('capture_before_registration')
    if phase=='preopen':
        if at>timestamp(s['preopen_cutoff_utc']):raise ValueError('preopen_deadline_missed')
    elif phase=='postclose':
        if not timestamp(s['close_utc'])+timedelta(minutes=15)<=at<=timestamp(s['receipt_deadline_utc']):raise ValueError('postclose_capture_outside_window')
    else:raise ValueError('unknown_capture_phase')
    return s
def seal_selection(protocol,day,panel,bundle,now):
    """Validate completed full-panel receipts before ranking; do not infer receipt truth."""
    out={'date':day,'accepted':False,'reason':None,'selection':None,'actual_fill':False}
    try:
        s=check_clock(protocol,day,now,'preopen');nowdt=timestamp(now)
        if bundle.get('protocol_id')!=protocol['id'] or bundle.get('date')!=day:raise ValueError('bundle_identity_mismatch')
        if bundle.get('panel_content_digest')!=digest(panel):raise ValueError('panel_digest_mismatch')
        if bundle.get('prior20_dates')!=s['prior20_dates']:raise ValueError('not_exact_registered_prior20')
        if bundle.get('feed')!='sip' or bundle.get('adjustment')!='raw' or bundle.get('timeframe')!='1Day':raise ValueError('source_parameters_mismatch')
        symbols={r['symbol'] for r in panel}
        if len(symbols)!=len(panel) or len(panel)!=protocol['fixed_panel_symbols']:raise ValueError('panel_size_or_uniqueness_mismatch')
        if protocol.get('fixed_panel_content_digest')!=digest(panel):raise ValueError('registered_panel_digest_mismatch')
        if bundle.get('source_complete') is not True:raise ValueError('incomplete_full_panel_source')
        requested=bundle.get('requested_symbols',[])
        if len(requested)!=len(set(requested)) or set(requested)!=symbols:raise ValueError('requested_symbols_not_full_panel')
        daily=bundle.get('daily');requests=bundle.get('requests')
        if not isinstance(daily,dict) or set(daily)!=symbols:raise ValueError('daily_symbol_keys_not_full_panel')
        if not isinstance(requests,list) or not requests:raise ValueError('missing_source_receipts')
        batches=Counter()
        for r in requests:
            if r.get('http_status')!=200 or r.get('complete') is not True:raise ValueError('failed_or_incomplete_source_batch')
            start=timestamp(r['requested_at']);end=timestamp(r['received_at'])
            check_clock(protocol,day,r['requested_at'],'preopen');check_clock(protocol,day,r['received_at'],'preopen')
            if start>end or end>nowdt:raise ValueError('receipt_chronology_invalid')
            h=r.get('body_sha256','')
            if len(h)!=64 or any(x not in '0123456789abcdef' for x in h):raise ValueError('invalid_source_hash')
            rs=r.get('requested_symbols',[])
            if not isinstance(rs,list) or not rs or len(rs)!=len(set(rs)):raise ValueError('invalid_batch_symbols')
            batches.update(rs)
        if set(batches)!=symbols or any(n!=1 for n in batches.values()):raise ValueError('batch_coverage_not_exactly_once')
        selected=selection(day,s['prior20_dates'],panel,daily,source_complete=True)
        if selected['status']!='ready':raise ValueError('selection_'+selected['status'])
        out.update(accepted=True,reason='accepted_before_open',selection=selected,sealed_at=now,protocol_id=protocol['id'],protocol_digest=digest(protocol),panel_content_digest=digest(panel),source_bundle_digest=digest(bundle),receipt_count=len(requests),scope='fixed_panel_prior20_selection_not_live_execution')
        out['seal_content_digest']=digest(out)
    except (ValueError,TypeError,KeyError,AttributeError) as error:
        out['reason']=str(error) if isinstance(error,ValueError) else 'malformed_input_'+type(error).__name__
    return out
def validate_postclose(protocol,day,seal,receipt,now):
    out={'date':day,'accepted':False,'reason':None,'live_received_intraday':False,'actual_fill':False}
    try:
        # The source must arrive on time; offline verification/review may run later.
        s=session(protocol,day);timestamp(now)
        if seal.get('accepted') is not True or seal.get('date')!=day or seal.get('protocol_digest')!=digest(protocol):raise ValueError('missing_or_foreign_preopen_seal')
        if seal.get('seal_content_digest')!=digest({k:v for k,v in seal.items() if k!='seal_content_digest'}):raise ValueError('preopen_seal_content_changed')
        check_clock(protocol,day,seal['sealed_at'],'preopen')
        if receipt.get('source_complete') is not True:raise ValueError('postclose_source_incomplete')
        h=receipt.get('body_sha256','')
        if receipt.get('http_status')!=200 or len(h)!=64 or any(x not in '0123456789abcdef' for x in h):raise ValueError('invalid_postclose_source_evidence')
        for key in ['requested_at','received_at']:check_clock(protocol,day,receipt[key],'postclose')
        if not timestamp(receipt['requested_at'])<=timestamp(receipt['received_at'])<=timestamp(now):raise ValueError('receipt_chronology_invalid')
        expected=seal['selection']['selected_symbols'];requested=receipt.get('requested_symbols',[])
        if len(requested)!=len(set(requested)) or set(requested)!=set(expected):raise ValueError('postclose_symbols_differ_from_sealed_selection')
        if receipt.get('feed')!='sip' or receipt.get('adjustment')!='raw' or receipt.get('timeframe')!='1Min':raise ValueError('postclose_source_parameters_mismatch')
        if receipt.get('session_date')!=day:raise ValueError('postclose_session_mismatch')
        if receipt.get('bar_window_start_utc')!=s['open_utc'] or receipt.get('bar_window_end_exclusive_utc')!=s['close_utc']:raise ValueError('postclose_bar_window_mismatch')
        out.update(accepted=True,reason='postclose_receipt_metadata_accepted',scope='Metadata gate only; raw hashes and bar validity require subsequent source verification and inherited engine. Late missing bars are not zero.')
    except (ValueError,TypeError,KeyError,AttributeError) as error:out['reason']=str(error) if isinstance(error,ValueError) else 'malformed_input_'+type(error).__name__
    return out
def initial_ledger(protocol,now):
    at=timestamp(now);rows=[]
    for s in protocol['sessions']:
        beginning=datetime.fromisoformat(s['date']+'T00:00').replace(tzinfo=ET).astimezone(timezone.utc)
        state='not_due' if at<beginning else ('preopen_capture_window' if at<=timestamp(s['preopen_cutoff_utc']) else 'missing_preopen_seal')
        rows.append({'date':s['date'],'ordinal':s['ordinal'],'status':state,'sealed_selection':False,'observed_cases':None,'signals':None,'complete_returns':None,'mean_return':None,'is_review_date':s['ordinal'] in protocol['review_ordinals']})
    return {'protocol_id':protocol['id'],'asof':now,'planned_dates':len(rows),'observed_future_dates':0,'actual_fills':False,'rows':rows,'scope':'Initial empty observation ledger; not_due or missed acquisition is not no_signal or zero return.'}
def write_once(path,obj):
    """Exclusive creation prevents accidental replacement of a first source or seal."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('x') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')
