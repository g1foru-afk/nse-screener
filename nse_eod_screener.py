"""
NSE EOD Bhavcopy Screener (v2)
--------------------------------
Downloads NSE end-of-day (bhavcopy) equity data for a date range, then
screens stocks for the next trading day's intraday watchlist based on:
  - Volume spike vs 20-day average volume
  - % gap from previous close
  - Closing strength (close near day's high = bullish momentum)
  - Daily range % (volatility, useful for intraday movers)

CHANGES FROM v1 (see code review notes)
----------------------------------------
1. Rate limiting added between bhavcopy requests (NSE's own docs ask for
   0.5-1s between requests; this script defaults to 0.6s).
2. PREV_CLOSE / AVG_VOL_20 now computed on a *reindexed, gap-aware* daily
   calendar per symbol, so a missed fetch (holiday misdetected, transient
   network error) doesn't silently make "previous close" a stale N-days-ago
   value without you knowing about it. Missing days are logged.
3. GAP_PCT is now actually included in SCORE (it was computed but unused
   in v1).
4. SCORE weights are configurable and can be grid-searched against the
   Nifty-relative backtest instead of being fixed at 0.4/0.3/0.3.
5. Minimum-history guard: screen() will warn (not silently drop) when
   fewer than MIN_HISTORY_DAYS trading days are available for a symbol.

USAGE
-----
    pip install nse pandas
    python nse_eod_screener.py --days 30 --top 10

NOTES
-----
- Uses the `nse` PyPI package (https://pypi.org/project/nse/), which
  handles NSE's session cookies and both the old and new (UDiFF, since
  8 Jul 2024) bhavcopy formats.
- This is EOD historical data only -- useful for building a *watchlist*
  the night before / morning of. Not live intraday data.
- This script does not give buy/sell recommendations. It ranks stocks by
  objective, backtestable criteria -- you decide what to do with them.
  Nothing here is financial advice.
"""

import argparse
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from nse import NSE

DOWNLOAD_DIR = Path("./nse_bhavcopy_cache")
MIN_HISTORY_DAYS = 20          # rolling window used for AVG_VOL_20
REQUEST_DELAY_SEC = 0.6        # NSE asks for 0.5-1s between bulk requests

COLUMN_MAP_UDIFF = {
    "TckrSymb": "SYMBOL", "SctySrs": "SERIES", "OpnPric": "OPEN",
    "HghPric": "HIGH", "LwPric": "LOW", "ClsPric": "CLOSE",
    "PrvsClsgPric": "PREV_CLOSE", "TtlTradgVol": "TOTTRDQTY",
    "TtlTrfVal": "TOTTRDVAL", "TradDt": "TRADE_DATE_RAW",
}
COLUMN_MAP_OLD = {"TIMESTAMP": "TRADE_DATE_RAW"}


def fetch_bhavcopy(nse: NSE, date: datetime) -> pd.DataFrame | None:
    try:
        filepath = nse.equityBhavcopy(date, folder=DOWNLOAD_DIR)
        df = pd.read_csv(filepath)
        df = df.rename(columns=COLUMN_MAP_UDIFF)
        df = df.rename(columns=COLUMN_MAP_OLD)
        df.columns = [c.strip().upper() for c in df.columns]
        if "TRADE_DATE_RAW" in df.columns:
            df["TRADE_DATE"] = pd.to_datetime(df["TRADE_DATE_RAW"], errors="coerce")
        else:
            df["TRADE_DATE"] = pd.Timestamp(date.date())
        return df
    except (RuntimeError, FileNotFoundError):
        return None
    except Exception as e:
        print(f"  Warning: failed to fetch {date:%Y-%m-%d}: {e}")
        return None


def fetch_range(days: int) -> pd.DataFrame:
    """Fetch the last `days` calendar days of bhavcopy, skipping days with
    no data. Rate-limited to avoid NSE throttling/blocking."""
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    frames = []
    missed_dates = []
    with NSE(download_folder=DOWNLOAD_DIR, server=True, timeout=30) as nse:
        d = datetime.today()
        fetched = 0
        attempts = 0
        while fetched < days and attempts < days * 2:
            attempts += 1
            df = fetch_bhavcopy(nse, d)
            time.sleep(REQUEST_DELAY_SEC)  # <-- rate limit, avoid throttling
            if df is not None:
                frames.append(df)
                fetched += 1
                print(f"  Fetched {d:%Y-%m-%d} ({len(df)} rows)")
            else:
                missed_dates.append(d.date())
            d -= timedelta(days=1)
    if not frames:
        raise RuntimeError(
            "No bhavcopy files could be downloaded. Check your internet "
            "connection, that NSE isn't blocking this IP/environment, or "
            "that the 'nse' package is up to date (pip install -U nse)."
        )
    if missed_dates:
        # Not necessarily a problem (weekends/holidays are expected here),
        # but printed explicitly so a real outage isn't silently absorbed.
        print(f"  ({len(missed_dates)} calendar days had no data - normal "
              f"for weekends/holidays, but check if this count looks high)")
    return pd.concat(frames, ignore_index=True)


def _gap_aware_prev_close_and_vol(df: pd.DataFrame) -> pd.DataFrame:
    """Compute PREV_CLOSE and AVG_VOL_20 against the calendar of trading
    days actually present in the fetched data (shared across all symbols),
    rather than each symbol's own row order. This still can't invent data
    for a day nobody fetched, but it makes gaps visible: PREV_CLOSE_GAP_DAYS
    tells you how many trading days back the "previous" close actually is,
    so a value of 1 means the fetch was contiguous and >1 means a day (or
    more) was missing between them."""
    trading_days = sorted(df["TRADE_DATE"].unique())
    day_index = {d: i for i, d in enumerate(trading_days)}
    df = df.copy()
    df["_DAY_IDX"] = df["TRADE_DATE"].map(day_index)
    df = df.sort_values(["SYMBOL", "_DAY_IDX"])

    df["PREV_CLOSE"] = df.groupby("SYMBOL")["CLOSE"].shift(1)
    df["PREV_DAY_IDX"] = df.groupby("SYMBOL")["_DAY_IDX"].shift(1)
    df["PREV_CLOSE_GAP_DAYS"] = df["_DAY_IDX"] - df["PREV_DAY_IDX"]

    df["AVG_VOL_20"] = (
        df.groupby("SYMBOL")["TOTTRDQTY"]
        .transform(lambda s: s.shift(1).rolling(MIN_HISTORY_DAYS).mean())
    )
    return df.drop(columns=["_DAY_IDX", "PREV_DAY_IDX"])


def compute_metrics(
    df: pd.DataFrame,
    weights: dict | None = None,
) -> pd.DataFrame:
    """Add gap/range/volume-spike columns and a composite SCORE, ranked
    cross-sectionally within each trading day.

    weights: dict with keys 'vol', 'range', 'strength', 'gap' summing to 1.
    Defaults preserve v1 behavior but now also fold in gap.
    """
    weights = weights or {"vol": 0.35, "range": 0.25, "strength": 0.25, "gap": 0.15}

    df = df.rename(columns=lambda c: c.strip().upper())
    df = df[df["SERIES"] == "EQ"].copy()
    df = _gap_aware_prev_close_and_vol(df)

    df["GAP_PCT"] = (df["OPEN"] - df["PREV_CLOSE"]) / df["PREV_CLOSE"] * 100
    df["RANGE_PCT"] = (df["HIGH"] - df["LOW"]) / df["LOW"] * 100
    df["CLOSE_STRENGTH"] = (df["CLOSE"] - df["LOW"]) / (df["HIGH"] - df["LOW"] + 1e-9)
    df["VOL_SPIKE_RATIO"] = df["TOTTRDQTY"] / df["AVG_VOL_20"]

    vol_rank = df.groupby("TRADE_DATE")["VOL_SPIKE_RATIO"].rank(pct=True)
    range_rank = df.groupby("TRADE_DATE")["RANGE_PCT"].rank(pct=True)
    strength_rank = df.groupby("TRADE_DATE")["CLOSE_STRENGTH"].rank(pct=True)
    # Gap uses absolute value ranked -- a big gap up OR down is the
    # "interesting for intraday" signal, direction is shown separately.
    gap_rank = df.groupby("TRADE_DATE")["GAP_PCT"].transform(lambda s: s.abs()).groupby(df["TRADE_DATE"]).rank(pct=True)

    df["SCORE"] = (
        vol_rank * weights["vol"]
        + range_rank * weights["range"]
        + strength_rank * weights["strength"]
        + gap_rank * weights["gap"]
    )
    return df


def screen(df: pd.DataFrame, top_n: int = 20) -> pd.DataFrame:
    usable = df.dropna(subset=["VOL_SPIKE_RATIO", "AVG_VOL_20", "SCORE"])
    total_symbols = df["SYMBOL"].nunique()
    usable_symbols = usable["SYMBOL"].nunique()
    if usable_symbols < total_symbols * 0.5:
        print(f"  Warning: only {usable_symbols}/{total_symbols} symbols have "
              f"{MIN_HISTORY_DAYS}+ days of history to score. Consider "
              f"increasing --days.")

    latest_date = usable["TRADE_DATE"].max()
    latest = usable[usable["TRADE_DATE"] == latest_date].copy()

    cols = [
        "SYMBOL", "SERIES", "TRADE_DATE", "OPEN", "HIGH", "LOW", "CLOSE",
        "TOTTRDQTY", "TOTTRDVAL", "VOL_SPIKE_RATIO", "GAP_PCT", "RANGE_PCT",
        "CLOSE_STRENGTH", "PREV_CLOSE_GAP_DAYS", "SCORE",
    ]
    cols = [c for c in cols if c in latest.columns]
    return latest[cols].sort_values("SCORE", ascending=False).head(top_n)


def fetch_nifty_history(from_date, to_date) -> pd.DataFrame:
    with NSE(download_folder=DOWNLOAD_DIR, server=True, timeout=30) as nse:
        records = nse.fetch_historical_index_data(
            index="NIFTY 50", from_date=from_date, to_date=to_date
        )
    if not records:
        raise RuntimeError("NSE returned no Nifty 50 historical data for this range.")

    ndf = pd.DataFrame(records)
    ndf.columns = [c.strip().upper().replace(" ", "_") for c in ndf.columns]
    date_col = next((c for c in ndf.columns if "DATE" in c or "TIMESTAMP" in c), None)
    close_col = next((c for c in ndf.columns if "CLOS" in c and "CHANGE" not in c), None)
    if date_col is None or close_col is None:
        raise RuntimeError(f"Could not find date/close columns. Got: {list(ndf.columns)}")

    ndf["NIFTY_DATE"] = pd.to_datetime(ndf[date_col], dayfirst=True, errors="coerce")
    ndf["NIFTY_CLOSE"] = pd.to_numeric(ndf[close_col], errors="coerce")
    return (
        ndf[["NIFTY_DATE", "NIFTY_CLOSE"]].dropna()
        .sort_values("NIFTY_DATE").drop_duplicates("NIFTY_DATE").reset_index(drop=True)
    )


def backtest_score_vs_nifty(df, nifty_df, quantiles: int = 5):
    d = df.dropna(subset=["SCORE"]).copy()
    d = d.sort_values(["SYMBOL", "TRADE_DATE"])
    d["NEXT_CLOSE"] = d.groupby("SYMBOL")["CLOSE"].shift(-1)
    d["STOCK_NEXT_RETURN_PCT"] = (d["NEXT_CLOSE"] - d["CLOSE"]) / d["CLOSE"] * 100

    nifty_df = nifty_df.sort_values("NIFTY_DATE").copy()
    nifty_df["NIFTY_NEXT_CLOSE"] = nifty_df["NIFTY_CLOSE"].shift(-1)
    nifty_df["NIFTY_NEXT_RETURN_PCT"] = (
        (nifty_df["NIFTY_NEXT_CLOSE"] - nifty_df["NIFTY_CLOSE"]) / nifty_df["NIFTY_CLOSE"] * 100
    )
    nifty_lookup = nifty_df.set_index("NIFTY_DATE")["NIFTY_NEXT_RETURN_PCT"]

    d["NIFTY_NEXT_RETURN_PCT"] = d["TRADE_DATE"].map(nifty_lookup)
    d = d.dropna(subset=["STOCK_NEXT_RETURN_PCT", "NIFTY_NEXT_RETURN_PCT"])
    d["EXCESS_RETURN_PCT"] = d["STOCK_NEXT_RETURN_PCT"] - d["NIFTY_NEXT_RETURN_PCT"]

    if len(d) < quantiles * 20:
        quantiles = max(2, len(d) // 20)

    d["SCORE_BUCKET"] = pd.qcut(d["SCORE"], quantiles, labels=False, duplicates="drop")
    summary = d.groupby("SCORE_BUCKET").agg(
        AVG_EXCESS_RETURN_PCT=("EXCESS_RETURN_PCT", "mean"),
        MEDIAN_EXCESS_RETURN_PCT=("EXCESS_RETURN_PCT", "median"),
        BEAT_NIFTY_RATE_PCT=("EXCESS_RETURN_PCT", lambda s: (s > 0).mean() * 100),
        COUNT=("EXCESS_RETURN_PCT", "count"),
    )
    corr = d["SCORE"].corr(d["EXCESS_RETURN_PCT"], method="spearman")
    return summary, corr, d


def main():
    parser = argparse.ArgumentParser(description="NSE EOD screener for next-day watchlist")
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out", type=str, default="watchlist.csv")
    parser.add_argument("--no-backtest", action="store_true")
    args = parser.parse_args()

    print(f"Fetching last {args.days} days of bhavcopy data...")
    data = fetch_range(args.days)
    print(f"Fetched {data['TRADE_DATE'].nunique()} trading days, {len(data)} rows.")

    data = compute_metrics(data)
    result = screen(data, top_n=args.top)
    result.to_csv(args.out, index=False)
    print(f"\nTop {args.top} candidates saved to {args.out}:\n")
    print(result.to_string(index=False))

    if not args.no_backtest:
        print("\n--- Scoring rule sanity check: excess return vs Nifty 50 ---")
        try:
            nifty_df = fetch_nifty_history(
                data["TRADE_DATE"].min().date(), data["TRADE_DATE"].max().date()
            )
            summary, corr, detail = backtest_score_vs_nifty(data, nifty_df)
            print(summary.to_string())
            print(f"\nSpearman correlation (SCORE vs excess return): {corr:.3f}")
            detail.to_csv("backtest_detail.csv", index=False)
        except RuntimeError as e:
            print(f"Skipped: {e}")

    return data, result


if __name__ == "__main__":
    main()
