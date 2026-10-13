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
  "notes": ["盘前看过的其他候选和不选的原因"],
  "generated_at": "2026-10-14T09:05:00-04:00"
}
```

规则（hero/sleeve_rules.py `Sniper`）：09:30 到 10:00 之间，只要股价没有逆着选的方向走，就买最近到期（有当天到期就用当天的）、
delta 约 0.10 的期权，账本一半的钱；翻倍后从最高价回撤 40% 卖，否则到期日 15:30 卖。

怎么选（参考 claude-b 的 journal/bias/README.md 和我们 10-13 的回测）：
- 只选大型股、有当天或几天内到期期权的：META、NVDA、TSLA、AAPL、AMZN、MSFT、GOOGL、AVGO、AMD、MU 等。
- 要有新的、非财报的消息，而且还没被盘前大幅跳空吃掉（跳空超过约 4% 通常已经反映）：产品数据、分析师上调或大幅上调目标价、
  监管或法律消息、大合同、整个行业的新闻。
- 同一条新闻有好几家公司受影响时，选跳空最小、消息分量最大的那家。
- 机械规则（数新闻条数、按跳空大小、分析师评级本身）回测都亏（research/2026-10-13-open-catalyst.md、2026-10-13-analyst.md），
  优势只可能来自判断，所以没有把握就写 null。
