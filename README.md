# Zero-or-Hero（Claude 方）

自主进化的模拟交易机器人，在 Alpaca **模拟盘**上交易美股和期权。本项目里 Claude 和 Codex 各自独立开发、独立演化，比谁的收益更好。

> 安全：`hero/alpaca.py` 硬性拒绝任何非 `paper` 的接口地址，代码不可能碰到实盘账户。

## 策略

| 模块 | 逻辑 |
|---|---|
| 股票 `hero/strategies/momentum.py` | 在股票池里按 N 日动量排名，只选价格在均线之上、RSI 不过热的标的，等权持有前 `top_n` 名。SPY 跌破 200 日均线（熊市）时，总仓位降到 30% |
| 期权 `hero/strategies/options.py` | 牛市：买入最强趋势股的看涨期权；熊市：买入 SPY 看跌期权。选 30–60 天到期、delta 最接近 0.5 的合约，限价挂在买卖中间价。盈利 +60% 止盈，亏损 −40% 止损，距到期 ≤7 天平仓 |
| 风控 | 单只标的最多占 20%，期权总预算 10%，当日亏损超过 3% 停止开新仓（平仓不受影响），同一标的有挂单时不重复下单 |

参数都在 `config/strategy.json`。

## 自我进化

每周六运行 `python -m hero evolve`：
1. 拉取约 3 年日线，在参数网格（动量周期 × 均线周期 × 持仓数）里做回测。
2. 每组参数分别在训练段和后面的验证段计算 Sharpe，**取两者中较小的值**作为得分，防止过拟合。
3. 最优参数的得分比现有参数至少高 0.15，才采用新参数，`generation` 加 1。
4. 每次决定（包括实盘收益）都追加到 `journal/evolution.md`。

## 运行

```bash
pip install -r requirements.txt
export ALPACA_API_KEY=...  ALPACA_SECRET_KEY=...   # Alpaca 模拟盘的 Key

python -m hero status            # 账户和持仓
python -m hero run --dry-run     # 跑一轮，只记录不下单
python -m hero run               # 跑一轮（休市时自动跳过，加 --force 可强制）
python -m hero backtest          # 回测当前参数
python -m hero evolve            # 参数进化
python -m unittest discover -s tests
```

## 自动化（GitHub Actions）

- `trade.yml`：美股交易时段每 10 分钟跑一轮（由 cron-job.org 调用 GitHub API 触发，GitHub 自带的定时器太不可靠）（期权止盈止损检查更及时），交易记录自动提交回仓库
- `evolve.yml`：每周六进化一次参数
- `patrol.yml`：交易日 3 次巡检（数据是否过期、当日亏损、期权止损线、挂单卡住），有问题就在「⚠️ 巡检告警」Issue 里留言
- `review.yml`：每个交易日美东 16:55 生成盘后复盘（规则自动生成要点 + 当日数据），保存到 `journal/reviews/` 并开 Issue
- `test.yml`：每次推送都跑测试
- `pages.yml`：每轮交易后重新生成监控面板，发布到 GitHub Pages（需要在 **Settings → Pages** 把 Source 设为 **GitHub Actions**）

需要在仓库 **Settings → Secrets and variables → Actions** 里添加 `ALPACA_API_KEY` 和 `ALPACA_SECRET_KEY`。

定时由 cron-job.org 负责（美东时间），通过 GitHub API 触发 workflow_dispatch：

| 任务 | 时间 |
|---|---|
| trade | 工作日 9:00–16:50，每 10 分钟 |
| patrol | 工作日 10:47、12:47、14:47 |
| review | 工作日 16:55 |
| evolve | 周六 10:17 |

## 监控面板

https://metrobasicsusa-wq.github.io/Zero-or-Hero/

显示账户净值、累计收益和 SPY 的对比曲线、当前持仓、挂单、交易记录、当前参数和进化日志。本地生成：`python -m hero dashboard`，输出到 `site/index.html`。

## 记录

- `journal/trades.jsonl`：每次下单、平仓、目标仓位和停止开仓事件
- `journal/equity.csv`：每日净值，以及当时运行的是哪一代参数
- `journal/evolution.md`：进化日志
- `journal/snapshot.json`：最近一次运行时的账户、持仓和挂单
