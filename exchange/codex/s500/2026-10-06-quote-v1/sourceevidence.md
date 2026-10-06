# 股票历史报价语义核对（2026-10-06）

本阶段结论：可分析历史报价状态、价格和时间戳；仍不能把显示尺寸视为已确认可成交股数，也不能把报价当实际成交。

## 分析器应冻结的规则

- **bs/as**：Preserve raw numeric values with unit_status=unknown_alpaca_normalization. Never multiply by100. Do not compare whole-share quantity to displayed size as a verified capacity test. Any raw-number ratio must be explicitly unit-unverified, preferably omit.
- **t**：Parse and preserve integer nanoseconds, use only t<=decision event cutoff. Name age API reported/event timestamp age; never call it client receipt age or verified historical availability.
- **same timestamp**：At latest eligible timestamp, identical normalized quote states may be deduplicated diagnostically. Distinct prices/sizes/venues/conditions produce ambiguous state; do not choose favorable row or assume page order is market sequence.
- **feed and condition**：Request SIP explicitly. Use current per-tapequote mapping; require exactly the preregisteredR-only set and retain unknown/closed/manual/nonfirm/etc as excluded evidence. Noncrossed positive prices andR alone do not establish executable state.
- **quote validity**：At decisiontime inspect latest observed state, including invalid/crossed/zero/non-R updates. Do not silently fall back to a stale earlier valid quote as if newer disqualifying update never happened.
- **asof and identity**：Record requested asof/default and retrievaldate. Previous symbol mapping/current-universe rows remain non-PIT and may represent aliases; raw price adjustment setting does not disable mapping.
- **full market availability**：Quote access/positive NBBO is a data-readiness diagnostic. Without receive time, halt/LULD/queue/route and fill records, no successful order/fill/capacity assertion.

## 关键原始证据

### A1 — Alpaca Python SDK Quote model

https://raw.githubusercontent.com/alpacahq/alpaca-py/master/alpaca/data/models/quotes.py

版本/时间：master observed2026-10-06; repository HEAD5049448ad218d8f89d3232c1d5bd891d032355b0。

> timestamp (datetime): The time of submission of the quote.

> bid_size (float): The size of the quote bid.

- Quote是Level1 bid/ask配对，含价格、尺寸、场所、条件、tape；源码称timestamp为提交时间。

- 限制：未说明size股数或roundlot单位；Quote类型亦服务多个资产类别，float类型不能证明股票尺寸单位。
- 限制：未给客户端接收时间字段，未指明t映射SIP输出时钟还是交易所publication时钟。

### A2 — Alpaca Python SDK StockQuotesRequest

https://raw.githubusercontent.com/alpacahq/alpaca-py/5049448ad218d8f89d3232c1d5bd891d032355b0/alpaca/data/requests.py

版本/时间：commit5049448ad218d8f89d3232c1d5bd891d032355b0 (2026-10-06)。

> sort (Optional[Sort]): The chronological order of response based on the timestamp. Defaults to ASC.

- 股票历史报价请求支持sort、feed、asof。排序依据timestamp。

- 限制：未承诺相同timestamp多条不同报价之间的先后规则；不能把返回列表位置或分页顺序解释为可验证的市场先后。

### A3 — Alpaca archived stock-pricing historical documentation

https://raw.githubusercontent.com/alpacahq/alpaca-docs/master/content/api-references/market-data-api/stock-pricing-data/historical.md

版本/时间：repository archived; HEAD2080c5e0efbbd812c48eef9209a7a9962de67d2a from2025-04-10。

> A Quote object contains the National Best Bid and Offer (NBBO) data for a security.

> Timestamp in RFC-3339 format with nanosecond precision

- 官方旧文档把股票Quote定义为NBBO，t精度为纳秒，报价条件在c、tape在z。

- 限制：这是2025-04已归档官方文档，早于2025-11上游roundlot变更；旧as=1/bs=4示例不能被当成2026尺寸转换规则。
- 限制：NBBO记录也不是整本深度、每笔订单队列或实际成交承诺。

### A4 — Alpaca archived data OpenAPI

https://raw.githubusercontent.com/alpacahq/alpaca-docs/master/oas/data/openapi.yaml

版本/时间：repository archived; HEAD2080c5e0efbbd812c48eef9209a7a9962de67d2a from2025-04-10。

> The Multi Quotes API provides NBBO quotes for multiple given ticker symbols over a specified time period.

> Returned results are sorted by symbol first then by Quote timestamp.

- 同份QuoteResponse示例存在相同纳秒t、不同ask size的两条记录；排序文字未规定tie次序。
- 条件枚举依tape/来源不同，必须读取对应quote condition映射。

- 限制：示例不是当前事件顺序保证；未发现可用于消除同纳秒冲突的quote sequence字段定义。
- 限制：旧condition例子不能替代本轮抓取的当前A/B/C映射。

### A5 — Alpaca Market Data FAQ

https://docs.alpaca.markets/docs/market-data-faq

版本/时间：undated live document captured2026-10-06T16:35:56Z。

> On the historical endpoints we introduced the asof parameter to link together the data before and after the rename.

> By default, this parameter is "enabled"

- 历史接口默认按asof连接改名前后的代码数据，未填asof不等于禁止历史代码映射。
- FAQ将SIP描述为各美股场所报送的合并数据，IEX是单个交易所。

- 限制：asof是标的代码映射时点，不能当成报价当时已可获得的数据库版本快照。
- 限制：缺证券身份ID时，当前active+inactive全池仍可能含历史别名与重复经济标的，报价对齐不解决幸存者偏差。

### A6 — Alpaca Historical Bars reference: asof parameter

https://docs.alpaca.markets/reference/stockbars

版本/时间：undated live document captured2026-10-06T16:35:57Z。

> The as-of date of the queried stock symbol(s). Format: YYYY-MM-DD. Default: current day.

- 当前官方bars参考页明确asof默认当前日，特殊值-禁用代码映射。结合FAQ和当前StockQuotesRequest确认报价也存在asof机制。

- 限制：本次current stockquotes参考页403，报价接口当前默认精确文字未直接取得；不要把bars参考页冒充quote参考页。

### U1 — UTP Data Feed Services Specification v4.1

https://www.utpplan.com/DOC/UtpBinaryOutputSpec.pdf

版本/时间：September2026。

> Effective November 3, 2025, with the implementation of the Round Lot changes, exchanges are expected to enter Quotation Size represented in the number of actual shares, rounded down to the nearest multiple of the round lot size assigned to the security.

> The sipTime is the time the outbound message is produced by the SIP.

> Firms should not use the Quote Condition field alone to determine trading status for a given issue.

- 上游UQDF自2025-11-03起size用实际股数，并按证券所属roundlot向下取整；不可套用旧的固定×100换算。
- p57列roundlot价格档：0–250美元100股；250.01–1000美元40股；1000.01–10000美元10股；更高1股。规范目录分配值才是依据，不应随即时价格自行重新分档。
- p8区分SIP发出时刻与participant timestamp；p43区分市场中心条件与NBBO条件，NBBO级R是regular两边开放，不表示自动执行资格。
- 交易状态还需要Trading Action消息；p44另有LULD可执行状态字段。

- 限制：这里只证明UTP上游语义，不证明Alpaca JSON的as/bs没有转换；Alpaca单位仍unknown。
- 限制：Alpaca API t对应上游哪一个时钟，当前证据不足。
- 限制：Alpaca常规quote字段中未看到这些完整Trading Action/LULD字段；不能仅凭R和正点差宣布可成交。

### U2 — Nasdaq UTP SIP Odd Lot Quotes & BOLO Implementation FAQ v1.2

https://www.utpplan.com/DOC/Nasdaq%20UTP%20SIP%20Odd%20Lot%20Quotes%20FAQ%20Finalized%20Version%201.2.pdf

版本/时间：May2026。

> Odd Lot Quotes are not protected under Regulation NMS and do not affect round‑lot quotes or the NBBO.

- FAQ说明oddlot/BOLO实施日期2026-04-27；oddlot不属于protected NBBO，BOLO可能与NBBO锁定/交叉。
- 截至本文件，深度oddlot信息延期，而每场所最佳oddlot和BOLO仍实施。

- 限制：这不是Alpaca已在同一个股票quote对象内返回BOLO/oddlot的证明。
- 限制：与2024SEC原始计划日期区分；不拿早期计划冒充最终上游实施日期。

### C1 — Alpaca current quote-condition metadata for tapes A/B/C

https://data.alpaca.markets/v2/stocks/meta/conditions/quote?tape={A|B|C}

版本/时间：live metadata snapshot2026-10-06。

- A/B的R=Regular Market Maker Open；C的R=Regular Two Sided Open。
- 其他代码包括slow/manual、nonfirm、closed、single-sided和auction；本研究选择R-only是保守研究筛选，不是完整市场执行规则。

- 限制：R不是保证firm可执行/未停牌，也不能证明本账户会获成交。
- 限制：quote condition与trade condition不同，代码字母不能跨类型或跨tape解释。

## 未解决项

- CurrentAlpacaAPIquote-size normalization despite officialUTPupstream-share documentation.
- Alpacaquote t precise upstream clock mapping and client-receive/history-revision time.
- Same-timestamp sequence guarantee and sequence identifier not established.
- Whether currentAlpacaquote endpoint includes2026BOLO/oddlot layers or onlyprotectedNBBO is not established.
- Actual security identity aliases, full historical quotecoverage, executable depth and orderfills.

当前Alpaca docs站部分URL返回403，保留全部失败记录。旧Alpaca仓库已归档、最后提交2025-04-10，已明确标注，不能用它替代2026最新API单位说明；此前成功缓存的官方FAQ/reference保留原访问时间与哈希。UTP规范是上游原始来源，不能自动证明Alpaca无转换。完整抓取正文仅本地保存。
