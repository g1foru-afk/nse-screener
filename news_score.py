"""
News validation / scoring module
----------------------------------
Adds a NEWS_SCORE (-1 to +1) and a HARD_EXCLUDE flag to each candidate
symbol, using only free public sources:

  1. Google News RSS search per symbol (no API key required).
  2. NSE corporate actions via nse.actions() (board meetings, dividends,
     splits, etc. -- best-effort; NSE's exact method surface changes
     between package versions, so this call is wrapped defensively).

IMPORTANT LIMITATIONS (read before trusting this)
---------------------------------------------------
- This is keyword matching, NOT real NLP sentiment analysis. It will
  miss sarcasm, nuance, and context, and can misfire on headlines that
  merely mention a keyword without it applying to the company itself.
- A "clean" NEWS_SCORE does not mean there's no bad news -- it means no
  headline matched these particular keyword lists in the last N days.
  Always read the actual headlines before acting (they're kept in the
  output for that reason).
- This is not financial advice and cannot substitute for reading the
  underlying articles / filings yourself.
"""

import time
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import requests

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=en-IN&gl=IN&ceid=IN:en"
REQUEST_DELAY_SEC = 1.0  # be polite to Google News too
MAX_HEADLINES_PER_SYMBOL = 15
NEWS_LOOKBACK_LABEL = "recent"  # Google News RSS doesn't take a date range param cleanly

POSITIVE_KEYWORDS = [
    "beats estimates", "record profit", "profit jumps", "profit surges",
    "wins order", "wins contract", "bags order", "upgraded to buy",
    "upgrade", "buyback", "special dividend", "expansion plan",
    "capacity expansion", "strong guidance", "raises guidance",
    "stake acquisition", "new plant", "record revenue",
]

NEGATIVE_KEYWORDS = [
    "misses estimates", "profit falls", "profit plunges", "downgrade",
    "downgraded to sell", "resignation", "resigns", "delay in results",
    "margin pressure", "cost overrun", "weak guidance", "cuts guidance",
    "loss widens", "stock slides", "shares slide", "shares tank",
]

# Any hit here forces HARD_EXCLUDE regardless of technical SCORE --
# these are the kind of headlines that should stop you cold, not just
# nudge a weighted average.
SEVERE_NEGATIVE_KEYWORDS = [
    "sebi ban", "sebi bars", "fraud", "insolvency", "cbi raid",
    "ed raid", "income tax raid", "trading suspended", "delisting",
    "delisted", "auditor resign", "forensic audit", "default on",
    "loan default", "bankruptcy",
]


@dataclass
class NewsResult:
    symbol: str
    headlines: list = field(default_factory=list)   # list of (title, link)
    positive_hits: list = field(default_factory=list)
    negative_hits: list = field(default_factory=list)
    severe_hits: list = field(default_factory=list)
    news_score: float = 0.0
    hard_exclude: bool = False


def _fetch_google_news_headlines(symbol: str, company_hint: str = "") -> list:
    """Returns list of (title, link). Best-effort: returns [] on any
    network/parse failure rather than raising, since news is an
    enrichment layer, not something that should crash the run."""
    query = urllib.parse.quote(f"{symbol} {company_hint} NSE stock".strip())
    url = GOOGLE_NEWS_RSS.format(query=query)
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        items = root.findall(".//item")[:MAX_HEADLINES_PER_SYMBOL]
        return [(it.findtext("title") or "", it.findtext("link") or "") for it in items]
    except Exception as e:
        print(f"  News fetch failed for {symbol}: {e}")
        return []


def _fetch_nse_actions(nse, symbol: str) -> list:
    """Best-effort corporate actions lookup. `nse.actions()` exists in the
    `nse` package but its exact filter parameters (symbol/segment/from-to
    date) vary by version -- verify against
    https://bennythadikaran.github.io/NseIndiaApi/api.html for your
    installed version before relying on this being symbol-filtered.
    Wrapped so a signature mismatch degrades to 'no data' rather than
    crashing the whole run."""
    try:
        actions = nse.actions(symbol=symbol)
        return actions if isinstance(actions, list) else []
    except TypeError:
        try:
            all_actions = nse.actions()
            return [a for a in all_actions if symbol.upper() in str(a).upper()]
        except Exception:
            return []
    except Exception:
        return []


def score_symbol_news(symbol: str, nse=None, company_hint: str = "") -> NewsResult:
    result = NewsResult(symbol=symbol)
    headlines = _fetch_google_news_headlines(symbol, company_hint)
    time.sleep(REQUEST_DELAY_SEC)
    result.headlines = headlines

    text_blob = " || ".join(t.lower() for t, _ in headlines)

    result.positive_hits = [kw for kw in POSITIVE_KEYWORDS if kw in text_blob]
    result.negative_hits = [kw for kw in NEGATIVE_KEYWORDS if kw in text_blob]
    result.severe_hits = [kw for kw in SEVERE_NEGATIVE_KEYWORDS if kw in text_blob]

    if nse is not None:
        actions = _fetch_nse_actions(nse, symbol)
        for a in actions:
            a_text = str(a).lower()
            if any(kw in a_text for kw in SEVERE_NEGATIVE_KEYWORDS):
                result.severe_hits.append(f"[corp-action] {a_text[:80]}")

    pos, neg = len(result.positive_hits), len(result.negative_hits)
    denom = pos + neg
    result.news_score = 0.0 if denom == 0 else (pos - neg) / denom
    result.hard_exclude = len(result.severe_hits) > 0

    return result


def enrich_watchlist_with_news(watchlist_df, nse=None, company_hints: dict = None):
    """Takes the screener's top-N DataFrame (must have a SYMBOL column),
    fetches news for each, and returns the DataFrame with NEWS_SCORE,
    HARD_EXCLUDE, and top headlines added, re-sorted by a FINAL_SCORE
    that combines technical SCORE with NEWS_SCORE, and with hard-excluded
    symbols dropped to the bottom with a visible flag (not silently
    removed -- you should see WHY something got excluded)."""
    company_hints = company_hints or {}
    rows = []
    for _, row in watchlist_df.iterrows():
        symbol = row["SYMBOL"]
        nr = score_symbol_news(symbol, nse=nse, company_hint=company_hints.get(symbol, ""))
        row = row.copy()
        row["NEWS_SCORE"] = nr.news_score
        row["HARD_EXCLUDE"] = nr.hard_exclude
        row["NEWS_POSITIVE_HITS"] = "; ".join(nr.positive_hits)
        row["NEWS_NEGATIVE_HITS"] = "; ".join(nr.negative_hits)
        row["NEWS_SEVERE_HITS"] = "; ".join(nr.severe_hits)
        row["TOP_HEADLINES"] = " | ".join(t for t, _ in nr.headlines[:3])
        row["HEADLINE_LINKS"] = " | ".join(l for _, l in nr.headlines[:3])
        rows.append(row)

    out = __import__("pandas").DataFrame(rows)
    # FINAL_SCORE blends technical score (0-1 already) with news score
    # rescaled to 0-1; weighted 70/30 technical/news by default.
    out["NEWS_SCORE_0_1"] = (out["NEWS_SCORE"] + 1) / 2
    out["FINAL_SCORE"] = out["SCORE"] * 0.7 + out["NEWS_SCORE_0_1"] * 0.3
    out.loc[out["HARD_EXCLUDE"], "FINAL_SCORE"] = -1  # sink to bottom, stay visible

    return out.sort_values("FINAL_SCORE", ascending=False).reset_index(drop=True)
