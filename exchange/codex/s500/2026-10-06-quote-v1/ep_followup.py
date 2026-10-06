"""Build a bounded chronological primary-source news audit, without strategy returns."""
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
OLD=ROOT.parent/'s500-orb-20261006'
RAW=ROOT/'raw-ep'


def read(path):return json.loads(path.read_text())
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    inventory=read(OLD/'ep-candidate-inventory.json')
    initial=read(OLD/'ep-event-audit.json')
    registry=read(OLD/'registry.json')
    candidates=sorted((r for r in inventory['candidates'] if r['stock_research_eligible_initial']),key=lambda r:(r['date'],r['symbol']))[:10]
    assert [r['symbol'] for r in candidates]==['BIDU','SLS','SMX','SOC','ALT','SIDU','SMR','AEVA','ARVN','ARWR']
    registered=next(x for x in registry['experiments'] if x['id']=='EP-NEWS-500-v1')
    assert 'earnings/guidance/contract/regulatory announcement' in registered['rule']
    prior=copy.deepcopy(initial['candidate_reviews'])
    for index,r in enumerate(prior,1):
        r['chronological_candidate_number']=index
        r['review_provenance']='carried_forward_unchanged_from_prior_audit; no new adjudication'
    reviews=[
      {'symbol':'SOC','news_status':'missing','event_found':False,'event_category':None,
       'qualifying_category_status':'unknown','in_window_confirmed':False,'pass_registered_news_gate':False,
       'source_keys':['SOC-submissions','SOC-IR','SOC-home','SOC-news'],
       'timestamp_evidence':{},
       'summary_zh':'SEC recent 记录未见目标窗内公司公告；最近检查到的 8-K 为 12月29日，早于目标窗口。公司新闻页返回动态页面，未获取具体历史公告。不能据此断言没有催化。',
       'reason':'No qualifying primary announcement verified within the bounded search. Dynamic IR archive was not fully extracted.'},
      {'symbol':'ALT','news_status':'confirmed','event_found':True,
       'event_category':'regulatory announcement: FDA Breakthrough Therapy Designation for pemvidutide in MASH',
       'qualifying_category_status':'passes exact original regulatory announcement category; designation is not marketing approval',
       'in_window_confirmed':True,'pass_registered_news_gate':True,
       'source_keys':['ALT-submissions','ALT-IR','ALT-IR-corrected','ALT-IR-page2','ALT-release'],
       'timestamp_evidence':{'visible_release_time':'2026-01-05T07:30:00-05:00',
          'json_ld_datePublished':'2026-01-05T07:31:02-0500',
          'conservative_available_by':'2026-01-05T07:31:02-05:00',
          'discrepancy':'Visible release time and structured publication time differ by 62 seconds. Both are within the frozen window.',
          'archival_limit':'Current first-party release is evidence of its stated publication time, not an independently archived capture from that morning.'},
       'summary_zh':'公司原始公告明确 FDA 已给予 pemvidutide 治疗 MASH 的突破性疗法资格，并说明这是加快开发与审评的监管资格，不是批准上市。可见 07:30 EST 与结构化 07:31:02 EST 两时间均早于当日开盘。',
       'reason':'A first-party, precisely timed announcement reports an actual regulatory authority designation. Passes the original regulatory announcement news gate only; volume/execution and profitability remain unevaluated here.'},
      {'symbol':'SIDU','news_status':'confirmed','event_found':True,
       'event_category':'appointment of Kelle Wendling to board of directors',
       'qualifying_category_status':'outside earnings, guidance, contract and regulatory announcement categories',
       'in_window_confirmed':True,'pass_registered_news_gate':False,
       'source_keys':['SIDU-submissions','SIDU-IR','SIDU-IR-page3'],
       'timestamp_evidence':{'ir_list_release_time':'2026-01-05T08:30:00-05:00',
          'evidence_scope':'Exact time and appointment headline from first-party release index; article body not requested.'},
       'summary_zh':'公司新闻索引显示 1月5日 08:30 EST 的董事任命，时间在窗口内，但不属于冻结催化类别；同页 SHIELD 合同公告为 12月22日，不能移作 1月5日新催化。',
       'reason':'Verified current first-party index entry has a disallowed event category. This is not proof that no other qualifying event existed.'},
      {'symbol':'SMR','news_status':'missing','event_found':False,'event_category':None,
       'qualifying_category_status':'unknown','in_window_confirmed':False,'pass_registered_news_gate':False,
       'source_keys':['SMR-submissions','SMR-IR','SMR-IR-corrected','SMR-IR-page2'],
       'timestamp_evidence':{},
       'summary_zh':'公司当前新闻索引在 2025年11月 与 2026年1月12日 之间未列出目标 1月5日公告；SEC 近窗记录为 Form 4，未核实符合类别的原始催化。搜索缺失不等于当天无消息。',
       'reason':'No qualifying in-window primary announcement found in examined current IR pages and SEC recent submissions. Archive completeness is not guaranteed.'},
      {'symbol':'AEVA','news_status':'ambiguous','event_found':True,
       'event_category':'NVIDIA DRIVE Hyperion sensor selection and future integration collaboration',
       'qualifying_category_status':'contract qualification unresolved; selection/collaboration is not assumed to be a signed purchase contract',
       'in_window_confirmed':False,'pass_registered_news_gate':False,
       'source_keys':['AEVA-submissions','AEVA-IR','AEVA-home','AEVA-news','AEVA-nvidia'],
       'timestamp_evidence':{'release_visible_date':'2026-01-05',
          'news_index_embedded_date':'2026-01-05 14:56:54',
          'embedded_timezone':None,
          'ambiguity':'Without the timezone, 14:56:54 could precede the 16:00 ET close or fall after it. Do not silently assume Pacific time.'},
       'summary_zh':'原始公告称 Aeva 激光雷达获 NVIDIA DRIVE Hyperion 参考平台选用并计划集成，目标 2028年量产。未证明本次公告为已签采购合同。列表含 14:56:54，但没有时区，无法确认是否在 1月5日收盘后发布。',
       'reason':'Both the precise pre-open event window and contract-category interpretation remain unresolved. No favorable timezone or binding order is inferred.'},
      {'symbol':'ARVN','news_status':'missing','event_found':False,'event_category':None,
       'qualifying_category_status':'unknown','in_window_confirmed':False,'pass_registered_news_gate':False,
       'source_keys':['ARVN-submissions','ARVN-IR','ARVN-IR-corrected','ARVN-IR-page1'],
       'timestamp_evidence':{},
       'summary_zh':'已查 SEC recent 近窗记录和公司当前新闻索引两页，未获取目标 1月6日盘前原始催化。公司索引所见最早日期为 2月12日，不代表 1月公告不存在。',
       'reason':'No qualifying primary announcement verified. The examined current IR archive does not establish exhaustive January coverage.'},
      {'symbol':'ARWR','news_status':'ambiguous','event_found':True,
       'event_category':'Health Canada approval dated January5; separate January6 interim clinical results headline',
       'qualifying_category_status':'Health Canada approval is regulatory; interim clinical readout is not automatically a regulatory decision',
       'in_window_confirmed':False,'pass_registered_news_gate':False,
       'source_keys':['ARWR-submissions','ARWR-IR','ARWR-IR-corrected','ARWR-canada'],
       'timestamp_evidence':{'canada_approval_release_dateline':'2026-01-05; no exact time exposed in examined first-party release',
          'clinical_data_listing_date':'2026-01-06; exact release time and full article were not fetched',
          'approval_source_release_url':'https://www.businesswire.com/news/home/20260105004282/en/',
          'approval_source_release_access_status':'linked by company; not fetched within request budget'},
       'summary_zh':'公司原始公告确认加拿大已批准 REDEMPLO，但页面仅有 1月5日日期，无法确定是否在当日 16:00 后才首次公开。1月6日另列中期临床结果，不能把临床数据冒充监管决定或把旧批准移至新窗口。',
       'reason':'Regulatory event exists, but its publication time relative to the prior close is unresolved. Separate clinical data do not by themselves satisfy the frozen category.'}
    ]
    by_symbol={r['symbol']:r for r in candidates}
    for index,r in enumerate(reviews,4):
        c=by_symbol[r['symbol']]
        r.update(date=c['date'],chronological_candidate_number=index,initial_news_status='missing',
            review_provenance='new bounded primary-source followup',
            news_window={'after_previous_session_close_et':c['prior_session']+'T16:00:00-05:00',
                         'before_candidate_open_et':c['date']+'T09:30:00-05:00'},
            regular_open_verification='not_evaluated_in_this_news_audit',
            first30minute_volume_gate='not_evaluated_in_this_news_audit',
            strategy_return='not_computed')
    sources=[]
    for p in sorted(RAW.glob('*-meta.json')):
        m=read(p)
        if 'sha256' in m:
            body=RAW/(m['source_key']+('.raw' if m.get('status')==200 else '.error'))
            assert sha(body)==m['sha256']
        m['evidence_type']='official_SEC_or_company_primary_source' if m.get('status')==200 else 'access_attempt_not_event_evidence'
        sources.append(m)
    assert len(sources)==30
    all_reviews=prior+reviews
    passed=[{'date':r['date'],'symbol':r['symbol'],'conservative_available_by':r['timestamp_evidence'].get('conservative_available_by'),
             'news_only':True} for r in all_reviews if r['pass_registered_news_gate']]
    result={'schema':'s500.ep-first10-primary-news-followup.v1','created_at':datetime.now(timezone.utc).isoformat(),
        'sampling':'Exactly the first10 stock-eligible rows ascending date then symbol from the existing inventory: three prior reviews carried unchanged, next seven reviewed. No selection by future return.',
        'registered_method':'EP-NEWS-500-v1','frozen_news_category_exact':'earnings/guidance/contract/regulatory announcement',
        'category_interpretation':'An actual FDA designation is a regulatory announcement but is not marketing approval. A planned listing, product collaboration, board appointment, or clinical result is not automatically a qualifying approval or signed contract. No rule was selected using strategy returns.',
        'time_policy':'Publication must be after the previous regular-session close and before 09:30 ET. Preserve timezone and first-publication ambiguities; do not reuse old news as a fresh event.',
        'coverage':{'initial_stock_candidates':inventory['coverage']['initial_stock_candidates'],'reviewed_total':10,
            'carried_forward_reviews':3,'new_reviews':7,'remaining_unreviewed':inventory['coverage']['initial_stock_candidates']-10,
            'new_primary_request_attempts':30,'news_status_counts':dict(Counter(r['news_status'] for r in all_reviews)),
            'confirmed_registered_news_gate':len(passed),'entire_EP_strategy_validated':False},
        'candidate_reviews':all_reviews,'confirmed_news_gate_candidates':passed,
        'sources':sources,'carried_forward_sources':initial['sources'],
        'input_sha256':{'ep-candidate-inventory.json':sha(OLD/'ep-candidate-inventory.json'),
            'ep-event-audit.json':sha(OLD/'ep-event-audit.json'),'registry.json':sha(OLD/'registry.json'),
            'ep_followup.py':sha(ROOT/'ep_followup.py')},
        'limitations':['Only 10 of 1079 price-gap stock candidates have been reviewed; 1069 remain unreviewed.',
            'One news-gate pass is not a trading signal: regular opening gap, volume gate, quotes, settlement and exits still require validation.',
            'Current primary websites and SEC recent lists are not a complete archived point-in-time news database.',
            'No event absence is inferred from an incomplete archive, failed request or no matching filing.',
            'No positive surprise, causal price attribution or profitable trade is inferred from an event or opening gap.',
            'Full source bodies remain local in raw-ep; public report contains short original summaries, URLs, timestamps and hashes only.',
            'Underlying candidate instrument identity and current-universe selection limitations remain inherited from the original inventory.'],
        'broker_orders_sent':0,'strategy_returns_computed':False}
    (ROOT/'ep-followup.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['# EP 前 10 个候选：原始催化核查','',
      '按原候选日期、股票代码顺序续查 7 个，加上先前 3 个，共核查 10/1,079 个；其余 1,069 个未审查。30 次新的一手来源请求，原报告未改写。未计算 EP 收益。','',
      '**仅 ALT 2026-01-05 通过新闻门槛。** 公司页显示 07:30 EST，结构化发布时间为 07:31:02 EST；保留 62 秒差异，以较晚时间作为保守可用时点，两者均在上次收盘至当日开盘窗口内。FDA 突破性疗法资格属于注册原文的 regulatory announcement，**不是批准上市**。首 30 分钟成交量和成交可行性另行核验。','',
      '| 顺序 | 日期 | 股票 | 新闻门槛 | 结论 |','|---:|---|---|---|---|']
    for r in all_reviews:
        state='通过' if r['pass_registered_news_gate'] else ('未找到可验证证据' if r['news_status']=='missing' else '未通过／仍有歧义')
        lines.append(f'| {r["chronological_candidate_number"]} | {r["date"]} | {r["symbol"]} | {state} | {r["summary_zh"]} |')
    lines+=['','关键原始资料：','',
      '- [ALT FDA designation 公告](https://ir.altimmune.com/news-releases/news-release-details/altimmune-receives-fda-breakthrough-therapy-designation)：显示准确盘前时间。',
      '- [SIDU 官方历史列表](https://investors.sidusspace.com/news-events/press-releases?page=3)：1月5日 08:30 EST 为董事任命；12月22日合同不能算成当日新催化。',
      '- [AEVA NVIDIA 公告](https://www.aeva.com/press/aeva-and-nvidia-to-integrate-4d-lidar-as-reference-sensor-within-the-nvidia-drive-hyperion-platform-ecosystem/)：平台选用与协作；嵌入列表时间无时区，签约性质也未明确。',
      '- [ARWR 加拿大批准公告](https://arrowheadpharma.com/en-us/newsroom/arrowhead-pharmaceuticals-announces-health-canada-approval)：日期明确、时点未明确；不能断言落在 Jan5 收盘后的窗口。','',
      '未找到不等于不存在；当前网页与 SEC recent 列表并非完整历史新闻档案。10 个候选的所有失败、缺失和歧义均保留。完整访问日期、URL、HTTP 状态、SHA-256 与原分类解释见 ep-followup.json。新闻存在不证明股价上涨原因或策略优势；未下单。']
    (ROOT/'ep-followup.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({'coverage':result['coverage'],'passes':passed},indent=2))


if __name__=='__main__':main()
