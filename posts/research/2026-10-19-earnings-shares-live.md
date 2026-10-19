# 已上线：财报账本改成两倍 ETF / 正股（10-20 起）

## 改成了什么

- 财报账本（earn-500、earn-1000）：财报前一天 15:40，从我们股票池里挑期权定价波动最大的 3 只，买它的两倍做多 ETF；没有两倍 ETF 或 Alpaca 不能交易就买正股。第二天 09:35 卖。
- 接力账本（relay-500、relay-1000）第一段：本周财报里 126 日动量最强的一只，同样买两倍 ETF 或正股、全仓，第二天 09:35 卖；账本到起点 3 倍后转 SOXL（不变）。
- 财报日期没核实的（两个日历对不上、或 Yahoo 的公司确认日期不同）不买。
- 所有账本：会拿过该股财报的单股期权一律不买（狙击、周内单等）。主账户本来就会在财报前一天平掉跨财报的期权。

## 有两倍 ETF 的（股票池里 25 只）

- NVDA→NVDL、TSLA→TSLL、AAPL→AAPU、AMZN→AMZU、MSFT→MSFU、META→METU、GOOGL→GGLL、AMD→AMDL
- MU→MUU、AVGO→AVL、NFLX→NFXL、TSM→TSMX、PLTR→PTIR、MSTR→MSTU、SMCI→SMCX、ARM→ARMG
- HOOD→ROBN、UBER→UBRL、LLY→LLYX、APP→APPX、ORCL→ORCX、CRWV→CWVX、NBIS→NBIL、INTC→INTW、NOW→NOWL
- 每只都在 Yahoo 核对过名字和成交量（日均 20 万股以下的太薄，没用）；下单前再查 Alpaca 能不能交易，不能买零股就买整股。

## 这周会怎么跑

- 10-20 15:40：在 ISRG、NFLX（当晚盘后）和 ABT、DHR、NEE、T、TMO（10-21 盘前）里挑 3 只；NFLX 会买 NFXL，其余是正股。
- 接力：本周财报里动量最强的一只，到它财报前一天 15:40 再买。
- 账本现在：earn-500 $211、earn-1000 $345、relay-500 $500、relay-1000 $1,000。

## 风险

- 两倍 ETF 一晚就可能 -30% 以上；回测（同一批 98 只今天的大票，幸存者偏差大）2024-02 以来两倍版最大回撤 67%。
- 只是模拟盘记录，不是建议。
