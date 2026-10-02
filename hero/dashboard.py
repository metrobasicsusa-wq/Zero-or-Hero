"""Render the static monitoring page (site/index.html) from the journal."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

TEMPLATE = Path(__file__).with_name("dashboard.html")
EVENT_KINDS = {"order", "close", "halt", "option_skip", "attempt_end", "attempt_end_confirmed", "earnings_watch"}
MAX_EVENTS = 200
REPO_BLOB = "https://github.com/metrobasicsusa-wq/Zero-or-Hero/blob/claude/cloud-paper-trading-ivwxqk"


def collect(journal: Path, cfg: dict) -> dict:
    equity_path = journal / "equity.csv"
    equity = list(csv.DictReader(open(equity_path))) if equity_path.exists() else []

    events, targets, gauges = [], None, None
    trades_path = journal / "trades.jsonl"
    if trades_path.exists():
        for line in open(trades_path):
            e = json.loads(line)
            if e["kind"] == "targets" and e.get("macro") is not None:
                gauges = {"ts": e.get("ts"), "gauges": e["macro"], "risk_off": e.get("macro_risk_off"),
                          "applied": e.get("macro_applied")}  # market data: rehearsal readings count too
            if e.get("dry_run"):
                continue
            if e["kind"] == "targets":
                targets = e
            elif e["kind"] in EVENT_KINDS:
                events.append(e)

    snap_path = journal / "snapshot.json"
    evo_path = journal / "evolution.md"
    reviews = sorted((journal / "reviews").glob("*.md")) if (journal / "reviews").exists() else []
    brief_dir = journal.resolve().parent / "macro" / "briefings"
    briefs = sorted(brief_dir.glob("20*.md")) if brief_dir.exists() else []
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "equity": equity,
        "snapshot": json.loads(snap_path.read_text()) if snap_path.exists() else None,
        "events": events[-MAX_EVENTS:][::-1],
        "targets": targets,
        "evolution": evo_path.read_text() if evo_path.exists() else "",
        "review": latest_review(reviews[-1], journal.name) if reviews else None,
        "config": cfg,
        "macro": gauges,
        "briefing": {"date": briefs[-1].stem, "text": briefs[-1].read_text()[:6000],
                     "url": f"{REPO_BLOB}/macro/briefings/{briefs[-1].name}"} if briefs else None,
    }


def latest_review(path: Path, journal_dir: str = "journal") -> dict:
    """Title, rule-based key points and whether the AI analysis has been appended."""
    lines = path.read_text().splitlines()
    points, in_points = [], False
    for line in lines:
        if line.startswith("#"):  # any heading ends the key-points section
            in_points = line.startswith("## 要点")
            continue
        if in_points and line.startswith("- "):
            points.append(line[2:])
    return {"date": path.stem, "title": lines[0].lstrip("# ") if lines else path.stem, "points": points,
            "has_ai": any(l.startswith("## AI 分析") for l in lines),
            "url": f"{REPO_BLOB}/{journal_dir}/reviews/{path.name}"}


def render(data: dict) -> str:
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return TEMPLATE.read_text().replace("/*__DATA__*/null", blob)


def write(journal: Path, cfg: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(collect(journal, cfg)))
