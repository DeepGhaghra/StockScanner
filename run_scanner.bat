@echo off
call venv\Scripts\activate.bat 2>nul || (
    echo [WARN] venv not found, using system Python
)
echo Starting Smart Stock Scanner...
streamlit run app.py
pause
