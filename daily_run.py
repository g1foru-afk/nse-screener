"""
Single entry point for the daily GitHub Actions run:
  1. Fetch EOD bhavcopy (last N days)
  2. Score technically
  3. Take a wider shortlist (top 15) so news exclusions still leave 5-10
  4. Enrich with free news sources, blend into FINAL_SCORE
  5. Write watchlist.csv + docs/index.html (for GitHub Pages)
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
    args = parser.parse_args()

    print("Step 1/4: fetching bhavcopy...")
    data = fetch_range(args.days)

    print("Step 2/4: computing technical scores...")
    data = compute_metrics(data)
    shortlist = screen(data, top_n=args.shortlist_top)

    print("Step 3/4: validating news for shortlisted symbols...")
    with NSE(download_folder=DOWNLOAD_DIR, server=True, timeout=30) as nse:
        final_df = enrich_watchlist_with_news(shortlist, nse=nse)

    final_df.to_csv("watchlist_final.csv", index=False)

    print("Step 4/4: building dashboard...")
    non_excluded = final_df[~final_df["HARD_EXCLUDE"]]
    display_df = non_excluded.head(args.final_top) if len(non_excluded) >= args.final_top \
        else final_df.head(args.final_top)  # show excluded ones too rather than an empty page
    path = build_dashboard(display_df, out_path="docs/index.html", top_n=args.final_top)

    print(f"\nDone. Dashboard written to {path}, data in watchlist_final.csv")
    print(display_df[["SYMBOL", "FINAL_SCORE", "SCORE", "NEWS_SCORE", "HARD_EXCLUDE"]].to_string(index=False))


if __name__ == "__main__":
    main()
