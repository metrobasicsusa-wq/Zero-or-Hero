#!/usr/bin/env python3
"""Build short public derived rules from private official-document captures."""
from pathlib import Path
from html.parser import HTMLParser
import datetime as dt
import hashlib
import json

ROOT=Path(__file__).resolve().parent
RAW=ROOT/'raw-docs'

class Rows(HTMLParser):
    def __init__(self): super().__init__(); self.rows=[]; self.row=None; self.cell=None
    def handle_starttag(self,tag,attrs):
        if tag=='tr': self.row=[]
        elif tag in ('td','th') and self.row is not None: self.cell=''
    def handle_data(self,data):
        if self.cell is not None: self.cell+=data
    def handle_endtag(self,tag):
        if tag in ('td','th') and self.cell is not None:
            self.row.append(self.cell); self.cell=None
        elif tag=='tr' and self.row is not None:
            self.rows.append(self.row); self.row=None

def make():
    captures=[]
    for file in sorted(RAW.glob('*.meta.json')):
        record=json.loads(file.read_text()); name=file.name.removesuffix('.meta.json')
        body=RAW/(name+'.raw')
        if 'body_sha256' in record:
            assert hashlib.sha256(body.read_bytes()).hexdigest()==record['body_sha256'], name
        captures.append(dict(record,doc_id=name))
    docs={r['doc_id']:r for r in captures}
    parser=Rows(); parser.feed((RAW/'alpaca_faq.raw').read_text())
    flags={'🟢':True,'🔴':False,'🟡':'update_only_if_first'}
    mapping=[]
    for row in parser.rows:
        if len(row)==7 and row[2] and set(row[2]) <= set('ABCO') and 'M' in row[3] and row[4] in flags:
            mapping.append({'condition':row[0],'meaning':row[1],'tapes':list(row[2]),
                'bar_type':'minute','open_close':flags[row[4]],'high_low':flags[row[5]],
                'volume':flags[row[6]],'source_doc_id':'alpaca_faq'})
    assert any(r['condition']==' ' and r['tapes']==['A','B'] for r in mapping)
    assert any(r['condition']=='I' and r['high_low'] is False for r in mapping)
    statements=[
      {'id':'bar_interval','level':'documented_with_clock_conflict','sources':['alpaca_faq','alpaca_minute_article'],
       'rule':'A minute bar uses a left-closed, right-open minute interval and is labeled by its left endpoint. The FAQ says SIP timestamp; the 2022-07-19 article says participant/execution timestamp. These official sources conflict on the clock.',
       'diagnostic_use':'Use returned t to define returned-timestamp minute membership only; do not claim exact vendor bar reconstruction or receipt-time/PIT availability.'},
      {'id':'sparse_bars','level':'officially_documented','sources':['alpaca_faq'],
       'rule':'A stock bar is emitted only when open, high, low, close and volume are nonzero. An interval with no trades, or only price-ineligible odd-lot trades, can legitimately have no bar.',
       'diagnostic_use':'Missing bar alone proves neither zero trades nor a data outage, halt, or unfillable order.'},
      {'id':'trade_conditions','level':'officially_documented','sources':['alpaca_faq'],
       'rule':'Update eligibility depends on tape, each condition and minute versus daily aggregation. Multiple conditions use the strictest update rule. Volume/trade count may change even when OHLC does not.',
       'diagnostic_use':'Use the derived minute_condition_rules for supported tape/condition pairs. A blocking I on A/B/C/O is sufficient to establish no minute-price update even if another condition is unknown. Do not equate missing c, empty array, empty string or unknown tape with regular sale.'},
      {'id':'historical_interval_pagination','level':'officially_documented','sources':['alpaca_trades_markdown','alpaca_quotes_markdown'],
       'rule':'REST historical start and end are inclusive. Results sort by symbol then timestamp; pagination limit is total across symbols, and a short page does not prove exhaustion.',
       'diagnostic_use':'Follow next_page_token to null; locally filter exact nanosecond half-open intervals [minute,minute+60s). Keep query/feed/asof settings in provenance.'},
      {'id':'historical_timestamp','level':'officially_documented_limited_semantics','sources':['alpaca_trades_markdown','alpaca_quotes_markdown'],
       'rule':'The t schema specifies RFC-3339 timestamps with nanosecond precision but does not itself identify receipt time, participant-versus-SIP clock, or historical availability of each revision.',
       'diagnostic_use':'Retain nanosecond precision at interval boundaries; a historical response is a current retrospective data snapshot.'},
      {'id':'trade_updates','level':'officially_documented','sources':['alpaca_trades_markdown'],
       'rule':'Historical trade.u is optional: missing means valid; canceled and incorrect are not valid trades; corrected identifies a correction of a prior incorrect trade.',
       'diagnostic_use':'Count raw rows separately from currently valid rows; exclude canceled/incorrect from current-valid counts; accept corrected as currently valid while flagging unknown update values. This does not reconstruct when a correction became observable.'},
      {'id':'late_updates','level':'officially_documented','sources':['alpaca_stream'],
       'rule':'The stream documents updated bars for late trades after the half-minute mark, and dedicated correction and cancel/error messages.',
       'diagnostic_use':'Downloaded final bars need not equal the first real-time bar. A single historical REST t is insufficient to reconstruct the original announcement/receipt sequence.'},
      {'id':'quote_units','level':'official_rest_documentation_conflicts_with_older_stream_description','sources':['alpaca_quotes_markdown','alpaca_stream'],
       'rule':'Historical REST quote sizes as/bs are shares on and after 2025-11-03, and round lots before that date. The stream page still describes sizes as round lots.',
       'diagnostic_use':'For 2026 historical REST data use shares; do not multiply by 100. Treat quotes as indicative historical observations, not proof of execution.'},
      {'id':'quote_sides','level':'officially_documented','sources':['alpaca_quotes_markdown'],
       'rule':'A zero ap or bp means that side has no active price. A single quote condition applies to both sides; two conditions apply to bid then ask.',
       'diagnostic_use':'Quote presence alone does not prove a two-sided valid executable market. Retain zero/crossed/locked/stale/condition flags as diagnostic limits.'},
      {'id':'symbol_mapping','level':'officially_documented','sources':['alpaca_trades_markdown','alpaca_quotes_markdown','alpaca_faq'],
       'rule':'Historical asof defaults to current date and can join prior symbols after renames. asof=- skips symbol mapping.',
       'diagnostic_use':'Explicit asof=- supports exact-symbol comparisons but does not establish complete corporate-action identity coverage.'},
      {'id':'halt_sources','level':'official_source_locations_verified_not_event_results','sources':['nasdaq_halt_search','nasdaq_halt_history','nasdaq_halt_codes','nyse_halts'],
       'rule':'Nasdaq search says it returns the last year and warns an empty result may mean no halt or invalid criteria. Its market selector includes multiple listing markets. NYSE history advertises one year of News Pending/Dissemination and LULD data with ET timestamps.',
       'diagnostic_use':'A page fetch is not a completed symbol/date query. Dynamic/empty/failed results cannot establish no halt. A halt explanation requires a matching authoritative symbol/date/start/resumption interval, with timezone and scope checked.'},
    ]
    payload={'schema_version':1,'generated_at':dt.datetime.now(dt.timezone.utc).isoformat(),
       'scope':'Official public documentation verification for the 38 missing-minute diagnostic study; no market-event classification or trading action.',
       'sources':captures,'statements':statements,'minute_condition_rules':mapping,
       'empty_condition_policy':{'literal_single_space':'Documented Regular Sale on tapes A/B.',
          'literal_at':'Documented Regular Sale on tapes C/O.',
          'empty_array_or_missing_or_empty_string':'Not established equivalent to regular sale by the inspected documents; unresolved.'},
       'valid_trade_update_policy':{'missing':'valid','corrected':'valid_current_revision','incorrect':'exclude','canceled':'exclude','other':'unknown'},
       'halt_event_records_verified':0,
       'raw_document_bodies_public':False,
       'limitations':['Current documentation is not archived proof that identical vendor rules applied on each historical date.',
          'Current API data does not independently prove completeness, earlier availability, or original correction history.',
          'Documentation conflicts are retained; no vendor support clarification was obtained.',
          'No successful event-specific halt query is asserted by this documentation subtask.'],
       'generator_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
       'fetch_code_sha256':hashlib.sha256((ROOT/'fetch_source_rules.py').read_bytes()).hexdigest()}
    (ROOT/'source-rules.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    selected=['alpaca_faq','alpaca_minute_article','alpaca_trades_markdown','alpaca_quotes_markdown','alpaca_stream','nasdaq_halt_search','nyse_halts']
    source_lines='\n'.join(f"- {k}: {docs[k]['url']}；获取 {docs[k]['retrieved_at']}；SHA256 `{docs[k]['body_sha256']}`。" for k in selected)
    text='''# 缺失分钟：官方资料核验

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

'''+source_lines+'\n'
    (ROOT/'source-rules.md').write_text(text)
    print(json.dumps({'sources':len(captures),'minute_condition_rules':len(mapping),'statements':len(statements)},ensure_ascii=False))

if __name__=='__main__': make()
