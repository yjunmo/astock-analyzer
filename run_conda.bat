@echo off
rem Start the A-share research desk in the Anaconda env "astock".
chcp 65001 >nul
cd /d %~dp0
call "D:\anaconda\Scripts\activate.bat" astock
if errorlevel 1 (
  echo [ERROR] Conda env "astock" not found.
  echo Create it with: conda create -n astock python=3.11 -y
  pause
  exit /b 1
)
python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
pause
