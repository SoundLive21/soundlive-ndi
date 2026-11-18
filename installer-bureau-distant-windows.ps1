[CmdletBinding()]
param([string]$VncMsi)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ouvre PowerShell en administrateur sous le compte du poste.'
}
$service = Get-Service -Name tvnserver -ErrorAction SilentlyContinue
if (-not $service) {
    Write-Host 'Dans l assistant TightVNC, installe Server comme service et choisis un mot de passe de controle.'
    if ($VncMsi) {
        $installer = (Resolve-Path -LiteralPath $VncMsi).Path
        $process = Start-Process -FilePath "$env:SystemRoot\System32\msiexec.exe" -Wait -PassThru -ArgumentList "/i `"$installer`" /norestart"
        if ($process.ExitCode -notin @(0, 3010)) { throw "Installation TightVNC interrompue ($($process.ExitCode))." }
    } else {
        $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
        if (-not $winget) { throw 'Installe App Installer ou indique le fichier MSI officiel avec -VncMsi.' }
        & $winget.Source install --id GlavSoft.TightVNC --exact --source winget --interactive --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Installation TightVNC interrompue ($LASTEXITCODE)." }
    }
    $service = Get-Service -Name tvnserver -ErrorAction SilentlyContinue
}
if (-not $service) { throw 'TightVNC Server doit etre installe comme service Windows.' }
Set-Service -Name tvnserver -StartupType Automatic
Start-Service -Name tvnserver
Write-Host 'TightVNC Server configure sur le port VNC (5900 par defaut).'
