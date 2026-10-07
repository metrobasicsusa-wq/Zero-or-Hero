"""Build reviewable prose and tables from the completed fixed-group outputs."""
from pathlib import Path
import json,csv,hashlib,datetime
ROOT=Path(__file__).parent
def read(name):return json.loads((ROOT/name).read_text())
def save(name,obj):(ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n')
def pct(x):return '—' if x is None else f'{float(x)*100:+.4f}%'
def rate(x):return '—' if x is None else f'{float(x)*100:.2f}%'
def ratio(x):return '—' if x is None else f'{float(x):.4f}'
def interval(c):return '—' if c['lower'] is None else '['+pct(c['lower'])+', '+pct(c['upper'])+']'
def sha(name):return hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
LABEL={'market_down':'基准同跌≥0.5%','market_not_down':'基准未跌到0.5%','unknown':'分类未知'}
def main():
    p=read('primary-results.json');s=read('summary.json');known=[g for g in p['groups'] if g['group']!='unknown']
    m={(g['benchmark'],g['group']):g for g in p['groups']};down=m['SPY','market_down'];other=m['SPY','market_not_down']
    dd=down['all']['target_net_distribution'];od=other['all']['target_net_distribution'];ct={c['benchmark']:c for c in p['contrasts']}
    assert ct['SPY']['bootstrap']['lower']<=0<=ct['SPY']['bootstrap']['upper']
    assert ct['QQQ']['bootstrap']['lower']<=0<=ct['QQQ']['bootstrap']['upper']
    assert all(g['primary_inference'][k]['lower']<=0<=g['primary_inference'][k]['upper'] for g in known for k in ['net_date_bootstrap','matched_excess_date_bootstrap'])
    cost_table=[]
    for b in ['SPY','QQQ']:
        for g in ['market_down','market_not_down']:
            for cost in [5,10,25]:
                variants=[x for x in s['groups'] if x['benchmark']==b and x['group']==g and x['variant'][3]==cost]
                cost_table.append({'benchmark':b,'group':g,'cost_bps_per_side':cost,'structural_variants':len(variants),'positive_event_mean':sum(float(x['all']['target_net_distribution']['mean'])>0 for x in variants),'positive_date_mean':sum(x['all']['date_equal_target_net']['mean']>0 for x in variants)})
    decision={'study':'s500_market_relative_flush_v1','created_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'decision':'retain_predeclared_SPY_down_cohort_as_unvalidated_observation_no_trading_activation',
      'reason':'SPYdown primarysample has positive historicalnetmean, but only11outcome dates, allinH1 and no H2 classifiedcases. Allprimary group net/excess andsame-date contrast intervals crosszero. Timing, same-date selection, currentuniverse andretrospective hypotheses remain.',
      'no_minimum_win_rate':True,'user_40percent_estimate_verified':False,
      'primary_groups':known,'primary_contrasts':p['contrasts'],'all_cost_sensitivity':cost_table,
      'next_research':{'status':'proposed_not_registered_or_run','task':'Audit the full eleven SPYdown primary outcome dates and every same-rule selected/flush/failed/unknown case on those dates; examine temporal concentration and contemporaneously available context before designing a future unseen-date observation protocol.', 'constraints':'Exploratory event anatomy, not winner selection or retuned profitablebacktest; do not remove largestwins frommainmean, and do not treat taildependence itself as failure. Keep existing parameters. Any future option study needs actual authorized bidask/size/time and capital-feasibility data.'},
      'option_or_capital_claims':'No optionprofit,0DTEwinprobability,500survivalprobability or10000targetprobability computed.',
      'new_market_requests':0,'orders_sent':0,'account_reads':0,'new_scheduler':False,'vendor_purchase':False,
      'bound_evidence':{n:sha(n) for n in ['study-design.json','classification-output-manifest.json','analysis-output-manifest.json','primary-results.json','summary.json']}}
    save('stage-decision.json',decision)
    allrows=[]
    fields=['benchmark','group','drop','family','exit','cost_bps_per_side','selected_cases','signals','complete_returns','complete_pairs','win_rate','average_win','average_absolute_loss','payoff_ratio','profit_factor','event_mean_net','date_mean_net','date_mean_matched_excess']
    for x in s['groups']:
        a=x['all'];d=a['target_net_distribution'];v=x['variant']
        allrows.append(dict(zip(fields,[x['benchmark'],x['group'],*v,a['counts'].get('selected_cases',0),a['signal_status_counts'].get('signal',0),d['n'],a['counts']['complete_matched_excess'],d['win_rate_all_n'],d['conditional_average_win'],d['conditional_average_absolute_loss'],d['payoff_ratio_average_win_to_average_absolute_loss'],d['profit_factor_total_positive_to_total_absolute_loss'],d['mean'],a['date_equal_target_net']['mean'],a['date_equal_matched_excess']['mean']])))
    with (ROOT/'all-variant-table.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(allrows)
    head=[];inference=[];tails=[];denominators=[];monthly=[]
    for x in known:
        a=x['all'];d=a['target_net_distribution'];b=x['benchmark'];g=LABEL[x['group']];ni=x['primary_inference']['net_date_bootstrap'];ei=x['primary_inference']['matched_excess_date_bootstrap']
        head.append(f"| {b} | {g} | {d['n']} | {a['date_equal_target_net']['n']} | {rate(d['win_rate_all_n'])} | {pct(d['conditional_average_win'])} | {pct(d['conditional_average_absolute_loss'])} | {ratio(d['payoff_ratio_average_win_to_average_absolute_loss'])} | {ratio(d['profit_factor_total_positive_to_total_absolute_loss'])} | {pct(d['mean'])} |")
        inference.append(f"| {b} | {g} | {pct(ni['point_estimate'])} | {interval(ni)} | {pct(ei['point_estimate'])} | {ei['n_dates']} | {interval(ei)} |")
        a_counts=a['counts'];st=a['signal_status_counts'];denominators.append(f"| {b} | {g} | {a_counts['selected_cases']} | {st.get('no_flush',0)} | {st.get('signal',0)} | {st.get('no_rebound',0)} | {st.get('unknown',0)} | {a_counts.get('signal_return_missing',0)} | {a_counts['complete_matched_excess']} |")
        for k,t in d['top_positive_tails'].items():
            tails.append(f"| {b} | {g} | {'1%' if '1_percent' in k else '5%'} | {t['actual_removed_count']} | {rate(t['share_of_total_positive_return'])} | {pct(t['mean_without_selected_observations'])} |")
        for period,z in list(x['months'].items())+list(x['periods'].items()):
            q=z['target_net_distribution'];monthly.append(f"| {b} | {g} | {period} | {q['n']} | {rate(q['win_rate_all_n'])} | {pct(q['mean'])} | {pct(z['date_equal_target_net']['mean'])} |")
    contrasts=[]
    for x in p['contrasts']:
        b=x['bootstrap'];c=x['date_status_counts'];contrasts.append(f"| {x['benchmark']} | {c.get('both_groups',0)} | {c.get('down_only',0)} | {c.get('not_down_only',0)} | {c.get('neither_group',0)} | {pct(b['point_estimate'])} | {interval(b)} |")
    costs=[f"| {x['benchmark']} | {LABEL[x['group']]} | {x['cost_bps_per_side']} | {x['positive_event_mean']}/{x['structural_variants']} | {x['positive_date_mean']}/{x['structural_variants']} |" for x in cost_table]
    report=f'''# 市场同跌分组与非对称盈亏：2026 年截至 10 月 6 日

本轮不设最低胜率门槛。发现一个可保留观察的小样本：预先固定的 **SPY 同跌组**在主方案下有 **{dd['n']} 个完整事件、11 个日期，平均成本后股票收益 {pct(dd['mean'])}**。这些同跌组样本全部位于上半年，下半年没有该组样本；日期等权净收益区间跨零，同日期两组的主要比较也未确认差异，因此尚不启用交易。

SPY 未跌到阈值组有 {od['n']} 个完整事件，平均成本后股票收益 {pct(od['mean'])}。QQQ 敏感性分类下，两组主方案事件平均净收益均为负。不会把某个分组的正数当作已验证优势，也不会因为它依赖少数大盈利便自动否定它。

## 先回应低胜率问题

用户希望接受低胜率，寻找小本金有较大盈利机会的结构。该偏好已单独保存为 `user-payoff-preference.json`：**没有 40% 胜率筛选线**。用户估计的“可能不到 40%”不是本项目已验证的末日期权统计。

应同时看净期望、每次平均赚多少与亏多少、盈亏分布、报价成本及资金能否承受连续损失。纯二元假设下，胜率 30%、失败亏 1R、成功净赚 3R，费用前期望为 `0.30×3−0.70×1=+0.20R`；净赚 3R 在本金为 1R 时意味着总回款 4R。20% 胜率的费用前盈亏平衡要求成功净赚 4R，扣费后要求更高。这些只是算术，并非观测到的期权价格或策略成功概率。

本轮的股票收益线性，无法直接验证末日期权的非线性收益。高胜率也可能亏钱，低胜率也可能赚钱；只有实际概率与可实现的净盈亏共同决定结果。

## 固定范围和分组方法

复用上一阶段全部 **191 日、19,519 个标的—交易日、2,025 个标的、36 个参数组合**，没有重选股票、修改信号、退出、成本或三只匹配对照。股票目标 19,137 个；SPY/QQQ 目标 382 个仍在完整账本，但不混入股票收益。

取股票 09:30—09:59 的完整 30 分钟中最早最低收盘价的分钟。查看基准在**同一分钟**相对自身 09:30 开盘的收益，≤−0.5% 为同跌组，否则为未跌到阈值组。SPY 是预定主基准，QQQ 是预定敏感性检验。双方首 30 分钟必须有效完整；未知单列。规则到 10:00 才完全可知，早于父方案所有参考买入时刻。

这一标签不是因果分类。“未跌到阈值”不等于已证实由个股消息驱动；同日不同股票的见底时刻不同，也会影响分组。报告保留了各组见底和买入分钟的分布以说明这项混杂。

2026 年尚未结束，覆盖区间是 1 月 2 日至 10 月 6 日。新方案在 **2026-10-07 11:12:35 EDT（15:12:35 UTC）**、新分类与分组收益计算前冻结；此前总体结果已知，因此属于探索性后续研究，不是独立样本外验证。代码和输入在计算前通过 107 项合成及独立测试并绑定哈希。

## 主方案：胜率和盈亏幅度一起看

沿用 2% 开盘急跌、已结束分钟确认反弹、延后参考买入、持有 60 分钟、每边 10 bp 成本。下面胜率只指完整目标股票净收益为正，**不是期权胜率**。平均亏损列为亏损幅度；盈亏比=平均盈利/平均亏损幅度，盈利因子=总盈利/总亏损幅度。

| 基准 | 分组 | 完整事件 | 有收益日期 | 事件胜率 | 平均盈利 | 平均亏损幅度 | 盈亏比 | 盈利因子 | 事件平均净收益 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(head)}

这是单个股票事件的收益比例，不是账户收益率；同时或重叠的事件不能假设 $500 全部都能参与。所有亏损和尾部都计入主均值。经验盈亏平衡胜率仅在固定已观测平均盈亏、条件于非零事件时有意义，完整数值见机器结果，不能当作未来保证。

## 日期权重与同日期主要比较

先在每天对完整事件求均值，再对有结果日期等权。相对三只原先选定对照的超额收益另算；正超额不等于交易赚钱。95% 区间来自 2,000 次整日期重抽样，只作描述，未校正跨日相关或多重探索。

| 基准 | 分组 | 日期等权净收益 | 净收益 95% 区间 | 日期等权配对超额 | 完整配对日期 | 超额 95% 区间 |
|---|---|---:|---|---:|---:|---|
{chr(10).join(inference)}

预先指定的主要比较只用同一天两组均有完整三对照结果的日期，计算“未跌到阈值组的平均配对超额−同跌组的平均配对超额”。两组单独的全期收益涉及不同日期、权重和比较对象，因此不能用它们相减来替代下表。

| 基准 | 两组都有 | 只有同跌组 | 只有未跌到阈值组 | 两组都无 | 日期等权差 | 差的 95% 区间 |
|---|---:|---:|---:|---:|---:|---|
{chr(10).join(contrasts)}

SPY 同跌组 363 个入选样本和 153 个完整事件全部位于 H1，H2 该组入选和完整事件数均为 0；这是无样本，不是下半年收益为零。SPY 主要比较只有 **10 个共同日期**，也都在上半年；QQQ 只有 39 个。区间均跨零。SPY 同跌组全期平均收益正，而共同日期的配对差方向偏向另一组，并不矛盾：比较样本、收益定义和权重不同。不能从中挑有利口径宣布某组更好。

## 完整分母和缺失

分类在所有股票样本上做，而不只在成功反弹的事件上做。SPY 同跌 363、未跌到阈值 15,011；QQQ 同跌 1,711、未跌到阈值 13,663。双方各另有 3,763 个目标前 30 分钟不足的未知样本；没有把未知划进未同跌组。

| 基准 | 分组 | 全部股票样本 | 未达急跌 | 反弹信号 | 完整观察未反弹 | 信号未知 | 信号后目标收益缺失 | 完整三对照配对 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
{chr(10).join(denominators)}

分类未知组各保留 3,763 个样本，收益全部未知。上表的信号未知发生在已完成分类之后的反弹搜索，不应与这 3,763 个未知混为一谈。所有未触发、失败和缺失结果都在 702,684 行派生账本中；行数包含 36 参数的重复，不能称为独立交易次数。

## 少数大赚的贡献：描述，不是排除规则

按全部完整事件数的 1%/5% 向上取整，再选最多该数量的最大正收益。贡献占比以**全部正收益总和**为分母。移除后的均值只用于说明依赖程度；实际主结果没有移除这些盈利。

| 基准 | 分组 | 最大正收益数量口径 | 实际事件数 | 占全部正收益 | 假设剔除后的事件均值 |
|---|---|---|---:|---:|---:|
{chr(10).join(tails)}

SPY 同跌组最大的 8 个盈利事件贡献约 29.36% 的总正收益；剔除后均值转负。**这本身不是否决低胜率大赔率策略的理由**：尾部盈利可能就是策略来源。它提醒我们，需要更多独立事件来验证尾部出现概率、报价可成交性和盈利能否持续；不能假设历史的大赢家一定会再次出现。

主方案观察到的最大连续负收益交易日数，SPY 同跌组为 2、未跌到阈值组为 8；QQQ 对应也是 2 和 8。没有完整结果或均值为零的日期会中断观察到的负日连续段，缺失日期完整保存。因此这些数字不是交易连败概率、资金归零概率或账户回撤。

## 全部成本与参数，不挑最高收益

每个基准×分组×成本有 12 种结构（两急跌阈值、两种进入规则、三持有期限）。以下只统计均值为正的结构数量；正数不等于检验通过。全部 216 组参数结果，包括未知组，按预定顺序见 `all-variant-table.csv` 和 `summary.json`。

| 基准 | 分组 | 每边成本 bp | 事件均值为正/结构数 | 日期均值为正/结构数 |
|---|---|---:|---:|---:|
{chr(10).join(costs)}

## 月度与半年描述

没有完整事件的切片收益显示空缺，不填零；SPY 同跌组下半年没有入选样本，不能声称跨半年稳健。10 月只截至 6 日。H2 已受此前 2026 年研究影响，不能称未见测试集。

| 基准 | 分组 | 时段 | 完整事件 | 事件胜率 | 事件平均净收益 | 日期等权净收益 |
|---|---|---|---:|---:|---:|---:|
{chr(10).join(monthly)}

## 验证、局限和后续决定

全部输入复用前阶段已核验的 7,259,432 根分钟线；本阶段新增行情请求为 0。独立审计重建 19,519 个样本分类及 38,274 个股票×基准分类，核对父账本与新派生账本没有改写收益；统计、全部盈亏分布、尾部、日期比较和区间再独立复算。具体通过情况与代码哈希随审计文件发布。

保留 SPY 同跌主样本作为**未验证观察线索**，不启用策略。下一研究提案是完整检查这 11 个日期以及同规则下当日的所有入选、急跌、未反弹和未知样本，研究时间集中度与当时可知背景，再制定未来未见日期的观察方案。该下一提案尚未登记或运行，不根据结果删亏损或调出赢家。

现有目录不是历史时点股票全集，存有幸存者与分类偏差；数据可能修订；完整分钟要求造成可观测性筛选；三对照仅按流动性匹配，不能控制所有行业、市场敏感度或消息差异；同日双组要求及股票各自见底时刻又增加选择。小样本描述性区间并不能消除这些问题。

末日期权真实历史 bid/ask、数量、时间和执行证据仍缺，不能把本轮股票收益乘个杠杆当期权收益。没有推算 $500 到 $10,000 的路径或概率，没有下单、重置账户、购买数据、部署定时器或改动独立的 $100,000 主账户。用户接受亏完一轮的目标保留，但以后任何资金模型必须累计全部失败、剩余资金和重新注资。
'''
    (ROOT/'REPORT.zh-CN.md').write_text(report)
    save('peer-exchange.json',{'study':decision['study'],'to':'Claude shared research exchange','parent_commit':'634a93c65e1adec6c5908f9ad249c5b8e6224ba8','user_preference':'No minimum40percent winrate. Evaluate complete netpayoff and tail probability; low winrate itself not failure.','result_summary':{'SPY_down_primary_complete_events':dd['n'],'SPY_down_outcome_dates':11,'SPY_down_period_scope':'AllclassifiedcasesandreturnsinH1; H2selectedcases0, notzeroH2return','SPY_down_event_mean_net':dd['mean'],'SPY_down_payoff_ratio':dd['payoff_ratio_average_win_to_average_absolute_loss'],'SPY_down_profit_factor':dd['profit_factor_total_positive_to_total_absolute_loss'],'SPY_same_date_primary_contrast':ct['SPY']['bootstrap'],'QQQ_same_date_primary_contrast':ct['QQQ']['bootstrap']},'limits':'Stockproxy returns only. Allprimaryintervals crosszero; smallcommon-date sample; no0DTEedge or500wealth claim.','request':'Ifproposing a lowwin/largepayoff option strategy, share allfailed andunknown candidates plus contemporaneously executable quotes; do not reject a tailstrategy simply because deletinglargestwins removesprofit.','next':decision['next_research']})
    print(json.dumps({'report_bytes':(ROOT/'REPORT.zh-CN.md').stat().st_size,'all_variant_rows':len(allrows)}))
if __name__=='__main__':main()
