"""Assemble the final independent audit record from completed offline checks."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib,json

R=Path(__file__).resolve().parent
def read(n):return json.loads((R/n).read_text())
def sha(n):return hashlib.sha256((R/n).read_bytes()).hexdigest()
def save(n,obj):(R/n).write_text(json.dumps(obj,indent=2,ensure_ascii=False)+'\n')
actual=read('independent-actual-audit.json');source=read('independent-source-audit.json');summary=read('run-summary.json')
assert actual['status']=='pass' and source['status']=='passed'
for obj,key in [(actual,'sha256'),(source,'input_sha256')]:
 for name,h in obj[key].items():assert sha(name)==h,(name,'changed_after_audit')
report=(R/'REPORT.zh-CN.md').read_text();decision=read('stage-decision.json')
assert decision['historical_result_rows']==800 and decision['current_result_rows']==80
assert decision['historical_discovery_cells']==400 and decision['current_discovery_cells']==40
for month in summary['monthly']:
 s=month['scenarios'][1]
 expected=f"| {month['month']} | {month['selection_statuses']['selected']} | {month['rows_with_trade_records']} | {s['one_contract_reference_affordable']} | {s['one_contract_reference_unaffordable']} | {s['unknown_price_or_multiplier']} |"
 assert expected in report,('monthly_report_row',month['month'])
current=read('current-indicative-results.json')['rows']
storage={'MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P'}
for row in current:
 if row['underlying_symbol'] not in storage:continue
 a=row['analysis'];s=a['affordability_scenarios'][1];c=a['contract_analysis']['contract']
 val=lambda x:'未知' if x is None else str(x)
 expected=f"| {row['underlying_symbol']} | {row['type']} | {c.get('symbol')} | {val(a['quote']['ask'])} | {val(s['one_contract_total_including_reserve'])} | {val(s['whole_contracts_budget_only'])} |"
 assert expected in report,('storage_report_row',row['underlying_symbol'],row['type'])
for phrase in ['180 天未抽样','403: OPRA agreement is not signed','60 条时间戳全部过时','另 20 条缺失','没有伪称重新预注册','签署实时协议不等于取得历史报价']:
 assert phrase in report,phrase
for name,h in decision['results_sha256'].items():assert sha(name)==h
review={'reviewed_at':datetime.now(timezone.utc).isoformat(),'status':'pass','monthly_table_rows_independently_matched':10,'storage_table_rows_independently_matched':16,'clarifications_resolved':['Discovery cells400/40 are distinct from call/put result rows800/80.','Only the60 returned current quotes are stale;20 missing quotes remain unknown.'],'scope_checks':['CurrentOPRA403 unsigned agreement remains separate from historicalquote404/unsupported route evidence.','190calendar dates and only10 sampled sessions are explicit; no full-year strategy-performance claim.','All quoted budget counts are price proxies or indicative arithmetic, not executable quotes, fills, win rates or strategyreturns.','Current directory/metadata bias, fee hypotheses, assignment/exit and size/condition mapping gaps remain explicit.','First arithmetic run preceded summary fix; second binding is not represented as first preregistration.','Public arithmetic replay is distinguished from unavailable complete private-source reproduction.'],'sha256':{n:sha(n) for n in ['REPORT.zh-CN.md','REPRODUCE.md','stage-decision.json','replay_public.py','public-replay.log','run-summary.json']}}
save('independent-report-review.json',review)
findings={'finalized_at':datetime.now(timezone.utc).isoformat(),'status':'passed_with_explicit_data_and_execution_limits','blocking_implementation_findings':[],
 'scope':'Offline independent protocol/sampling, all source acquisition and contract selection, pure analyzer tests, every actual budget row and published report. No network, broker account calls, orders, capital resets or strategy simulations by auditors.',
 'validation':{'pre_first_arithmetic_author_tests':37,'pre_first_arithmetic_independent_tests':25,'pre_first_arithmetic_total':62,'post_summary_fix_independent_regression_tests':3,'all_tests_passed':True,'historical_rows':800,'current_indicative_rows':80,'fee_scenarios':2640,'monthly_groups':10,'all_880_original_and_corrected_rows_identical':True,'source_tasks':472,'source_pages_and_http_attempts':510,'source_bytes':107881146,'returned_contract_metadata_rows':149682,'selected_contract_rows_independently_rebuilt':880,'original104daily_source_hashes_verified_in_sampling_audit':True,'supplemental_SPY_prior_days':11},
 'observed_results':{'historical_reference_affordable_unaffordable_unknown_per_reserve':[298,102,400],'current_indicative_affordable_unaffordable_unknown_per_reserve':[41,19,20],'same_at_each_hypothetical_reserve':['0','0.10','1'],'current_quotes_stale_over5s':60,'current_quotes_missing':20,'primary_executable_quotes_verified':0,'historical_bidask_rows_verified':0,'strategy_wealth_paths':0,'orders_sent':0},
 'resolved_findings':[{'issue':'Contract identity, exact-time and primary-quote evidence boundary hardening','resolution':'Explicit symbol join and OCC metadata match, delayed/missing delivery blocked, exactnanoseconds/UTC offset validation, literalspace/A conditions only; synthetic casespass.'},{'issue':'Provider selfassertion could be labelled independent adjusted-deliverable proof','resolution':'Output independentverification staysFalse; separate providerassertion only. Oldcode and testbinding retained; corrected before actual arithmetic.'},{'issue':'Collector initially used wrong deliverable/count field names','resolution':'Documented fields corrected before any optionprice requests; oldcode/plans preserved; request/selection rules unchanged.'},{'issue':'Summary failed on actual missing counts None>0 after first complete arithmetic','resolution':'Only positive known eventcounts contribute. First code, binding and rowoutputs retained; all880 rows identical after rerun; unknown prices never become zero; three extra regression tests after incident.'},{'issue':'Report result-row/cell and stale/missing labels','resolution':'Finalreport and decision distinguish400/40 discoverycells vs800/80 rows, and60 stale vs20 missingcurrentquotes.'}],
 'unresolved_evidence_limits':['Current optionable directory and contract metadata are not point-in-time historical membership or deliverable versions.','Only10 of190 sessions sampled;180 intentionally unsampled. HTTP/pagination completeness is only completeness of returned request scope.','Historical trades/minutebars do not establish historical bid/ask, historicalreceipt, fills, spreads, exitliquidity, or strategyperformance.','CurrentOPRA returned403 with exact unsigned-agreement reason; testedhistoricalquote path404 is distinct. Signing currentagreement doesnot establish historicalquotedata.','All currentindicative quotes are derived values from afterhours; 60 returnedquotes stale and20 missing. Provider size/condition mapping remainsunverified.','Fees0/.10/1 percontract roundtrip are researchhypotheses. Fullfee history, current/PAPERcharges, assignment, exercise, settlement and forcedexit readiness remainunverified.','Raw107.9MB sources and officialdocument caches are private; publicbudget replay works, complete public-source reproduction is not claimed.'],
 'publication_exclusions':['independent-option-initial-tests.log','independent-actual-audit-incomplete-summary.private.log','raw-option-inputs/','raw-probes/','raw-docs/'],
 'artifact_sha256':{n:sha(n) for n in ['independent-protocol-review.json','independent-sampling-review.json','independent-supplement-review.json','independent-source-audit.json','independent-source-audit.py','independent-test-binding.json','independent_option_tests.py','independent-option-tests.log','independent_summary_tests.py','independent-summary-tests.log','independent-actual-audit.json','independent_actual_audit.py','independent-actual-audit.log','independent-report-review.json','REPORT.zh-CN.md','REPRODUCE.md','stage-decision.json']}}
save('audit-findings.json',findings)
(R/'audit-findings.md').write_text('''# 独立审计结论

本阶段在明确的数据与执行边界内通过审计，没有未解决的实施阻断。结论仅覆盖数据来源、样本选择与整张预算算术。

- 37 项作者测试和25项独立测试在首次真实算术前通过；摘要缺失值错误后另加3项独立回归测试并通过，没有把后加测试冒称预注册。
- 472项任务、510页／HTTP响应、107,881,146字节逐正文和元数据哈希、参数、分页链核验一致。149,682条合约元数据独立重建全部880条选中或缺失记录。11个SPY补充参考先于合约选择，合约选择先于价格请求；原始抽样文件未改。
- 800条历史和80条当前结果、2,640个费用场景及10个月汇总独立复算一致。首次摘要因None计数失败，旧版保留；修正后全部880条行结果与首次完全相同，未知价格没有填零。
- 每档费用假设下，历史参考价可负担／不可负担／未知为298／102／400；当前indicative为41／19／20。60条实际返回的当前报价均超过5秒，另20条缺失。历史已验证买卖盘口与当前通过真实报价核验均为零。
- 报告全部10个月与16条存储样本表对账通过。400／40个标的日目录查询，与800／80条call/put结果分母明确区分。

当前目录、当前合约元数据及有限月度抽样不能证明历史完整可交易池。历史成交不是ask或成交保证；盘后indicative不是真实OPRA。当前未签OPRA协议导致403，与历史报价路径404/尚未找到受支持入口是两项独立缺口。费用、条件和数量映射、连续退出、到期与交割能力仍待证据。

没有策略财富路径、账户余额推断、订单、资本重置或定时器变更；没有500到10000的验证结论。公共包可复算派生预算，完整市场来源复现仍需授权与私有缓存。失败日志含私有路径而不公开，错误与修正经过已记录。完整字段和最终文件哈希见audit-findings.json及independent-report-review.json。
''')
print(json.dumps({'status':'final_audit_passed','final_files':{n:sha(n) for n in ['audit-findings.json','audit-findings.md','independent-report-review.json','REPORT.zh-CN.md','REPRODUCE.md']}},indent=2))
