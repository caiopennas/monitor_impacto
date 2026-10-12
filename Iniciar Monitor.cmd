@echo off
cd /d "%~dp0"
echo Abra http://127.0.0.1:8130 no navegador. Mantenha esta janela aberta.
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" server.py
) else if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" server.py
) else (
  python server.py
)
pause
