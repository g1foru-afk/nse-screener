# NSE Pre-Market Watchlist

Daily EOD screener + free-source news check + a static dashboard, run
automatically on GitHub Actions and viewable every weekday morning
via GitHub Pages.

**Not financial advice.** This ranks stocks by backtestable technical
criteria and a keyword-based news check — it does not recommend buying
or selling anything. Read the linked headlines before acting on anything.

## Setup (one-time)

1. **Clone the repo locally** (not just download the zip) so `git push` works from your machine:
   ```
   git clone https://github.com/<you>/nse-screener.git
   cd nse-screener
   pip install -r requirements.txt
   ```
   If you already have the folder from the zip instead, run `git init`,
   commit, and `git remote add origin <your-repo-url>` inside it so it's
   a real git checkout, not just files.
2. **Enable GitHub Pages**: repo Settings → Pages → Source: "Deploy from
   a branch" → Branch: `main`, folder: `/docs`. Dashboard will be at
   `https://<you>.github.io/<repo>/`.
3. **Set up git to push without a password prompt** (needed since Task
   Scheduler runs unattended, with no one there to type a password):
   - Easiest: use a [GitHub personal access token](https://github.com/settings/tokens)
     (classic, `repo` scope) as your git credential once — Windows'
     Credential Manager will remember it after the first manual `git push`.
   - Test this *before* scheduling anything: run `run_daily.bat` once by
     double-clicking it, and confirm it pushes without asking for
     credentials interactively.
4. **Edit `run_daily.bat`**: change `REPO_DIR` at the top to wherever you
   cloned the repo, e.g. `C:\Users\yourname\nse-screener`.
5. **Set up Windows Task Scheduler** (see below).
6. Check `run_log.txt` (created next to the script) after each run —
   this is your primary debugging tool since Task Scheduler runs silently.

## Windows Task Scheduler setup

**GUI method:**
1. Open **Task Scheduler** (Start menu → search "Task Scheduler")
2. **Action → Create Task** (not "Create Basic Task" — the full dialog
   gives more control)
3. **General tab**: Name it `NSE Daily Watchlist`. Select **"Run whether
   user is logged on or not"**. Check **"Run with highest privileges"**
   if you hit permission issues (usually not needed).
4. **Triggers tab → New**: Begin the task **"On a schedule"** → Weekly →
   check Mon–Fri → set time to **7:45:00 AM** (adjust earlier if your
   internet/machine needs longer to wake up) → OK.
5. **Actions tab → New**: Action "Start a program" → Program/script:
   browse to `run_daily.bat` (the full path, e.g.
   `C:\Users\yourname\nse-screener\run_daily.bat`) → OK.
6. **Conditions tab**: if this is a laptop, check **"Wake the computer
   to run this task"** — otherwise a sleeping laptop just silently skips
   the run. Uncheck "Start the task only if the computer is on AC power"
   if you want it to run on battery too.
7. **Settings tab**: check "Run task as soon as possible after a
   scheduled start is missed" (covers a missed run if the PC was off).
8. Save. Right-click the task → **Run** to test it immediately rather
   than waiting for tomorrow morning.

**Command-line alternative** (run once in an elevated Command Prompt,
adjust the path):
```
schtasks /create /tn "NSE Daily Watchlist" /tr "C:\Users\yourname\nse-screener\run_daily.bat" /sc weekly /d MON,TUE,WED,THU,FRI /st 07:45 /rl HIGHEST
```

**Reality check:** this only works if your PC is on, awake, and has
internet at 7:45 AM. If it's a laptop that's regularly closed/off at
that hour, this approach has a real gap — the practical alternatives at
that point are an always-on mini-PC, a cheap always-on VM in a
non-blocked region, or accepting that some mornings it just doesn't run
and you trigger it manually when you're at your desk.


## Files

- `nse_eod_screener.py` — fetches bhavcopy, computes technical SCORE
- `news_score.py` — free RSS + NSE corporate-action news check
- `build_dashboard.py` — renders `docs/index.html`
- `daily_run.py` — orchestrates all three, entry point for the workflow
- `.github/workflows/daily-screener.yml` — schedule (Mon–Fri, 07:45 IST)

## Known limitations (read this before relying on it)

- **NSE actively blocks cloud/datacenter IPs** (AWS, Azure, GCP, and by
  extension GitHub Actions runners) with plain HTTP requests — this is a
  documented, known issue in the `nse` package's own GitHub repo
  (issue #9), not something specific to this project. The code here
  already uses the package's `server=True` mode (`nse[server]`, HTTP/2
  via httpx) to work around it. If runs still fail with the same
  `ReadTimeoutError` on `www.nseindia.com` after this, NSE has likely
  widened the block — the next step is moving the job to an always-on
  VM with a non-cloud-flagged IP, or your own machine's cron, rather than
  fighting it further in code.
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
