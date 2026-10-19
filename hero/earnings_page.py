"""Render the earnings page of the site (site/earnings.html).

Owner, 10-19: the platform should show the earnings calendar. Three parts, all from files already in the repo:
  1. tonight's after-close and tomorrow's before-open reports across the US market (the latest
     research/earnings-forecast/YYYY-MM-DD.json), with the options' implied move, Claude's judgment and, once the
     reaction day has closed, the result;
  2. our pool's reports for the next three weeks (data/earnings.json, Alpha Vantage, refreshed each morning),
     names either account holds marked;
  3. the forecast scorecard (research/earnings-forecast/scorecard.md).
Run: python -m hero.earnings_page --out site/earnings.html (pages.yml does it after every push).
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from hero.exchange_page import REPO_BLOB, esc, md, styles

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
WEEKDAY = "一二三四五六日"
TOP = 25


def held(root: Path) -> dict[str, list[str]]:
    """Underlying symbol -> which accounts hold it (shares or options)."""
    out: dict[str, list[str]] = {}
    for d, name in (("journal", "主账户"), ("journal-s500", "多账本")):
        try:
            pos = json.loads((root / d / "snapshot.json").read_text()).get("positions") or []
        except Exception:
            continue
        for p in pos:
            sym = str(p.get("symbol") or "")
            und = sym[:-15] if len(sym) > 15 and sym[-9] in "CP" else sym
            if und and name not in out.setdefault(und, []):
                out[und].append(name)
    return out


def upcoming(root: Path, today: date, days: int = 21) -> tuple[list[dict], str]:
    """Our pool's reports from today on, one row per date."""
    try:
        cal = json.loads((root / "data" / "earnings.json").read_text())
    except Exception:
        return [], ""
    end = (today + timedelta(days=days)).isoformat()
    by: dict[str, dict[str, list[str]]] = {}
    for sym, rs in (cal.get("reports") or {}).items():
        for r in rs:
            if today.isoformat() <= r["date"] <= end:
                t = r.get("time") or ""
                slot = "pre" if "pre" in t else "post" if "post" in t else "unknown"
                by.setdefault(r["date"], {"pre": [], "post": [], "unknown": []})[slot].append(sym)
    return [{"date": d, **{k: sorted(v) for k, v in by[d].items()}} for d in sorted(by)], cal.get("fetched_at", "")


def latest_forecast(root: Path) -> tuple[dict | None, dict]:
    """The newest forecast file and its judgment calls by symbol."""
    folder = root / "research" / "earnings-forecast"
    files = sorted(folder.glob("20??-??-??.json"))
    if not files:
        return None, {}
    rep = json.loads(files[-1].read_text())
    calls = {}
    j = folder / f"{rep['date']}.judgment.json"
    if j.exists():
        calls = {c["symbol"]: c for c in json.loads(j.read_text()).get("calls") or []}
    return rep, calls


def day_label(d: str) -> str:
    x = date.fromisoformat(d)
    return f"{x:%m-%d} 周{WEEKDAY[x.weekday()]}"


def chips(syms: list[str], hold: dict[str, list[str]]) -> str:
    if not syms:
        return '<span class="muted">—</span>'
    return " ".join(f'<b title="{esc("、".join(hold[s]))}持有">{esc(s)}★</b>' if s in hold else esc(s) for s in syms)


def forecast_table(rep: dict | None, calls: dict, hold: dict[str, list[str]]) -> str:
    if not rep:
        return '<div class="empty">还没有财报预测</div>'
    rows = []
    for r in rep.get("reports", [])[:TOP]:
        c = calls.get(r["symbol"])
        imp = f"{r['implied_move']:.1%}" if r.get("implied_move") else "—"
        if r.get("implied_move") and r.get("weekly") is False:
            imp += "（月）"
        prior = f"{r['prior_avg_abs']:.1%}" if r.get("prior_avg_abs") else "—"
        judge = "—"
        if c:
            dirn = {"up": "涨", "down": "跌"}.get(c.get("direction"), "不判")
            big = {True: "会", False: "不会"}.get(c.get("big"), "—")
            judge = f"{dirn} · 超过定价{big} · 信心 {float(c.get('confidence') or 0):.2f}"
        res = r.get("result")
        out = "等反应日收盘"
        if res:
            g = res.get("gap_pre", res.get("gap"))
            ok = ""
            if c and c.get("direction") in ("up", "down"):
                ok = " · 方向" + ("对" if (c["direction"] == "up") == res["up"] else "错")
            out = f"开盘 {g:+.1%} · 收盘 {res['move']:+.1%}{' · 超过定价' if res.get('big') else ''}{ok}"
        name = f"<b>{esc(r['symbol'])}★</b>" if r["symbol"] in hold else esc(r["symbol"])
        when = f"{r['report_date'][5:]} {'盘后' if r['time'] == 'post-market' else '盘前'}"
        rows.append(f"<tr><td>{name}</td><td>{esc(when)}</td>"
                    f"<td>{esc(r['reaction_day'][5:])}</td><td>${r['spot']:,.2f}</td><td>{imp}</td><td>{prior}</td>"
                    f"<td>{r['p_big']:.0%}</td><td>{esc(judge)}</td><td>{esc(out)}</td></tr>")
    more = len(rep.get("reports", [])) - TOP
    link = f'{REPO_BLOB}/research/earnings-forecast/{rep["date"]}.md'
    return (f'<div class="meta">{esc(rep["date"])} 记录：当晚盘后、下一个交易日盘前，全美股 $5 以上、有期权的 '
            f'{len(rep.get("reports", []))} 只，按前一天成交额排序'
            + (f'，这里显示前 {TOP} 只，<a href="{link}" target="_blank" rel="noopener">完整列表</a>' if more > 0 else "")
            + '。隐含 = 平值跨式 ÷ 股价；（月）= 只有月度期权，偏大。</div>'
            '<div class="table-wrap"><table><tr><th>股票</th><th>发财报</th><th>反应日</th><th>股价</th><th>期权定价</th>'
            '<th>过去平均反应</th><th>超过定价的基线</th><th>Claude 判断</th><th>结果</th></tr>'
            + "".join(rows) + "</table></div>")


def collect(root: Path = ROOT, today: date | None = None) -> dict:
    today = today or datetime.now(ET).date()
    rows, fetched = upcoming(root, today)
    rep, calls = latest_forecast(root)
    sc = root / "research" / "earnings-forecast" / "scorecard.md"
    score = sc.read_text() if sc.exists() else ""
    return {"generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"), "today": today.isoformat(),
            "hold": held(root), "pool": rows, "fetched": fetched, "forecast": rep, "calls": calls,
            "scorecard": "\n".join(score.splitlines()[1:])}


def render(data: dict) -> str:
    hold = data["hold"]
    pool = "".join(f'<tr{" class=today" if r["date"] == data["today"] else ""}><td>{esc(day_label(r["date"]))}</td>'
                   f'<td>{chips(r["pre"], hold)}</td><td>{chips(r["post"], hold)}</td><td>{chips(r["unknown"], hold)}</td></tr>'
                   for r in data["pool"])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zero-or-Hero 财报</title>
<style>{styles()}
.md {{ font-size: 13px; line-height: 1.6; overflow-x: auto; }}
.md h4 {{ font-size: 14px; margin: 12px 0 4px; }}
tr.today td {{ background: var(--chip); }}
.muted {{ color: var(--ink-2); }}
td, th {{ vertical-align: top; }}
</style></head>
<body><main>
<header><h1>Zero-or-Hero <span>· 财报</span></h1>
<nav class="meta"><a href="index.html">Claude 主实验 $100k</a> · <a href="s500.html">Claude-500 多账本 $50k</a> · <a href="exchange.html">三方交流</a> · <b>财报</b></nav>
<div class="meta">更新于 {esc(data['generated'])}（UTC）· ★ = 两个账户里正拿着（正股或期权）· 模拟盘，参考记录，不是投资建议</div></header>
<section class="card"><h2>今晚盘后 · 明早盘前（全美股）</h2>{forecast_table(data['forecast'], data['calls'], hold)}</section>
<section class="card"><h2>我们股票池接下来三周</h2>
<div class="meta">data/earnings.json（Alpha Vantage，{esc(data['fetched'] or '—')} 更新）。有些日期是数据商按往年推算的，以公司公告为准。</div>
<div class="table-wrap"><table><tr><th>日期</th><th>盘前</th><th>盘后</th><th>时间未知</th></tr>{pool or '<tr><td colspan=4>没有</td></tr>'}</table></div></section>
<section class="card"><h2>记分卡</h2><div class="md">{md(data['scorecard']) or '还没有'}</div></section>
<footer>由 hero/earnings_page.py 从 data/earnings.json 和 research/earnings-forecast/ 生成，每次推送后更新。</footer>
</main></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site" / "earnings.html"))
    out = Path(ap.parse_args().out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(collect()))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
