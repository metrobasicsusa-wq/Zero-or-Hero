"""Register a new exploratory stratification before computing stratified outcomes."""
from pathlib import Path
import json,hashlib,datetime
ROOT=Path(__file__).parent;PARENT=ROOT.parent/'s500-flush-rebound-20261007'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    result={'id':'s500_market_relative_flush_v1','registered_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'status':'frozen_before_new_classification_and_group_outcome_math','exploratory':True,
      'parent_commit':'634a93c65e1adec6c5908f9ad249c5b8e6224ba8','period':['2026-01-02','2026-10-06'],
      'scope':{'sessions':191,'selected_cases':19519,'stock_cases':19137,'unique_symbols':2025,'variants':36,'parent_rows':702684,'reuse_all_parent_cases_and_variants':True},
      'hypothesis':'First30 stock declines may differ with contemporaneous broad-market declines; no predeclared positive direction and no winner filter.',
      'user_clarification':{'text_zh':'末日期权胜率可能低于40%，不能只看胜率。','interpretation':'No minimum winning-percentage requirement. Evaluate complete net payoff distribution, asymmetric wins/losses, costs and tails; observed low winrate is not itself rejection or evidence of positive expectancy. User estimate is not a verified market statistic.','unchanged_goal':'PAPER500perround goal10000; retain all failure/residual/injection history in any future capital model.'},
      'classification':{'information_time':'All first30 minutes end at10:00ET, before all inherited signal entries; no bars with known t>=30 used.',
        'target_prefix':'All t=0..29 valid unique OHLCV; anchor target exactlyt0open. Earliestt attaining minimumfirst30close is t_min. Unknown targetprefix retained, never assigned market_not_down.',
        'benchmarks':['SPY','QQQ'],'primary_benchmark':'SPY','sensitivity_benchmark':'QQQ',
        'benchmark_prefix':'All t=0..29 valid unique OHLCV required; benchmark_return=benchmarkclose[t_min]/benchmarkopen[0]-1. Unknown benchmarkprefix retained.',
        'market_down':'benchmark_return <= Decimal(-0.005), inclusive boundary',
        'market_not_down':'benchmark_return > Decimal(-0.005)',
        'relative_return':'targetclose[t_min]/targetopen[0]-1 - benchmark_return. Descriptive simple difference, not beta-adjusted/idiosyncratic causal residual.',
        'benchmark_targets':'Keep SPY/QQQ targetcases as not_stock_target in ledgers, exclude from stock group estimates.',
        'label_caveat':'market_not_down means benchmark did not decline at least0.5% at target trough; it does not prove stock-specific news or sector independence.'},
      'inherited_execution':'All36 parent entry/exit/cost variants, original preselected three-peer controls and exact same-time benchmark returns reused unchanged. Do not rematch peers by new groups or change returns.',
      'groups':['market_down','market_not_down','unknown'],
      'statistics':{'primary_variant':['0.02','REBOUND','60m',10],
        'primary_contrast':'For each date with at leastone complete original matched3pair in each SPY group, mean(market_not_down matched_excess)-mean(market_down matched_excess). Equal weight the dates. This is a descriptive association, not treatment effect.',
        'contrast_dates':'Keep all191 dates with both/one/neither groups and group_unknown counts. Dates without bothgroups notzero. Shared-date restriction creates selection and must be disclosed.',
        'bootstrap':'2000 replacement draws of whole date means; linear95percentile; SHA256 first16hex seed namespace s500-relative-flush-v1|label; n_dates<2 noCI. Primarygroup netmean, matchedexcess and contrast for SPY andQQQ only. No other variant CIs inthisstage.',
        'all_variants':'All36 xSPYQQQ xall3groups counts, status/missing distributions, complete target net distributions and matchedexcess; all/month/H1/H2 descriptive, same-date contrast point estimates for all36.',
        'payoff':'target netreturns only for win/loss interpretation; positive/negative/zero separately, wins/Ncomplete and wins/nonzero, averagewin, averageloss magnitude, payoffratio, profitfactor, mean=sumwin/N-sumloss/N. Matchedexcess signs do not mean trading wins.',
        'break_even':'Conditional-on-nonzero L/(G+L); hypothetical or empirical descriptive, not future guarantee. Missingrequiredwins/losses givesnull withreason, neverInfinity. No additional cost deduction from already netreturns.',
        'tails':'q=1percent,5percent; requestedk=ceil(q*Ncomplete); take min(k,positivecount) largestpositive netreturns, stablecaseid ties. Report contribution=sumselected/N, shareofallpositiveprofits, removedcount, residualn and residualpooledmean. Only sensitivity, never an implemented selltarget or new stock exclusion.',
        'negative_day_runs':'Primaryonly, eachgroup dateequal mean targetnet<0; adjacency follows all191 calendar sessions. Zero/no-complete-outcome breaks observed negative-day streak; track unknown days. This is not chronological trade loss streak or fundedaccount drawdown.',
        'slices':'Months andH1/H2 are descriptive after observedparentresults, not untouchedOOS.',
        'multiplicity':'No bestgroup/variant declaredvalidated; no40percentthreshold; descriptiveCIs not serial-dependence or multiplicity adjusted.'},
      'option_scope':'No historical optionbidask acquired; cannot estimate 0DTE success rate, fillable convexpayoff or500wealthsurvival from stocklinearreturns. No order or capital simulation.',
      'source_limits':['Parent currentdirectory/ETFclassifications notPIT, survivorship','Historical provider revisedprices not realtime receipts','Completeholding-window and double-group-day selection','Liquiditymatching notsector/beta/news control','Hypothesis explored after prior2026study outcomes'],
      'validation':'Synthetic production+independent edge tests and code/input hashbinding before actual new classifications; then full independent classifications/summary arithmetic audit before publication.',
      'publication':'New immutableexchange/codex only; full new classification/derivedjoinedledger and allstatistics, parent immutablelink/sourcehashes/code/tests/failedunknownrows; no rawmarketexports/privatecredentials/IDs/absolutepaths.',
      'parent_bound_sha256':{n:sha(PARENT/n) for n in ['study-design.json','analysis-output-manifest.json','selected-case-registry.json','calendar-2026-ytd.json','minute-source-manifest.json','statistics/summary.json','publication-verification.json']},
      'orders':False,'account_reads':False,'new_scheduler':False,'vendor_purchase':False}
    path=ROOT/'study-design.json';assert not path.exists();path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'registered_at':result['registered_at'],'design_sha256':sha(path)}))
if __name__=='__main__':main()
