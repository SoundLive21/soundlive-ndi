[CmdletBinding()]
param([string]$VncMsi, [switch]$ActiverRdp, [switch]$Regie)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ouvre PowerShell en administrateur sous le compte du poste.'
}
if ($Regie) {
    $client = 'OpenSSH.Client~~~~0.0.1.0'
    if ((Get-WindowsCapability -Online -Name $client).State -ne 'Installed') {
        Add-WindowsCapability -Online -Name $client | Out-Null
    }
    $script = Join-Path $PSScriptRoot 'tableau.py'
    $python = 'python.exe'
    $pythonArgs = @()
    if (Get-Command py.exe -ErrorAction SilentlyContinue) {
        $python = 'py.exe'
        $pythonArgs = @('-3')
    }
    & $python @pythonArgs -m pip install paramiko pyte
    if ($LASTEXITCODE -ne 0) { throw 'Installation des dependances du terminal SSH impossible.' }
    & $python @pythonArgs $script --preparer-installations
    if ($LASTEXITCODE -ne 0) { throw 'Preparation des cles de la regie impossible.' }
}

$viewer = Join-Path $env:ProgramFiles 'TigerVNC\vncviewer.exe'
if (-not (Test-Path -LiteralPath $viewer -PathType Leaf)) {
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("soundlive-tigervnc-" + [guid]::NewGuid().ToString() + ".exe")
    try {
        $url = 'https://downloads.sourceforge.net/project/tigervnc/stable/1.16.2/tigervnc64-1.16.2.exe'
        Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $temporary -TimeoutSec 120
        $expected = '96A0117C8D0D55EA2E2C20E3D9D9A798011DA8C5A813A35CD0B9E006D58BB7F4'
        if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash -ne $expected) {
            throw 'Empreinte inattendue pour le programme officiel TigerVNC Viewer.'
        }
        $process = Start-Process -FilePath $temporary -Wait -PassThru -ArgumentList '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-'
        if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $viewer -PathType Leaf)) {
            throw "Installation de TigerVNC Viewer interrompue ($($process.ExitCode))."
        }
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
Write-Host 'TigerVNC Viewer installe.'

if ($ActiverRdp -or $Regie) {
    $edition = Get-ItemPropertyValue -Path 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion' -Name EditionID
    $policy = Get-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows NT\Terminal Services' -Name fDenyTSConnections -ErrorAction SilentlyContinue
    if ($edition -like 'Core*' -or $edition -like 'Starter*') {
        Write-Warning 'Windows Home ne peut pas recevoir de connexions MSRDP. VNC reste disponible.'
    } elseif ($policy.fDenyTSConnections -eq 1) {
        Write-Warning 'MSRDP est desactive par une strategie Windows.'
    } else {
        $terminal = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
        $listener = Join-Path $terminal 'WinStations\RDP-Tcp'
        $port = Get-ItemPropertyValue -LiteralPath $listener -Name PortNumber
        Set-ItemProperty -LiteralPath $listener -Name UserAuthentication -Value 1
        Set-ItemProperty -LiteralPath $terminal -Name fDenyTSConnections -Value 0
        $rule = 'SoundLive-NDI-MSRDP'
        if (Get-NetFirewallRule -Name $rule -ErrorAction SilentlyContinue) {
            Set-NetFirewallRule -Name $rule -Enabled True -RemoteAddress LocalSubnet -Profile Any
            Get-NetFirewallRule -Name $rule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $port
        } else {
            New-NetFirewallRule -Name $rule -DisplayName 'SoundLive-NDI MSRDP (LAN)' -Direction Inbound -Action Allow -Protocol TCP -LocalPort $port -RemoteAddress LocalSubnet -Profile Any | Out-Null
        }
        Set-Service -Name TermService -StartupType Manual
        Start-Service -Name TermService
        Write-Host "MSRDP active (TCP $port) avec authentification au niveau du reseau."
        if ($port -ne 3389) { Write-Warning 'Utilise le port MSRDP indique ci-dessus dans ton client.' }
    }
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
$settings = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\TightVNC\Server' -ErrorAction SilentlyContinue
if ($settings.UseVncAuthentication -ne 1 -or $settings.Password.Length -ne 8 -or
    -not ($settings.Password | Where-Object { $_ -ne 0 })) {
    throw 'Active VNC Authentication et definis le mot de passe de connexion dans TightVNC Server Configuration (Service Mode), puis relance.'
}
Set-Service -Name tvnserver -StartupType Automatic
Start-Service -Name tvnserver
Write-Host 'TightVNC Server configure sur le port VNC (5900 par defaut).'
