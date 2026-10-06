# 前10项原始新闻核验的继承审计

14/14 项通过；这是既有记录的离线复核，本阶段新增原始来源请求为0。

最关键映射：ALT 为监管资格公告，满足已冻结新闻类别与时窗；SIDU 虽确认董事任命的时间，但类别不符，不能将 `news_status=confirmed` 当作 `pass_registered_news_gate=true`。

| 日期 | 代码 | 新闻状态 | 时间窗口确认 | 冻结新闻门通过 |
| --- | --- | --- | --- | --- |
| 2026-01-02 | BIDU | ambiguous | False | False |
| 2026-01-02 | SLS | missing | False | False |
| 2026-01-02 | SMX | ambiguous | False | False |
| 2026-01-02 | SOC | missing | False | False |
| 2026-01-05 | ALT | confirmed | True | True |
| 2026-01-05 | SIDU | confirmed | True | False |
| 2026-01-05 | SMR | missing | False | False |
| 2026-01-06 | AEVA | ambiguous | False | False |
| 2026-01-06 | ARVN | missing | False | False |
| 2026-01-06 | ARWR | ambiguous | False | False |

首3项与原始审计字段逐一相等；10项顺序与初始全体候选按日期/代码排序的前10一致。29份有哈希的原始来源均匹配，1次未获取正文的访问失败保留。ALT原文的07:30和07:31:02两个时刻都在窗口内；其62秒差异未掩盖。

BIDU保留来源时间冲突；SMX保留首次公开时刻不明；AEVA不猜未标时区的时刻；ARWR不把前一天的加拿大批准改写为次日新事件。SIDU董事任命不等于合同。

这10项全部来自既有阶段，不能计为本阶段10项新发现；当时为3项继承、7项新增。没有计算收益、发单或修改冻结策略。
