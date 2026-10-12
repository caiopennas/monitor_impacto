@echo off
cd /d "%~dp0"
echo Acesso HTTPS temporario. Mantenha esta janela aberta.
echo O endereco sera salvo em %%LOCALAPPDATA%%\ImpactoMonitor\Endereco-internet.txt
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" internet.py
) else if exist "..\.venv\Scripts\python.exe" (
  "..\.venv\Scripts\python.exe" internet.py
) else (
  python internet.py
)
pause
