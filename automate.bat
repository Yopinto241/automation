@ECHO OFF
TITLE Web Automation Console (SeleniumBase + LibreWolf)
cd /d "%~dp0"
python run_automation.py %*
ECHO.
PAUSE
