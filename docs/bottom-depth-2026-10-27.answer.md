# 深度抄底规则：本地会话的回答（2026-10-29）

任务页：`docs/bottom-depth-2026-10-27.md`。分支 `depth-ledger`，基于 `ladder`（复用那里的 `hero/signals.py` `depth()` 和 Ladder 的月度到期选法；合并时请先合 `ladder`）。用户 10-29「看看有什么新任务研究」时发现这页还没人做，按「看任务」的约定做了。

## 做了什么

1. `hero/sleeve_rules.py`：新规则 `Depth`，`RULES["depth"]`。把 Ladder 的「75–130 天内最近月度 + 合约链」抽成模块级 `monthly_calls()` 和 `strike_at()`，`Ladder.depth_legs` 变成薄包装，行为不变（测试里断言）。
   - 15:45–15:58 检查；信号用 `hero.signals.depth`（21 根收盘 + 现价，第一次收在 20 日高点下 `level`，昨天还在线上）。
   - 剔除本账本拿着的标的（按 ticket 的 `und`）；同一天几只都触发就都给，按 `unds` 顺序。
   - 平值买不起按 `shifts` 0 → 5% → 10% 往价外挪；都买不起记 `sleeve_skip`「买不起」（列出每档一张多少钱）；窗口里没有月度到期也记 skip。
   - 每只最多一张（任务页 2.1）：pick 的钱是一半现金，封顶一张；后面触发的标的按**剩下的钱**判断买不买得起、要不要挪档（校验时发现第一版没扣前一只的钱，已改并加测试）。
   - 出场 `held_out(run, exp, 63)`：第 63 个工作日 15:50，到期先到就到期日 15:30；`trail: False`、不设 take。
   - `shares` 模式给 3 倍基金演算：SMH→SOXL、QQQ→TQQQ、SPY→SPXL、IWM→TNA，正股 pick，拿 63 个工作日。
   - `why`：「深度抄底：{und} 首次收在 20 日高点下 {x}%（线 {level}，高点 {h} → 现价 {p}）；买 {exp} 到期平值看涨 {k}（{dte} 天，离现价 …，买价 / 卖价）；拿到第 63 个交易日或到期（{日期 时刻}），不止盈」。
2. `config/s500.json` `sleeves.list` 末尾文本插入 4 条，全部 `plan_only: true`，diff 只有插入的 12 行：
   `depth-plan-2000`（四只 ETF，8%）、`depth-plan-500`（同上，平值买不起挪 5% / 10%）、`depth-semi-plan-2000`（14 只半导体股，20%）、`depth-letf-plan-2000`（3 倍基金正股）。
3. `hero/notify.py`：账本中文名在 `SLEEVE`（任务页写的 `EVENT_KINDS` 不在这个文件里），加 `depth` / `depth-semi` / `depth-letf`；`direction()` 的正则不用改，所有 id 都带金额后缀（`depth-letf-plan` 改名 `depth-letf-plan-2000`）。
4. `hero/feature_pages.py`：`TITLE` 三条、「抄底和跳空」分组、`SHOW_SPEC` 加 `level / dte / max_spread / shares`；生成的 depth.html / depth-semi.html / depth-letf.html 都显示「只演算（不下单）」。
5. `docs/sleeves-plan.md`：「10-27：深度抄底、$2,000 账本」一节。
6. `tests/test_hero.py` `class SleeveDepth`，10 个测试：(a) 首次跌破触发、第二天不触发、一天一次；(b) 拿着不再买、同日两只都给；(c) 窗口内最近月度（不是周度；没挂牌取下一档；窗口随日期走；没有月度记 skip；Ladder 不变）；(d) 买不起 0 → 5% → 10% 再跳过；(e) 出场第 63 个工作日或到期；验收用例；LETF 正股；真 `Runner.plan()` 写演算账本；id 解析；同一天第二只按剩余现金挪档。
7. 全部测试 332 个全绿；推送钩子会再跑。

## 验收（第 5 节）

本地没有 Alpaca 钥匙，用合成 K 线复现任务页的数字：SMH 20 日高 632.5、10-13 收 590、10-14 15:50 现价 564.74 → `SMH2027-01-15C565`，出场 2027-01-11 15:50；10-15 不再给 SMH；AMD 10-22 507.93（20% 线）→ `AMD2027-01-15C510`，到期日 15:30。演算账本能记下 `why / expiry / exit_day / trail`，一半 $1,000 买不起一张时押上账本买 1 张。

## 和任务页不同的地方

- **只有演算账本，没有实盘 `depth-2000`**：任务页说等用户点头。要上实盘：复制 `depth-plan-2000` 去掉 `plan_only`，但先定两件事：(1) 实盘 `enter()` 不读 pick 的 `each`，同一天几只触发会平分一半现金（演算是每只各一张），要在 `hero/sleeves.py` `enter()` 加一行读 `pk["each"]`；(2) 单股拿 63 天会被财报守门 `report_inside` 拦住，`depth-semi` 上实盘前要加开关。
- **出场时刻**：任务页 2.1 写第 63 个交易日 15:30，4.1 指定 `held_out`（15:50）；按 4.1。`business_days_after` 只跳周末不跳交易所假日，10 月下旬进场跨感恩节 / 圣诞 / 元旦，实际约第 59 个交易日卖，比回测早几天（Streak / Ladder 同口径）。
- **2.2「演算不受现金限制也行」做不到**：演算和实盘一样走 `units()`，买不起只能靠 `sleeve_skip` 事后统计比例。
- **2.4 `depth-spread-500`（$5 价差）没做**：按任务页放第二阶段；`ladder-plan-500` 阶段一已在演算同样的价差。
- 买得起的口径是 `ask × 100 ≤ 现金`（和 Streak 一样），引擎实际挂 `ask + buy_pad`，差 $1 以内的边界会在引擎处跳掉。

## 推送

改了 `hero/sleeve_rules.py` 和 `config/s500.json`，按 CLAUDE.md 收盘后（16:00 ET 后）或用户同意再推；不直接推云端的交易分支。
