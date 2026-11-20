[CmdletBinding()]
param([string]$VncMsi, [switch]$ActiverRdp, [switch]$Regie, [switch]$ClientSsh, [string]$Python)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ouvre PowerShell en administrateur sous le compte du poste.'
}
$failures = [Collections.Generic.List[string]]::new()
function Invoke-AccessStep([string]$Label, [scriptblock]$Action) {
    try {
        & $Action
    } catch {
        $message = "${Label}: $($_.Exception.Message)"
        $failures.Add($message)
        Write-Warning $message
    }
}

function Read-VncPassword {
    do {
        $first = Read-Host 'Mot de passe VNC (8 lettres, chiffres ou !@#%+_.=-)' -AsSecureString
        $second = Read-Host 'Confirme le mot de passe VNC' -AsSecureString
        $left = [Net.NetworkCredential]::new('', $first).Password
        $right = [Net.NetworkCredential]::new('', $second).Password
        $first.Dispose()
        $second.Dispose()
        if ($left -cmatch '^[A-Za-z0-9!@#%+_.=\-]{8}$' -and $left -ceq $right) { return $left }
        Write-Warning 'Le mot de passe doit comporter huit caracteres autorises et correspondre a la confirmation.'
    } while ($true)
}

function Install-TightVncServer {
    $directory = Join-Path ([IO.Path]::GetTempPath()) ('soundlive-vnc-' + [guid]::NewGuid().ToString('N'))
    [void](New-Item -ItemType Directory -Path $directory)
    try {
        if ($VncMsi) {
            $package = (Resolve-Path -LiteralPath $VncMsi).Path
        } else {
            $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
            $package = $null
            if ($winget) {
                & $winget.Source download --id GlavSoft.TightVNC --exact --version 2.8.90 --source winget --architecture x64 `
                    --download-directory $directory --accept-package-agreements --accept-source-agreements --disable-interactivity
                if ($LASTEXITCODE -eq 0) {
                    $packages = @(Get-ChildItem -LiteralPath $directory -Filter '*.msi' -File -Recurse)
                    if ($packages.Count -eq 1) { $package = $packages[0].FullName }
                }
            }
            if (-not $package) {
                $package = Join-Path $directory 'tightvnc-officiel-x64.msi'
                $url = 'https://www.tightvnc.com/download/2.8.90/tightvnc-2.8.90-gpl-setup-64bit.msi'
                Invoke-WebRequest -Uri $url -UseBasicParsing -OutFile $package -TimeoutSec 120
                $signature = Get-AuthenticodeSignature -LiteralPath $package
                if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '\bGlavSoft\b') {
                    throw 'Signature du MSI TightVNC non reconnue. Fournis le MSI officiel verifie avec -VncMsi.'
                }
            }
        }
        $password = Read-VncPassword
        $installer = New-Object -ComObject WindowsInstaller.Installer
        try {
            $installer.UILevel = 2
            $settings = 'ADDLOCAL=Server SERVER_REGISTER_AS_SERVICE=1 SERVER_ADD_FIREWALL_EXCEPTION=0 ' +
                'REBOOT=ReallySuppress SET_USEVNCAUTHENTICATION=1 VALUE_OF_USEVNCAUTHENTICATION=1 ' +
                'SET_ACCEPTHTTPCONNECTIONS=1 VALUE_OF_ACCEPTHTTPCONNECTIONS=0 SET_RUNCONTROLINTERFACE=1 VALUE_OF_RUNCONTROLINTERFACE=0 ' +
                'SET_PASSWORD=1 VALUE_OF_PASSWORD="{0}" MsiHiddenProperties=VALUE_OF_PASSWORD'
            $installer.InstallProduct($package, ($settings -f $password))
        } catch {
            throw "L'installation silencieuse de TightVNC a echoue. Verifie les evenements MsiInstaller de Windows."
        } finally {
            $password = $null
            [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($installer)
        }
    } finally {
        Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Invoke-AccessStep 'SSH de la regie' {
    if ($Regie -or $ClientSsh) {
        $client = 'OpenSSH.Client~~~~0.0.1.0'
        if ((Get-WindowsCapability -Online -Name $client).State -ne 'Installed') {
            Add-WindowsCapability -Online -Name $client | Out-Null
        }
    }
    if ($Regie) {
        $script = Join-Path $PSScriptRoot 'tableau.py'
        $pythonArgs = @()
        if (-not $Python) {
            $Python = 'python.exe'
            if (Get-Command py.exe -ErrorAction SilentlyContinue) {
                $Python = 'py.exe'
                $pythonArgs = @('-3')
            }
        }
        & $Python @pythonArgs -m pip --disable-pip-version-check install --no-input paramiko pyte
        if ($LASTEXITCODE -ne 0) { throw 'Installation des dependances du terminal SSH impossible.' }
        & $Python @pythonArgs $script --preparer-installations
        if ($LASTEXITCODE -ne 0) { throw 'Preparation des cles de la regie impossible.' }
    }
}

Invoke-AccessStep 'TigerVNC Viewer' {
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
}

Invoke-AccessStep 'Bureau a distance Microsoft' {
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
}

Invoke-AccessStep 'TightVNC Server' {
    $service = Get-Service -Name tvnserver -ErrorAction SilentlyContinue
    if (-not $service) {
        Install-TightVncServer
        $service = Get-Service -Name tvnserver -ErrorAction SilentlyContinue
    }
    if (-not $service) { throw 'TightVNC Server doit etre installe comme service Windows.' }
    $settings = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\TightVNC\Server' -ErrorAction SilentlyContinue
    if ($settings.UseVncAuthentication -ne 1 -or $settings.Password.Length -ne 8 -or
        -not ($settings.Password | Where-Object { $_ -ne 0 })) {
        throw 'Active VNC Authentication et definis le mot de passe de connexion dans TightVNC Server Configuration (Service Mode), puis relance.'
    }
    Set-Service -Name tvnserver -StartupType Automatic
    $vncPort = if ($settings.RfbPort) { [int]$settings.RfbPort } else { 5900 }
    $vncRule = 'SoundLive-NDI-VNC'
    if (Get-NetFirewallRule -Name $vncRule -ErrorAction SilentlyContinue) {
        Set-NetFirewallRule -Name $vncRule -Enabled True -RemoteAddress LocalSubnet
        Get-NetFirewallRule -Name $vncRule | Get-NetFirewallPortFilter | Set-NetFirewallPortFilter -LocalPort $vncPort
    } else {
        New-NetFirewallRule -Name $vncRule -DisplayName 'SoundLive-NDI VNC (LAN)' -Direction Inbound `
            -Action Allow -Protocol TCP -LocalPort $vncPort -RemoteAddress LocalSubnet -Profile Any | Out-Null
    }
    Start-Service -Name tvnserver
    Write-Host 'TightVNC Server configure sur le port VNC (5900 par defaut).'
}

if ($failures.Count) { throw ($failures -join "`n") }
