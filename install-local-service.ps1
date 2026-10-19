param(
    [string]$InstallRoot = 'C:\ProgramData\ImpactoLocal',
    [Parameter(Mandatory=$true)][string]$PythonBase
)
$ErrorActionPreference = 'Stop'
$log = Join-Path $InstallRoot 'install-result.json'
try {
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    if (!$principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Execute como administrador para instalar o servico e a regra de rede.' }
    if (!(Test-Path (Join-Path $InstallRoot 'ImpactoLocal.xml'))) { throw 'Pacote local incompleto.' }
    & icacls $InstallRoot /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)RX' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Falha nas permissoes da instalacao.' }
    foreach ($folder in @('data','logs')) {
        & icacls (Join-Path $InstallRoot $folder) /inheritance:r /grant:r '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-18:(OI)(CI)F' '*S-1-5-19:(OI)(CI)M' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Falha nas permissoes dos dados.' }
    }
    # LocalService needs read/execute access to the installed base Python runtime only.
    & icacls $PythonBase /grant '*S-1-5-19:(OI)(CI)RX' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao permitir execucao do Python pelo servico.' }
    $exe = Join-Path $InstallRoot 'ImpactoLocal.exe'
    $service = Get-Service ImpactoLocal -ErrorAction SilentlyContinue
    if (!$service) { & $exe install; if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar servico.' } }
    if (!(Get-NetFirewallRule -Name 'ImpactoLocal-LAN' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'ImpactoLocal-LAN' -DisplayName 'Impacto Local - rede das impressoras' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8130 -RemoteAddress '192.168.1.0/24' -Profile Any | Out-Null
    }
    Start-Service ImpactoLocal
    $ready=$false
    for ($i=0;$i -lt 30;$i++) {
        Start-Sleep -Seconds 1
        try { $r=Invoke-RestMethod 'http://127.0.0.1:8130/health' -TimeoutSec 2; if ($r.service -eq 'impacto-local') { $ready=$true;break } } catch {}
    }
    if (!$ready) { throw 'Servico instalado mas sem resposta. Consulte logs.' }
    # Verify recovery against our worker only; this never commands a printer.
    $listener = Get-NetTCPConnection -LocalPort 8130 -State Listen | Select-Object -First 1
    $worker = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $workerPath = Join-Path $InstallRoot 'app\local_app.py'
    if (!$worker.CommandLine.Contains($workerPath)) { throw 'Nao foi possivel identificar o processo para testar recuperacao.' }
    Stop-Process -Id $worker.ProcessId -Force
    $recovered=$false
    for ($i=0;$i -lt 35;$i++) {
        Start-Sleep -Seconds 1
        try { $r=Invoke-RestMethod 'http://127.0.0.1:8130/health' -TimeoutSec 2; if ($r.service -eq 'impacto-local') { $recovered=$true;break } } catch {}
    }
    if (!$recovered) { throw 'Falha no teste de reinicio automatico. Consulte logs.' }
    @{status='success';service='ImpactoLocal';recovery_test='passed';time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content $log -Encoding UTF8
} catch {
    @{status='error';message=$_.Exception.Message;time=(Get-Date).ToString('o')} | ConvertTo-Json | Set-Content $log -Encoding UTF8
    exit 1
}

