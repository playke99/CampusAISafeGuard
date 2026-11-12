@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   数智卫校 · 校园安全 AI 预警与干预系统
echo ============================================
echo.
if not exist ".venv\Scripts\python.exe" (
  echo [提示] 未检测到 .venv，使用系统 Python 启动
  set "PY=python"
) else (
  set "PY=.venv\Scripts\python.exe"
)
echo 正在启动服务，浏览器将自动打开 http://127.0.0.1:8100
start "" http://127.0.0.1:8100
%PY% -m server.main --port 8100
pause
