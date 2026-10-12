@echo off
cd /d "%~dp0"
python -m venv .venv
if errorlevel 1 goto erro
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto erro
echo Instalado. Execute Criar administrador.cmd e depois Iniciar Monitor.cmd.
pause
exit /b 0
:erro
echo Falha. Verifique se Python 3.12 esta instalado e disponivel no PATH.
pause
exit /b 1
