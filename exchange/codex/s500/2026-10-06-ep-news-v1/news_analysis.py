"""Offline news metadata screening. Time-clean metadata is not catalyst proof."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parent
TIMESTAMP=re.compile(r'^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:\d{2})$')
# Frozen in source before full-batch analysis. Hints route human review only.
KEYWORD_PATTERNS={
    'earnings':r'\b(?:earnings|eps|financial results|quarterly results)\b',
    'guidance':r'\b(?:guidance|outlook|forecast(?:s|ed)?)\b',
    'contract':r'\b(?:contracts?|agreements?|awarded|purchase orders?|licen[cs](?:e|ing))\b',
    'regulatory':r'\b(?:fda|ema|health canada|regulatory|approval|approved|designation|clearance)\b',
}


def fingerprint(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()


def timestamp_ns(value):
    """Strict RFC3339 subset with exact integer nanoseconds and known timezone."""
    if not isinstance(value,str):raise ValueError('timestamp_not_string')
    match=TIMESTAMP.fullmatch(value)
    if not match:raise ValueError('invalid_timestamp_format')
    year,month,day,hour,minute,second=map(int,match.groups()[:6])
    fraction,zone=match.groups()[6:]
    if zone=='-00:00':raise ValueError('unknown_timezone_offset')
    offset=0
    if zone!='Z':
        zh,zm=map(int,zone[1:].split(':'))
        if zh>23 or zm>59:raise ValueError('invalid_timezone_offset')
        offset=(zh*60+zm)*60*(1 if zone[0]=='+' else -1)
    base=datetime(year,month,day,hour,minute,second)  # Reject invalid dates and leap-second ambiguity.
    delta=base-datetime(1970,1,1)
    seconds=delta.days*86400+delta.seconds-offset
    return seconds*1_000_000_000+int((fraction or '').ljust(9,'0'))


def article_key(article):
    if not isinstance(article,dict):return None
    identity=article.get('id')
    if isinstance(identity,bool) or not isinstance(identity,(int,str)):
        return None
    if isinstance(identity,str) and not identity.strip():return None
    # The numeric ID 100 and string ID "100" share an identity, but payload type changes
    # still count as conflicting versions and are quarantined rather than preferred.
    return hashlib.sha256(str(identity).encode()).hexdigest()


def build_global_versions(by_candidate_articles):
    """Index every retrieved full payload, across every candidate, without text output."""
    versions=defaultdict(set)
    windows=by_candidate_articles.values() if isinstance(by_candidate_articles,dict) else by_candidate_articles
    for articles in windows:
        for article in articles:
            key=article_key(article)
            if key is not None:versions[key].add(fingerprint(article))
    return dict(versions)


def hints(article):
    text=' '.join(article.get(k,'') for k in ('headline','summary') if isinstance(article.get(k),str))
    return [name for name,pattern in KEYWORD_PATTERNS.items() if re.search(pattern,text,re.IGNORECASE)]


def public_string(value,limit=None):
    return value if isinstance(value,str) and (limit is None or len(value)<=limit) else None


def analyze_candidate(candidate,articles,global_versions,fetch_complete=True):
    issues=[];lower=upper=None
    try:
        lower=timestamp_ns(candidate['news_request_start']);upper=timestamp_ns(candidate['news_request_end'])
        if lower>=upper:raise ValueError('inverted_window')
    except (KeyError,ValueError,TypeError):issues.append('invalid_candidate_time_window')
    if not isinstance(articles,list):raise ValueError('articles_must_be_list')
    grouped={}
    for index,article in enumerate(articles):
        payload_hash=fingerprint(article)
        if payload_hash not in grouped:grouped[payload_hash]={'raw':article,'indexes':[]}
        grouped[payload_hash]['indexes'].append(index)
    evidence=[];reason_counts=Counter()
    for payload_hash,group in grouped.items():
        article=group['raw'];indexes=group['indexes'];reasons=list(issues)
        key=article_key(article)
        if not isinstance(article,dict):
            article={};reasons.append('article_payload_not_object')
        if key is None:reasons.append('invalid_news_id')
        else:
            versions=global_versions.get(key,set())
            if payload_hash not in versions:reasons.append('payload_missing_from_global_version_index')
            if len(versions)>1:reasons.append('conflicting_global_news_id_versions')
        symbols=article.get('symbols')
        if not isinstance(symbols,list) or not all(isinstance(s,str) for s in symbols):
            reasons.append('invalid_symbol_tags')
        elif candidate.get('symbol') not in symbols:reasons.append('exact_symbol_tag_missing')
        created=updated=None
        for field in ('created_at','updated_at'):
            try:
                stamp=timestamp_ns(article.get(field))
                if field=='created_at':created=stamp
                else:updated=stamp
            except ValueError:
                reasons.append(('missing_' if article.get(field) is None else 'malformed_')+field)
        if created is not None and lower is not None and upper is not None:
            if created<=lower:reasons.append('created_not_strictly_after_previous_close')
            if created>=upper:reasons.append('created_not_strictly_before_open')
        if updated is not None:
            if created is not None and updated<created:reasons.append('updated_before_created')
            if upper is not None and updated>=upper:reasons.append('updated_not_strictly_before_open')
        if not isinstance(article.get('headline'),str) or not article['headline'].strip():
            reasons.append('missing_or_empty_headline')
        reasons=list(dict.fromkeys(reasons))
        row={'article_key':key,'source_payload_sha256':payload_hash,
            'input_row_indexes':indexes,'occurrences':len(indexes),
            'source_url':public_string(article.get('url')),'source':public_string(article.get('source'),200),
            'created_at':public_string(article.get('created_at'),80),'updated_at':public_string(article.get('updated_at'),80),
            'created_ns':created,'updated_ns':updated,
            'headline_sha256':hashlib.sha256(article['headline'].encode()).hexdigest() if isinstance(article.get('headline'),str) else None,
            'headline_field_status':'nonempty_string' if isinstance(article.get('headline'),str) and article['headline'].strip() else 'missing_empty_or_invalid',
            'metadata_status':'metadata_ready_for_review' if not reasons else 'quarantined_metadata',
            'reasons':reasons,'keyword_hints':hints(article),
            'hint_meaning':'triage only; no event category, positive surprise or catalyst verified',
            'independently_verified_catalyst':False}
        evidence.append(row)
        for reason in reasons:reason_counts[reason]+=len(indexes)
    ready=[r for r in evidence if r['metadata_status']=='metadata_ready_for_review']
    status=('retrieval_incomplete' if not fetch_complete else 'source_no_articles' if not articles
            else 'metadata_ready_for_review' if ready else 'no_metadata_ready')
    return {'candidate_id':candidate.get('candidate_id',str(candidate.get('date'))+'__'+str(candidate.get('symbol'))),
        'date':candidate.get('date'),'symbol':candidate.get('symbol'),'chronological_index':candidate.get('chronological_index'),
        'status':status,'news_metadata_status':status,'fetch_complete':bool(fetch_complete),
        'window':{'strict_after':candidate.get('news_request_start'),'strict_before':candidate.get('news_request_end')},
        'candidate_issues':issues,'source_evidence_status':'source_missing' if not articles else 'retrieved_metadata_only',
        'input_article_rows':len(articles),'unique_payload_rows':len(evidence),
        'exact_duplicate_rows_collapsed':len(articles)-len(evidence),
        'metadata_ready_unique_payloads':len(ready),'metadata_ready_input_rows':sum(r['occurrences'] for r in ready),
        'row_reason_counts':dict(reason_counts),'articles':evidence,
        'no_articles_is_not_no_event':True,'verified_catalyst_count':0,
        'retrieval_limit':'Current provider payload and version checks do not establish a complete historical publication/revision archive.'}


def read(path):return json.loads(path.read_text())
def filehash(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cached_window(candidate,record):
    """Validate complete manifests or retained partial page chains, never fetch anything."""
    identity=candidate['candidate_id']
    assert identity==candidate['date']+'__'+candidate['symbol'] and '/' not in identity
    folder=ROOT/'raw-news'/identity
    route='/v1beta1/news'
    params={'symbols':candidate['symbol'],'start':candidate['news_request_start'],
        'end':candidate['news_request_end'],'sort':'asc','limit':50,'include_content':'false','exclude_contentless':'false'}
    base=fingerprint({'route':route,'parameters':params});page_records=[]
    if record is not None:
        assert record['stage']=='news' and record['task_id']==identity and record['request_sha256']==base
        assert record['route']==route and record['parameters']==params
        assert read(folder/'complete.json')==record
        pages=[folder/p['name'] for p in record['pages']]
        assert [p.name for p in pages]==[f'page-{i:04d}.json' for i in range(len(pages))]
        for path,entry in zip(pages,record['pages']):assert filehash(path)==entry['sha256']
    else:
        pages=sorted(folder.glob('page-[0-9][0-9][0-9][0-9].json')) if folder.exists() else []
    rows=[];request=dict(params);last_token=None;seen=set()
    for index,path in enumerate(pages):
        assert path.name==f'page-{index:04d}.json'
        page=read(path)
        assert page['base_request_sha256']==base and page['page_number']==index
        assert page['page_request_sha256']==fingerprint({'route':route,'parameters':request})
        payload=page['response'];assert isinstance(payload,dict) and 'next_page_token' in payload
        assert payload.get('news') is None or isinstance(payload.get('news'),list)
        rows.extend(payload.get('news') or [])
        page_records.append({'page':path.name,'sha256':filehash(path),'rows':len(payload.get('news') or []),
                             'retrieved_at':page.get('retrieved_at')})
        last_token=payload['next_page_token']
        if last_token:
            assert isinstance(last_token,str) and last_token not in seen
            seen.add(last_token);request['page_token']=last_token
        else:assert index==len(pages)-1
    complete=bool(record and record.get('pages_complete') is True and pages and not last_token)
    if record:assert complete
    return rows,complete,{'candidate_id':identity,'request_sha256':base,'pages':page_records,'fetch_complete':complete}


def main():
    design=read(ROOT/'study-design.json');manifest=read(ROOT/'news-input-manifest.json')
    cohort=read(ROOT/'candidates.json');candidates=cohort['candidates']
    assert filehash(ROOT/'candidates.json')==design['input_sha256']['candidates.json']
    assert len(candidates)==cohort['candidate_count']==design['candidate_count']==1079
    assert len({c['candidate_id'] for c in candidates})==1079
    for name in ('candidates.json','bar-tasks.json','study-design.json'):
        assert manifest['input_sha256'][name]==filehash(ROOT/name)
    records={r['task_id']:r for r in manifest['records']};assert len(records)==len(manifest['records'])
    assert set(records)<={c['candidate_id'] for c in candidates}
    by_candidate={};retrieval={}
    for candidate in candidates:
        rows,complete,evidence=load_cached_window(candidate,records.get(candidate['candidate_id']))
        by_candidate[candidate['candidate_id']]=rows;retrieval[candidate['candidate_id']]=evidence
    global_versions=build_global_versions(by_candidate)
    results=[];private=[]
    for candidate in candidates:
        identity=candidate['candidate_id'];articles=by_candidate[identity]
        result=analyze_candidate(candidate,articles,global_versions,retrieval[identity]['fetch_complete'])
        result['source_page_evidence']=retrieval[identity]
        results.append(result)
        raw_by_hash={fingerprint(a):a for a in articles}
        private.append({'candidate_id':identity,'date':candidate['date'],'symbol':candidate['symbol'],
            'status':result['status'],'articles':[{'article_key':r['article_key'],
                'source_payload_sha256':r['source_payload_sha256'],'metadata_status':r['metadata_status'],
                'reasons':r['reasons'],'keyword_hints':r['keyword_hints'],
                'review_material':{k:raw_by_hash[r['source_payload_sha256']].get(k) for k in
                    ('headline','summary','url','source','created_at','updated_at','symbols')}
                    if isinstance(raw_by_hash[r['source_payload_sha256']],dict) else None} for r in result['articles']]})
    bindings={name:filehash(ROOT/name) for name in ('study-design.json','candidates.json','news-input-manifest.json','news_analysis.py')}
    payload={'study_id':design['id'],'created_at':datetime.now(timezone.utc).isoformat(),'input_sha256':bindings,
        'candidate_count':len(results),'candidates':results,
        'retrieval_failures':manifest.get('failures',[]),
        'global_version_index':{k:sorted(v) for k,v in sorted(global_versions.items())},
        'identity_policy':'SHA256 of string form of scalar nonempty news ID; integer/string numeric IDs share identity. Full payload fingerprints preserve every field and quarantine any difference.',
        'keyword_patterns':KEYWORD_PATTERNS,'keyword_patterns_sha256':fingerprint(KEYWORD_PATTERNS),
        'keyword_policy':'Frozen source regexes are review hints only, never event proof or retrieval selection.',
        'verified_catalysts':0,'broker_orders_sent':0,'strategy_returns_computed':False}
    reason_counts=Counter()
    for result in results:reason_counts.update(result['row_reason_counts'])
    summary={'study_id':design['id'],'created_at':payload['created_at'],'input_sha256':bindings,
        'candidates_preserved':len(results),'candidate_status_counts':dict(Counter(r['status'] for r in results)),
        'input_article_rows':sum(r['input_article_rows'] for r in results),
        'unique_payload_rows_within_windows':sum(r['unique_payload_rows'] for r in results),
        'exact_duplicate_rows_collapsed':sum(r['exact_duplicate_rows_collapsed'] for r in results),
        'metadata_ready_unique_payloads_within_windows':sum(r['metadata_ready_unique_payloads'] for r in results),
        'global_unique_news_ids':len(global_versions),'global_conflicting_news_ids':sum(len(v)>1 for v in global_versions.values()),
        'quarantine_reasons_counted_per_input_row':dict(reason_counts),
        'verified_catalysts':0,'keyword_patterns_sha256':fingerprint(KEYWORD_PATTERNS),
        'limitations':['Metadata ready for review is not a verified earnings/guidance/contract/regulatory catalyst.',
            'No supplied news hit is source_missing, not proof no event occurred.',
            'Retrieval follows the provider endpoint behavior; later updates may make historical created-time coverage incomplete.',
            'Cross-window payload conflicts quarantine all observed versions; unseen historical revisions remain unknown.',
            'Rows across windows are correlated and may refer to the same story.','No news-based winner selection, strategy returns or wealth are computed.'],
        'broker_orders_sent':0}
    (ROOT/'news-enriched.json').write_text(json.dumps(payload,separators=(',',':'))+'\n')
    summary['news_enriched_sha256']=filehash(ROOT/'news-enriched.json')
    (ROOT/'news-summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    (ROOT/'review-material-private.json').write_text(json.dumps({'publication':'PRIVATE raw headlines/summaries; exclude from public package',
        'input_sha256':bindings,'candidates':private},separators=(',',':'))+'\n')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
