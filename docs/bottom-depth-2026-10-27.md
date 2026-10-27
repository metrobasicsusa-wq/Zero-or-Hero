# 深度抄底规则：给本地会话的任务说明（2026-10-27）

用户 10-27：「写到 github 上 我让 ultracode 本地看下」。这页把今天五个回测的结论、要做的账本规则、要改的代码和验收标准写清楚，本地会话读完就能动手。先读 `CLAUDE.md`（中文回复、钥匙只在 GitHub Secrets、公开仓库不放账户号 / 订单号、改实盘交易代码的推送在收盘后、推送前跑测试、不用 `--no-verify`）。

## 1. 结论（哪条规则站得住）

今天的回测（`research/2026-10-27-bottom-2m.md`、`-bottom-semi.md`、`-bottom-spread.md`、`-bottom-spread25.md`、`-bottom-ledger.md`；昨天的 `2026-10-26-bottom*.md`）只有一个变量有效：**深度**。连跌几天、第一根阳线、当天反弹追龙头都没有优势。

| 规则 | 笔数 | 拿 63 天 | 去掉最好 3 笔 | 中位数 | 赚钱 | 各年份 | 来源 |
|---|---|---|---|---|---|---|---|
| 四只 ETF 首次收在 20 日高点下 8% → 75–130 天月度平值看涨 | 29 | +190% | +137% | +166% | 76% | 2024 +55%、2025 +98%、2026 +417% | bottom-2m |
| 同上 · 价外 5%（拿 42 天） | 26 | +193% | +128% | +151% | 73% | +164 / +79 / +370% | bottom-2m |
| 14 只半导体股 首次跌 20% → 同样的平值看涨 | 76 | +173% | +133% | +130% | 63% | +11 / +222 / +249% | bottom-semi |
| 四只 ETF 跌 8% → 平值买、高 $5 卖的价差，到 $4 就卖 | 28 | +64% | +37% | +28% | 86% | +120 / +35 / +78% | bottom-spread |
| 对照：ETF 每第 10 天随便买平值拿 63 天 | 73 | +82% | +66% | +88% | 71% | +11 / +62 / +215% | bottom-2m |
| 对照：半导体股每第 20 天随便买 | 240 | +132% | +118% | +69% | 61% | +15 / +151 / +298% | bottom-semi |

价格层（2017 起，不算成本）：ETF 跌 8% 后拿 63 天 +9.2%（随便买 +4.7%），3 倍基金 +29%（SOXL +52%），除 2022 每年正；单股跌 8% ≈ 随便买，跌 20% 才有差别（两个月 +11.9%、中位数 +10%、73% 赚、每年正）。真正的最低点在信号后 1–3 周、再低 4–9%，所以要拿两三个月，短期权会死在第 4 天。

限定词：
- 深度是离**最近 20 个交易日**的最高收盘，不是离 52 周高点（后者比随便买还差，是熊市的刀）。
- 每档只在**第一次**跌破时算一次信号；同一只拿着时不再买（一次下跌只押一次）。
- 出场拿到第 63 个交易日或到期，不设止盈、不设回撤卖（3 倍止盈把大赢家砍掉：+86% 对 +190%）。
- 押满的账本死于一串连亏（2025 年 1–3 月四次归零），押一半才活得到后面的大涨（`bottom-ledger.md`）。
- 期权数据只有 2024-02 起、两年八个月、多是涨势；2024 这种平年 ETF 版 +55%、单股版只 +11%。

$1,000 / $500 开局按日期重放（`bottom-ledger.md`）：裸买 SMH 平值一张 $2,300 买不起；唯一买得起、活下来又赚的是 ETF 的 $5 价差：$500 → $3,192、$1,000 → $6,592（6 笔、回撤 −18%，打乱重抽中位数 $3,522 / $7,423，没有归零）。

## 2. 要做的账本（等用户点头再上实盘；先做演算版没问题）

### 2.1 `depth-2000`（实盘，$2,000）
- 标的顺序：SMH、QQQ、IWM、SPY（四只 ETF）。
- 信号：15:50 检查，`close_today / max(close[-20:] 含今天) - 1 <= -0.08` 且昨天不满足（昨天对它前 20 天的高点 > -8%）。用当时的现价代替今天收盘（和 `Streak` 一样）。
- 合约：最近的**月度**到期（第三个周五；遇假日是周四）且距今 75–130 天，平值看涨（`offset` 0.0）；买不起一张就把行权价往价外挪，挪到一张不超过账本可用的钱为止，最多价外 10%，再买不起就跳过并记录（`skipped: 买不起`）。
- 数量：押一半（`fraction` 0.5）；一半买不起一张时允许用全部现金买一张（现有 `enter()` 的「押上账本 1 张」逻辑）。
- 出场：第 63 个交易日 15:30，或到期日 15:30（先到为准）；`trail: false`、不 `take`。
- 同一只拿着时不再买；别的 ETF 触发可以再买（四只各最多一张）。
- 演算分身 `depth-plan-2000`：同样四只；`depth-plan-500`：四只，平值买不起就价外 5% / 10%。

### 2.2 `depth-semi-plan-2000`（只演算）
- 14 只：NVDA、AVGO、AMD、MU、TSM、ASML、AMAT、LRCX、KLAC、QCOM、INTC、MRVL、TXN、ARM；门槛 **20%**；其余同 2.1。
- 一天几只同时触发按上面的顺序，账本钱够就都记（演算不受现金限制也行，但要记「买不起」的比例）。

### 2.3 `depth-letf-plan`（只演算，正股）
- SMH 跌 8% → SOXL；QQQ → TQQQ；SPY → SPXL；IWM → TNA；买正股拿 63 个交易日；这是「稳」线的参考（2017 起 $1,000 → $32,687，回撤 −60%）。

### 2.4 第二阶段：`depth-spread-500`（$500，ETF 的 $5 价差）
- 买平值看涨、卖高 $5 的看涨，同一到期；价差到 $4（宽度八成）就卖，否则到期。
- 需要两腿下单：Alpaca 多腿期权单（`order_class: "mleg"`）和演算层的两腿估值（长腿收盘 − 短腿收盘，到期按内在价值封顶 $5）。现有 `hero/sleeves.py` 只会买卖单腿，先做演算（用 `research_bottom_spread.spread_path` 的口径），实盘两腿单放到之后。

### 2.5 现有的 `bottom-2000` / `green-2000`（连跌第 3 天 / 第一根阳线）
- 回测说天数不是信号；但「连跌第 3 天买 20–45 天平值拿 10 天」是周转最快又每年正的一条（两周一轮、去掉最好 3 笔 +16%）。保留，不动；用户要停再停。

## 3. 现在的信号（10-27 收盘后扫描 118 只，按「第一次跌破」记）

| 第一次跌破 | 名字 | 对信号日收盘 |
|---|---|---|
| 10-08 / 10-09 | SOXL（8%）、ARM | −29%、−15% |
| 10-12 | SNDK、STX | −12%、−11% |
| 10-14 | SMH（8%）、TQQQ（8%）、INTC、LITE、COHR、CRWV、NBIS、ALAB、WDC、SMCI、APP | SMH −1%、TQQQ −4%、COHR −10%、WDC +5%，其余 −2% 到 −9% |
| 10-15 / 10-16 | AMAT、LRCX、HPE、GLW | −8%、−13%、+4%、−4% |
| 10-20 | KLAC | −5% |
| 10-22 | AMD、MU、MRVL、DELL、ANET、VRT、CRDO、BE、HOOD、NOK、XLK（8%） | +10%、+8%、+9%、+11%、+9%、+7%、+3%、0、−2%、+13%、+4% |
| 10-23 | CAT、GEV | +4%、+10% |

按规则这些都是 10 月中下旬就该买进、拿到 12 月中的单子；10-27 没有新的第一次跌破。新账本从下一次信号开始，不追这些。

## 4. 要改的代码

1. `hero/sleeve_rules.py`：新规则类 `Depth`（可以从 `Streak` 抄骨架：`entries()` 用 `history(run, unds, days=40)`、`snaps`、`spot_open`、`option_pick(run, und, "C", spot, offset, gte, lte)`、`too_wide`、`budget`、`calm`）。
   - 条件：`spot / max(cs[-20:] + [spot]) - 1 <= -level` 且 `cs[-1] / max(cs[-21:-1] + [cs[-1]]) - 1 > -level`（`level` 来自 spec，ETF 0.08、单股 0.20）。
   - 到期：算出 75–130 天内的第三个周五（参考 `hero/research_bottom_2m.py: monthlies()`），`option_pick` 的 `gte` / `lte` 给同一周（周四到周五），取最近的一档；没有就取下一档。
   - 买不起时往价外挪：按 `offset` 0.0 → 0.05 → 0.10 依次试，直到一张 ≤ 预算。
   - pick 字典和 `Streak` 一样（`sym / asset / und / ask / expiry / exit_day / exit_at / trail: False`），`exit_day` 用 `held_out(run, exp, 63)`，到期先到就到期日 15:30。
   - `why` 文字写清：「深度抄底：{und} 首次收在 20 日高点下 {x}%（高点 {h} → 现价 {p}）；买 {exp} 到期平值看涨 {k}（{dte} 天）；拿到第 63 个交易日或到期，不止盈」。
   - 注册：`RULES["depth"] = Depth()`。
2. `config/s500.json` `sleeves.list`：加 2.1–2.3 的条目，`kind: "depth"`，`spec` 键：`unds`、`level`、`dte: [75, 130]`、`hold: 63`、`offset: 0.0`、`fraction: 0.5`、`max_spread: 0.1`、`plan_only`；`name` / `note` 写清来源（研究文件名）。不要用 `json.dumps` 重写整个文件（会产生 800 行 diff），只在列表末尾插入条目。
3. `hero/notify.py`：`EVENT_KINDS` 加 `"depth": "深度抄底"`；`direction()` 的正则已经认 `-(500|1000|2000)$`，`depth-letf-plan` 这种没有金额后缀的 id 要么加 `-2000`，要么改正则。
4. `hero/feature_pages.py`：`TITLE` 加 `"depth": "深度抄底（跌 8% / 20% 买三个月）"`，分组「抄底和跳空」加 `"depth"`；`SIZE` 正则同上。
5. `docs/sleeves-plan.md`：加一节「深度抄底」，写规则、回测数字、账本 id。
6. `tests/test_hero.py`：新 `class SleeveDepth(unittest.TestCase)`：(a) 第一次跌破才触发、第二天不再触发；(b) 拿着时不再买；(c) 到期窗口选最近的月度；(d) 买不起往价外挪、挪到 10% 还买不起就跳过；(e) 出场日 = 第 63 个交易日或到期日。参考现有 `Streak` 的测试写法（搜 `test_bottom`）。
7. 跑 `python3 -m unittest tests.test_hero`（约 300 个测试）；`.githooks/pre-push` 推送时会再跑一遍。

## 5. 验收

- 用 2026-10-14（SMH 收 564.74，20 日高 632.5）和 2026-10-22（AMD 收 507.93）两天的历史数据跑 `Depth.entries()`，能各给出一张 2027-01-15 到期的平值看涨；10-15 不再给 SMH。
- `plan_only` 账本在 `journal-s500/sleeves_plan.json` 里能记下 `why`、`expiry`、`exit_day`。
- 推送：研究 / 文档随时可推；`hero/sleeve_rules.py`、`config/s500.json` 这类实盘交易代码只在 16:00 ET 收盘后推（或用户同意）。

## 6. 本地操作

```bash
git clone https://github.com/metrobasicsusa-wq/Zero-or-Hero && cd Zero-or-Hero
git checkout claude/cloud-paper-trading-ivwxqk && git pull
pip install -r requirements.txt
git checkout -b depth-ledger          # 新分支开发，完成后合回 claude/cloud-paper-trading-ivwxqk
python3 -m unittest tests.test_hero    # 改之前先确认全绿
```

云端会话（这一份）每天的定时任务还在跑，不要在本地改 `.claude/` 的定时配置；两边同时改 `config/s500.json` 时先 `git pull --rebase`。
