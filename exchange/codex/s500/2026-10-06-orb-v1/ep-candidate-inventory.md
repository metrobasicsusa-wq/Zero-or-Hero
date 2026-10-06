# EP全年初始候选清单

这是 EP-NEWS-500-v1 的数据准备，不是12组策略收益回测。

- 期间：2026-01-02 至2026-10-05，共 190 个交易日。
- 全量扫描 6633 个原始代码，未截取流动性前500或存储8股。
- 初始跳空候选 1080 条；剔除当前已知ETF/测试标记后股票候选 1079 条、723 个代码。
- 全部事件状态初始为 missing；不把缺资料当无催化。
- 仅前20个完整交易日成交额/前收盘、拆股和当天日线开盘引用价用于筛选；没有使用当天最终成交量或后续涨跌。

规则：前20日中位日成交额至少1000万美元、拆股调整前收盘至少2美元、开盘跳空至少10%。开盘价及前日量仍须与分钟常规时段核验，10:00 的30分钟量门槛还未评估。

## 按时间选定的首3例新闻核验样本

| 日期 | 代码 | 当前事件状态 |
|---|---|---|
| 2026-01-02 | BIDU | ambiguous |
| 2026-01-02 | SLS | missing |
| 2026-01-02 | SMX | ambiguous |

三例有限核验后的状态：{"ambiguous": 2, "missing": 1077}；通过注册新闻门槛 0 例。详细时间冲突、事件分类与失败访问见 ep-event-audit.json。BIDU 拆分上市计划、SMX 产品市场扩展不自动改写为财报、合同或监管批准；SLS 缺证据不是无催化。

## 覆盖限制

- Current active/inactive directory and current official classifications are not point-in-time; omitted historical listings and symbol reuse remain possible.
- Daily opening/closing values and volumes have not been reconciled to regular-session minute bars; this is a preliminary candidate index, not a tradable opening screen.
- Corporate action process-date range may omit late-processed historical events; unmapped actions and timestamp availability remain unresolved.
- No same-day volume, high, low, close or later returns used to accept/rank candidates. No 10:00 volume gate, breakout, fills or EP12 portfolio returns have been evaluated.
- All candidate event statuses initialize missing. Missing primary news evidence does not mean there was no catalyst.
- The already inspected2026 period is exploratory; no untouched holdout claim.
