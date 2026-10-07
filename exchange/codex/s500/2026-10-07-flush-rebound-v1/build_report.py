"""Render findings from immutable, independently checked study outputs."""
from pathlib import Path
import json, hashlib
from datetime import datetime, timezone

ROOT = Path(__file__).parent
def read(name): return json.loads((ROOT / name).read_text())
def save(name, obj): (ROOT / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
def pct(x): return 'NA' if x is None else f'{100*x:+.4f}%'
def sha(name): return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()

def main():
    p = read('statistics/primary-inference.json')
    s = read('statistics/summary.json')
    stock = [v for v in s['variants'] if v['group'] == 'stock']
    a = p['all']; ci = p['matched_excess_date_cluster_bootstrap']
    costs = []
    for cost in [5, 10, 25]:
        vs = [v for v in stock if v['variant']['cost_bps_per_side'] == cost]
        costs.append({'cost_bps_per_side': cost, 'structural_variants': len(vs),
                      'positive_event_mean_count': sum(v['all']['event_weighted']['returns']['mean'] > 0 for v in vs),
                      'positive_date_mean_count': sum(v['all']['date_weighted']['returns']['mean'] > 0 for v in vs),
                      'matched_excess_intervals_excluding_zero': sum(not (v['matched_excess_date_cluster_bootstrap']['lower'] <= 0 <= v['matched_excess_date_cluster_bootstrap']['upper']) for v in vs)})
    assert all(c['positive_event_mean_count'] == c['positive_date_mean_count'] == 0 for c in costs if c['cost_bps_per_side'] >= 10)
    assert all(c['matched_excess_intervals_excluding_zero'] == 0 for c in costs)
    decision = {'study': 's500_intraday_flush_rebound_v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'decision': 'do_not_promote_this_rule_to_paper_execution',
                'reason': 'Negative primary net-return means; all twelve structures negative at10/25bps per side; every matched-excess interval crosses zero. No established edge.',
                'primary': p, 'cost_sensitivity': costs,
                'claims_not_supported': ['Positive expected trading profit', 'Causal alpha', 'Executable option profits', '500-to10000 success probability', 'Strict unseen out-of-sample validation'],
                'next_research': {'status': 'proposed_not_registered_or_run', 'hypothesis': 'Separate market-synchronous early declines from idiosyncratic early declines using only completed first30 stock and benchmark bars.', 'constraints': 'New exploratory protocol before new calculations; reuse all retained failures and same-time controls; no winner filtering, no H2-as-unseen claim, no new scheduler or orders. Freeze any future prospective test before its dates.'},
                'orders_sent': 0, 'new_scheduler': False, 'purchases': False,
                'bound_evidence': {n: sha(n) for n in ['study-design.json', 'analysis-output-manifest.json', 'statistics/summary.json', 'independent-actual-audit.json', 'independent-summary-audit.json']}}
    save('stage-decision.json', decision)
    rows = []
    for v in stock:
        z = v['variant']; st = v['all']; b = v['matched_excess_date_cluster_bootstrap']
        rows.append(f"| {float(z['drop_threshold'])*100:.0f}% | {z['family']} | {z['exit_horizon']} | {z['cost_bps_per_side']} | {st['counts']['complete_returns']} | {pct(st['event_weighted']['returns']['mean'])} | {pct(st['date_weighted']['returns']['mean'])} | {pct(b['point_estimate'])} | [{pct(b['lower'])}, {pct(b['upper'])}] |")
    monthly = []
    for k, z in list(p['months'].items()) + list(p['periods'].items()):
        monthly.append(f"| {k} | {z['counts']['complete_returns']} | {z['counts']['complete_matched_excess']} | {pct(z['event_weighted']['returns']['mean'])} | {pct(z['date_weighted']['returns']['mean'])} | {pct(z['date_weighted']['matched_excesses']['mean'])} |")
    report = f'''# 2026 年开盘急跌后反弹：全年截至 10 月 6 日研究

这套规则暂不进入 PAPER 执行。主方案成本后平均收益为 **{pct(a['event_weighted']['returns']['mean'])}/完整事件**；将每天先求均值、再对日期等权后为 **{pct(a['date_weighted']['returns']['mean'])}**。相对三只预先匹配对照股的日期等权超额为 **{pct(ci['point_estimate'])}**，描述性 95% 日期重抽样区间 **[{pct(ci['lower'])}, {pct(ci['upper'])}]**，跨过零。没有证实正向优势。

每边 10 或 25 个基点成本下，12 种信号与退出结构的事件均值和日期均值全部为负。每边 5 个基点下，3/12 结构事件均值为正、1/12 日期均值为正；全部 36 个参数组合的配对超额区间均跨零。不能从中挑一个最好结果称为有效策略，也不据此断言所有反弹策略永远无效。

## 范围、输入与预先固定的规则

- 2026-01-02 至 2026-10-06，共 **191 个交易日**；不是尚未结束的全年。
- 当前目录 6,633 个标的。每天用此前 20 个完整交易日数据要求前收盘不低于 $5、日成交金额近似值中位数不低于 $20m；排除当前明确 ETF 的股票目标。取流动性前 64、剩余合格池固定哈希抽样 32、合格存储观察组，并加入 SPY/QQQ 对照。当天的跌幅不参与选样。
- **19,519 个标的—交易日、2,025 个不同标的**，其中股票目标 19,137 个、ETF 对照 382 个。并非每天覆盖全部 6,633 个标的。
- 764 次成功请求返回 **7,259,432 根 SIP/raw 分钟线**；191 日分页完整。6,389 个标的—交易日缺少至少一分钟；缺分钟可能源于无成交或停牌等，不能全部叫接口故障。原始市场输入保留私有，公开源文件哈希、覆盖情况与派生结果。
- 研究方案在新分钟采集和实际收益计算前于 2026-10-07 10:02:09 EDT（14:02:09 UTC）冻结。假设受到此前 2026 年研究及 Claude 的研究建议启发，所以不是未接触历史信息的独立样本外试验。

基准是 09:30 分钟开盘价。用 09:30—09:59 的 **30 根完整分钟收盘价**判断是否下跌至少 2% 或 3%；这不是相对昨收的跳空，也不是用当天最低价回头找买点。

FIXED 在 10:00 得知首 30 分钟结果，以 10:01 分钟开盘为买入参考。REBOUND 从 10:00 至 10:59 扫描已结束的分钟：收盘比严格更早的最低收盘高至少 1% 才确认；信号分钟为 t，参考买入为 t+2 的开盘，留一整分钟延迟。没有用同一根分钟线同时确认和成交。

分别在买入后 30 分钟、60 分钟、交易日收盘前 10 分钟退出。参考持有窗口每一分钟都要求有效；缺数据保留信号，但收益未知。每边固定成本为 5/10/25 bp，净收益为 `exit_open*(1-cost)/(entry_open*(1+cost))-1`。这些是成本假设和分钟成交价代理，不是实际买卖报价或成交。

主方案在结果前固定为：**2% 急跌、REBOUND、持有 60 分钟、每边 10 bp 成本，股票目标相对三只对照的日期等权超额**。总计 36 个组合、702,684 行完整参数记录；行数包含重复参数与未触发样本，不是独立交易数。

## 主方案的全部分母

| 状态 | 标的—交易日数 |
|---|---:|
| 全部股票目标 | 19,137 |
| 首 30 分钟完整、可判断 | 15,374 |
| 未达 2% 急跌 | 12,370 |
| 已确认急跌 | 3,004 |
| 已确认反弹信号 | 2,681 |
| 急跌后完整观察但未确认反弹 | 286 |
| 信号未知：首 30 分钟不完整 3,763，加反弹搜索中缺数据 37 | 3,800 |
| 信号后目标收益完整 | 2,542 |
| 信号保留但目标收益未知 | 139 |
| 目标和三只预选对照收益均完整 | 2,203 |
| 已有信号但配对收益不完整（包含上述 139） | 478 |

完整目标事件正收益比例为 **{100*a['event_weighted']['returns']['positive_fraction']:.2f}%**。事件均值、日期等权均值及配对超额采用不同权重或样本，不能直接相减倒推出对照收益。未触发或未知事件不被填成零收益。

配对在同一交易日、同一进入与退出分钟，使用相同成本。候选对照来自当日预选股票中首 30 分钟完整且未达同一急跌阈值者；按此前 20 日成交金额近似中位数的对数距离取最近三只。选定后不因未来收益缺失换股。SPY、QQQ 同时间比较单列，不能将重复出现的指数窗口误当作独立样本。

## 全部参数结果

净收益是百分比；超额和区间是收益差，表中以百分数表示（百分点）。日期等权先对同日完整结果求均值，再对有结果日期等权；不是账户每日收益。以下按预定参数顺序，没有按赢家排序。

| 急跌阈值 | 规则 | 退出 | 每边 bp | 完整目标事件 | 事件平均净收益 | 日期等权净收益 | 日期等权配对超额 | 超额 95% 区间 |
|---|---|---|---:|---:|---:|---:|---:|---|
{chr(10).join(rows)}

目标与对照采用相同固定成本，相减会大体抵消成本；超额在成本情景间变化小并不能证明真实执行成本稳健。

## 主方案按月及前后半年

| 时段 | 完整目标事件 | 完整配对 | 事件平均净收益 | 日期等权净收益 | 日期等权配对超额 |
|---|---:|---:|---:|---:|---:|
{chr(10).join(monthly)}

10 月只有截至 6 日的四个交易日。H2 到截止日的表现转弱；这些切片只是描述，不是因看见 H1 后便可将 H2 称为独立验证。

## 集中度、验证和局限

主配对样本涉及 191 个日期和 201 个股票符号；完整逐股计数、正负贡献及固定存储观察组见 `primary-concentration.json`。贡献只是解释总均值由哪些样本组成，不据此删除拖累股票或重新挑选股票。观察组中 P 的存储业务分类尚未独立验证。

实际计算前 160 项测试通过，并绑定最终生产代码及输入清单。独立检查重建全部日选样、原始页面到分钟输入、702,684 行信号和收益、233,847 个同时间股票对照窗口及 155,934 个指数窗口。最终 108 个分组参数汇总、1,080 个按月汇总、216 个半年度汇总、20,628 个逐日记录及全部 bootstrap 均独立复算一致。测试和审计证明实现符合本方案，不证明供应商价格准确或可成交。

仍然存在当前目录的幸存者偏差、当前 ETF 分类误差、历史价格修订、先前研究影响假设、完整分钟筛选的可观测性偏差。三只对照只按流动性匹配，没有充分控制行业、市场敏感度或消息催化；比较是观察性关联，不是因果效应。日期重抽样处理同日事件共振，但未充分处理跨日序列相关，也没有多重比较校正，区间只是描述。

## 本阶段决定和下一项研究

把本次规则保留为失败或未证实的研究结果，不升级为 PAPER 下单策略。不把股票分钟价格代理收益写成期权收益，不推算 $500 到 $10,000 的成功率；本研究没有模拟账户资金、并发头寸、结算、重开或追加注资。

下一项候选研究是区分“大盘同步下跌”与“个股独立下跌”，仅用当时已结束的前 30 分钟股票及基准信息。它目前只是研究提案，尚未登记或运行；需先固定新规则及比较对象，沿用全部失败样本，明确属于探索性研究。不能在本批数据上调出赢家后称为样本外有效。未来若做前瞻观察，必须在观察日期之前冻结方案。

用户没有所询问的历史期权数据订阅。本次无需购买；历史可成交期权 bid/ask 缺口仍在。此阶段未下单、未重置模拟账户、未新增定时器，也未改动独立的 $100,000 主账户项目。已有 connection rehearsal 的成功只表示观察流程成功，不证明 AI 自动研究或交易运行。

研究代码、完整失败及未知样本、源哈希、独立核验随本报告发布；复核入口见 `REPRODUCE.md`、`stage-decision.json` 与 `artifact-manifest.json`。
'''
    (ROOT/'REPORT.zh-CN.md').write_text(report)
    save('peer-exchange.json', {'to': 'Claude research exchange', 'study': decision['study'], 'reply_to': 'exchange/claude/2026-10-06.reply-claude-b.json', 'peer_hypothesis_status': 'inspiration_only_not_independently_verified', 'result': decision['reason'], 'primary_net_event_mean': a['event_weighted']['returns']['mean'], 'primary_date_matched_excess': ci, 'all_variants_retained': 36, 'comparability': 'This tests close-based first30 intraday declines and strictly later stock-price-proxy entries. It is not the same as prior-close gaps, intrabar lows, option bar returns or butterfly PnL.', 'questions_for_peer': ['Can your proposed flush rule be specified with contemporaneously observable timestamps, all failed signals and same-time controls?', 'Please preserve actual option bid/ask/size/conditions and receive-time evidence before asserting executable option profit.'], 'no_actual_fill_claim': True})
    print(json.dumps({'report_bytes': (ROOT/'REPORT.zh-CN.md').stat().st_size, 'costs': costs}))

if __name__ == '__main__': main()
