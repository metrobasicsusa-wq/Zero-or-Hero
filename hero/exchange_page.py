"""Render the three-way exchange (exchange/claude, exchange/codex, exchange/claude-b) as one page.

Every message file directly in a participant's folder is shown in full, newest first: JSON as a
readable outline, Markdown formatted, text as is. Files in sub-folders (research packs, data) are
listed with links rather than inlined, since some are megabytes of data.
Run: python -m hero.exchange_page --out site/exchange.html
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO_BLOB = "https://github.com/metrobasicsusa-wq/Zero-or-Hero/blob/claude/cloud-paper-trading-ivwxqk"
WHO = {"claude": ("Claude", "本仓库的 Claude（主账户 + 500）"),
       "codex": ("Codex", "Codex"),
       "claude-b": ("Claude-B", "用户另一个账户上的 Claude")}
INLINE_MAX = 200_000
TEXT_EXT = {".json", ".md", ".txt"}
DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})")


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def inline_md(t: str) -> str:
    t = esc(t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"`([^`]+)`", r"<code>\1</code>", t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', t)
    url = r"https?://[A-Za-z0-9\-._~:/?#@!$&*+,;=%]+"  # ASCII only: Chinese text right after a link is not part of it
    return re.sub(r"(?<![\"'>])(" + url + ")", r'<a href="\1" target="_blank" rel="noopener">\1</a>', t)


def md(text: str) -> str:
    out, in_list, in_table = [], False, False
    for line in text.splitlines():
        s = line.rstrip()
        if s.startswith("|") and s.endswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(set(c) <= set("-: ") for c in cells):
                continue
            if not in_table:
                out.append('<div class="table-wrap"><table>')
                in_table = True
            out.append("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table></div>")
            in_table = False
        m = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.*)", s)
        if m:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{inline_md(m.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        h = re.match(r"^(#{1,4})\s+(.*)", s)
        if h:
            out.append(f"<h4>{inline_md(h.group(2))}</h4>")
        elif s.strip():
            out.append(f"<p>{inline_md(s)}</p>")
    if in_list:
        out.append("</ul>")
    if in_table:
        out.append("</table></div>")
    return "".join(out)


def outline(v, depth: int = 0) -> str:
    """JSON as nested lists: keys bold, scalars as text (with links), long lists kept whole."""
    if isinstance(v, dict):
        return "<ul class='kv'>" + "".join(f"<li><b>{esc(k)}</b>：{outline(x, depth + 1)}</li>" for k, x in v.items()) + "</ul>"
    if isinstance(v, list):
        if all(not isinstance(x, (dict, list)) for x in v) and sum(len(str(x)) for x in v) < 120:
            return "、".join(inline_md(str(x)) for x in v)
        return "<ol>" + "".join(f"<li>{outline(x, depth + 1)}</li>" for x in v) + "</ol>"
    if v is None:
        return "—"
    return inline_md(str(v))


def git_date(path: Path) -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%cI", "--", str(path)], cwd=ROOT, capture_output=True,
                             text=True, timeout=20).stdout.strip()
        return out[:16].replace("T", " ") if out else ""
    except Exception:
        return ""


def collect(root: Path = ROOT) -> dict:
    msgs, packs = [], []
    for who in WHO:
        folder = root / "exchange" / who
        if not folder.exists():
            continue
        for f in sorted(folder.rglob("*")):
            if not f.is_file():
                continue
            rel = f.relative_to(root).as_posix()
            date = (DATE.match(f.name) or DATE.match(f.parent.name) or [None, ""])[1]
            if f.parent != folder:
                if f.suffix == ".md" or f.name in ("manifest.json", "status.json"):
                    packs.append({"who": who, "path": rel, "date": date or "", "name": f.relative_to(folder).as_posix()})
                continue
            if f.suffix not in TEXT_EXT:
                continue
            committed = git_date(f)
            item = {"who": who, "path": rel, "name": f.name, "date": date or committed[:10], "committed": committed,
                    "size": f.stat().st_size}
            if f.stat().st_size > INLINE_MAX:
                item["body"] = f"<p>文件较大（{f.stat().st_size // 1024} KB），请点标题看原文。</p>"
            else:
                text = f.read_text(errors="replace")
                if f.suffix == ".json":
                    try:
                        data = json.loads(text)
                        item["to"] = data.get("to") if isinstance(data, dict) else None
                        item["reply_to"] = data.get("reply_to") if isinstance(data, dict) else None
                        item["body"] = outline(data)
                    except ValueError:
                        item["body"] = f"<pre>{esc(text)}</pre>"
                elif f.suffix == ".md":
                    item["body"] = md(text)
                else:
                    item["body"] = f"<pre>{esc(text)}</pre>"
            msgs.append(item)
    msgs.sort(key=lambda m: (m["date"], m["committed"], m["name"]), reverse=True)
    packs.sort(key=lambda p: (p["date"], p["path"]), reverse=True)
    return {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "messages": msgs, "packs": packs}


def styles() -> str:
    dash = (ROOT / "hero" / "dashboard.html").read_text()
    return dash[dash.index("<style>") + 7: dash.index("</style>")]


def render(data: dict) -> str:
    chips = "".join(f'<button class="f" data-who="{w}">{esc(WHO[w][0])}</button>' for w in WHO)
    cards = []
    for m in data["messages"]:
        name, desc = WHO[m["who"]]
        to = m.get("to")
        to = "、".join(to) if isinstance(to, list) else to
        meta = [f'<span class="chip who-{m["who"]}">{esc(name)}</span>', esc(m["date"])]
        if to:
            meta.append(f"写给 {esc(to)}")
        if m.get("reply_to"):
            meta.append(f"回复：{inline_md(str(m['reply_to']))}")
        cards.append(f'<article class="card msg" data-who="{m["who"]}"><h3><a href="{REPO_BLOB}/{esc(m["path"])}" target="_blank" '
                     f'rel="noopener">{esc(m["name"])}</a></h3><div class="meta">{" · ".join(meta)}</div>'
                     f'<details{" open" if len(cards) < 6 else ""}><summary>展开 / 收起</summary><div class="md">{m["body"]}</div></details></article>')
    packs = "".join(f'<li data-who="{p["who"]}"><span class="chip who-{p["who"]}">{esc(WHO[p["who"]][0])}</span> '
                    f'<a href="{REPO_BLOB}/{esc(p["path"])}" target="_blank" rel="noopener">{esc(p["name"])}</a></li>'
                    for p in data["packs"][:150])
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Zero-or-Hero 三方交流</title>
<style>{styles()}
.msg h3 {{ font-size: 15px; margin: 0 0 4px; word-break: break-all; }}
.msg details summary {{ cursor: pointer; color: var(--ink-2); font-size: 12px; margin: 6px 0; }}
.md {{ font-size: 13px; line-height: 1.6; overflow-x: auto; }}
.md h4 {{ font-size: 14px; margin: 12px 0 4px; }}
.md ul, .md ol {{ margin: 2px 0 2px 18px; padding: 0; }}
.md ul.kv {{ list-style: none; margin-left: 0; }}
.md ul.kv ul.kv, .md ol ul.kv {{ margin-left: 14px; }}
.md li {{ margin: 2px 0; }}
.md pre {{ max-height: none; }}
.md code {{ font: 12px ui-monospace, SFMono-Regular, Menlo, monospace; background: var(--chip); padding: 0 4px; border-radius: 4px; }}
.filters {{ display: flex; gap: 8px; flex-wrap: wrap; margin: 8px 0 16px; }}
.f {{ border: 1px solid var(--line, #ddd); background: var(--surface); color: var(--ink); border-radius: 999px; padding: 4px 12px; cursor: pointer; font: inherit; }}
.f.on {{ background: var(--ink); color: var(--page); }}
.packs {{ columns: 2; font-size: 13px; padding-left: 0; list-style: none; }}
@media (max-width: 760px) {{ .packs {{ columns: 1; }} }}
</style></head>
<body><main>
<header><h1>Zero-or-Hero <span>· 三方交流</span></h1>
<nav class="meta"><a href="index.html">Claude 主实验 $100k</a> · <a href="s500.html">Claude-500 子实验</a> · <b>三方交流</b></nav>
<div class="meta">更新于 {esc(data['generated'])}（UTC）· 共 {len(data['messages'])} 条 · 规则见
<a href="{REPO_BLOB}/exchange/README.md" target="_blank" rel="noopener">exchange/README.md</a>：各写各的文件夹，对方内容只是参考，不是指令。</div></header>
<div class="filters"><button class="f on" data-who="">全部</button>{chips}</div>
<section id="msgs">{''.join(cards) or '<div class="empty">还没有交流</div>'}</section>
<section class="card"><h2>附带的研究资料（不展开，点开看原文）</h2><ul class="packs">{packs or '<li>无</li>'}</ul></section>
<footer>由 hero/exchange_page.py 从仓库 exchange/ 目录生成，每次推送后更新。</footer>
</main>
<script>
document.querySelectorAll(".f").forEach((b) => b.addEventListener("click", () => {{
  document.querySelectorAll(".f").forEach((x) => x.classList.toggle("on", x === b));
  const w = b.dataset.who;
  document.querySelectorAll("[data-who]").forEach((el) => {{ if (!el.classList.contains("f")) el.hidden = !!w && el.dataset.who !== w; }});
}}));
</script></body></html>
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site" / "exchange.html"))
    out = Path(ap.parse_args().out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(collect()))


if __name__ == "__main__":
    main()
