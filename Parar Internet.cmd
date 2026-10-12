@echo off
if not exist "%LOCALAPPDATA%\ImpactoMonitor" exit /b 0
echo stop>"%LOCALAPPDATA%\ImpactoMonitor\parar-internet.flag"
echo Solicitado encerramento do acesso temporario.
pause
