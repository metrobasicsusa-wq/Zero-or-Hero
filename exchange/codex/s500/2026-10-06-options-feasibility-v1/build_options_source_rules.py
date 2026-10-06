"""Derive short, auditable option API rules from private official captures."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,re

ROOT=Path(__file__).resolve().parent;RAW=ROOT/'raw-docs'

def norm(text):return ' '.join(text.split())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
 sources=[json.loads(p.read_text())for p in sorted(RAW.glob('*.meta.json'))]
 by_id={r['source_id']:r for r in sources}
 for record in sources:assert sha(RAW/(record['source_id']+'.raw'))==record['body_sha256']
 def evidence(source,quote,section=None):
  path=RAW/(source+'.txt')
  text=path.read_text()if path.exists()else(RAW/(source+'.raw')).read_text()
  assert norm(quote)in norm(text),(source,quote)
  return {'source_id':source,'url':by_id[source]['url'],'body_sha256':by_id[source]['body_sha256'],
          'retrieved_at':by_id[source]['retrieved_at'],'exact_quote_whitespace_normalized':quote,'section':section}
 rules=[
  {'id':'historical_coverage_start','status':'officially_documented',
   'summary':'The general historical options offering starts in February2024; this does not guarantee every contract/tick is present.',
   'evidence':[evidence('historical_options','Currently we only offer historical option data since February 2024.')]},
  {'id':'expired_contract_discovery','status':'officially_documented_filters_not_historical_listing_proof',
   'summary':'Contract search defaults to active contracts and expiry before upcoming weekend. Historical expiry investigations require explicit inactive status and exact/bounded expiry dates, not current-chain membership. Follow next_page_token to null; limit defaults100, max10000.',
   'evidence':[evidence('option_contracts','By default only active contracts that expire before the upcoming weekend are returned.'),
     evidence('option_contracts','Filter contracts by status (active/inactive). By default only active contracts are returned.'),
     evidence('option_contracts','The number of contracts to limit per page (default=100, max=10000).')],
   'unknowns':['No contract listing/creation timestamp or historical asof filter is supplied by the inspected contract schema. Current status/tradable/open_interest/close_price fields are not proof of their values at an earlier decision time.',
     'Finding an inactive expired contract does not prove a complete historical opportunity universe or contemporaneous availability.']},
  {'id':'historical_routes_and_parameters','status':'officially_documented',
   'summary':'Documented historical market routes are options/bars and options/trades. Both use symbols/start/end/limit/page_token/sort; bars also timeframe. Neither inspected historical OpenAPI path lists feed. Latest quotes has feed. Symbols are limited100 per request; start/end inclusive; page limit applies across symbols.',
   'evidence':[evidence('option_bars','A comma-separated list of contract symbols with a limit of 100.'),
     evidence('option_bars','The inclusive start of the interval.'),
     evidence('option_bars','The inclusive end of the interval.'),
     evidence('option_bars','The limit applies to the total number of data points, not per symbol!')],
   'operational_implication':'Do not label an unrecognized feed=opra query as proof of provenance. Preserve request schema and returned metadata. Exhaust pagination, even after a short page.'},
  {'id':'historical_bid_ask_not_documented','status':'not_found_in_official_index_and_inspected_openapi',
   'summary':'The current official index documents historical option bars/trades, latest quotes, snapshots and latest option chain; no historical option quotes route was found. This bounded documentation search does not claim every possible commercial source lacks history.',
   'evidence':[evidence('option_latest_quotes','The latest multi-quotes endpoint provides the latest bid and ask prices for each given contract symbol.')],
   'index_source_id':'alpaca_llms',
   'operational_implication':'Latest quotes cannot establish historical bid/ask. Historical trade/bar prices cannot substitute for executable historical ask-entry and bid-exit evidence.'},
  {'id':'opra_vs_indicative','status':'officially_documented',
   'summary':'Indicative is a modified derivative feed, not actual OPRA quotes; its trades are derivatives delayed15minutes. Official OPRA feed and indicative must be separated.',
   'evidence':[evidence('historical_options','the quotes are not actual OPRA quotes, they’re just indicative derivatives.'),
     evidence('historical_options','The trades are also derivatives and they’re delayed by 15 minutes.'),
     evidence('option_latest_quotes','`opra` is the official OPRA feed, `indicative` is a free indicative feed where trades are delayed and quotes are modified.')],
   'operational_implication':'Never silently fallback to indicative after an OPRA authorization failure, or report indicative as executable NBBO.'},
  {'id':'quote_units_and_conditions','status':'native_opra_official_vendor_mapping_not_explicit',
   'summary':'Alpaca option quote schema labels as/bs as sizes and c as one string. Native OPRA specifies bid/offer size as contract counts and condition codes below. Do not apply stock quote units or stock condition arrays. The inspected Alpaca schema does not explicitly restate unit conversion or complete condition mapping.',
   'evidence':[evidence('opra_output_spec','The Best Bid Size identifies the number of contracts being bought for an option at the Best Bid price.','7.07'),
     evidence('opra_output_spec','The Best Offer Size identifies the number of contracts for sale for an option at the Best Offer price.','7.09'),
     evidence('option_latest_quotes','Quote condition.')],
   'operational_implication':'Retain raw as/bs/c and unit provenance; require documented/empirically verified vendor mapping before using size as a hard executable-capacity filter. Displayed size never guarantees fill.'},
  {'id':'multiplier_and_adjusted_deliverables','status':'officially_documented_current_metadata',
   'summary':'Premium calculation uses multiplier; size describes underlying shares delivered and must not replace multiplier. Request show_deliverables=true. Nonstandard contracts can deliver several assets or cash, and delayed/unknown amounts exist.',
   'evidence':[evidence('option_contracts','In standard contracts, the multiplier is always set to 100.'),
     evidence('option_contracts','This field should **not** be used as a multiplier, specially for non-standard contracts.'),
     evidence('option_contracts','This array is included in the list contracts response only if the query parameter show_deliverables=true is provided.'),
     evidence('option_contracts','This field can be null in case the deliverable settlement is delayed and the amount is yet to be determined.')],
   'operational_implication':'A present-day100multiplier/100size record is current contract metadata, not a dated historical deliverable version. Nonstandard, missing, ambiguous or changed deliverables require historical OCC/action evidence or exclusion as unknown.'},
  {'id':'whole_contract_budget','status':'officially_documented',
   'summary':'Option orders require whole-number qty and no notional. A500budget feasibility check must include whole contracts × premium × actual multiplier plus all applicable costs. Fractional-contract simulations are not directly placeable orders.',
   'evidence':[evidence('options_trading','Ensuring `qty` is a whole number'),
     evidence('options_trading','`Notional` must not be populated')]},
  {'id':'fees_current_version','status':'official_schedule_revised_2026_10_01_not_all_year_rates',
   'summary':'Use the dated current brokerage schedule with scope distinctions: index-option commissions, professional/non-retail equity-option commissions, and regulatory pass-through fees. A broad support statement of no option commissions is not a universal zero-cost rule.',
   'evidence':[evidence('alpaca_fee_schedule','Revised on October 1, 2026'),
     evidence('alpaca_fee_schedule','Each fee type is aggregated separately at the daily, per-account level.'),
     evidence('alpaca_fee_schedule','After aggregation, each fee total is rounded up to the nearest cent ($0.01).'),
     evidence('option_commissions','Alpaca Trading API users will not encounter any commissions for options trading.')],
   'operational_implication':'Preserve fee version/date and account/category assumptions; historical early2026 fee applicability and PAPER cash booking are not verified by this document study.'},
  {'id':'physical_delivery_and_auto_exercise','status':'officially_documented',
   'summary':'Standard equity contracts deliver underlying shares; a long call exercise requires strike × multiplier funding. ITM expiry can trigger automatic exercise. Small premium cost does not establish sufficient exercise funding.',
   'evidence':[evidence('option_contracts','Similarly, when exercising a call contract, the total cost will be equal to the strike price times the multiplier.'),
     evidence('options_trading','In the event no instruction is provided on an ITM contract, the Alpaca system will exercise the contract as long as it is ITM by at least $0.01 USD.'),
     evidence('options_trading','In the event the account does not have sufficient buying power to exercise an ITM position, Alpaca will sell-out the position within 1 hour before expiry.')],
   'operational_implication':'Do not assume forced liquidation guarantees price, execution or a premium-only terminal account. Any future protocol needs explicit closing, failed-exit, exercise and residual-stock accounting, and contract-specific settlement scope.'},
  {'id':'dne_documentation_conflict','status':'official_documents_disagree_on_mechanism',
   'summary':'The general options guide says contact support for DNE, while the current OpenAPI documents a do-not-exercise POST endpoint. Documentation is not evidence the account can use it or that it has been submitted.',
   'evidence':[evidence('options_trading','To submit a Do-not-exercise (DNE) instruction, please contact our support team.'),
     evidence('option_dne','This endpoint enables users to submit a do-not-exercise (DNE) instruction for a held option contract, preventing automatic exercise at expiry.')],
   'operational_implication':'No position/exercise/DNE endpoint is invoked in this research. Do not treat an unverified future DNE instruction as existing protection.'},
  {'id':'opra_subscriber_agreement','status':'official_subscriber_terms_not_account_state_verification',
   'summary':'OPRA electronic subscriber agreement requires the subscriber to accept terms; its nonprofessional addendum requires personal eligibility/use/professional-status representations. Subscription data coverage and agreement acceptance are distinct requirements.',
   'evidence':[evidence('opra_electronic_agreement','PLEASE INDICATE YOUR AGREEMENT TO BE BOUND BY ITS TERMS AND CONDITIONS BY CLICKING ON THE “I AGREE” BUTTON AT THE END.'),
     evidence('opra_electronic_agreement','The purpose of this Addendum is to determine whether you are a “Nonprofessional” for OPRA’s purposes.')],
   'operational_implication':'An account-specific agreement-not-signed403 should be reported exactly, not reduced to a generic paid-plan error. The account owner must review/accept any agreement and make their own eligibility representations; this research does not sign, change a subscription, or retry around the restriction.',
   'ui_limit':'No verified Alpaca dashboard click-path was obtained; use the owner-facing Alpaca account flow or official support rather than inventing UI instructions.'}
 ]
 conditions=[{'code':c,'meaning':m}for c,m in [(' ','Regular Trading'),('F','Non-Firm Quote'),('I','Indicative Value'),
   ('R','Rotation'),('T','Trading Halted'),('A','Eligible for Automatic Execution'),('B','Bid contains Customer Trading Interest'),
   ('O','Offer contains Customer Trading Interest'),('C','Both Bid and Offer contain Customer Trading Interest'),
   ('X','Offer side not firm; bid side firm'),('Y','Bid side not firm; offer side firm')]]
 result={'schema_version':1,'generated_at':datetime.now(timezone.utc).isoformat(),
   'scope':'Independent official public-document review only; no account/order/market-quote requests or profit backtests.',
   'sources':sources,'rules':rules,'native_opra_quote_conditions':conditions,
   'quote_condition_source':{'source_id':'opra_output_spec','version':'6.4b','document_date':'2026-08-25','section':'6.04 Equity and Index Quote Messages; page30'},
   'current_option_fee_schedule':{'source_id':'alpaca_fee_schedule','revised_on':'2026-10-01',
      'equity_etf_options_retail_commission':'Zero under stated normal self-directed retail scope; partner and non-retail exceptions remain.',
      'index_options_commission_per_contract':'0.50 plus regulatory/exchange fees',
      'professional_equity_option_commissions':'Tiered0.40/0.30/0.20/0.15/0.10 per contract, depending monthly volume; not assumed applicable here.',
      'pass_through':[
        {'fee':'SEC','side':'sell','rate':'0.0000206 * trade_value'},
        {'fee':'TAF','side':'sell','rate':'0 per contract in this current Alpaca schedule'},
        {'fee':'CAT','side':'buy_and_sell','rate':'0.000003 per executed-equivalent share; standard option100 equivalents or applicable multiplier'},
        {'fee':'ORF','side':'buy_and_sell','rate':'0.015 per contract'},
        {'fee':'OCC','side':'buy_and_sell','rate':'0.025 per contract'}],
      'rounding':'Aggregate each fee type separately per account per day, then round each total up toUSD0.01.',
      'historical_rate_versions_verified':False,'actual_account_fee_booking_verified':False,
      'cap_caveat':'Older regulatory guide mentions OCC up to2750contracts; current PDF excerpt does not specify that cap. Do not invent treatment beyond the supported small-account scope.'},
   'multiplier_policy':{'premium':'price * multiplier * whole_contracts','never_substitute_size_for_multiplier':True,
      'deliverables_required_for_classification':True,'historical_version_proven':False},
   'not_performed':{'market_quote_requests':0,'broker_account_requests':0,'broker_orders':0,
      'exercise_or_DNE_actions':0,'agreement_acceptances':0,'subscription_changes':0,'profit_backtests':0},
   'peer_review':'No new peer source consumed in this documentation subtask; earlier peer simulations are not an official evidence source.',
   'raw_documents_public':False,'fetch_code_sha256':sha(ROOT/'fetch_options_docs.py'),
   'summary_code_sha256':sha(Path(__file__))}
 (ROOT/'options-source-rules.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
 md='''# 期权数据与500美元约束：官方资料核验

本文件只核验官方公开文档，不查询账户、订单或市场报价，不做收益回测。完整正文只留在私有raw-docs/；JSON内保存短引文、URL、获取时刻和正文SHA256。

## 能确认的数据边界

- 历史期权数据官方起点为2024年2月。当前OpenAPI提供历史bars/trades及latest quotes，未查到历史options quotes路由；latest不能替代历史bid/ask，成交价或分钟高低价不能充当历史可成交报价。
- 历史bars/trades未列feed参数；latest quotes才列opra/indicative。不要通过给历史URL加feed=opra就声称确认了行情源。行情symbols每次最多100，分页limit默认1000/最多10000，start/end含端点，必须翻完跨symbol分页。
- 过期合约需显式查询inactive及具体到期日/区间；默认只返回active、最近周末前到期的合约。合约limit默认100/最多10000，翻到next_page_token为空。今天取到的合约状态、OI、close及交割字段不能证明过去决策时已知。
- indicative的报价是修改后的衍生值，并非真实OPRA报价；成交也是衍生且延迟15分钟。不得在OPRA失败后无提示降级并宣称真实报价。

## 数量、交割与条件

期权qty必须整张，不支持按notional下单。权利金预算为价格×**multiplier**×整张数量，加费用；**size不是multiplier**。请求show_deliverables=true；调整合约可能交割多种股票/现金，数量可能延迟确定。当前元数据不能证明历史交割版本，不能把所有合约硬编码100。

OPRA原生规范明确报价size单位为合约数；Alpaca schema只写bid/ask size，未明确重述映射。因此要保留原始值与来源，不能套用股票的单位。OPRA的A为可自动执行，空格为普通交易，R为轮转，F/I/T分别为非确定报价（non-firm）/指示值/停牌，X/Y为单边不firm；完整派生表见JSON。不能把股票的R条件规则复制过来，也不能把显示尺寸当保证成交量。

## 费用与到期

当前Alpaca收费表修订于2026-10-01。普通零售股票/ETF期权佣金与指数期权、专业订单流不同。当前表列每边ORF0.015美元/张、OCC0.025美元/张、CAT0.000003美元×等价股数；卖出另有SEC按交易额0.0000206、该表TAF为0。每个fee种类按账户日汇总后向上取整到分。不能把这个版本无验证套到全年，也未证明PAPER实际扣费口径。

标准股票期权会交割股票，长call行权资金为strike×multiplier；官方写ITM至少0.01美元默认行权、购买力不足时到期前1小时内尝试由券商卖出。500美元能支付权利金不等于能支付行权款，不能把强平当保证成交。普通指南说DNE联系支持，最新OpenAPI另列DNE接口：保留文档差异，本次不调用或假设已有保护。

## OPRA协议

OPRA电子协议要求订阅者亲自同意条款，非专业附录涉及身份、用途与职业资格陈述。若实际接口返回未签协议，应保留该具体原因；这与是否具备OPRA套餐不是同一个状态。账户持有人需自行核对并接受适用协议，本研究不代签、不改订阅、不绕过限制。没有核实到Alpaca具体UI路径，不编造点击步骤。

## 主要官方来源

'''
 for key in ['historical_options','option_contracts','option_bars','option_trades','option_latest_quotes','opra_output_spec','alpaca_fee_schedule','options_trading','option_dne','opra_electronic_agreement']:
  row=by_id[key];md+=f"- [{key}]({row['url']})；SHA256 `{row['body_sha256']}`。\n"
 (ROOT/'options-source-rules.md').write_text(md)
 print(json.dumps({'official_sources':len(sources),'rules':len(rules),'native_quote_conditions':len(conditions),'market_requests':0}))

if __name__=='__main__':main()
