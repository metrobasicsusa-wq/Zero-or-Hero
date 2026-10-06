"""Freeze the early-entry protocol before evaluating the new gates or returns."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT = Path(__file__).resolve().parent
PARENTS = {'news': ROOT.parent/'s500-news-20261006',
           'paths': ROOT.parent/'s500-ep-paths-20261006',
           'event_v2': ROOT.parent/'s500-ep-event-v2-20261006'}
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(p, obj): p.write_text(json.dumps(obj, indent=2, ensure_ascii=False)+'\n')

def main():
    assert not (ROOT/'study-design.json').exists()
    inputs = {group+'/'+name:sha(base/name) for group,base in PARENTS.items() for name in {
        'news':['candidates.json','minute-market.json','bars-input-manifest.json','market_gates.py','market-gates.json','candidate-readiness.json','download_inputs.py'],
        'paths':['holding-input-design.json','holding-input-manifest.json','holding-market.json','portfolio-coverage.json','all-new-primary-reviews.json'],
        'event_v2':['ep_engine.py','ep_event_engine.py','gap-evidence.json','verified-symbol-sessions.json','source-verification.json','next-stage-proposal.json','isolated-event-results.json','portfolio-event-results.json']
    }[group]}
    now=datetime.now(timezone.utc).isoformat()
    design={
      'id':'s500_technical_gap_early_entry_v1','registered_at':now,
      'status':'frozen_before_new_early_gates_and_returns',
      'period':['2026-01-02','2026-10-05'],
      'parent_commit':'4919eeb5366f3a60a132e9ef3450fabc92ce7cb0',
      'scope':'All1079 original candidate rows, all families; original retrospective/current-directory daily-opening preselection is not a complete point-in-time market universe.',
      'hypothesis':'Compare packages of earlier opening-range breakout and proportional opening-volume threshold; this changes timing AND qualification, not a timing-only treatment.',
      'families':[{'id':'OR'+str(n),'opening_minutes':n,'volume_threshold_numerator':n,'volume_threshold_denominator':30} for n in (5,15,30)],
      'volume_assumption':'At opening-range completion use accumulated V_N/prior20 adjusted vendor FULL-DAY mean >= N/30. This linear-duration heuristic is NOT historical same-time RVOL; opening volume is nonlinear. No future30minute or full-day input.',
      'gate_rules':{'prior_close_minimum':'2','prior20_median_dollar_volume_minimum':'10000000','regular_open_gap_minimum':'0.10',
        'opening_range':'Validate all offsets0..N-1 with original exact-timestamp/OHLCV rules; high/low/volume from those only.',
        'signal':'First valid completed minute m in N..89 with close strictly above range high; earlier missing/malformed signal input is unknown, not silently skipped.',
        'clock':'Source bar m completes m+1, then one full minute latency; intended entry is exact m+2 minute open. First possible OR5 entry09:37 ET. Source bars are revised historical aggregates, not proven realtime receipt.',
        'entry_gap_stop':'Planned entry at/below already known opening-range low stays unresolved, no favorable retrospective cancellation.'},
      'variant_grid':{'exit_mode':['fixed10'],'fraction':[0.5,1.0],'cost_bps_each_side':[25,100]},
      'modes':['evidence_gated','provider_event_series_assumption'],
      'money_and_exit_rules':{'initial_cash':'500','target':'10000','shares':'Whole shares; quantity fixed using signal close plus cost and fraction of settled cash. At execution skip if original quantity unaffordable; no resizing or alternate symbol.',
        'stop':'Opening-range low; same inherited gap-open/intrabar OHLC cost-stress rule. Not executable bid/ask fill evidence.',
        'time_exit':'Tenth trading session including entry, last regular-minute OPEN; exact execution reference required; no deferral.',
        'portfolio':'One position, T+1 sales receivables, no entry on holding/exit day; earliest signal completion then descending opening V_N/prior20mean then symbol.',
        'corporate_actions':'Inherit unsupported-held-event unknown guard; no synthetic share/price adjustments.',
        'data_and_valuation':'Inherit v2 verified full-session requirement for provider absence assumption, exact-coordinate evidence for strict mode, malformed-bar/empty-session failure, fresh-only daily drawdown/milestones and null terminal stale equity.',
        'restarts':'No injections or resets in continuous portfolios; isolated500 cases are separate diagnostics, not cumulative wealth. No old40pctstop override.'},
      'news_policy':'Technical-only gap experiment. News/primary-source statuses retained as descriptive evidence, never used to select, rank or filter returns. Do not invent passed news for new candidates. Not a verified-catalyst EP strategy.',
      'registered_outputs':{
        'gates':3237,'isolated':'Every ready candidate in each family x4 financial variants x2 modes, with all non-ready1079 rows retained in gate registry.',
        'portfolios_per_family_mode_variant':['full_universe_strict','original_registry_unknown_guard','retrospective_ready_cohort'],
        'portfolio_count':72,
        'strict':'Full-market coverage holes must stop full_universe_strict. Registry unknown guard retains unresolved same-day candidates conservatively; no unknown news guard because news is not part of this strategy.',
        'retrospective':'Only ready market-gate members; explicitly conditional omission of unknown candidates, not a complete implementable market policy.',
        'comparison':'OR30 gates and applicable financial fields must reproduce parent model given same inputs. Compare all frozen variants, do not promote best retrospectively.'},
      'input_plan':'After gate tests, evaluate all1079 eachfamily; freeze union of every ready case and its first10 sessions through cutoff before new holding returns. Reuse source-verified old complete symbol-sessions, fetch missing sessions only with read-only fixed paginated requests. On overlap retain immutable old source; revised entry-prefix conflicts are explicit unknown, never favorable blending. Freeze full coverage/source hashes before return runs.',
      'evidence_limitations':['2026Jan-Oct5 and prior strategies already observed; this is exploratory, not independent holdout. Oct6 excluded to hold comparison period fixed.',
        'Original current6633-symbol directory, daily-open/RTH mismatch and incomplete daily histories remain; all1079 is broader than73 but not fullmarket.',
        'Historical feeds/corporate actions/news availability and identity mapping are not proven point-in-time.',
        'Cost scenarios do not establish actual execution, liquidity capacity or future edge. Multiple observed variants need independent future confirmation.'],
      'publication':'New exchange/codex-only immutable paths, all candidates/variants/failures/code/hashes/audits, exact-byte readback; no raw full feed/news corpus, credentials, private IDs or paths.',
      'broker_orders_sent':0,'new_scheduler_deployed':False,'input_sha256':inputs}
    save(ROOT/'study-design.json',design)
    notes=ROOT.parent/'s500-session-notes'
    save(notes/'S500_ACTIVE_RESEARCH.json',{'project':'s500','study':design['id'],'workspace':ROOT.name,'status':'registered_implementation_in_progress','updated_at':now,'study_design_sha256':sha(ROOT/'study-design.json'),'prior_stage_handoff':'S500_EP_EVENT_V2_STAGE_HANDOFF.json','broker_orders_sent':0,'new_scheduler_deployed':False})
    save(notes/'S500_LAST_RESULT_CHECK.json',{'checked_at':now,'trigger':'user_next_stage','prior_local_stage':'completed_audited_and_published','prior_commit':design['parent_commit'],'observer':{'run_id':37523995825,'created_at':'2026-10-06T20:07:01Z','conclusion':'success','meaning':'read-only connection observer, not autonomous research/trading'},'running_prior_research':[]})
    print(json.dumps({'registered_at':now,'study_design_sha256':sha(ROOT/'study-design.json'),'input_hashes':len(inputs)}))

if __name__=='__main__':main()
