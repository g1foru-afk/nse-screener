"""
Builds a single static HTML file (docs/index.html) from the final
watchlist DataFrame -- no server, no JS framework, safe to host on
GitHub Pages for free. Re-run daily; each run overwrites the file.
"""

import html
from datetime import datetime

import pandas as pd

TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>NSE Pre-Market Watchlist — {date}</title>
<style>
  :root {{
    --bg: #0f1115; --card: #171a21; --border: #262b36;
    --text: #e8eaed; --muted: #9aa2b1;
    --green: #3ecf8e; --red: #ef5b5b; --amber: #e8b339;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; padding: 24px; background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  }}
  h1 {{ font-size: 20px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--muted); font-size: 13px; margin-bottom: 20px; }}
  .disclaimer {{
    background: #1d2230; border: 1px solid var(--border); border-radius: 8px;
    padding: 12px 14px; font-size: 12px; color: var(--muted); margin-bottom: 20px;
  }}
  .grid {{ display: grid; gap: 14px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }}
  .card {{
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    padding: 16px;
  }}
  .card.excluded {{ opacity: 0.55; border-color: var(--red); }}
  .row {{ display: flex; justify-content: space-between; align-items: baseline; }}
  .symbol {{ font-size: 17px; font-weight: 600; }}
  .rank {{ color: var(--muted); font-size: 12px; }}
  .score {{ font-size: 22px; font-weight: 700; color: var(--green); }}
  .metrics {{ display: grid; grid-template-columns: 1fr 1fr; gap: 6px 12px; margin: 12px 0; font-size: 12px; }}
  .metrics div span {{ color: var(--muted); }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 999px; font-size: 11px; margin: 2px 4px 2px 0; }}
  .badge.pos {{ background: rgba(62,207,142,0.15); color: var(--green); }}
  .badge.neg {{ background: rgba(239,91,91,0.15); color: var(--red); }}
  .badge.excl {{ background: var(--red); color: #fff; font-weight: 600; }}
  .headlines {{ font-size: 12px; color: var(--muted); margin-top: 10px; line-height: 1.5; }}
  .headlines a {{ color: #7aa2f7; text-decoration: none; }}
  footer {{ margin-top: 30px; color: var(--muted); font-size: 11px; }}
</style>
</head>
<body>
  <h1>NSE Pre-Market Watchlist</h1>
  <div class="subtitle">Generated {date} from EOD bhavcopy through {last_trade_date} · not financial advice</div>
  <div class="disclaimer">
    Ranked by objective, backtestable criteria (volume spike, range, closing
    strength, gap) blended with a keyword-based news check. This is a
    screening tool, not a recommendation to buy or sell. Keyword news
    matching can miss nuance — read the linked headlines yourself before
    acting, especially for anything flagged excluded.
  </div>
  <div class="grid">
    {cards}
  </div>
  <footer>Source: NSE EOD bhavcopy · Nifty 50 used as benchmark in backtest · Screener is EOD data only, not live intraday.</footer>
</body>
</html>
"""

CARD_TEMPLATE = """
<div class="card {excluded_class}">
  <div class="row">
    <span class="symbol">{symbol}</span>
    <span class="rank">#{rank}</span>
  </div>
  <div class="row">
    <span class="score">{final_score:.2f}</span>
    <span class="rank">final score</span>
  </div>
  <div class="metrics">
    <div><span>Close:</span> {close}</div>
    <div><span>Gap %:</span> {gap_pct:.2f}</div>
    <div><span>Range %:</span> {range_pct:.2f}</div>
    <div><span>Vol spike:</span> {vol_spike:.2f}x</div>
    <div><span>Close strength:</span> {close_strength:.2f}</div>
    <div><span>Tech score:</span> {tech_score:.2f}</div>
  </div>
  {exclude_badge}
  {pos_badges}
  {neg_badges}
  <div class="headlines">{headlines}</div>
</div>
"""


def _badge(items, css_class):
    return "".join(f'<span class="badge {css_class}">{html.escape(i)}</span>' for i in items[:4])


def _headline_links(titles: str, links: str) -> str:
    titles_list = [t for t in (titles or "").split(" | ") if t]
    links_list = [l for l in (links or "").split(" | ") if l]
    out = []
    for t, l in zip(titles_list, links_list):
        out.append(f'<a href="{html.escape(l)}" target="_blank">{html.escape(t)}</a>')
    return "<br>".join(out) if out else "No recent headlines found."


def build_dashboard(final_df: pd.DataFrame, out_path: str = "docs/index.html", top_n: int = 10):
    df = final_df.head(top_n).reset_index(drop=True)
    cards = []
    for i, row in df.iterrows():
        excluded = bool(row.get("HARD_EXCLUDE", False))
        pos_hits = [h for h in str(row.get("NEWS_POSITIVE_HITS", "")).split("; ") if h]
        neg_hits = [h for h in str(row.get("NEWS_NEGATIVE_HITS", "")).split("; ") if h]
        severe_hits = [h for h in str(row.get("NEWS_SEVERE_HITS", "")).split("; ") if h]

        cards.append(CARD_TEMPLATE.format(
            symbol=html.escape(str(row["SYMBOL"])),
            rank=i + 1,
            final_score=row.get("FINAL_SCORE", 0.0),
            close=row.get("CLOSE", "-"),
            gap_pct=row.get("GAP_PCT", 0.0),
            range_pct=row.get("RANGE_PCT", 0.0),
            vol_spike=row.get("VOL_SPIKE_RATIO", 0.0),
            close_strength=row.get("CLOSE_STRENGTH", 0.0),
            tech_score=row.get("SCORE", 0.0),
            excluded_class="excluded" if excluded else "",
            exclude_badge=_badge(severe_hits or (["EXCLUDED — severe news flag"] if excluded else []), "excl"),
            pos_badges=_badge(pos_hits, "pos"),
            neg_badges=_badge(neg_hits, "neg"),
            headlines=_headline_links(row.get("TOP_HEADLINES", ""), row.get("HEADLINE_LINKS", "")),
        ))

    last_trade_date = ""
    if "TRADE_DATE" in df.columns and len(df):
        last_trade_date = str(pd.to_datetime(df["TRADE_DATE"].iloc[0]).date())

    html_out = TEMPLATE.format(
        date=datetime.now().strftime("%Y-%m-%d %H:%M IST"),
        last_trade_date=last_trade_date,
        cards="".join(cards),
    )

    import os
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return out_path
