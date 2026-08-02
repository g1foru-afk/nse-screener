"""
Single entry point for the daily automated run:
  1. Fetch EOD bhavcopy (last N days)
  2. Score technically
  3. Take a wider shortlist (top --shortlist-top), now optionally
     restricted to a price band and/or an explicit bullish requirement,
     BEFORE news filtering narrows it further
  4. Enrich with free news sources, blend into FINAL_SCORE
  5. Write watchlist_final.csv + docs/index.html (for GitHub Pages)
"""

import argparse

from nse import NSE

from nse_eod_screener import fetch_range, compute_metrics, screen, DOWNLOAD_DIR
from news_score import enrich_watchlist_with_news
from build_dashboard import build_dashboard


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=45)
    parser.add_argument("--final-top", type=int, default=10,
                         help="How many stocks to show after news filtering (5-10 typical)")
    parser.add_argument("--shortlist-top", type=int, default=15,
                         help="How many to pull news for, before exclusions (> final-top)")
    parser.add_argument("--min-price", type=float, default=None, help="e.g. 500")
    parser.add_argument("--max-price", type=float, default=None, help="e.g. 2000")
    parser.add_argument("--bullish-only", action="store_true",
                         help="Require Close > Prev Close AND Close Strength >= --min-close-strength")
    parser.add_argument("--min-close-strength", type=float, default=0.6)
    args = parser.parse_args()

    print("Step 1/4: fetching bhavcopy...")
    data = fetch_range(args.days)

    print("Step 2/4: computing technical scores...")
    data = compute_metrics(data)
    shortlist = screen(
        data, top_n=args.shortlist_top,
        min_price=args.min_price, max_price=args.max_price,
        bullish_only=args.bullish_only, min_close_strength=args.min_close_strength,
    )

    print("Step 3/4: validating news for shortlisted symbols...")
    with NSE(download_folder=DOWNLOAD_DIR) as nse:
        final_df = enrich_watchlist_with_news(shortlist, nse=nse)

    final_df.to_csv("watchlist_final.csv", index=False)

    print("Step 4/4: building dashboard...")
    non_excluded = final_df[~final_df["HARD_EXCLUDE"]]
    display_df = non_excluded.head(args.final_top) if len(non_excluded) >= args.final_top \
        else final_df.head(args.final_top)
    path = build_dashboard(display_df, out_path="docs/index.html", top_n=args.final_top)

    print(f"\nDone. Dashboard written to {path}, data in watchlist_final.csv")
    print(display_df[["SYMBOL", "FINAL_SCORE", "SCORE", "NEWS_SCORE", "HARD_EXCLUDE"]].to_string(index=False))


if __name__ == "__main__":
    main()