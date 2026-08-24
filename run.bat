@echo off
chcp 65001 >nul
rem 通用启动脚本：请确保 python 在 PATH 中，或自行改为完整路径
cd /d %~dp0
python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
pause
