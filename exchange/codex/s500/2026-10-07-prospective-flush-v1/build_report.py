"""Describe a registered empty future cohort without inventing outcomes."""
from pathlib import Path
from datetime import datetime,timezone
import json,csv,hashlib
ROOT=Path(__file__).parent
def read(n):return json.loads((ROOT/n).read_text())
def save(n,o):(ROOT/n).write_text(json.dumps(o,ensure_ascii=False,indent=2)+'\n')
def main():
    p=read('protocol.json');reg=read('protocol-registration.json');ledger=read('observation-ledger.json');test=read('pre-registration-validation.json')
    assert p['status']=='registered' and ledger['observed_future_dates']==0 and all(r['status']=='not_due' for r in ledger['rows'])
    with (ROOT/'planned-sessions.csv').open('w',newline='') as f:
        fields=['ordinal','date','open_et','close_et','open_utc','close_utc','preopen_cutoff_utc','receipt_deadline_utc','review_at_utc','is_checkpoint'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for s in p['sessions']:w.writerow({**{k:s[k] for k in fields if k!='is_checkpoint'},'is_checkpoint':s['ordinal'] in p['review_ordinals']})
    decision={'study':p['id'],'status':'registered_future_observation_protocol_zero_future_results','registered_at':p['registered_at'],'window':p['window'],'planned_sessions':59,'fixed_panel_symbols':6633,'observed_future_dates':0,'primary_future_complete_returns':None,'future_mean_return':None,'checkpoints':p['reviews'],'no_minimum_40_percent_winrate':True,'all_tail_winners_and_losses_retained':True,'new_scheduler':False,'existing_observer_connected_to_this_study':False,'preopen_collector':'implemented_with_mock_tests_and_before_date_refusal; successful real future capture not yet verified','postclose_pipeline':'receipt metadata guard and inherited economic methods registered; automatic postclose capture and analysis not deployed in this stage','next_action':'On an eligible date, invoke preopen capture before09:20ET; if not run or late, preserve missed date. Connect daily execution separately without duplicating the existing observer.','account_reads':0,'orders_sent':0,'this_stage_calendar_GET':1,'this_stage_market_price_GET':0,'vendor_purchase':False}
    save('stage-decision.json',decision)
    save('peer-exchange.json',{'study':p['id'],'parent_commit':p['parent_commit'],'status':decision['status'],'shared_for_review':True,'questions':['Is a frozen6633 panel with daily preopen prior20 selection clearly separated from a dynamic market universe?','Do all source failures and missed deadline dates remain visible without retrospectively substituting improved versions?','Are 20 distinct outcome dates only a sufficiency label, with one final endpoint and no winner-bin selection?','What real contract bidask and whole-contract affordability evidence would be needed before moving from stock proxies to options?'],'no_new_research_results_claimed':True,'new_scheduler':False,'options_returns_verified':False})
    report=f'''# $500 研究：未来日期观察协议已登记

本阶段完成了**事前规则登记、固定股票池、开盘前采集工具和来源时间校验**。登记时间为纽约时间 **{p['registered_at_et']}**。从 **2026 年 10 月 8 日至 12 月 31 日** 共 **59 个计划交易日**观察旧研究提出的线索。目前 **0 个未来日期已观察**；收益字段保持未知，未填零，也没有新增胜率或盈利结论。

## 固定范围和评估节点

| 项目 | 登记规则 |
| --- | --- |
| 观察池 | 原研究已知的 6,633 个符号及原ETF标签固定不变；不是每天重建的全市场目录 |
| 每日选池 | 先前20个完整交易日，前收≥5美元，成交额代理 close×volume 的20日中位数≥2,000万美元 |
| 样本组成 | 前64个流动性符号＋剩余合格符号按原日期/符号 SHA 顺序取32个＋合格存储股＋SPY/QQQ控制 |
| 主方案 | SPY同跌组、2%急跌、REBOUND、持有60分钟、每边10bp成本情景 |
| 保留的其他观察 | 全36组合、SPY/QQQ分组、全部原gap/close29/trough固定箱及所有失败和未知 |
| 中期复盘 | 第20日 **11月4日17:30**；第40日 **12月3日17:30**，纽约时间 |
| 期末复盘 | 第59日 **12月31日17:30**，纽约时间 |

今天10月7日已经开盘，不能放进事先登记的新日期。11月27日和12月24日均为13:00早收市；UTC时刻按纽约夏令时变化保存，不能全年固定一个UTC开盘时间。官方交易日历已成功读取，完整59日清单在 `planned-sessions.csv`。

固定池沿用原6633符号，保留已知目录偏差、符号复用及过时ETF标签风险，未来新上市股票不加入本版主轨。它是固定队列的新日期观察，不能称为动态全市场扫描或完全无幸存者偏差。没有新增 `has_options`、可交易标志或期权价格筛选。强制基准不会混入股票收益统计。

## 防止事后改变样本

**每天纽约时间09:20前**必须完成全固定池先前20日数据的抓取和选池封存。任何批次或分页没有完成，整池停止排名；只有源请求本身成功且完整时，某只股票真实缺历史数据才按该股排除原因记录。当天或未来日线、重复日期、遗漏批次、变动后的封存记录均会被拒绝。

日终主行情包必须在**当天收盘后15分钟至17:15之间**取得。主版本是第一个所有请求/分页完成的数据包，不要求每个股票每根分钟都存在；缺分钟仍应保存为未知，不能等到补齐、盈利更好后替换首版。迟到修订另存版本。离线验收和17:30复盘可以晚些运行，前提是原封存与资料收取已在相应窗口内完成。

每个计划日期都保留：未到日期、没有运行、错过开盘前封存、数据失败、观察到无信号、信号收益缺失，彼此分开。没有记录不代表当天没有机会；没有交易也不是一笔零收益交易。发生休市变更按运行偏差追加，不能为了凑够结果换日期或延长窗口。

## 如何评估

唯一预先指定的期末主指标，是**主方案各有完整收益日期的平均净股票代理收益，再对这些日期等权平均**。同时报告完整事件、不同日期、缺失及覆盖分母。事件等权、控制超额、QQQ、36组合和所有分箱都是次要描述，不能选最高者当作验证成功。

至少20个不同的完整收益日期只是样本充分性标签，**不是独立性证明、功效计算或显著性门槛**。不足时仍按固定期末结束并标注样本不足。中期只看固定摘要，不调参数、不提前结束；期末按固定种子进行日期均值的描述性bootstrap，少于2个日期不算区间，序列相关和缺失偏差仍需说明。没有自动通过、失败或开启交易的条件。

继续接受低胜率、高赔率研究，**不设40%胜率门槛，大赢家和亏损都保留**。新日期对旧假设有用，并不使股票代理自动成为可成交期权结果。收盘后取得的SIP分钟数据不能证明10:00时交易程序已经收到同样的报价。尚未验证实际期权合约买卖价、整张合约成本、执行和500美元资金存活路径。

## 已验证与尚未运行

本轮 **{test['tests_passed']} 项测试通过**，覆盖完整池与时序、精确边界、缺失、ETF控制、分页、拒绝授权、迟到数据、夏令时、早收市和封存一致性，并有独立选池及协议检查。开盘前采集入口为 `capture_prior20.py`，使用现有受限目标的凭据绑定，通过继承的代理和TLS执行只读GET；禁止账户、订单及期权接口。真实日期到来前的调用已验证会被时间规则拒绝，市场价格请求为0。

**登记不等于自动运行。** 已核查的GitHub工作流仍是连接观察器，通过外部 `workflow_dispatch` 被触发，没有接入这套前瞻采集或AI研究。外部调度器的具体配置没有核实。本阶段没有新建定时器，也没有部署日终自动采集和分析。开盘前工具已实现，日终来源验收规则与旧分析引擎已固定；未来完整日的真实采集和运行仍需实际验证。若到了17:30才首次运行且没有此前按时保存的资料，当天必须记错过。

日常执行说明见 `OPERATIONS.zh-CN.md`。当前研究记录和规则已保存为可持续交接材料；以后可将经过核验的执行器接上，不能把本轮登记或观察工作流成功当作已完成自动部署。

原研究 commit：`{p['parent_commit']}`。本轮经纪商请求只有1次交易日历GET；市场价格请求0、账户读取0、订单0、数据购买0。原每轮500美元PAPER、目标10,000美元及失败、剩余资金、重开注资累计记账要求保持。
'''
    (ROOT/'REPORT.zh-CN.md').write_text(report)
    save('report-output-manifest.json',{'files':[{'name':n,'sha256':hashlib.sha256((ROOT/n).read_bytes()).hexdigest()} for n in ['REPORT.zh-CN.md','stage-decision.json','peer-exchange.json','planned-sessions.csv']]})
    print(json.dumps({'report_ready':True,'future_results':0,'scheduled_automatically':False}))
if __name__=='__main__':main()
