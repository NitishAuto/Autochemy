@echo off
setlocal
title AutoChemy Prerequisite Launcher

echo ============================================
echo   AutoChemy Prerequisite Launcher
echo ============================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo Python is not installed or not available in PATH.
    echo.
    echo Please install Python 3.10 or newer.
    echo The official download page will open now.
    echo.
    start "" "https://www.python.org/downloads/"
    echo After installing Python, run this launcher again.
    echo.
    pause
    exit /b 1
)

echo Python detected. Starting prerequisite checker...
echo.
python "%~dp0pre_requisite.py"

if %errorlevel% neq 0 (
    echo.
    echo Failed to run pre_requisite.py.
    echo Make sure this file exists in the same folder as this launcher.
    echo.
    pause
    exit /b 1
)

endlocal
exit /b 0
