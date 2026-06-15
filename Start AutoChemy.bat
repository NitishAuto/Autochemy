@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "ROOT=%~dp0"
set "CFG=%ROOT%AutoChemy_User_Data\.autochemy_ready"
set "REQ=%ROOT%requirements.txt"

if not exist "%ROOT%AutoChemy_User_Data" mkdir "%ROOT%AutoChemy_User_Data"

REM One-time migration from older setup flag
if exist "%ROOT%AutoChemy_User_Data\.launcher_setup_ok" if not exist "%CFG%" (
    call :find_python
    if defined PYCMD (
        echo %PYCMD%> "%CFG%"
        del "%ROOT%AutoChemy_User_Data\.launcher_setup_ok" >nul 2>nul
    )
)

REM --- Already set up: try to launch AutoChemy ---
if exist "%CFG%" (
    set "PYCMD="
    set /p PYCMD=<"%CFG%"
    
    REM Verify the saved python command still works on this PC
    if defined PYCMD (
        %PYCMD% -c "pass" >nul 2>nul
        if not errorlevel 1 (
            start "" %PYCMD% "%ROOT%autochemy.py"
            exit /b 0
        )
    )
    
    REM If we reach here, the saved command is invalid or missing (e.g. copied to a new PC)
    del "%CFG%" >nul 2>nul
)

REM --- First time only: Python + Python libraries (not xTB/CREST) ---
title AutoChemy - first-time setup

set "PYCMD="
for %%P in ("py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
    call :try_python %%~P
    if defined PYCMD goto :first_setup
)
goto :no_python

:find_python
set "PYCMD="
for %%P in ("py -3.12" "py -3.11" "py -3.10" "py -3" "python" "python3") do (
    call :try_python %%~P
    if defined PYCMD exit /b 0
)
exit /b 1

:first_setup
echo        _         _         ____  _                            
echo       / \  _   _^| ^|_ ___  / ___^|^| ^|__   ___ _ __ ___  _   _ 
echo      / _ \^| ^| ^| ^| __/ _ \^| ^|    ^| '_ \ / _ \ '_ ` _ \^| ^| ^| ^|
echo     / ___ \ ^|_^| ^| ^|^| (_) ^| ^|___ ^| ^| ^| ^|  __/ ^| ^| ^| ^| ^| ^|_^| ^|
echo    /_/   \_\__,_^|\__\___/ \____^|^|_^| ^|_^|\___^|_^| ^|_^| ^|_^|\__, ^|
echo                                                       ^|___/ 
echo.
echo  AutoChemy - first-time setup
echo  Checking Python libraries...
echo.

%PYCMD% -c "import pandas, numpy, matplotlib, PIL" >nul 2>nul
if errorlevel 1 (
    echo  Some libraries are missing.
    call :ask_install
    if errorlevel 1 (
        echo  Cancelled. Run Start AutoChemy.bat again when ready.
        pause
        exit /b 1
    )
    echo  Launching Setup UI...
    "%PYCMD%" "%ROOT%install_deps.py" "%REQ%"
    if errorlevel 1 (
        echo  Install failed. Check internet and try again.
        pause
        exit /b 1
    )
    %PYCMD% -c "import pandas, numpy, matplotlib, PIL" >nul 2>nul
    if errorlevel 1 (
        echo  Libraries still missing after install.
        pause
        exit /b 1
    )
)

echo  Setup complete. Opening AutoChemy...
echo %PYCMD%> "%CFG%"
start "" %PYCMD% "%ROOT%autochemy.py"
exit /b 0

:no_python
echo.
echo  Python 3.10+ is not installed.
echo  Install from https://www.python.org/downloads/
echo  Check "Add python.exe to PATH", then run this file again.
echo.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:try_python
set "TRY=%~1"
%TRY% -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "PYCMD=%TRY%"
exit /b 0

:ask_install
powershell -NoProfile -ExecutionPolicy Bypass -Command "Add-Type -AssemblyName System.Windows.Forms; $m = 'Some Python libraries are missing.' + [Environment]::NewLine + [Environment]::NewLine + 'Install them now? (needs internet)'; $r = [System.Windows.Forms.MessageBox]::Show($m, 'AutoChemy', 'YesNo', 'Question'); if ($r -eq 'Yes') { exit 0 } else { exit 1 }"
exit /b %ERRORLEVEL%
