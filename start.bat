@echo off
REM Double-click this file to start the Research Outreach app (Windows).
cd /d "%~dp0"

REM First run: install dependencies if Flask isn't present.
python -c "import flask" 2>NUL
if errorlevel 1 (
  echo Installing dependencies (first run only)...
  python -m pip install -r requirements.txt
)

python run_app.py
pause
