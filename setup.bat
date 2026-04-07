@echo off
echo ========================================
echo   Smart Stock Scanner - Setup
echo ========================================
echo.

:: Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Please install Python 3.10+
    pause
    exit /b 1
)

echo [1/3] Creating virtual environment...
python -m venv venv
if %errorlevel% neq 0 (
    echo [ERROR] Failed to create venv
    pause
    exit /b 1
)

echo [2/3] Activating venv and installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if %errorlevel% neq 0 (
    echo [ERROR] pip install failed
    pause
    exit /b 1
)

echo [3/3] Setup complete!
echo.
echo ----------------------------------------
echo  Next steps:
echo   1. Run 'generate_token.bat' to login
echo   2. Run 'run_scanner.bat' to start app
echo ----------------------------------------
echo.
pause
