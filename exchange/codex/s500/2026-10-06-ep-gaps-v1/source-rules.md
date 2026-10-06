# 缺失分钟：官方资料核验

## 已证实的规则

- Alpaca FAQ 明确：分钟 bar 仅在 OHLCV 均非零时生成。没有成交或只有不更新价格的零股成交，都可能合法地没有 bar。缺 bar 不能直接等同于数据损坏、停牌或无法成交。
- 更新规则按 tape、成交条件和 bar 周期区分；多个条件取最严格规则。I、P、U 在 A/B/C/O 分钟中均不更新 OHLC、但更新成交量；W 在 C/O 同理。@ 仅在 C/O、字面单空格仅在 A/B 被列为普通成交。未给出的条件/tape 组合与空数组不作普通成交假设。完整派生映射见 source-rules.json。
- 历史 trades 的 u 缺失表示有效，corrected 是当前更正成交，incorrect/canceled 应剔除；未知 u 保留未定。只读取历史 t 不能恢复更正到达时间。
- REST start/end 都包含端点；必须翻完 next_page_token，再按纳秒精度筛选左闭右开的目标分钟。
- **2026 年历史 REST 报价 as/bs 的单位是股**。OpenAPI 明确 2025-11-03 以前才是 round lots；不能统一乘 100。ap/bp 为零代表相应方向没有活跃报价。

## 明确保留的官方文档冲突

当前 FAQ 将 bar 分桶时钟写成 SIP timestamp，2022-07-19 官方文章写 participant/execution timestamp；历史 REST schema 只定义 RFC-3339 纳秒 t，没有在该字段中裁定两种时钟。因此本次只能称“按返回 t 的分钟归属”，不能声称精确重建官方 bar。旧 stream 页面还把报价尺寸写作 round lots，与当前历史 REST 的日期化单位说明不一致；本研究使用 2026 historical REST 的说明。

官方 stream 文档列出迟到成交导致更新 bar，以及 correction/cancel-error 消息。历史最终数据不等于当时第一次可见的数据；全部结果仍是事后资料核验。

## 停牌来源与边界

Nasdaq Trading Halt Search 明确只展示最近一年，并提醒空结果也可能来自无效条件；查询可选多个市场。NYSE 页面写明一年 News Pending/Dissemination 与 LULD 历史、使用 ET。仅访问这些页面并不等于完成某股票某分钟的有效查询；动态页面、空结果、失败均不能证明未停牌。本资料子任务未核实任何具体停牌记录，不能给 38 点统一加上“未停牌”。

## 获取与失败留痕

所有文档使用继承的代理与 TLS 验证、无 Alpaca 认证头。旧的股票文档/参考路径返回 404，正确 quotes 参考路径首次 500、重试 200；这些失败保留在 sources 中。原始完整正文只在私有 raw-docs/，公开包仅短摘要、派生规则、代码及哈希。

## 主要来源

- alpaca_faq: https://docs.alpaca.markets/docs/market-data-faq；获取 2026-10-06T19:18:02.115686+00:00；SHA256 `f648f02e9b6ed5ec0c454b58fcdb257b606b621faf09543b7b673e67032dd69c`。
- alpaca_minute_article: https://alpaca.markets/learn/stock-minute-bars/；获取 2026-10-06T19:18:56.480459+00:00；SHA256 `c077240fcde693779f57b7115c8302a0f539cad7331487902f76201c2dc6d2ae`。
- alpaca_trades_markdown: https://docs.alpaca.markets/us/reference/stocktrades-1.md；获取 2026-10-06T19:19:37.402301+00:00；SHA256 `492986a54cd1a0c12a9f7e88d2b0b15c396b605f30fbb96b3f516f7e8b48113a`。
- alpaca_quotes_markdown: https://docs.alpaca.markets/us/reference/stockquotes-1.md；获取 2026-10-06T19:19:37.403027+00:00；SHA256 `d408356bc3fc73d19fbab43a01501f05d1e888da60e21a03460cd3d2d7696cd9`。
- alpaca_stream: https://docs.alpaca.markets/docs/real-time-stock-pricing-data；获取 2026-10-06T19:18:03.280111+00:00；SHA256 `821d9206e8a277614a9edbf13921e42a30fb233c6f7d770893a97bd0f5b769d1`。
- nasdaq_halt_search: https://www.nasdaqtrader.com/Trader.aspx?id=TradingHaltSearch；获取 2026-10-06T19:18:56.600726+00:00；SHA256 `431eb3281d7d4fb42bb00e6198150b4d430da1a7f7d9cfda40689b0e53ae4ad0`。
- nyse_halts: https://www.nyse.com/trade-halt；获取 2026-10-06T19:18:04.550792+00:00；SHA256 `213374ef2f78345d8ee09e7e1aa5b8baaf51a880a28df2de676a2bd1b70baa0f`。
