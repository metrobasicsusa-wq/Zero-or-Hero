"""Freeze an explicitly outcome-informed descriptive anatomy, not a new strategy."""
from pathlib import Path
import json,datetime,hashlib
ROOT=Path(__file__).parent
def sha(name):return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
def main():
    scope=json.loads((ROOT/'focus-scope.json').read_text());assert scope['anatomy_selected_cases']==1227 and scope['anatomy_date_count']==12
    design={'id':'s500_flush_event_anatomy_v1','registered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'status':'frozen_before_new_features_and_contribution_decomposition',
      'exploratory':True,'parent_commit':'a73297d47a6076e4f4a4725eab494f0308d7fe33','grandparent_commit':'634a93c65e1adec6c5908f9ad249c5b8e6224ba8',
      'scope':{'parent_calendar_sessions':191,'outcome_support_dates':scope['focus_dates'],'diagnostic_dates':['2026-06-18'],'anatomy_dates':scope['anatomy_dates'],'cases':1227,'stock_cases':1203,'benchmark_cases':24,'all_original_variants':36,'inherited_rows':44172},
      'date_selection':'11dates with previouslyobserved at leastone complete parent SPYdownprimary targetreturn, plus everyextra date withSPYdown selectedcases butnocompleteprimaryreturn (June18). Allselectedstocks andETFcontrols onall12dates retained. Thisisnotprospectivedate selection.',
      'retained_parent':'Everyparent signal/entry/exit/cost/peer/net/excess record unchanged; no new trades or portfolio. The original153complete returns/134pairs remain reference totals, withallnonsignal/fail/unknowncases retained.',
      'primary_variant':['0.02','REBOUND','60m',10],
      'primary_concentration_scope':'AllSPYdown primarysignalrows onthe12dates, includingmissingnet/excess. EveryknowncompleteparentSPYdown net/excess mustreconcile exactly; othergroups/all36 are retained ratherthan retuned.',
      'features':{'information_time':'Onlycompleted minute0..29, available10:00ET; exact same rulesforstocks andbenchmarks. Parententry_minute islater_realized_strategy_timing, notpartof10:00feature set.',
        'validity':'Uniquecomplete validOHLCV prefixrequired; knownfuturebarsignored, unlocatabletimestamp unknown. Noffill orzeroimputation.',
        'previous_close':'Use originalregistry selection_metrics.prior_close only, preservingrawhistorical-source/revision/splitlimitations. ForcedETFpriorclose isnotprovided and remainsunknown; no newdailyquery.',
        'fields':['gap_to_previous_close','close29_return','min_close_return','max_close_return','t_min','first30_volume','first30_high_low_range'],
        'volume':'Rawsumof30volumes, notRVOL or normalizeagainstwrongtimewindow.',
        'range':'(maxfirst30high-minfirst30low)/open0; fractionnotpercent.',
        'gap_and_close29_bins':['le_minus_2pct','minus_2pct_to_below_zero','zero_or_positive','unknown'],
        'trough_time_bins':['minute0_9','minute10_19','minute20_29','unknown'],
        'bin_boundaries':'For gap/close29 <=Decimal(-.02), then<0, else>=0. Forinteger t_min floor(t/10). No thresholds selectedfromoutcomes.',
        'description_only':'Report fixedbins among allSPYdownprimarycases withallstatus counts andcomplete netdistributions. Alsofirst30 summariesbysignalpositive/negative/zero/unknown_or_not_triggered, explicitly outcome-conditioneddescription notpredictiveproof. No new fitted model.'},
      'statistics':{'primary':'ExactFraction date/symbol/date×symbol signedcontribution fornet/excess separately. Pooled=sum/N; dateequal=mean(per-date available-eventmean). Unobserved dates notzero.',
        'leave_one_out':'For each date andeachsymbol, remove allits observedandmissing targetsignalrecords in sensitivityonly; retainoriginalpeers/otherreturns, recompute remaining N andnonemptydates andlistdisappearingdates. Includeeveryentitypositiveornegative; notleaveoutwinneroptimization, notOOS.',
        'all_dates':'Dailycase/status/return/pair tablefor12datesand3SPYgroups. Keepannual191datecoverage. DetailJune18 all3SPYdown selectedcases toexplainno completeoutcome.',
        'other_variants':'All44172inheritedrows and counts/status/net/excess summaries for36×3SPYgroups onthisfixed12dates, no winner selection ornewCIs.',
        'tails':'Allpositiveandnegativeevents remain inprimary estimates; concentration is descriptive andnotautomaticrejection. No minimumwinrate gate.',
        'new_inference':'No newbootstrap orcausaltest; existingparentCIs staycontextonly. No fundedcapital/optionreturn orprobabilitycalculation.'},
      'official_macro_context':{'dates':'All12anatomydates, identicalcategoryqueryscope','categories':['BLS ConsumerPriceIndex','BLS ProducerPriceIndex','BLS EmploymentSituation','BEA GDP advance/second/third','BEA PersonalIncomeAndOutlays','FOMC statement/meeting'],
        'retrieval':'OfficialpublicHTTPS only,bounded20GETtotal, perhostatmost1/sec,no401403retry; preserveURL/status/hash/retrievedtime andshortquotes; rawHTMLprivate.',
        'time':'Scheduledtime andactualrelease-page statedtime separate; before09:30,during09:30-10:00,exact10:00,after10:00,unknown. A timestamp at10:00 isnotproofinformation receivedbeforedecision.',
        'negative_evidence':'Noevent claim forbiddenfrommissing/deniedsources; usecurrentofficialcalendar_not_listed onlywithadequatecoverage, otherwiseunknown.',
        'causality':'Currentretrospectivecalendar andreleasecontent nothistoricalreceiptlogs. Listedrelease doesnotprove itcausedrebound; limitedmacroinventorynotallnews.'},
      'limitations':['Knownparentoutcomes influencedfocus; noneofthisisanunseentest','Currentdirectorysurvivorship andretrospectivedataversions','Completeholding/prefixanddateavailabilityselection','Marketclassificationatstock-specifict_minconfoundedbytiming','Linearstockreturns do notshow0DTEconvexprofits; historicalbidaskmissing','Group/tail/leaveoneout descriptions notcausalor futureprobabilities'],
      'user_preference':'No minimum40percentwinrate; examine asymmetry/netexpectancy andalltaiIs. PAPER500perroundgoal10000,allfailures/residualsandnewinjections countinanyfuturewealthmodel.',
      'publication':'Newimmutableexchange/codex only, completeallcases/allvariants/features/status/contributions/sourcehashes/code/audits; no rawmarketexports/secrets/accountIDs/privateabsolute paths.',
      'scope_sha256':{n:sha(n) for n in ['focus-scope.json','focus-case-registry.json','diagnostic-case-registry.json','anatomy-case-registry.json','annual-date-coverage.json','reuse-input-manifest.json']},
      'new_market_requests':0,'orders_sent':0,'account_reads':0,'new_scheduler':False,'vendor_purchase':False}
    path=ROOT/'study-design.json';assert not path.exists();path.write_text(json.dumps(design,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'registered_at':design['registered_at'],'sha256':sha('study-design.json')}))
if __name__=='__main__':main()
