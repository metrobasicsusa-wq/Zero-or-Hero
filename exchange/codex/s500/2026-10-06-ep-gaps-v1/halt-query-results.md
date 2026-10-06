# REPL / SPCE 两个目标分钟的停牌核查

- REPL：2026-06-01 09:31 ET，停牌状态 **未知**。
- SPCE：2026-06-01 10:16 ET，停牌状态 **未知**。

仅对这两个股票各发出 1 次公共只读事件查询，未超过每股 2 次的上限。Nasdaq 官方搜索页面给出 SearchTradeHaltsNEW 方法及七个参数的顺序；其 rpcclient.axd 返回 “Request is not valid”，因此试用的 RPCHandler.axd 传输路径仍标为未从页面确认。

两个请求均返回 HTTP 200，但响应正文只有 Incapsula JavaScript 挑战页，没有事件表格或 JSON。该状态不是零条停牌记录；未绕过挑战，也未据此声称没有停牌。NYSE 页面是动态历史组件，捕获页面没有给出可确认的查询端点与参数，因此未猜测接口发请求。

查询参数、URL、抓取时刻、正文 SHA256 与失败状态见 halt-query-results.json。完整正文留在私有 raw-halts/ 与 raw-docs/。没有发送券商认证头，没有查询 HTFL，没有推断暂停原因。
