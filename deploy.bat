@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul

cd /d "%~dp0"

echo [AIProxy] Checking Python...
where python >nul 2>nul
if %errorlevel%==0 (
    set "PYTHON_CMD=python"
) else (
    where py >nul 2>nul
    if %errorlevel%==0 (
        set "PYTHON_CMD=py"
    ) else (
        echo [AIProxy] Python 3 is not installed or not available in PATH.
        pause
        exit /b 1
    )
)

if not exist ".venv" (
    echo [AIProxy] Creating virtual environment...
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 goto :error
)

echo [AIProxy] Activating virtual environment...
call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

echo [AIProxy] Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 goto :error

echo [AIProxy] Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist ".env" (
    echo [AIProxy] Creating .env from .env.example...
    copy /Y ".env.example" ".env" >nul
    if errorlevel 1 goto :error
)

echo [AIProxy] Starting AIProxy services...
python main.py
if errorlevel 1 goto :error

goto :end

:error
echo.
echo [AIProxy] Deployment or startup failed.
pause
exit /b 1

:end
echo.
echo [AIProxy] Process exited.
pause
