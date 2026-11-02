@echo off
rem Journal Scope - Windows setup. Double-click once; it creates a desktop shortcut.
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (set PY=py -3) else (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python 3.10+ is required. Install it from https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^), then run this again.
  pause & exit /b 1
)

echo Creating virtual environment...
if not exist .venv %PY% -m venv .venv || (pause & exit /b 1)
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
echo Installing packages...
pip install -r requirements.txt || (pause & exit /b 1)
echo Installing Chromium for page rendering...
python -m playwright install chromium || (pause & exit /b 1)

echo Creating desktop shortcut...
set TARGET=%~dp0.venv\Scripts\pythonw.exe
powershell -NoProfile -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\Journal Scope.lnk');" ^
  "$s.TargetPath='%TARGET%'; $s.Arguments='-m app.desktop'; $s.WorkingDirectory='%~dp0'; $s.Description='Journal Scope'; $s.Save()"

echo.
echo Done. Open "Journal Scope" from your desktop.
pause
