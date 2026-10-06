from pathlib import Path
import json,hashlib
from datetime import datetime,timezone
R=Path(__file__).resolve().parent
read=lambda n:json.loads((R/n).read_text())
s=read('run-summary.json');cur=read('current-indicative-results.json')['rows'];hist=read('historical-reference-results.json')['rows']
monthly=[]
for m in s['monthly']:
 q=m['scenarios'][1];monthly.append(f"| {m['month']} | {m['selection_statuses']['selected']} | {m['rows_with_trade_records']} | {q['one_contract_reference_affordable']} | {q['one_contract_reference_unaffordable']} | {q['unknown_price_or_multiplier']} |")
storage=[]
for r in cur:
 if r['underlying_symbol'] not in ['MU','STX','WDC','SNDK','NTAP','RMBS','SIMO','P']:continue
 a=r['analysis']['affordability_scenarios'][1];q=r['source_quote_evidence'].get('quote') or {};c=r['analysis']['contract_analysis']['contract'];
 storage.append(f"| {r['underlying_symbol']} | {r['type']} | {c.get('symbol','缺失')} | {q.get('ap','未知')} | {a['one_contract_total_including_reserve'] or '未知'} | {a['whole_contracts_budget_only'] if a['whole_contracts_budget_only'] is not None else '未知'} |")
report='''# $500 期权数据与整张合约可负担性核验 v1

完成日期：2026-10-06。历史样本截止：2026-10-05。当前快照：2026-10-06 21:00:16 UTC，盘后。

本阶段确认：当前凭据能读取过期合约目录、历史期权成交和分钟 K 线；**尚不能据此开展有历史盘口证据的期权收益回测**。当前真实 OPRA 请求返回 `403: OPRA agreement is not signed`；本次查询的历史报价路径返回 404，官方现行接口目录没有找到历史期权买卖报价入口。两者是不同问题，签署实时协议不等于取得历史报价。

本阶段是数据与预算核验，没有生成收益路径、实际 FILL、账户余额结论或下单。项目目标仍是每轮 PAPER $500，研究增长至 $10,000；失败轮、全部注资及残值须保留。没有用旧版 40% 回撤或其他本金门槛替代用户目标。

## 范围与方法

从当前 3,916 个标记有期权的标的出发，固定 2026 年以前日线流动性前 24 个、确定性哈希抽样 8 个，再加入预先指定的存储股与 SPY/QQQ 对照，去重得到 40 个标的。既不是上一轮赢家名单，也不是全市场完整检验。当前目录存在时点与幸存者偏差。

索引今年截至 10 月 5 日的 190 个交易日；本次仅抽样 1—10 月各月第一个交易日的 10:00—10:01 美东窗口，**10 天已抽样、180 天未抽样**。400 个历史标的日分别保留 call/put，共 800 行。另保留 40 个标的的当前 10 月 9 日到期 call/put，共 80 行。没有把月度样本写成整年策略回测。

历史合约取样本日起最多 7 个日历日内、截至 10 月 5 日已到期的合约，取最近到期，再按前一交易日未复权收盘价选择最近行权价，代码打破平局。当前合约以 10 月 5 日收盘价作同样参考。不按期权价格、盈利、隐含波动率或事后流动性挑选。所有空列表与缺失行保留。

SPY 原缓存缺少 11 个前日参考价。独立补充计划先冻结，再用股票日线只读获取，成功后才选期权；原始空值文件未改写。期权交割字段命名修正也发生在任何本轮期权价格查询前，旧计划与旧代码均保留。

## 覆盖与价格量级

472 个采集任务、510 个已记录 HTTP 响应均完整，来源正文与元数据哈希核验通过。合约列表 440 个任务无请求失败。历史 800 行中，598 行选中合约，202 行对应本次条件下的完整空列表；已选合约中，400 行有目标分钟成交及 K 线，198 行没有返回事件。空列表不证明当时没有任何该股票的期权，空分钟也不证明全天不能成交。

以下用每张往返预留 $0.10 的**假设**作统一展示：$500 ÷（参考价格 × 明确合约乘数 + 预留费用），取整张向下取整。乘数缺失时结果未知，不从合约代码或 size 猜成 100。

| 月份 | 选中合约 / 80 | 有分钟成交 | 参考价可负担 ≥1 张 | 参考价超过 $500 | 价格或合约未知 |
|---|---:|---:|---:|---:|---:|
'''+ '\n'.join(monthly)+'''
| 合计 | 598 | 400 | 298 | 102 | 400 |

全部三种预留费用假设（每张往返 $0 / $0.10 / $1）均保留；本样本中“至少一张”的计数相同，各行数量仍分别计算。这些数值不是报价可执行率、胜率或盈利概率。历史第一条返回成交的条件与更正语义尚未独立认证；成交价格不是 ask，不能假设自己当时也成交。

当前 80 行中取到 60 个 indicative 报价，另 20 行为空列表。按 indicative ask 计算，41 行可负担至少一张、19 行超过 $500、20 行未知。**80 行都未通过真实报价核验**：本次请求是 indicative，收集在盘后；已返回的 60 条时间戳全部过时，另 20 条缺失。Alpaca 的数量单位与条件映射尚未完成独立确认。因此这里的“未通过”主要反映数据证据，不意味着 80 个合约都买不起或策略失败。

## 预先指定的存储标的

下表是原定存储组全部当前样本，未按价格筛选。全部都是盘后 indicative 的数学示例，均无成交资格。费用列使用每张往返 $0.10 假设；数量只看预算，不代表盘口容量。P 保留为原目录代码，未独立证明其存储业务分类；没有因此删除其缺失结果。

| 标的 | 方向 | 合约 | indicative ask | 一张含预留费用 / USD | 预算数量 |
|---|---|---|---:|---:|---:|
'''+ '\n'.join(storage)+'''

## 费用、交割与到期限制

598 个已选历史合约和 60 个已选当前合约均在供应商现行元数据中满足 100 乘数、100 股标准交割的结构检查。这不代表独立 OCC 调整公告验证，也不证明这些元数据在历史决策时刻可得。Delta、历史上市时点及调整生效历史仍未验证。

读到的 Alpaca 官方收费表修订于 2026-10-01；它包括每张每边 ORF $0.015、OCC $0.025、CAT 按等价股票量计算，以及卖出 SEC 费用和按账户日/费用类别向上取整。不可未经版本证据套用于 1—9 月，也没有确认 PAPER 账本实际如何计费。上面的 $0 / $0.10 / $1 是敏感性假设，不是券商收费承诺。

买得起权利金不等于有钱行权：看涨行权可能需要行权价乘交割数量的现金，看跌可能需要交付股票；不能假设只损失权利金后什么都不用处理。到期自动行权、券商提前平仓、DNE 时限、节假日与多腿同步盘口均未建成执行模型。当前一般指南与 DNE 新接口描述的不一致保留在来源规则中，未发送 DNE、行权或订单请求。

## 验证、修正与可复算范围

分析器在真实样本算术前通过 37 项作者测试和 25 项独立测试，合计 62 项。测试覆盖纳秒边界、未来/过时报价、空条件与 literal space、交叉盘口、合约编码一致性、明确乘数、数量单位、费用边界和敏感字段剔除。

首次真实运行完成全部 800/80 行算术后，在月汇总中遇到缺失计数 `None > 0` 的 TypeError。保留首次代码、输入绑定和完整行结果；只修正“已知正记录数”计数后重跑，不更改样本、价格、预算或门槛。第二次绑定明确发生在已有算术之后，没有伪称重新预注册。最终独立审计另核来源、全部行、全部 2,640 个预算场景及汇总，详见审计 JSON。

公开包包含每个样本、失败与未知行、三档费用结果、方法代码、测试、版本与来源哈希。约 107.9 MB 原始合约/行情正文、官方资料缓存及敏感标识不公开。派生预算运算可本地重算；完整采集复现仍需要授权来源与私有缓存，不能称全链条公开可复现。

## 下一步

1. 当前真实 OPRA：本次明确阻断是未签署 OPRA 协议；这涉及账户持有人自身资格和协议确认，需由账户持有人通过 Alpaca 官方渠道处理。界面路径未核验，不能编造；也不把它简化为“买套餐即可”。只有实际授权状态变化后才重新做一次只读控制请求；返回 200 后，仍需在正常交易时段固定样本核验时钟、双边盘口、条件/单位映射与交割，不能直接启用交易。
2. 历史买卖报价：核对已获许可的历史 OPRA 报价和历史合约主表来源，先冻结覆盖及成本计划再拉取；无现成许可时保留缺口，不反复请求不支持的路径，也不以成交价或 indicative 填补。仅拿到真实买卖报价还不够，后续收益协议还需验证连续退出 bid、历史费用、行权与结算。
3. 只有上述证据足够后，再独立登记期权策略实验。没有真实历史报价时，可以继续明确标注的信号研究，但不报告期权可成交收益或“500 到 10000 已验证”。

本阶段没有部署或修改定时器。已有连接巡检成功不能证明研究助手已自动唤醒，也不能证明账户允许期权交易。
'''
(R/'REPORT.zh-CN.md').write_text(report)
decision={'recorded_at':datetime.now(timezone.utc).isoformat(),'status':'capability_and_affordability_complete_no_executable_option_backtest','historical_result_rows':800,'current_result_rows':80,'historical_discovery_cells':400,'current_discovery_cells':40,'verified_executable_quotes':0,'verified_500_to_10000_paths':0,'historical_quote_gap':'No official supported route identified; sampled hypothesis404','current_opra_gap':'403 OPRA agreement is not signed; only account holder may complete applicable agreement/qualification','next_research':'Independently verify licensed historical bidask and contract-master availability; after actual authorization change verify currentOPRA in regular trading hours. Do not substitute bars/trades/indicative.','strategy_enabled':False,'orders_sent':0,'account_reads':0,'new_scheduler':False,'prior_stage_commit':'ba999fff6c09ee41c45dffaf409c6d53375d9008','method_corrections':['pre-price-correction.json','analyzer-evidence-label-correction.json','summary-null-correction.json'],'results_sha256':{n:hashlib.sha256((R/n).read_bytes()).hexdigest() for n in ['run-summary.json','historical-reference-results.json','current-indicative-results.json']}}
(R/'stage-decision.json').write_text(json.dumps(decision,indent=2)+'\n')
print('Report and stage decision written')
