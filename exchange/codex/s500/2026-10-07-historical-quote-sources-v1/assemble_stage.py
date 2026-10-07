from pathlib import Path
import json,hashlib
from datetime import datetime,timezone
R=Path(__file__).resolve().parent
sha=lambda n:hashlib.sha256((R/n).read_bytes()).hexdigest()
paths=['vendor-a/databento/findings.json','vendor-a/massive/findings.json','vendor-a/massive/evidence.json','vendor-b/theta/findings.json','vendor-b/cboe/findings.json']
plan={'registered_at':datetime.now(timezone.utc).isoformat(),'purpose':'Provider-specific unresolved adaptations for existingfrozen800row historyplan; no requests executed andno sample/gate relaxed.','generic_request_plan_sha256':sha('historical-request-plan.json'),'evidence_sha256':{n:sha(n) for n in paths},'inherited_quality_gate':'Realhistoricalbidask/sizeconditionmapping andtimestampsemantics remainrequired; missingconditionsorunitmetadata isunknown, not an automaticpass.','provider_adaptations':[
{'provider':'ThetaData','conditional_priority':'lowest_publicly_verified_monthly_tick_tier','documented_plan':'Options Standard','monthly_list_usd':80,'time_scope':'2016onward tier advertised; no actual2026dataobtained','quote_route':'/v3/option/history/quote; interval=tick explicitly, onehistoricaldate perrequest','trap':'Defaultinterval=1s; Value40/month minutequotes nottick. Recent7day minuteflatfile notwhole2026archive.','must_resolve_before_data':['Personal/professionalstatus and applicable storage/cloud/use terms','SDKgRPC orLinuxterminal supportedproxy/network/auth pathway; no currentTheta secret','Generalhistorytimestamp timezone/event-vs-grid semantics, sizeunits andconditionmappings','Activitybasedhistoricalchain isnotcompleteasoflisting master; multiplier/deliverables andadjustment lineage independentverification','Exactrequestendinclusivity andboundaryfilters; do not assumegeneric65secparams verbatim']},
{'provider':'Massive','conditional_priority':'documented_HTTPS_historical_quote_route','documented_plan':'Options Advanced individual/nonprofessional ifqualified','monthly_list_usd':199,'time_scope':'Historicalquotes since2022-03-07; actual2026contractcoverageuntested','quote_route':'GET https://api.massive.com/v3/quotes/{optionsTicker}; documentedtimestamp.gte/lt, sorttimestamp, orderasc, boundedpagination','time_adapter':'sip_timestamp isSIPreceipt nanoseconds, notexchangeevent oruserreceipt. Do notrenameasgeneric eventtime; retaindistinctroles andbounds.','must_resolve_before_data':['Actualplaneligibility, properlicense/retention/cloudresearch rights andsecureMASSIVE_API_KEY binding ifselected','Historicalquotes have noquotecondition/flags; require documentedfiltering/nonfirm semantics or retainineligibility underexistinggate','Provideras_ofcontractmetadata, explicitpremium multiplier reconciliation andadditionalunderlyings; shares_per_contractnotautomaticallypremium multiplier','Pagination/completeness/sequence reset andsource cutoff; do notfollowunvalidatednext_url withcredentials','Actualall-incontractprice/taxes andbudget approvalbeforepurchase; Basic0/Starter29/Developer79 lackhistoricalquotes']},
{'provider':'Databento','conditional_priority':'technical_candidate_pending_readable_product_and_license_evidence','monthly_list_usd':None,'time_scope':'OPRA.PILLAR identifierverified; actualdatasetcoverage/schemaunknown','quote_route':'SupportedhistoricalSDK after legitimatelyconfiguredaccess; list_schemas/get_dataset_range/get_cost methods documented generically','time_adapter':'get_range bounds filterts_recv ifpresent else ts_event. Generic65s eventwindow isNOT directlyanAPIrequest. Need separatelyregisteredreceive-window anddelay/boundary policy; unboundedlatency means eventcompletenesscannotbeasserted.','must_resolve_before_data':['ReadableOPRA productscope,currentpricingandapplicablelicense; clientopensourcelicense doesnotgrantmarketdatarights','Account-entitleddataset/schema/range metadata; no keycurrentlyobserved','Metadatarequestchargingsemantics beforecallingcostendpoint; cost includesquotes,definitions,symbology andotherbillablecalls','Schema cmbp1 vs intervalcbbo vs trade-triggeredtcbbo; fullcondition/correctionmapping andtyped instrumentdefinitions','HTTPS BasicAuth/proxy credentialinjection compatibility andverifieddestination']},
{'provider':'Cboe DataShop','conditional_priority':'minute_snapshot_comparison_only_not_tick_replacement','monthly_list_usd':None,'time_scope':'Intervalsproduct2012onward;FAQ2010discrepancy;2026bracketedbutnotactuallyqueried','quote_route':'HistoricalOptionQuoteIntervalsfile order; tickrequiresseparatecontact/productspecification andquote','time_adapter':'quote_datetime is intervalend snapshot inUS Eastern, notper-updateeventtimestamp. No5sagefreshness inference.','must_resolve_before_data':['Finalhistoricalorderprice; unconfiguredsubtotal0.00 isnotfreequote; AllAccessAPItierpricing isdifferentproduct','Quotecondition/seq/corrections absent; standaloneintervalcannotpasscurrenttickqualitygate','June22 2026 NBBOsizesmethodchange: latermostrecentpricechangewithininterval vsolderlatestsize; segmentedanalysisneeded','Applicableinternaluse/orderexceptions/cloudaccess andretentionterms; no blanketclaimallpersonalbacktestingbanned','SupportedSFTPnetworkgrant/account plusdeliveryexpiry; currentTCPdestinationsunset','Completehistoricalidentity/multiplier/deliverables stillmissing']}
], 'paid_request_or_purchase_authorized':False,'vendor_credential_values_received':False,'generic_window_plan_overwritten':False,'primary_quality_gate_relaxed':False}
(R/'provider-adaptation-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2)+'\n')
reco={'generated_at':datetime.now(timezone.utc).isoformat(),'status':'public_source_research_complete_data_acquisition_blocked','recommended_decision':'User confirmsnoneofthese subscriptions. Savecomparison; deferpaiddata acquisition. Continuealreadylicensedunderlying signalresearch asaseparateexperiment, neverlabelasoptionPnL. Ifpurchaseconsideredlater, clarifyTheta80ticktransport/semantics andMassive199HTTPS conditions/rights beforeapproval. Databento exactcostunknown;Cboeminuteintervalnottickreplacement.','public_prices_are_not_user_quotes':True,'possible_monthly_costs_as_fraction_of_500_paper_start':{'ThetaStandard':0.16,'MassiveAdvanced':0.398},'cost_accounting':'Trackdata/researchfees separatelyfromPAPERcapital andincludeinprojecteconomiccost. No realpayment,noPAPERfundingreset.','existing_vendor_subscriptions':'User explicitly answered on2026-10-07: none ofthese subscriptions. No purchase authorization supplied.','blocked_beforepilot':['Existinglicensedentitlement/vendorselection andintendeduserstatus','Resolveprovider-specific fields,timebounds,conditions andidentity gaps','Supportedcredential/networksetup forselectedprovider','Known nonbillablecostcheckorfinalquotedcost; explicitpurchaseauthorizationifmoneyrequired'],'prepared_outputs':{'all_prior_rows_preserved':800,'plannable_prior_contract_windows':598,'retained_unavailable_contracts':202,'pilot_SPY_windows':20,'pilot_contract_seconds':1300,'all_598_contract_seconds':38870,'actual_new_historicalquote_rows_obtained':0,'new_strategy_wealth_paths':0},'next_scope':'Only20SPY coveragepilotafterprerequisites; addsource andqualityauditbeforeexpandingtoall598fixedwindows(includingthe20pilotwindows). Full-yearPnLrequiresnewprotocolwithentry/exitpaths.','research_does_not_wait_for_data':'Otheralreadylicensedstocksignalresearchcancontinueinaseparatestage; do notrelabelitoptionexecutionperformance.','source_provenance':{n:sha(n) for n in paths+['provider-adaptation-plan.json']},'no_orders':True,'no_new_scheduler':True,'no_runtime_config_change':True}
(R/'stage-decision.json').write_text(json.dumps(reco,ensure_ascii=False,indent=2)+'\n')
report='''# $500 项目：历史期权报价来源核验

2026 年 10 月 7 日（纽约时间）。本轮沿用已固定的 2026-01-02 至 2026-10-05 样本，不自动扩大为“截至今天的全年回测”。

**历史报价有可行的数据产品，但当前环境还没有已验证、获授权的新来源。** 本轮完成四家官方资料核验和小样本获取方案，没有新增历史报价、收益路径或订单。

先查了本项目任务：唯一启用的 GitHub 工作流是连接巡检，检查时无运行中或排队任务。纽约时间 9:22 的已保存观察记录是 PAPER 现金/权益 $500、零持仓、零挂单、交易未启用；这是当时的记录，不是现在直接查询券商余额。上一阶段 800/80 行可负担性报告已发布，未冒充收益回测。

## 数据来源比较

| 来源 | 官方资料支持的历史数据 | 已核实公开价格 | 当前关键缺口 |
|---|---|---|---|
| ThetaData | Standard 档逐笔 OPRA NBBO；标称从 2016 年起 | Standard $80/月；Value $40/月仅分钟档 | 云端 gRPC/终端连接未测；一般历史接口时间戳语义、数量单位、完整历史合约主表和适用保存条款待确认 |
| Massive（原 Polygon） | 逐笔历史期权买卖报价，自 2022-03-07；支持按合约 HTTPS 获取和历史 `as_of` 元数据 | Advanced $199/月，个人/非专业资格；$0/$29/$79 档不含历史报价 | 没有报价条件/flags 字段；需确认非 firm 报价处理、实际权限、保存及云端使用范围 |
| Databento | 官方代码证明通用 consolidated BBO、采样 BBO、成交触发 BBO 是不同格式，存在 `OPRA.PILLAR` 标识 | 未核实 | 产品/价格/条款页面返回 JS 空壳；通用代码不能证明 OPRA 覆盖、所需 schema 或购买权利 |
| Cboe DataShop | 已核实产品为一分钟 NBBO 快照；逐笔需另询产品规格 | 历史文件报价未取得 | 分钟快照不满足逐笔要求；盘口数量口径在 2026-06-22 改变，不能把整年无差别拼接 |

以上是 10 月 7 日取得的公开价目，不是用户的最终报价或资格判断。Massive Business $1,999/月是另一使用范围；Cboe All Access API 的价目也不等于历史文件价格。Databento 没有编造每 GB 或小样本价格。Cboe 订单页面初始 $0.00 不代表免费。

一个月 $80/$199 分别相当于 $500 模拟起始本金的 16%/39.8%。数据费用须单列，并计入项目总研究成本；不能在评估项目回报时忽略。

## 已准备的获取方案

完整保留上一阶段 **800 行**：598 个可规划合约窗口和 202 个未选中合约。不会只取其中已有成交的 400 行，以免筛掉不活跃或缺失样本。

首批固定为跨 10 个月的 **20 个 SPY 看涨/看跌合约窗口**，每个窗口为纽约时间 09:59:55 至 10:01:00，半开区间，共 1,300 合约秒。前 5 秒用于观察窗口开始前的报价状态；10:00、10:00:15、10:00:30、10:00:45 是数据诊断检查点。全部 598 个窗口合计 38,870 合约秒，但时间长度不能直接换算数据费用，报价密度、产品、请求和许可均影响成本。

这些窗口只检验数据覆盖和字段质量，不包含全天退出、资金结算或全年交易策略。原 190 个交易日中只抽样了 10 天，另 180 天仍未抽样。需要先通过首批来源、合约身份、时间戳和盘口核验，再登记扩大计划。

## 不能忽略的接口差别

- ThetaData 默认间隔是 1 秒，逐笔必须明确 `interval=tick`；近期七天分钟 flat file 不能补齐整年。一般历史响应的毫秒格式并未充分解释时区和事件时间，不能从另一个接口套用语义。合约列表基于某天有交易或报价，不等于所有历史上市合约。
- Massive 的 `sip_timestamp` 是 SIP 接收时间，不是用户收到行情的时间。其历史报价没有条件字段；不能为了让测试通过就把“未知条件”改成“有效”。`as_of` 有用，但不自动证明每个调整合约的乘数和交割物全部正确。
- Databento 的范围过滤优先使用 `ts_recv`，否则使用 `ts_event`。因此原方案的事件时间区间不能直接复制为 API 参数；必须另定接收时间范围和边界处理，并保留延迟造成的未知完整性。
- Cboe 的时间是区间终点快照，不能解释成该时刻刚更新的盘口。6 月 22 日后，报价数量取区间内最近一次价格变化时的数量，不保证是快照时刻最新数量。

四家都尚未通过本环境的实际数据认证。原来关于条件映射、数量单位、报价新鲜度和明确合约乘数的门槛保持不变。

## 与 Claude 的交流

已只读核对新增交流，并回应它关于低价 0DTE 价差的提问。它举出的 bid $0.06 / ask $0.11，若假设 100 乘数、45 张和双边足量成交，买入需 $495，立即按相同 bid 卖出只有 $270，未计费用已损失 $225，约为权利金的 **45.5%**；价差占中间价约 **58.8%**。

这只是对未独立验证的同业示例作算术说明，不是实际成交，也不是本项目损失。便宜权利金未必意味着低交易成本。我们尚无经验证的“低于 $0.20 合约最优价差上限”，原 10% 筛选仍是研究假设；挂中间价也不能在回测里直接当作成交。

## 结果与下一步

用户已明确回复：**没有这四家的订阅**。本轮据此将真实历史期权报价回测标为尚未接入，不发起购买。若未来决定投入数据费用，先确认 ThetaData $80 逐笔档的云端接入与字段问题，再和 Massive $199 的 HTTPS 路线比较；当前证据不足以直接推荐购买。接下来可使用已获授权的股票分钟数据研究开盘急跌反弹等方向信号；只有信号与成本证据充分后再评估期权数据投入，股票信号结果不写成期权可成交收益。

原有 Alpaca 密钥不能用于其他供应商。实时 OPRA 未签协议的历史错误也没有无变化地重复请求；即使解决实时协议，历史报价来源仍需单独取得。没有代为开户、接受协议、购买数据、修改网络/密钥设置或启用交易。

本轮公开包保留官方 URL、取回时间、正文 SHA256、短引文、失败/JS 空壳记录、全部 800 行计划、供应商适配缺口及独立审计。原始完整资料和已有账户观察原件留在私有缓存，不公开。独立检查可验证计划与证据引用，不等于验证任何供应商的真实行情完整性。

主要官方入口：[ThetaData 报价接口](https://thetadata.net/docs/operations/option_history_quote.html)、[ThetaData 价格](https://www.thetadata.net/pricing)、[Massive 历史报价](https://massive.com/docs/rest/options/trades-quotes/quotes.md)、[Massive 价格](https://massive.com/pricing?product=options)、[Databento 官方客户端](https://github.com/databento/databento-python)、[Cboe 区间报价](https://datashop.cboe.com/option-quote-intervals)。逐项证据见各供应商 `findings.json` 和来源清单。
'''
(R/'REPORT.zh-CN.md').write_text(report)
(R/'REPRODUCE.md').write_text('''# Evidence and plan reproduction

This stage is official-document research and an unexecuted acquisition plan, not a backtest. No vendor subscription, entitlement, connection or paid market data was validated.

- The provider manifests list public URLs, retrieval times, statuses and private-body hashes; findings contain short attributed quotations and limitations. Full source bodies are private and are not bundled. Some live links and SDK main branches can change; use the recorded body hashes for exact captured-source identity. Hashes alone do not provide public access to those bodies.
- `historical-request-plan.json.gz` preserves all 800 rows from the prior publication; decompress with Python gzip or a standard gzip reader. The prior sanitized contract source is included as `prior-selected-contracts.json.gz`, byte-identical to prior `selected-contracts.json` after decompression.
- Independent audit scripts under `audit/` reconstruct scope and UTC/DST windows, check preserved rows and source evidence, and recompute the peer arithmetic. They may require prior stage files and private captured source bodies. They are audit methods, not vendor download tools.
- `provider-adaptation-plan.json` expressly leaves endpoint mapping, time semantics, fields, cost, rights and transport issues unresolved. Do not convert the generic event-time plan into API requests without resolving them. Do not bypass missing conditions, quote size units or historical identity requirements.
- Public prices are not the user's contractual charges or permission. There is no zero-cost paid-data authorization hidden in the study. User credentials must stay in secure environment settings for the selected official destination, never in this report or another vendor's endpoint.
- `build_peer_reply.py` only reproduces the clearly hypothetical quoted-spread arithmetic. `assemble_stage.py` assembles interpretation and plans from frozen findings; rerunning it changes generated timestamps and does not prove new market data availability.

The environment already supports this standard-library document/audit workflow; no installation, service startup or saved environment configuration change was needed. Existing checkouts and prior artifacts were preserved; no worktree was created. Data-provider integration remains untested and separate from this completed research stage.
''')
print('Provider adaptation, decision and report written')
