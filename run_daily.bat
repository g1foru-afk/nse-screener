@echo off
REM ============================================================
REM Daily NSE watchlist runner for Windows Task Scheduler.
REM Edit REPO_DIR below to the actual path where you cloned the
REM nse-screener repo, then point Task Scheduler at this .bat file.
REM ============================================================

set REPO_DIR=E:\GitHub\Projects\nse-screener
set LOGFILE=%REPO_DIR%\run_log.txt

cd /d "%REPO_DIR%" || (echo Could not cd to %REPO_DIR% & exit /b 1)

REM Critical for unattended runs: never let git open an interactive editor
REM (e.g. for a merge commit message) -- there's no one there to close it,
REM and Task Scheduler runs would hang forever waiting for input.
set GIT_EDITOR=true
git config core.editor true >nul 2>&1

echo ==== Run started %DATE% %TIME% ==== >> "%LOGFILE%"

REM Uncomment the next line once, the first time, to install/update deps.
REM pip install -r requirements.txt >> "%LOGFILE%" 2>&1

REM UPDATED: now restricts the shortlist to Rs.500-2000 AND requires
REM Close > Prev Close with Close Strength >= 0.6 (default) before news
REM filtering narrows it further. See nse_eod_screener.py's screen()
REM docstring for exactly what --bullish-only checks.
python daily_run.py --days 45 --final-top 10 --shortlist-top 15 --min-price 500 --max-price 2000 --bullish-only >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Screener run FAILED - see log above >> "%LOGFILE%"
    exit /b 1
)

git pull --no-edit >> "%LOGFILE%" 2>&1
git add docs\index.html watchlist_final.csv >> "%LOGFILE%" 2>&1
git commit -m "Daily watchlist update %DATE%" >> "%LOGFILE%" 2>&1
git push >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Push FAILED - remote likely has changes not pulled, check manually >> "%LOGFILE%"
)

echo ==== Run finished %DATE% %TIME% ==== >> "%LOGFILE%"