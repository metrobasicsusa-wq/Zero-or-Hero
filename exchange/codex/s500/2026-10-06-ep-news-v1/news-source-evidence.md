# Alpaca 新闻接口：官方证据与历史时点限制

准备时间：2026-10-06T17:33:35.708451+00:00。仅核对公开官方文档及 SDK，不代表任何新闻事件已确认。

核心结论：`start/end` 文档未明确按创建还是更新时间过滤；`sort` 明确按更新时间排序。`created_at` 在窗口内且 `updated_at <= 开盘` 只能称“元数据符合时限”，不能证明当前返回标题就是开盘前的历史版本。

## window_filter_axis

Official sources call start/end inclusive, and explicitly call sort updated-date ordering, but do not explicitly identify the time field used for server start/end filtering. Sorting and filtering axes must not be assumed identical.

**冻结研究规则：** Persist requested bounds and returned metadata; explicitly filter created_at in the chosen prior-close to open window locally. Do not label the result an exhaustive archive of all news originally available in that window.

证据：news-request-pinned, legacy-openapi-pinned。

## cutoff_and_revisions

created_at is article creation time and updated_at is update time; neither is documented as client receipt time or exchange event time. No historical headline/summary version or point-in-time retrieval guarantee appears in the reviewed interfaces.

**冻结研究规则：** Require parseable timezone-aware created_at and updated_at, created within the frozen interval, updated_at >= created_at and updated_at <= decision cutoff. Missing, inconsistent, or later-updated metadata remains unresolved. Passing these checks means metadata-clean only, not proof that this exact title was public before the cutoff.

证据：news-model-pinned, legacy-news-historical-pinned。

## revision_coverage_bias

Rejecting later-updated articles can omit genuine preopen events. If server bounds use update time, the initial request can also omit articles created in the window but updated later. There is no basis to turn absence into no event.

**冻结研究规则：** Report missing/unconfirmed event coverage. Primary company/SEC evidence with reliable publication timing is still required for event confirmation.

证据：news-request-pinned, news-model-pinned。

## symbol_tags

Symbols are related or mentioned tickers; unsymbolized/global news may have an empty list. No reviewed source promises immutable historical tags, exact entity identity, or complete coverage of issuer events.

**冻结研究规则：** Use requested-symbol membership as a relevance index only; check legal issuer, event category and timing against original source. An article tagged to several tickers is not several independent events. Missing symbol tags remain explicit.

证据：news-model-pinned, legacy-news-historical-pinned。

## article_identity

id identifies a news article; the current typed field is integer. No documented guarantee makes it a revision ID, unique company event ID, or globally unique identifier across all providers. Same-time ordering/tie-break rules are not documented.

**冻结研究规则：** Deduplicate exact payload matches conservatively by provider/article ID. Preserve conflicts, raw article hashes, observed updated_at, and request provenance. Never silently keep the last version of a conflicting same-ID object. Do not infer an event count from raw article count or use incidental page order as chronology.

证据：news-model-pinned, legacy-openapi-pinned。

## pagination

News REST reference caps each page at 50; next_page_token drives continuation. The current SDK source uses an explicit limit as a cumulative total, despite request-class wording of per-page limit. No reviewed source guarantees a stable point-in-time snapshot across all pages while articles are revised.

**冻结研究规则：** For direct HTTP, retain every page and follow tokens until documented terminal absence, with cycle detection and failure/truncation states. Never call a first page exhaustive. Empty pages with a continuation token are not terminal. Preserve conflicting duplicate IDs across pages rather than erasing evidence.

证据：legacy-news-historical-pinned, news-client-pinned, sdk-rest。

## content_parameters

include_content controls whether article body is returned; exclude_contentless independently controls whether headline/summary-only articles are excluded.

**冻结研究规则：** Use include_content=false and exclude_contentless=false explicitly for the metadata stage. Do not request content=false, which is not the documented parameter. No body is evidence not reviewed, not evidence absent; fetching full current body later does not prove its historical version.

证据：news-request-pinned, legacy-news-historical-pinned。

## precision_and_nulls

Response dates are RFC3339 datetime fields; response precision, clock latency, receive time, null updated_at semantics, and tie ordering are not guaranteed by reviewed sources. Legacy query bounds prohibit fractional seconds. Current typed model requires updated_at but cannot establish all server error behavior.

**冻结研究规则：** Use explicit UTC query bounds at whole seconds; retain raw response timestamp strings and reject missing/unparseable/naive/inconsistent metadata from confirmation. Do not invent zero latency or substitute created_at for absent updated_at.

证据：news-model-pinned, legacy-openapi-pinned。

## 可复核官方来源

- [Alpaca-py NewsRequest](https://raw.githubusercontent.com/alpacahq/alpaca-py/5049448ad218d8f89d3232c1d5bd891d032355b0/alpaca/data/requests.py)：HTTP 200；获取 2026-10-06T17:31:48.878985+00:00；官方 SDK 固定 commit。
  - SHA-256：`b751946aef57ee02bea8a7886a71208faba06b6402b832942211bb8b6f7f41f8`。
  - 仓库 commit 日期：2026-10-06T13:16:17Z；单页发布日期未单独确认。
  - 必要原文摘录：“The inclusive start of the interval.” / “The inclusive end of the interval.” / “Sort articles by updated date.” / “Boolean indicator to include content for news articles (if available)” / “Boolean indicator to exclude news articles that do not contain content”
  - The time window is inclusive but this request documentation does not identify whether server filtering uses created_at or updated_at. Parameter name is include_content, not content. Its per-page limit prose differs from the SDK helper implementation.
- [Alpaca-py News and NewsSet models](https://raw.githubusercontent.com/alpacahq/alpaca-py/5049448ad218d8f89d3232c1d5bd891d032355b0/alpaca/data/models/news.py)：HTTP 200；获取 2026-10-06T17:31:48.876969+00:00；官方 SDK 固定 commit。
  - SHA-256：`4e62a46bc22fc725f136c3b53356de5533c3d4a37ec3a63716f3ec70efbfa19e`。
  - 仓库 commit 日期：2026-10-06T13:16:17Z；单页发布日期未单独确认。
  - 必要原文摘录：“Date article was created (RFC 3339)” / “Date article was updated (RFC 3339)” / “List of related or mentioned symbols. May be empty for unsymbolized/global news, but the API always includes the key, so it is intentionally not defaulted here.” / “News article ID”
  - Actual id field is int despite docstring saying str. created_at and updated_at are required datetime fields. The documented article object has no revision ID or received_at; empty symbol lists are allowed. Metadata is not proof of the exact headline/summary visible at a historical instant.
- [Alpaca-py historical NewsClient](https://raw.githubusercontent.com/alpacahq/alpaca-py/5049448ad218d8f89d3232c1d5bd891d032355b0/alpaca/data/historical/news.py)：HTTP 200；获取 2026-10-06T17:31:48.882737+00:00；官方 SDK 固定 commit。
  - SHA-256：`b8ba51f837c0df36a8abc6031dd7bca70dabd47625946612f88aa09f87a4e650`。
  - 仓库 commit 日期：2026-10-06T13:16:17Z；单页发布日期未单独确认。
  - 必要原文摘录：“page_limit=50,” / “page_size=50,”
  - NewsClient calls /v1beta1/news through _get_marketdata with a 50-item request page size and 50-item page cap. Read the helper to distinguish per-request size from cumulative limit.
- [Alpaca-py RESTClient pagination implementation](https://raw.githubusercontent.com/alpacahq/alpaca-py/5049448ad218d8f89d3232c1d5bd891d032355b0/alpaca/common/rest.py)：HTTP 200；获取 2026-10-06T17:31:12.104179+00:00；官方 SDK 固定 commit。
  - SHA-256：`887753b055a4b8e8a07468b4702cfad1fa3aa66ac73427fe0c62971aa3b165c5`。
  - 仓库 commit 日期：2026-10-06T13:16:17Z；单页发布日期未单独确认。
  - 必要原文摘录：“actual_limit = min(int(limit) - total_items, page_limit)” / “page_token = response.get("next_page_token", None)” / “if page_token is None: break”
  - The SDK follows next_page_token, but when a nonzero explicit request limit is supplied, it stops once cumulative returned items reach that limit. With no explicit limit it continues pages. This is source-code behavior at the pinned revision, not a promise about every SDK release. Do not equate SDK limit=50 with complete pagination.
- [Alpaca official archived Historical News reference](https://raw.githubusercontent.com/alpacahq/alpaca-docs/2080c5e0efbbd812c48eef9209a7a9962de67d2a/content/api-references/market-data-api/news-data/historical.md)：HTTP 200；获取 2026-10-06T17:31:48.883940+00:00；已归档官方仓库。
  - SHA-256：`b714168d893addf072e3dc65076f79ec9bf9532ca4d6bfb1b13db1fef4564dc8`。
  - 仓库 commit 日期：2025-04-10T06:21:35Z；单页发布日期未单独确认。
  - 必要原文摘录：“(Default: 10, Max: 50) Limit of news items to be returned for given page” / “Sort articles by updated date.” / “Pagination token to continue to next page” / “Exclude news articles that do not contain content (just headline and summary)”
  - The raw REST reference describes a maximum 50 articles per page with next_page_token. It documents related/mentioned symbol tags and distinct creation/update dates. The legacy default start is 2015-01-01, unlike the current SDK docstring default start-of-current-day: set explicit boundaries and never rely on these conflicting defaults. The official repository is archived, so exact live defaults remain unverified.
- [Alpaca official archived market-data OpenAPI](https://raw.githubusercontent.com/alpacahq/alpaca-docs/2080c5e0efbbd812c48eef9209a7a9962de67d2a/oas/data/openapi.yaml)：HTTP 200；获取 2026-10-06T17:31:48.883411+00:00；已归档官方仓库。
  - SHA-256：`b94f6775b0d595f3284d64141738592d96b4768f382b9307ba0a59e569c54e75`。
  - 仓库 commit 日期：2025-04-10T06:21:35Z；单页发布日期未单独确认。
  - 必要原文摘录：“Filter data equal to or after this time in RFC-3339 format. Fractions of a second are not accepted.” / “Filter data equal to or before this time in RFC-3339 format. Fractions of a second are not accepted.” / “News article ID”
  - The news route references generic inclusive start/end parameters; neither identifies the field being filtered. Generic limit schema in this archived OpenAPI says 10000 and conflicts with news-specific max50, so use the news-specific reference and current SDK page size. Query fractions are disallowed in this legacy schema, but this says nothing about guaranteed precision of response event timestamps. News schema has article ID and creation/update fields, with no documented revision/asof parameter.

## 已知失败和核验

当前参考页 https://docs.alpaca.markets/reference/news-3 返回 HTTP HTTPError: HTTP Error 403: Forbidden；未绕过拒绝。

已验证 6 份原始来源 SHA-256、21 条短引文精确存在；3 份 master 文件与固定 commit 文件一致。

仍未知：服务器过滤轴、不可变历史修订快照、同时间戳排序规则、历史标签是否变动、响应时间精度/空更新时间语义。标签不足以确认公司事件，缺新闻不等于无事件；后续需要公司 IR/SEC 原文与可靠发表时间。

没有访问凭据、账户或订单；没有生成策略收益或启用交易。完整网页/源码仅保留在私有 scratch 的 raw-docs，不纳入公共包。
