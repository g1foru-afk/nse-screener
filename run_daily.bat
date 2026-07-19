@echo off
REM ============================================================
REM Daily NSE watchlist runner for Windows Task Scheduler.
REM Edit REPO_DIR below to the actual path where you cloned the
REM nse-screener repo, then point Task Scheduler at this .bat file.
REM ============================================================

set REPO_DIR=C:\Users\YOURNAME\nse-screener
set LOGFILE=%REPO_DIR%\run_log.txt

cd /d "%REPO_DIR%" || (echo Could not cd to %REPO_DIR% & exit /b 1)

echo ==== Run started %DATE% %TIME% ==== >> "%LOGFILE%"

REM Uncomment the next line once, the first time, to install/update deps.
REM pip install -r requirements.txt >> "%LOGFILE%" 2>&1

python daily_run.py --days 45 --final-top 10 --shortlist-top 15 >> "%LOGFILE%" 2>&1
if errorlevel 1 (
    echo Screener run FAILED - see log above >> "%LOGFILE%"
    exit /b 1
)

git add docs\index.html watchlist_final.csv >> "%LOGFILE%" 2>&1
git commit -m "Daily watchlist update %DATE%" >> "%LOGFILE%" 2>&1
git push >> "%LOGFILE%" 2>&1

echo ==== Run finished %DATE% %TIME% ==== >> "%LOGFILE%"
