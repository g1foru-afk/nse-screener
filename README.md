# NSE Pre-Market Watchlist

Daily EOD screener + free-source news check + a static dashboard, run
automatically on GitHub Actions and viewable every weekday morning
via GitHub Pages.

**Not financial advice.** This ranks stocks by backtestable technical
criteria and a keyword-based news check — it does not recommend buying
or selling anything. Read the linked headlines before acting on anything.

## Setup (one-time)

1. Push this folder to a new GitHub repo.
2. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from
   a branch" → Branch: `main`, folder: `/docs`. Your dashboard will be at
   `https://<you>.github.io/<repo>/`.
3. **Enable Actions write permission**: Settings → Actions → General →
   Workflow permissions → "Read and write permissions" (needed so the
   workflow can commit the daily dashboard back to the repo).
4. Test it manually first: Actions tab → "Daily NSE Pre-Market Watchlist"
   → "Run workflow" (uses the `workflow_dispatch` trigger). Check the run
   logs before trusting the schedule.

## Files

- `nse_eod_screener.py` — fetches bhavcopy, computes technical SCORE
- `news_score.py` — free RSS + NSE corporate-action news check
- `build_dashboard.py` — renders `docs/index.html`
- `daily_run.py` — orchestrates all three, entry point for the workflow
- `.github/workflows/daily-screener.yml` — schedule (Mon–Fri, 07:45 IST)

## Known limitations (read this before relying on it)

- **NSE may throttle or block cloud/datacenter IPs** (including GitHub
  Actions runners) more aggressively than home IPs. If runs start failing
  with connection/auth errors that don't happen locally, try:
  `pip install nse[server]` and pass `server=True` to `NSE(...)` — this
  switches to an HTTP/2 client that has worked around some of these
  blocks historically. If that still fails consistently, move the job to
  an always-on VM (a $5/mo box works) or your own machine's cron.
- **GitHub's scheduled cron is best-effort**, not guaranteed to the
  minute — it can run late under load. The workflow schedules a buffer
  before 9 AM, but for a hard guarantee, self-host the cron instead.
- **News scoring is keyword matching, not real sentiment analysis.** It
  will miss context and nuance. Headlines are always shown with links so
  you can read the source yourself.
- **Technical score is a heuristic, not a fitted model.** The
  `backtest_score_vs_nifty` function in `nse_eod_screener.py` gives you a
  way to check whether higher scores actually preceded beating the Nifty
  — run it periodically and adjust the `weights` dict in
  `compute_metrics()` if the correlation is weak.
- 20 trading days minimum history is needed before a symbol scores at
  all (rolling volume average) — `--days 45` gives comfortable headroom
  including weekends/holidays.
- No adjustment for corporate actions (splits/bonuses) distorting a
  symbol's own historical return series.
