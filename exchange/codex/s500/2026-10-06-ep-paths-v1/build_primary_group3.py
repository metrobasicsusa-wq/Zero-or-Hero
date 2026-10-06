"""Manually reviewed first-party evidence; no market outcomes accessed."""
import json,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
RAW=ROOT/'raw-source-group-3'
base=json.loads((ROOT/'group-3-private.json').read_text())['cases']
D={
'WYFI':dict(gate=None,status='missing',category=None,time=None,available=None,window=None,story=None,rationale='Only a multi-stock premarket mover mention was available in the input. Bounded searches and issuer investor-news landing page did not yield an identifiable in-window primary announcement. The current issuer page is dynamically populated and provided no historical release content; this is missing evidence, not proof of no catalyst.'),
'AMBQ':dict(gate=True,status='confirmed',category='earnings',time='2026-05-12T11:30:00Z',available='2026-05-12T11:30:00Z',window=True,story=True,rationale='Issuer-issued original BusinessWire release reports first-quarter results and second-quarter guidance. Its published and modified time is 11:30 UTC. Issuer Q4 JSON-LD instead labels 06:30 UTC, a five-hour conflict; both timestamps are strictly within the window and the later time is used. This does not establish universal Q4 timezone semantics.'),
'WEN':dict(gate=None,status='missing',category='unconfirmed_take_private_report',time=None,available=None,window=None,story=False,rationale='Supplied stories describe press reports that an investor was seeking financing for a potential take-private bid. No executed agreement or issuer confirmation in the required window was found in the bounded source attempts. A rumor does not satisfy the registered contract category; absence of another qualifying story is not established.'),
'VELO':dict(gate=True,status='confirmed',category='earnings',time='2026-05-12T20:05:00Z',available='2026-05-12T20:05:31Z',window=True,story=True,rationale='Issuer IR and issuer-issued PR Newswire release report first-quarter financial results and reaffirm annual revenue guidance. The original wire declares publication at 16:05:00 EDT and modification at 16:05:31 EDT; the later instant is used and both are after the prior regular close.'),
'IMVT':dict(gate=True,status='confirmed',category='earnings',time='2026-05-20T11:00:00Z',available='2026-05-20T11:00:00Z',window=True,story=True,rationale='Issuer IR displays May 20 at 07:00 EDT and includes fourth-quarter and fiscal-year financial results. The same release includes clinical results, but the earnings content independently satisfies the frozen category. No drug approval is asserted.'),
'INFQ':dict(gate=None,status='ambiguous',category='proposed_government_funding_letter_of_intent',time='2026-05-21T10:49:00Z',available='2026-05-21T10:49:00Z',window=True,story=None,rationale='Issuer confirms a signed LOI at 06:49 EDT, but explicitly states proposed funding remains contingent on diligence, definitive award documents and government internal approvals. The timing is established; whether this preliminary LOI meets the frozen contract category is unresolved. It is not recorded as a definitive grant or funded award.'),
'RGTI':dict(gate=None,status='ambiguous',category='proposed_government_funding_letter_of_intent',time='2026-05-21T11:15:49Z',available='2026-05-21T11:15:49Z',window=True,story=None,rationale='Issuer confirms a signed LOI for potential government funding, with datePublished 07:15:49 EDT. Its release describes definitive transaction agreements and related transaction conditions as prospective. This preliminary LOI is not silently promoted to a definitive funded contract; category remains unresolved.'),
'MNTS26':dict(gate=None,status='ambiguous',category='employee_inducement_equity_awards',time='2026-05-22T21:00:27Z',available='2026-05-22T21:00:27Z',window=True,story=False,rationale='The issuer archive identifies an in-window release granting equity inducement awards to six employees. This is compensation governance, not earnings, guidance, a new business contract, or a completed regulatory communication under the frozen categories. The supplied mover story supplies no identified alternative catalyst, whose absence is unproven.'),
'MNTS27':dict(gate=True,status='confirmed',category='contract',time='2026-05-27T12:01:01Z',available='2026-05-27T12:01:01Z',window=True,story=True,rationale='Issuer says it entered into securities purchase agreements for a private placement and declares publication at 08:01:01 EDT. An executed agreement satisfies the frozen contract category without imposing a new revenue requirement. This is financing with potential dilution, not a customer order, realized proceeds or a claim of favorable news; closing remained conditional.'),
'SNOW':dict(gate=True,status='confirmed',category='earnings',time='2026-05-27T20:05:00Z',available='2026-05-27T20:05:00Z',window=True,story=True,rationale='Issuer-issued original BusinessWire release reports first-quarter fiscal 2027 financial results. Published and modified JSON-LD timestamps both equal 20:05 UTC, strictly after the prior close and before the candidate open. Other same-day stories are not needed to classify this earnings announcement.'),
'UMAC':dict(gate=None,status='missing',category='reported_funding_talks',time=None,available=None,window=None,story=False,rationale='The input identifies a press report about government funding talks. Bounded search and issuer press-release landing page did not establish an in-window signed agreement or completed government announcement. Ongoing talks alone are outside the frozen categories; the dynamically populated issuer page is not a complete historical archive.'),
'OKTA':dict(gate=None,status='ambiguous',category='earnings',time=None,available='2026-05-28T20:01:00Z',window=None,story=True,rationale='The earnings announcement is genuine, but issuer IR JSON-LD labels publication and modification 15:01 UTC on May 28, before the prior regular close, while issuer-issued original BusinessWire labels both 20:01 UTC. A five-hour CMS timezone error is plausible but unproven. Since interpretations cross the strict window boundary, the candidate remains unknown; original wire availability at 20:01 alone does not prove first publication was after the close.'),
'REPL':dict(gate=True,status='confirmed',category='regulatory announcement',time='2026-05-29T12:00:48Z',available='2026-05-29T12:00:48Z',window=True,story=True,rationale='Issuer publication reports completed FDA communications and agreement on a path for BLA resubmission and reconsideration, with datePublished 08:00:48 EDT. This is a completed regulatory communication, not approval of RP1 and not proof that a resubmission had already occurred.'),
'SPCE':dict(gate=None,status='ambiguous',category='operational_flight_test_update_outside_window',time='2026-05-27T20:15:00Z',available='2026-05-27T20:15:00Z',window=False,story=False,rationale='The identified issuer-issued original wire concerns a glide-flight operational update, dated May 27 at 20:15 UTC, before the candidate window beginning May 29 at 20:00 UTC. Weekend retrospective/mover mentions cannot refresh that original event into a new in-window catalyst. Bounded search does not exclude another undiscovered qualifying announcement.'),
'ABAT':dict(gate=True,status='confirmed',category='regulatory announcement',time='2026-06-08T09:51:04Z',available='2026-06-08T09:51:04Z',window=True,story=True,rationale='Issuer-issued original GlobeNewswire release declares publication and modification at 09:51:04 UTC and reports that DOE reinstated a previously terminated grant following appeal. This is a completed government decision. The $115 million describes total project cost; it must not be mislabeled as the grant amount.'),
'QURE':dict(gate=True,status='confirmed',category='regulatory announcement',time='2026-06-17T11:05:23Z',available='2026-06-17T11:05:23Z',window=True,story=True,rationale='Issuer IR declares 07:05:23 EDT and reports a completed Type B FDA meeting in which the agency communicated that three-year trial data could form the primary basis of an accelerated-approval BLA. Planned submission and possible accelerated approval remain prospective; this is regulatory communication, not marketing approval.'),
'VOD':dict(gate=None,status='ambiguous',category='contract',time=None,available=None,window=None,story=True,rationale='e& confirms signing a binding agreement to sell its Vodafone stake, and Vodafone acknowledges the announcement. Retrieved official pages and the official announcement PDF provide July 10, 2026 but no sufficiently precise publication time. Third-party article times cannot establish first-party preopen availability; exact-window evidence remains unresolved.')}
primary_times={
'AMBQ-ir':('2026-05-12T06:30:00Z','2026-05-12T06:30:00Z','Q4 JSON-LD, conflicts with original wire; both in window'),
'AMBQ-wire':('2026-05-12T11:30:00Z','2026-05-12T11:30:00Z','original issuer-issued BusinessWire JSON-LD'),
'VELO-ir':('2026-05-12T20:05:00Z',None,'visible 4:05 pm EDT and datetime'),
'VELO-wire':('2026-05-12T20:05:00Z','2026-05-12T20:05:31Z','original issuer-issued PR Newswire JSON-LD'),
'IMVT-ir':('2026-05-20T11:00:00Z',None,'visible May 20, 2026 7:00 am EDT'),
'INFQ-ir':('2026-05-21T10:49:00Z',None,'visible May 21, 2026 6:49 am EDT'),
'RGTI-ir':('2026-05-21T11:15:49Z',None,'issuer JSON-LD timezone -0400'),
'MNTS26-awards':('2026-05-22T21:00:27Z',None,'issuer JSON-LD timezone -0400'),
'MNTS27-placement':('2026-05-27T12:01:01Z',None,'issuer JSON-LD timezone -0400'),
'SNOW-wire':('2026-05-27T20:05:00Z','2026-05-27T20:05:00Z','original issuer-issued BusinessWire JSON-LD'),
'OKTA-ir':('2026-05-28T15:01:00Z','2026-05-28T15:01:00Z','Q4 JSON-LD conflicts across prior-close boundary'),
'OKTA-wire':('2026-05-28T20:01:00Z','2026-05-28T20:01:00Z','original issuer-issued BusinessWire JSON-LD'),
'REPL-announcement':('2026-05-29T12:00:48Z',None,'issuer JSON-LD timezone -0400'),
'SPCE-wire':('2026-05-27T20:15:00Z','2026-05-27T20:15:00Z','original issuer-issued BusinessWire JSON-LD'),
'ABAT-wire':('2026-06-08T09:51:04Z','2026-06-08T09:51:04Z','original issuer-issued GlobeNewswire JSON-LD and time element'),
'QURE-ir':('2026-06-17T11:05:23Z',None,'issuer JSON-LD timezone -0400'),
'VOD-ir':(None,None,'visible date July 10, 2026 only'),
'VOD-response':(None,None,'visible date July 10, 2026 only'),
'VOD-pdf':(None,None,'official announcement document date only')}
reviews=[]
for row in base:
 c=row['candidate'];sym=c['symbol'];key=sym+('26' if c['date']=='2026-05-26' else '27') if sym=='MNTS' else sym
 d=D[key];sources=[]
 for p in sorted(RAW.glob(key+'-*-meta.json')):
  m=json.loads(p.read_text());sk=m['source_key'];t=primary_times.get(sk)
  s={k:m[k] for k in ['source_key','url','retrieved_at','status','final_url','sha256','bytes'] if k in m}
  s['role']='primary_announcement' if t else 'discovery_or_failed_attempt'
  if t:s.update(publication_time_utc=t[0],declared_revision_time_utc=t[1],time_evidence=t[2])
  sources.append(s)
 assert len(sources)<=8,(key,len(sources))
 rev={'candidate_id':c['candidate_id'],'date':c['date'],'symbol':sym,'news_status':d['status'],'pass_registered_news_gate':d['gate'],'reviewed_story_pass_registered_category':d['story'],'event_category':d['category'],'rationale':d['rationale'],'announcement_time_utc':d['time'],'conservative_available_by_utc':d['available'],'in_window_confirmed':d['window'],'required_window':{'strict_after':c['news_request_start'],'strict_before':c['news_request_end']},'source_request_count':len(sources),'sources':sources,'historical_revision_limit':'Current retrieved primary page and declared timestamps, not a contemporaneously archived historical version. Absence of a declared modification does not prove absence of silent revisions.','review_method':'Manual source and timestamp assessment before viewing exit/PnL outcomes.'}
 if d['gate'] is True:
  assert d['window'] is True and d['time'] and d['available']
  dt=lambda x:datetime.fromisoformat(x.replace('Z','+00:00'))
  assert dt(c['news_request_start'])<dt(d['time'])<=dt(d['available'])<dt(c['news_request_end'])
 reviews.append(rev)
out={'stage':'s500_EP_full_primary_group_3','created_at':datetime.now(timezone.utc).isoformat(),'study_design_sha256':hashlib.sha256((ROOT/'study-design.json').read_bytes()).hexdigest(),'private_assignment_sha256':hashlib.sha256((ROOT/'group-3-private.json').read_bytes()).hexdigest(),'cases':len(reviews),'counts':{'pass':sum(r['pass_registered_news_gate'] is True for r in reviews),'fail':sum(r['pass_registered_news_gate'] is False for r in reviews),'unknown':sum(r['pass_registered_news_gate'] is None for r in reviews)},'reviews':reviews,'scope':'All 17 assigned cases attempted; no exit/PnL outcomes consulted; no market API, credentials, order, or repository writes. Raw source pages remain private.'}
(ROOT/'primary-group-3.json').write_text(json.dumps(out,indent=2,ensure_ascii=False)+'\n')
(ROOT/'primary-group-3-progress.json').write_text(json.dumps({'completed':17,'remaining':0,'counts':out['counts'],'updated_at':out['created_at']},indent=2)+'\n')
lines=['# EP first-party review — group 3','',f"All 17 cases attempted. Pass {out['counts']['pass']}; unknown {out['counts']['unknown']}; definitive candidate failures {out['counts']['fail']}.",'','Categories and request windows follow the frozen protocol. An identified outside-category story does not establish absence of other news, so that candidate remains unknown. Current-source timestamp evidence is not a historical archive. No exit or profit/loss outcomes were inspected.','', '| Candidate | Gate | Event | Time UTC |','|---|---|---|---|']
for r in reviews:lines.append(f"| {r['candidate_id']} | {'pass' if r['pass_registered_news_gate'] is True else 'unknown'} | {r['event_category'] or 'not established'} | {r['announcement_time_utc'] or 'unresolved'} |")
for r in reviews:
 lines+=['',f"## {r['candidate_id']}",'',r['rationale'],'']
 for s in r['sources']:
  if s['role']=='primary_announcement':lines.append(f"- [{s['source_key']}]({s['url']}); SHA-256 `{s.get('sha256','unavailable')}`. {s['time_evidence']}.")
(ROOT/'primary-group-3.md').write_text('\n'.join(lines)+'\n')
print(json.dumps({'cases':len(reviews),'counts':out['counts'],'requests':sum(r['source_request_count'] for r in reviews)},indent=2))
