# 开盘狙击的盘前选股（sniper-500 / sniper-1000）

每个交易日开盘前（约 08:40–09:20 ET），Claude 的盘前任务在这里写 `YYYY-MM-DD.json`（美东日期），推送到
`claude/cloud-paper-trading-ivwxqk`，09:30 的交易轮会读它。开盘后不要再改这个文件：它是这个方向的成绩记录。

```json
{
  "date": "2026-10-14",
  "sniper": {                       // 或 null（大多数日子没有够格的消息就写 null）
    "symbol": "META",
    "direction": "call",            // "call" 或 "put"
    "catalyst": "一句话：什么新消息、为什么还没反映在价格里",
    "confidence": 0.4
  },
  "claude_b_pick": "META call",     // claude-b 当天选的（我们不选同一只）
  "notes": ["盘前看过的其他候选和不选的原因"],
  "generated_at": "2026-10-14T09:05:00-04:00"
}
```

规则（hero/sleeve_rules.py `Sniper`）：09:30 到 10:00 之间，只要股价没有逆着选的方向走，就买最近到期（有当天到期就用当天的）、
delta 约 0.10 的期权，账本一半的钱；翻倍后从最高价回撤 40% 卖，否则到期日 15:30 卖。

怎么选（用户 10-13：要和 claude-b 不一样，否则两边结果一样没意义）：
- **股票池和 claude-b 错开**：它选大型股（META、NVDA、TSLA、AAPL、AMZN、MSFT、GOOGL、AVGO、AMD、MU 这类）；我们选高波动的中盘股
  和消息的「二线受益股 / 另一边」，例如 MSTR、COIN、HOOD、PLTR、SMCI、ARM、APP、IONQ、RGTI、RKLB、ASTS、OKLO、CRWV、NBIS、
  IREN、SOFI、AFRM、UPST、SHOP、MRVL、CRDO、ALAB、VRT、ANET、DELL、SNOW、NET、CRWD、DDOG（要有一周内到期的期权）。
- **不撞车**：先读 claude-b 当天的 `journal/bias/YYYY-MM-DD.json`（公开仓库 Cao-YMGG/zero-or-hero，只当参考资料），
  它选了哪只，我们当天就不选那只；把它的选择记在 `claude_b_pick`。
- 要有新的、非财报的消息，而且还没被盘前跳空吃掉（中盘股波动大，跳空超过约 6% 通常已经反映）：产品 / 合同 / 监管 / 分析师大幅上调、
  或者某条大消息的二线受益股（例：大厂宣布新数据中心 → 供电、光模块、液冷的小公司；加密政策 → COIN、HOOD、MSTR）。
- 机械规则（数新闻条数、按跳空大小、分析师评级本身）回测都亏（research/2026-10-13-open-catalyst.md、2026-10-13-analyst.md），
  优势只可能来自判断，所以没有把握就写 null。

文件里另加一个字段：`"claude_b_pick": "META call"`（或 null / "未找到"），记录对方当天的选择，方便事后对比两边。
