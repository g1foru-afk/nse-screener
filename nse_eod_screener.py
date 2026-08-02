"""
NSE EOD Bhavcopy Screener
--------------------------
Downloads NSE end-of-day (bhavcopy) equity data for a date range, then
screens stocks for the next trading day's intraday watchlist based on:
  - Volume spike vs 20-day average volume
  - % gap from previous close
  - Closing strength (close near day's high = bullish momentum)
  - Daily range % (volatility, useful for intraday movers)

NEW: price-range and explicit bullish filters
------------------------------------------------
--min-price / --max-price restrict the shortlist to a price band (e.g.
500-2000) BEFORE ranking, so cheap/expensive names never crowd out
mid-priced ones just by scoring well on volume/range.

--bullish-only adds an explicit directional requirement, built from
metrics this screener already computes (not a new, unvalidated signal):
  - Close > Previous Close (the stock actually closed up that day)
  - Close Strength >= --min-close-strength (default 0.6, i.e. closed in
    the upper 40% of the day's range -- shows buyers were in control
    into the close, not a stock that spiked and gave it back)
This is a reasonable, inspectable filter built on existing columns, NOT
a separately validated "this predicts tomorrow" signal -- treat it the
same way as the rest of this screener's SCORE: a starting shortlist to
investigate, not a guarantee, and check backtest_score_vs_nifty's output
periodically to see whether the ranking rule is actually adding value.

USAGE
-----
    pip install nse pandas
    python nse_eod_screener.py --days 30 --top 10 --min-price 500 --max-price 2000 --bullish-only

NOTES
-----
- NSE retired the old "archives.nseindia.com/.../cmDDMMMYYYYbhav.csv.zip"
  format on 8 July 2024 (switched to the new "UDiFF" file format on a new
  domain). This script uses the `nse` PyPI package (a maintained,
  open-source client), which handles NSE's session cookies and picks the
  correct file format automatically for both old and new dates.
- The backtest compares each stock's next-day return against the Nifty
  50's next-day return (excess return), not the stock's raw return in
  isolation.
- This is EOD historical data only -- useful for building a *watchlist*
  the night before. For live intraday price/volume during market hours
  you need a broker API (Kite Connect, Upstox, Fyers, etc.).
- This script does not give buy/sell recommendations. It ranks stocks
  by objective, backtestable criteria -- you decide what to do with them.
"""

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from nse import NSE

DOWNLOAD_DIR = Path("./nse_bhavcopy_cache")

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
    DOWNLOAD_DIR.mkdir(exist_ok=True)
    frames = []
    with NSE(download_folder=DOWNLOAD_DIR) as nse:
        d = datetime.today()
        fetched = 0
        attempts = 0
        while fetched < days and attempts < days * 2:
            attempts += 1
            df = fetch_bhavcopy(nse, d)
            if df is not None:
                frames.append(df)
                fetched += 1
                print(f"  Fetched {d:%Y-%m-%d} ({len(df)} rows)")
            d -= timedelta(days=1)
    if not frames:
        raise RuntimeError(
            "No bhavcopy files could be downloaded. Check your internet "
            "connection, or that the 'nse' package is up to date "
            "(pip install -U nse)."
        )
    return pd.concat(frames, ignore_index=True)


def compute_metrics(df: pd.DataFrame) -> pd.DataFrame:
    df = df.rename(columns=lambda c: c.strip().upper())
    df = df[df["SERIES"] == "EQ"].copy()
    df = df.sort_values(["SYMBOL", "TRADE_DATE"])

    df["PREV_CLOSE"] = df.groupby("SYMBOL")["CLOSE"].shift(1)
    df["GAP_PCT"] = (df["OPEN"] - df["PREV_CLOSE"]) / df["PREV_CLOSE"] * 100
    df["RANGE_PCT"] = (df["HIGH"] - df["LOW"]) / df["LOW"] * 100
    df["CLOSE_STRENGTH"] = (df["CLOSE"] - df["LOW"]) / (df["HIGH"] - df["LOW"] + 1e-9)
    df["AVG_VOL_20"] = (
        df.groupby("SYMBOL")["TOTTRDQTY"].transform(lambda s: s.shift(1).rolling(20).mean())
    )
    df["VOL_SPIKE_RATIO"] = df["TOTTRDQTY"] / df["AVG_VOL_20"]

    vol_rank = df.groupby("TRADE_DATE")["VOL_SPIKE_RATIO"].rank(pct=True)
    range_rank = df.groupby("TRADE_DATE")["RANGE_PCT"].rank(pct=True)
    strength_rank = df.groupby("TRADE_DATE")["CLOSE_STRENGTH"].rank(pct=True)
    df["SCORE"] = vol_rank * 0.4 + range_rank * 0.3 + strength_rank * 0.3

    return df


def screen(
    df: pd.DataFrame,
    top_n: int = 20,
    min_price: float | None = None,
    max_price: float | None = None,
    bullish_only: bool = False,
    min_close_strength: float = 0.6,
) -> pd.DataFrame:
    """Return the top-scoring stocks on the most recent available day,
    optionally restricted to a price band and/or an explicit bullish
    requirement (Close > Prev Close AND Close Strength >= min_close_strength)."""
    df = df.dropna(subset=["VOL_SPIKE_RATIO", "AVG_VOL_20", "SCORE"])
    latest_date = df["TRADE_DATE"].max()
    latest = df[df["TRADE_DATE"] == latest_date].copy()

    total_candidates = len(latest)

    if min_price is not None:
        latest = latest[latest["CLOSE"] >= min_price]
    if max_price is not None:
        latest = latest[latest["CLOSE"] <= max_price]
    after_price_filter = len(latest)

    if bullish_only:
        latest = latest[
            (latest["CLOSE"] > latest["PREV_CLOSE"])
            & (latest["CLOSE_STRENGTH"] >= min_close_strength)
        ]
    after_bullish_filter = len(latest)

    print(f"  Screening funnel: {total_candidates} candidates -> "
          f"{after_price_filter} after price filter -> "
          f"{after_bullish_filter} after bullish filter")

    cols = [
        "SYMBOL", "SERIES", "TRADE_DATE", "OPEN", "HIGH", "LOW", "CLOSE",
        "PREV_CLOSE", "TOTTRDQTY", "TOTTRDVAL", "VOL_SPIKE_RATIO", "GAP_PCT",
        "RANGE_PCT", "CLOSE_STRENGTH", "SCORE",
    ]
    cols = [c for c in cols if c in latest.columns]
    return latest[cols].sort_values("SCORE", ascending=False).head(top_n)


def fetch_nifty_history(from_date, to_date) -> pd.DataFrame:
    with NSE(download_folder=DOWNLOAD_DIR) as nse:
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
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--out", type=str, default="watchlist.csv")
    parser.add_argument("--min-price", type=float, default=None, help="e.g. 500")
    parser.add_argument("--max-price", type=float, default=None, help="e.g. 2000")
    parser.add_argument("--bullish-only", action="store_true",
                         help="Require Close > Prev Close AND Close Strength >= --min-close-strength")
    parser.add_argument("--min-close-strength", type=float, default=0.6,
                         help="Close position within the day's range, 0-1 (default 0.6 = upper 40%%)")
    parser.add_argument("--no-backtest", action="store_true")
    args = parser.parse_args()

    print(f"Fetching last {args.days} days of bhavcopy data...")
    data = fetch_range(args.days)
    print(f"Fetched {data['TRADE_DATE'].nunique()} trading days, {len(data)} rows.")

    data = compute_metrics(data)

    result = screen(
        data, top_n=args.top, min_price=args.min_price, max_price=args.max_price,
        bullish_only=args.bullish_only, min_close_strength=args.min_close_strength,
    )
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
            print(
                "NOTE: this backtest validates the underlying SCORE across ALL stocks/days -- "
                "it does NOT specifically re-validate the price-range or bullish-only filters "
                "applied above. Those are inspectable, sensible filters built on existing columns, "
                "not separately backtested claims."
            )
            detail.to_csv("backtest_detail.csv", index=False)
        except RuntimeError as e:
            print(f"Skipped: {e}")

    return data, result


if __name__ == "__main__":
    main()