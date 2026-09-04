@ECHO OFF
TITLE Web Automation Console (SeleniumBase)
cd /d "%~dp0"
python run_automation.py %*
ECHO.
PAUSE
