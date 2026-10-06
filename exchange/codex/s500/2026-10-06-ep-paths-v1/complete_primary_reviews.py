"""Complete all frozen primary-source dispositions before seeing any exit results.
Manual event assessments are explicit below; cached source timestamps are not
contemporaneous historical archives or proof of an exhaustive catalyst search.
"""
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter
import hashlib
import json
import re

ROOT=Path(__file__).resolve().parent
PRIOR=ROOT.parent/'s500-news-20261006'


def read(path): return json.loads(Path(path).read_text())
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def dt(s): return datetime.fromisoformat(s.replace('Z','+00:00'))
def utc(s): return dt(s).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def write(name,value): (ROOT/name).write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')


def metas(group,symbol):
    folder=ROOT/f'raw-source-group-{group}'
    found=list(folder.glob(symbol+'__*-meta.json')) if group==4 else list((folder/symbol).glob('*-meta.json'))+list((folder/'sub-six'/symbol).glob('*-meta.json'))
    found+=list((ROOT/'raw-primary-root'/f'group-{group}'/symbol).glob('*-meta.json'))
    return sorted(found)


def source(path,role='discovery_corroboration_or_failed_attempt'):
    m=read(path); raw=path.with_name(path.name.replace('-meta.json','.raw'))
    error=path.with_name(path.name.replace('-meta.json','.error'))
    body=raw if raw.exists() else error if error.exists() else None
    observed_hash=sha(body) if body else None
    verified=body is not None and observed_hash==m.get('sha256')
    s=raw.read_text(errors='replace') if raw.exists() and verified else ''
    pubs=list(dict.fromkeys(re.findall(r'"datePublished"\s*:\s*"([^"]+)"',s)))
    mods=list(dict.fromkeys(re.findall(r'"dateModified"\s*:\s*"([^"]+)"',s)))
    return {'url':m.get('url',m.get('requested_url')),'final_url':m.get('final_url'),
        'status':m.get('status'),'retrieved_at':m.get('retrieved_at'),
        'bytes':m.get('bytes'),'sha256':m.get('sha256'),'body_hash_verified':verified,
        'observed_body_sha256':observed_hash,'source_integrity_status':'verified' if verified else 'quarantined_no_proof',
        'role':role,'declared_publication_values':pubs,'declared_modification_values':mods}


# Positive entries identify the exact first-party proof file, category and
# independently assessed facts. A visible timestamp override is used only where
# explicitly printed with timezone on the source page, never inferred from price.
POSITIVES={
 (1,'ICHR'):('raw-primary-root/group-1/ICHR/primary-meta.json','earnings',
    'Issuer-issued original BusinessWire announces fourth-quarter and full-year financial results; publication and declared revision both precede the candidate open.',None),
 (1,'RNG'):('raw-primary-root/group-1/RNG/wire-meta.json','earnings/guidance',
    'Issuer-issued original BusinessWire announces fourth-quarter results and next-year guidance. Dividend plans are not needed to qualify the earnings announcement.',None),
 (1,'TNDM'):('raw-source-group-1/TNDM/wire-release-meta.json','earnings/guidance',
    'Issuer-issued BusinessWire announces quarterly/full-year results and annual financial guidance, corroborated by issuer IR.',None),
 (1,'ZD'):('raw-primary-root/group-1/ZD/primary-meta.json','contract',
    'Issuer announces a definitive agreement to sell its Connectivity division to Accenture. It is an executed transaction agreement, not a completed sale or realized proceeds. Current CMS time is in the required window; historical timezone/revision fidelity remains unproven.',None),
 (1,'PDYN'):('raw-primary-root/group-1/PDYN/wire-meta.json','earnings/guidance',
    'Issuer-issued BusinessWire reports fourth-quarter/full-year results and reiterates guidance. Aggregator EPS corrections are not used as evidence of a positive earnings surprise.',None),
 (1,'MRVL'):('raw-primary-root/group-1/MRVL/wire-meta.json','earnings/guidance',
    'Issuer-issued BusinessWire financial results supply an explicit timezone, unlike the issuer-page timezone-free time element.',None),
 (2,'AEHR'):('raw-source-group-2/AEHR/discovery-meta.json','earnings/guidance',
    'Company page announces fiscal third-quarter results and bookings outlook; a SEC exhibit corroborates content. Declared publication is within the overnight window; no declared revision was supplied, so historical immutability is not certified.',None),
 (2,'CRML'):('raw-source-group-2/CRML/release-ir-meta.json','regulatory announcement',
    'Company announces completed Greenland government approval of an ownership-interest transfer. The two timezone-aware publication/modification fields are in-window; no independent government decision document was obtained, so this verifies the company announcement only.',None),
 (2,'MXL'):('raw-primary-root/group-2/MXL/wire-meta.json','earnings/guidance',
    'Company-issued original BusinessWire reports first-quarter results and second-quarter outlook, corroborated by official IR and SEC exhibit.',None),
 (2,'AAON'):('raw-source-group-2/AAON/release-ir-meta.json','earnings/guidance',
    'Issuer first-quarter results and raised outlook are corroborated in the SEC exhibit. Both publication and later declared modification are strictly before the open.',None),
 (4,'LIFE'):('raw-source-group-4/LIFE__06-meta.json','earnings/guidance',
    'Ethos issuer IR announces quarterly results and financial outlook, explicitly identifying Nasdaq LIFE. This is not Life360/LIF. Historical ticker-to-issuer mapping remains a broader source limitation.',None),
 (4,'QNST'):('raw-source-group-4/QNST__06-meta.json','earnings',
    'Company-issued BusinessWire reports fiscal-quarter and full-year results, corroborated by issuer IR.',None),
 (4,'CAPR'):('raw-source-group-4/CAPR__02-meta.json','earnings',
    'Issuer page explicitly displays August 13, 2026 4:05 pm EDT and announces second-quarter financial results. Older FDA committee news in the release is not relabeled as a fresh approval.', '2026-08-13T16:05:00-04:00'),
 (4,'HTFL'):('raw-source-group-4/HTFL__05-meta.json','earnings/guidance',
    'Issuer IR announces financial results and raises guidance. Primary revenue is 64.1 million; 53.19 million is gross profit, which an aggregator headline mislabeled as sales. That headline is not accepted as financial fact.',None),
}

UNKNOWNS={
 (1,'INMD'):('missing','No identifiable in-window company announcement was obtained from the bounded company/IR searches. A mover mention is not catalyst proof.'),
 (1,'MSGS'):('governance_or_proposed_transaction','Issuer page describes board approval to explore a possible spin-off, not a definitive transaction or completed regulatory communication; its declared publication is also after the candidate open. Other unknown news is not ruled out.'),
 (1,'SRPT'):('clinical_data','Available clue concerns initial clinical results. Bounded company archive attempts did not establish a separate in-window registered catalyst; future study plans do not equal regulatory action.'),
 (2,'STAA'):('earnings_timing_unknown','SEC exhibit confirms preliminary sales announcement dated April 8, but exact original first-publication time was not established. Filing acceptance cannot re-time a possibly earlier company release.'),
 (2,'OGN'):('takeover_report','The identified clue is a press report about a possible acquisition. No executed issuer agreement within the window was obtained; absence of another event is unproven.'),
 (2,'ALLO'):('clinical_data','Issuer announces interim trial data. This is outside the frozen categories; no separate registered catalyst has been established.'),
 (2,'IDYA'):('clinical_data','Issuer announces trial topline data and prospective filing plans. It does not establish a newly completed regulatory decision or communication.'),
 (2,'NKTR'):('clinical_data','SEC exhibit announces clinical study extension results and future plans. No separate registered in-window catalyst established.'),
 (2,'MANE'):('clinical_data','SEC exhibit announces clinical trial results. Potential future FDA approval is not current approval or completed agency communication.'),
 (2,'EVER'):('earnings_timing_conflict','Issuer CMS labels publication/modification at May4 15:05Z, before the prior close. Later SEC dissemination does not resolve original-publication timing; a plausible CMS timezone error is not silently corrected.'),
 (2,'OSS'):('earnings_timing_unknown','SEC records support quarterly reporting, but original public announcement time was not established by the bounded source attempts.'),
 (2,'HIMX'):('earnings_timing_unknown','SEC earnings and dividend exhibits are present but exact first-publication time is not established. The dividend release alone is not substituted for the registered earnings event.'),
 (2,'FLNC'):('old_or_missing_announcement','Discovery points to May6 earnings, earlier than the May7-close to May8-open candidate window. No independently timed fresh qualifying announcement obtained; do not re-time old results.'),
 (2,'INOD'):('earnings_timing_conflict','Issuer CMS labels May7 15:05Z before the prior close; issuer-linked ACCESS page shows 4:05PM without a confirmed timezone. Later filing/aggregator timestamps cannot resolve that conflict.'),
 (2,'DXYZ'):('instrument_and_event_unknown','Issuer describes a non-diversified closed-end investment company. The original ETF/test filter did not resolve this fund classification. No in-window primary catalyst established; retain rather than retroactively delete the case.'),
 (2,'MRAM'):('missing','Mover mentions and bounded current issuer/SEC searches did not establish an exactly timed qualifying announcement.'),
 (4,'PYPL'):('takeover_report','Third-party sources describe a proposed bid; bounded issuer searches did not supply a definitive executed agreement or issuer-confirmed registered event.'),
 (4,'AMLX'):('clinical_data','Issuer reports Phase3 topline trial results. This is a clinical announcement, not an approval; existence of another qualifying event remains unknown.'),
 (4,'BNTX'):('missing','No exactly timed qualifying first-party release obtained from issuer archives and searches. A zero supplier news hit does not prove no event.'),
 (4,'MRNA'):('clinical_data_unverified_timing','Supplied clue concerns cancer-therapy clinical trial results. Bounded issuer/partner archive attempts did not establish a separate registered in-window event.'),
 (4,'RZLV'):('old_or_missing_announcement','An issuer post obtained from the CMS is dated August18, before this August25 window. It cannot be recycled as a new catalyst; no timely alternative established.'),
 (4,'ANF'):('earnings_source_missing','Quarterly earnings clue exists but issuer release API attempts returned400/403 and did not supply the exact announcement record. No invented release time.'),
 (4,'CRM'):('earnings_version_and_timing_unknown','Issuer page has results content but no exact publication time; two captures of the same URL have different HTML titles. A later favorable capture is not selected to certify historical timing.'),
 (4,'VKTX'):('clinical_data','Issuer announces trial-maintenance topline data, outside registered categories. Another qualifying event is not ruled out.'),
 (4,'KOD'):('clinical_data','Issuer reports pivotal trial results. Neither future regulatory plans nor clinical success establishes current approval.'),
 (4,'ACN'):('earnings_timing_unknown','Issuer financial results page is dated October1 but does not establish exact preopen publication. Aggregator timestamps cannot supply that missing primary evidence.'),
 (4,'PAGS'):('missing','Only mover clues/current issuer archive material were obtained; no in-window primary registered catalyst established.'),
 (4,'STNE'):('missing','Only mover clues/current issuer archive material were obtained; no in-window primary registered catalyst established.'),
 (4,'XP'):('missing','Only mover clues/current issuer archive material were obtained; no in-window primary registered catalyst established.'),
}


def main():
    assignment=read(ROOT/'primary-review-assignment.json')
    carry=read(ROOT/'primary-group-1.json')
    checkpoint=ROOT/'primary-group-1-partial-checkpoint.json'
    if not checkpoint.exists():checkpoint.write_bytes((ROOT/'primary-group-1.json').read_bytes())
    carried={r['candidate_id']:r for r in read(checkpoint)['reviews']}
    allrows=[]
    for group in (1,2,3,4):
        expected=assignment['groups'][f'group-{group}']
        material=read(ROOT/f'group-{group}-private.json')['cases']
        candidates={x['candidate']['candidate_id']:x['candidate'] for x in material}
        if group==3:
            output=read(ROOT/'primary-group-3.json')
            assert {r['candidate_id'] for r in output['reviews']}==set(expected)
            allrows.extend(output['reviews']);continue
        rows=[]
        for key in expected:
            c=candidates[key];symbol=c['symbol'];case=(group,symbol)
            if key in carried:
                row=carried[key]
            else:
                paths=metas(group,symbol)
                assert paths,(group,symbol)
                src=[source(p) for p in paths]
                proof=None; publication=available=None; gate=None; inwindow=False
                if case in POSITIVES:
                    rel,category,reason,override=POSITIVES[case]
                    proof=source(ROOT/rel,'primary_announcement')
                    assert proof['body_hash_verified'] and proof['status']==200,(key,'primary_source_integrity_failed')
                    publication=utc(override or proof['declared_publication_values'][0])
                    times=[publication]+[utc(x) for x in proof['declared_modification_values']]
                    available=max(times,key=dt)
                    inwindow=all(dt(c['news_request_start'])<dt(t)<dt(c['news_request_end']) for t in times)
                    assert inwindow,(key,times)
                    gate=True
                    src=[proof]+[s for s in src if s['sha256']!=proof['sha256']]
                else:
                    category,reason=UNKNOWNS[case]
                row={'candidate_id':key,'date':c['date'],'symbol':symbol,
                    'news_status':'confirmed_with_historical_version_limit' if gate else 'unresolved_after_bounded_primary_review',
                    'pass_registered_news_gate':gate,'event_category':category,'rationale':reason,
                    'announcement_time_utc':publication,'conservative_available_by_utc':available,
                    'in_window_confirmed':inwindow,'required_window':{'strict_after':c['news_request_start'],'strict_before':c['news_request_end']},
                    'source_request_count':len(paths),'sources':src,
                    'historical_revision_limit':'Current primary pages/declared times are not contemporaneous archived versions. Missing dateModified does not prove no revisions.',
                    'all_possible_registered_catalysts_excluded':False,
                    'review_completed_before_exit_results':True}
            rows.append(row)
        write(f'primary-group-{group}.json',{'group':group,'assigned_count':17,'reviewed_count':len(rows),
            'state':'all_assigned_cases_reviewed_bounded_sources_no_forced_pass','reviews':rows,
            'generated_at':datetime.now(timezone.utc).isoformat(),'study_design_sha256':sha(ROOT/'study-design.json'),
            'completion_note':'Saved parallel-source work was continued by the primary agent after subagent usage interruption; no exit outcomes were viewed to classify cases.'})
        lines=['# Primary group '+str(group),'','All17 assigned cases retained; unknown means unresolved evidence, not absence of an event.','',
               '| Candidate | Gate | Event assessment |','|---|---|---|']
        for r in rows:lines.append('| '+r['candidate_id']+' | '+str(r['pass_registered_news_gate'])+' | '+r['rationale'].replace('|','/')+' |')
        (ROOT/f'primary-group-{group}.md').write_text('\n'.join(lines)+'\n')
        allrows.extend(rows)
    assert len(allrows)==len({r['candidate_id'] for r in allrows})==68
    for r in allrows:
        r['all_possible_registered_catalysts_excluded']=False
        r['strict_selection_status']='confirmed_catalyst_subject_to_source_limits' if r['pass_registered_news_gate'] is True else 'unknown_possible_registered_catalyst'
    write('all-new-primary-reviews.json',{'generated_at':datetime.now(timezone.utc).isoformat(),'cases':68,'reviews':sorted(allrows,key=lambda r:r['candidate_id']),
        'counts':dict(Counter('pass' if r['pass_registered_news_gate'] is True else 'reviewed_story_failed_candidate_unknown' if r['pass_registered_news_gate'] is False else 'unknown' for r in allrows)),
        'source_files_sha256':{f'primary-group-{i}.json':sha(ROOT/f'primary-group-{i}.json') for i in range(1,5)},
        'all_frozen_cases_retained':True,'no_exit_results_used':True})
    write('primary-classification-lock.json',{'locked_at':datetime.now(timezone.utc).isoformat(),
        'all68_dispositions_sha256':sha(ROOT/'all-new-primary-reviews.json'),
        'classification_code_sha256':sha(ROOT/'complete_primary_reviews.py'),'before_first_real_exit_simulation':True,
        'no_changes_based_on_returns_permitted':True})
    print(json.dumps(read(ROOT/'all-new-primary-reviews.json')['counts']))


if __name__=='__main__':main()
