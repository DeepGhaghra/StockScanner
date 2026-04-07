@echo off
echo ========================================
echo   Smart Stock Scanner - Generate Token
echo ========================================
echo.
call venv\Scripts\activate.bat 2>nul || (
    echo [WARN] venv not found, using system Python
)
python auth.py
pause
