"""Review fixed dates against captured BLS/BEA/Fed official calendars.

Offline transformation of retrospective sources. No prices, returns, account data,
news causality, publication-time or historical information-availability inference.
"""
import argparse
from collections import Counter
from datetime import datetime, time, timedelta, timezone
import hashlib
from html import unescape
import json
from pathlib import Path
import re
from urllib.parse import urljoin
from zoneinfo import ZoneInfo


DATES = ["2026-01-29", "2026-02-13", "2026-02-17", "2026-03-09", "2026-03-20", "2026-03-27", "2026-03-30", "2026-04-30", "2026-05-15", "2026-06-12", "2026-06-18", "2026-06-25"]
CATEGORIES = ["BLS_CPI", "BLS_PPI", "BLS_EmploymentSituation", "BEA_GDP", "BEA_PersonalIncomeAndOutlays", "Fed_FOMCMeeting", "Fed_FOMCStatement", "Fed_FOMCMinutes"]
NY = ZoneInfo("America/New_York")


def clean(text):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", text))).strip()


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_id(category):
    if category.startswith("BLS"):
        return "bls-annual"
    if category.startswith("BEA"):
        return "bea-full-current"
    return "fed-calendar"


def temporal_labels(value):
    if value is None:
        return "unknown_time", "unknown_time"
    t = time.fromisoformat(value)
    if t < time(9, 30):
        return "before_09_30", "strictly_before_10_00"
    if t < time(10):
        return "09_30_to_before_10_00", "strictly_before_10_00"
    if t == time(10):
        return "exactly_10_00", "equal_10_00_not_proven_received_before_decision"
    return "after_10_00", "strictly_after_10_00"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage-dir", required=True)
    args = parser.parse_args()
    stage = Path(args.stage_dir)
    manifest = json.loads((stage / "macro-source-manifest.json").read_text())
    requests = manifest["requests"]
    assert len(requests) <= 20
    sources = {row["document_id"]: row for row in requests if row["status"] == 200}
    for row in requests:
        assert sha(stage / row["raw_relative_path"]) == row["body_sha256"]
    def body(ident):
        return (stage / sources[ident]["raw_relative_path"]).read_text()
    def text(ident):
        return (stage / sources[ident]["raw_relative_path"]).with_suffix(".txt").read_text()
    def evidence(ident, quote=None):
        source = sources[ident]
        result = {"source_id": ident, "url": source["final_url"], "retrieved_at_utc": source["retrieved_at_utc"], "body_sha256": source["body_sha256"]}
        if quote:
            assert clean(quote) in clean(text(ident)), (ident, quote)
            result["short_quote_whitespace_normalized"] = clean(quote)
        return result
    calendars = {category: [] for category in CATEGORIES}
    bls = body("bls-annual")
    assert "NOTE: All times on calendar are Eastern Time." in text("bls-annual")
    for html_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", bls, re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", html_row, re.S)
        if len(cells) != 3:
            continue
        date_text, time_text, title = map(clean, cells)
        category = next((category for prefix, category in [("Consumer Price Index for", "BLS_CPI"), ("Producer Price Index for", "BLS_PPI"), ("Employment Situation for", "BLS_EmploymentSituation")] if title.startswith(prefix)), None)
        if category is None:
            continue
        date = datetime.strptime(date_text, "%A, %B %d, %Y").date()
        if date.year != 2026:
            continue
        time_et = datetime.strptime(time_text, "%I:%M %p").strftime("%H:%M:%S")
        calendars[category].append({"date": date.isoformat(), "scheduled_time_et": time_et, "title": title, "gdp_estimate_vintage": None, "source_row_quote": f"{date_text} {time_text} {title}"})
    # This is the actual current Full Schedule link; two literal /2026 pages are empty.
    assert "Year 2026" in text("bea-full-current")
    machine = json.loads(body("bea-machine-json"))
    for html_row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", body("bea-full-current"), re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", html_row, re.S)
        if len(cells) != 4:
            continue
        date_time_text, _, title, _ = map(clean, cells)
        if title.startswith("Personal Income and Outlays,"):
            category, machine_key = "BEA_PersonalIncomeAndOutlays", "Personal Income and Outlays"
        elif title.startswith("GDP (") or title.startswith("Gross Domestic Product,"):
            category, machine_key = "BEA_GDP", "Gross Domestic Product"
        else:
            continue
        match = re.fullmatch(r"([A-Za-z]+ \d{1,2}) (\d{1,2}:\d{2} [AP]M)", date_time_text)
        assert match, date_time_text
        date = datetime.strptime(match.group(1) + " 2026", "%B %d %Y").date()
        time_et = datetime.strptime(match.group(2), "%I:%M %p").strftime("%H:%M:%S")
        local = datetime.fromisoformat(date.isoformat() + "T" + time_et).replace(tzinfo=NY)
        iso_dates = [datetime.fromisoformat(value) for value in machine[machine_key]["release_dates"]]
        assert local.astimezone(timezone.utc) in iso_dates, (category, date, "HTML/JSON schedule disagreement")
        vintage = next((version for version in ["Advance", "Second", "Third", "Updated"] if version + " Estimate" in title), None) if category == "BEA_GDP" else None
        urls = re.findall(r'href="([^"]+)"', cells[3])
        calendars[category].append({"date": date.isoformat(), "scheduled_time_et": time_et, "scheduled_time_utc": local.astimezone(timezone.utc).isoformat(), "title": title, "gdp_estimate_vintage": vintage, "release_document_url": urljoin("https://www.bea.gov", urls[0]) if urls else None, "source_row_quote": f"{date_time_text} News {title}"})
    fed = body("fed-calendar")
    fed_2026 = fed.split("2026 FOMC Meetings", 1)[1].split("2025 FOMC Meetings", 1)[0]
    pattern = r'fomc-meeting__month[^>]*><strong>([^<]+)</strong></div>\s*<div class="[^"]*fomc-meeting__date[^>]*>([^<]+)</div>'
    meeting_rows = re.findall(pattern, fed_2026, re.S)
    assert len(meeting_rows) == 8, "Incomplete 2026 meeting parse"
    for month, days in meeting_rows:
        start, end = [int(day) for day in days.replace("*", "").split("-")]
        for day in range(start, end + 1):
            date = datetime.strptime(f"{month} {day} 2026", "%B %d %Y").date().isoformat()
            calendars["Fed_FOMCMeeting"].append({"date": date, "scheduled_time_et": None, "title": "Scheduled FOMC meeting day", "source_row_quote": f"{month} {days}"})
    statement_dates = sorted(set(re.findall(r'/newsevents/pressreleases/monetary(2026\d{4})a\.htm', fed_2026)))
    for stamp in statement_dates:
        date = datetime.strptime(stamp, "%Y%m%d").date().isoformat()
        calendars["Fed_FOMCStatement"].append({"date": date, "scheduled_time_et": None, "title": "Regular meeting statement link present in retrospective FOMC calendar", "release_document_url": f"https://www.federalreserve.gov/newsevents/pressreleases/monetary{stamp}a.htm", "source_row_quote": None})
    minutes_dates = sorted(set(re.findall(r"Released ([A-Za-z]+ \d{1,2}, 2026)", fed_2026)))
    for stamp in minutes_dates:
        date = datetime.strptime(stamp, "%B %d, %Y").date().isoformat()
        calendars["Fed_FOMCMinutes"].append({"date": date, "scheduled_time_et": None, "title": "FOMC minutes release date displayed in retrospective calendar", "source_row_quote": f"Released {stamp}"})
    assert all(len(calendars[category]) >= 10 for category in CATEGORIES[:5])
    assert len(calendars["Fed_FOMCStatement"]) >= 4 and len(calendars["Fed_FOMCMinutes"]) >= 3
    release_lookup = {("2026-02-13", "BLS_CPI"): "bls-cpi-20260213", ("2026-04-30", "BEA_GDP"): "bea-gdp-20260430", ("2026-04-30", "BEA_PersonalIncomeAndOutlays"): "bea-pio-20260430", ("2026-06-25", "BEA_GDP"): "bea-gdp-20260625", ("2026-06-25", "BEA_PersonalIncomeAndOutlays"): "bea-pio-20260625"}
    rows = []
    for date in DATES:
        for category in CATEGORIES:
            matches = [row for row in calendars[category] if row["date"] == date]
            events = []
            for match in matches:
                event = dict(match)
                event["schedule_evidence"] = evidence(source_id(category), match.get("source_row_quote"))
                event.pop("source_row_quote", None)
                if category.startswith("BEA"):
                    event["machine_readable_time_evidence"] = evidence("bea-machine-json")
                bucket, relation = temporal_labels(match["scheduled_time_et"])
                event.update({"scheduled_time_bucket_et": bucket, "scheduled_time_relation_to_10_00_et": relation, "actual_first_publication_timestamp": None, "historical_received_at": None, "information_available_to_strategy_then": "unknown", "causes_stock_return": "not_evaluated"})
                ident = release_lookup.get((date, category))
                if ident:
                    content = clean(text(ident))
                    if ident.startswith("bls"):
                        expected = "Transmission of material in this release is embargoed until 8:30 a.m. (ET) Friday, February 13, 2026"
                    else:
                        day = datetime.fromisoformat(date).strftime("%A, %B %d, %Y")
                        expected = f"EMBARGOED UNTIL RELEASE AT 8:30 a.m. EDT, {day}"
                    assert expected in content, (ident, "release page embargo time not matched")
                    event["release_document_annotation"] = {"type": "embargo_release_time_label_not_observed_publication_or_receipt", "time_et": "08:30:00", "date": date, "matches_current_schedule": True, "evidence": evidence(ident, expected)}
                else:
                    event["release_document_annotation"] = None
                events.append(event)
            rows.append({"date": date, "date_role": "no_complete_outcome_diagnostic" if date == "2026-06-18" else "outcome_support", "category": category, "status": "listed_in_captured_official_calendar" if matches else "not_listed_in_captured_official_calendar", "source_scope": "Current retrospective official calendar only; not all news and not a historical as-of schedule", "calendar_evidence": evidence(source_id(category)), "scheduled_events": events, "no_event_claim": False, "unknown_if_outside_limited_category_or_absent_from_current_calendar": True})
    assert len(rows) == len(DATES) * len(CATEGORIES) == 96
    positive = [event for row in rows for event in row["scheduled_events"]]
    assert len(positive) == 5
    counts = Counter(event["scheduled_time_bucket_et"] for event in positive)
    result = {
        "reviewed_at_utc": datetime.now(timezone.utc).isoformat(),
        "reviewed_date_timezone": "America/New_York",
        "status": "limited_retrospective_official_calendar_review_complete",
        "scope": {"dates": DATES, "date_roles": {date: "no_complete_outcome_diagnostic" if date == "2026-06-18" else "outcome_support" for date in DATES}, "categories": CATEGORIES, "matrix_rows": 96, "outcome_support_dates": 11, "diagnostic_dates_without_complete_outcome": 1, "date_added_for_coverage": "2026-06-18", "date_addition_reason": "Preserve diagnostic day without complete primary outcome; no strategy or return selection changed"},
        "summary": {"listed_category_date_cells": sum(row["status"] == "listed_in_captured_official_calendar" for row in rows), "not_listed_in_current_captured_calendar_cells": sum(row["status"] == "not_listed_in_captured_official_calendar" for row in rows), "calendar_source_unknown_cells": 0, "scheduled_events": len(positive), "positive_dates": sorted({event["date"] for event in positive}), "time_buckets_et": {bucket: counts[bucket] for bucket in ["before_09_30", "09_30_to_before_10_00", "exactly_10_00", "after_10_00", "unknown_time"]}, "actual_publication_or_strategy_receipt_verified": 0},
        "rows": rows,
        "calendar_inventory_for_audit": {category: {"parsed_rows": len(events), "dates": sorted({event["date"] for event in events}), "titles_outside_fixed_dates_not_republished": True} for category, events in calendars.items()},
        "source_quality_notes": [
            "BEA /news/schedule/2026 and /news/schedule/full/2026 returned HTTP 200 but no calendar rows; absence on these pages was not classified as no release.",
            "The linked current /news/schedule/full explicitly says Year 2026 and supplies row titles. Advertised machine-readable JSON independently matches dates/times for all parsed national GDP and Personal Income and Outlays rows.",
            "BLS annual calendar explicitly labels Eastern Time; current month revisions may differ from earlier published schedules.",
            "Fed annual calendar supplies meeting ranges and linked regular statements/minute release dates; meeting day does not disclose a decision, and unscheduled Fed communications are outside this review.",
            "All five matched release pages carry 08:30 embargo labels agreeing with current schedules. These are document annotations, not observed first availability, server publication logs or strategy receipt timestamps.",
        ],
        "limits": [
            "Not a complete news calendar: excludes company earnings, filings, guidance, geopolitical events, all other BLS/BEA releases, Fed speeches and unscheduled communications.",
            "Employment Situation alone is the jobs category; other labor reports are not substituted. PCE here denotes Personal Income and Outlays, not every release containing consumption data.",
            "GDP estimate vintage is retained: April 30 is Advance and June 25 Third; published revisions are not known at earlier decisions.",
            "No event values, surprises, return direction, causal explanations or profitable-strategy conclusions are calculated.",
            "A 10:00 annotation would be equal to the checkpoint, not evidence of receipt before a 10:00 decision; after-10:00 events cannot explain a prior information set.",
            "Calendar absence means not listed in the captured finite official calendar, not a proven absence of all events or news. Retrospective pages do not reconstruct what the strategy knew then.",
        ],
        "short_calendar_evidence": [evidence("bls-annual", "NOTE: All times on calendar are Eastern Time."), evidence("bea-full-current", "Year 2026"), evidence("fed-calendar", "2026 FOMC Meetings")],
        "request_control": {"actual_get_requests_including_redirects": len(requests), "maximum_get_requests": 20, "minimum_host_spacing_seconds": 1.05, "access_denial_retries": 0, "market_data_requests": 0, "account_requests": 0, "orders": 0},
        "source_manifest_sha256": sha(stage / "macro-source-manifest.json"),
        "code_sha256": {name: sha(stage / name) for name in ["fetch_macro_calendar.py", "build_macro_calendar_review.py"]},
    }
    (stage / "macro-calendar-review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    labels = {"BLS_CPI": "CPI", "BLS_PPI": "PPI", "BLS_EmploymentSituation": "Employment Situation", "BEA_GDP": "GDP", "BEA_PersonalIncomeAndOutlays": "Personal Income and Outlays", "Fed_FOMCMeeting": "FOMC 会期", "Fed_FOMCStatement": "FOMC 声明", "Fed_FOMCMinutes": "FOMC 纪要"}
    table = ["| 日期 | 样本角色 | 当前官方日历列出的限定事件 | ET 时间及范围 |", "|---|---|---|---|"]
    for date in DATES:
        matches = [(row["category"], event) for row in rows if row["date"] == date for event in row["scheduled_events"]]
        description = "；".join(labels[category] + (f"（{event['gdp_estimate_vintage']}，Q1 2026）" if event.get("gdp_estimate_vintage") else "") for category, event in matches) or "这八类在当前日历中未列出"
        role = "无完整主结果的诊断日" if date == "2026-06-18" else "结果支撑日"
        time_label = "08:30，开盘前" if matches else "不适用；不代表当天没有其他新闻"
        table.append(f"| {date} | {role} | {description} | {time_label} |")
    report = """# 限定官方宏观日历核查

本轮核对 12 个固定日期：11 个结果支撑日，以及未取得完整主结果的 6 月 18 日诊断日。共 96 个“日期 × 类别”单元。仅检查 BLS 的 CPI、PPI、Employment Situation，BEA 的国家 GDP 和 Personal Income and Outlays，以及 Fed 的 FOMC 会期、常规声明与纪要；不是全部新闻扫描。

**当前官方日历在 3 天列出 5 项限定事件，全部为纽约时间 08:30。** 2 月 13 日是 1 月 CPI；4 月 30 日是 Q1 GDP Advance 与 3 月 Personal Income and Outlays；6 月 25 日是 Q1 GDP Third 与 5 月 Personal Income and Outlays。五个官方发布页面的 embargo 标时与日历一致。网页标时不能证明实际首发时间或策略当时已收到。

""" + "\n".join(table) + """

在这 12 天的限定类别中，09:30 至 10:00 前、恰好 10:00、10:00 后均未列出匹配项。JSON 为这些时间范围及未知时间分别设栏；即使未来发现恰好 10:00 的事件，也不能把它当作早于 10:00 决策收到的信息。

“当前官方日历未列出”不是绝对的“无事件”。例如其他就业、生产率、进出口、公司财报、公司公告和地缘新闻均不属于这次类别；不能据此排除这些背景。FOMC 会期也不等于当时已知决议内容。这里不使用任何公布数值、预期差、股价结果或因果判断，不把日历重合写成某只股票反弹的原因。

资料在 2026 年 10 月 7 日追溯取得。BLS 年度页可能已经修订；BEA 两个带 `/2026` 的页面虽然返回 200，却没有日历行，因此先保留为无法判断，随后使用官网链接的当前 Full Schedule 和官方 JSON 对照。没有把空白页面误判为无发布。当前年度页和机器可读时间相符，不能据此还原历史当时的公告日程版本。

全部 11 次官方 GET、URL、时间、状态、哈希保留在 `macro-source-manifest.json`。未调用账户、期权、行情或下单接口，未分析实际收益；完整原文留在私有 `private-macro/`，公开文件只保留必要短引文和派生矩阵。

来源：[BLS 2026 年度日历](https://www.bls.gov/schedule/2026/home.htm)、[BEA 完整日历](https://www.bea.gov/news/schedule/full)、[BEA 官方机器可读日历](https://apps.bea.gov/API/signup/release_dates.json)、[Fed FOMC 日历](https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm)。匹配的五份发布页面链接及标时短引文见 `macro-calendar-review.json`。
"""
    (stage / "MACRO_CONTEXT.zh-CN.md").write_text(report)
    print(json.dumps({"status": result["status"], "summary": result["summary"], "calendar_row_counts": {category: len(events) for category, events in calendars.items()}, "get_requests": len(requests)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
