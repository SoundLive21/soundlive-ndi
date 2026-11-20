# SoundLive-NDI. Run in an elevated PowerShell under the account used for display.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidatePattern('^(?![0-9]+$)[A-Za-z0-9](?:[A-Za-z0-9-]{0,13}[A-Za-z0-9])?$')][string]$Hote,
    [Parameter(Mandatory=$true)][string]$Ip,
    [Parameter(Mandatory=$true)][string]$Wifi,
    [Parameter(Mandatory=$true)][uri]$Regie,
    [string]$Code,
    [string]$Ethernet,
    [string]$InterfaceWifi,
    [string]$ProfilWifi,
    [string]$ProfilWifiXml,
    [switch]$WifiOuvert,
    [string]$CleWifiFichier,
    [switch]$SaisirCleWifi,
    [string]$CleSsh,
    [ValidatePattern('^[a-f0-9]{64}$')][string]$EmpreinteRegie,
    [string]$Passerelle,
    [string]$Dns,
    [string]$Dns2,
    [switch]$SansDhcpInstallation,
    [switch]$AttendreReseauEcrans,
    [switch]$Vnc,
    [string]$VncMsi,
    [string]$Ffplay,
    [string]$Lecteur,
    [string]$NdiSdk = "$env:ProgramFiles\NDI\NDI 6 SDK",
    [string]$SdlSdk,
    [switch]$Verifier
)
$ErrorActionPreference = 'Stop'
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'Utilise PowerShell Windows x64.' }
$wifiName = 'SOUNDLIVE-WIFI'
$wiredName = 'SOUNDLIVE-ETHERNET'
if ([Text.Encoding]::UTF8.GetByteCount($Wifi) -notin (1..32) -or $Wifi -match '[\x00-\x1f]') {
    throw 'Le SSID doit comporter 1 a 32 octets.'
}
if (@($ProfilWifiXml, $CleWifiFichier, $WifiOuvert.IsPresent, $SaisirCleWifi.IsPresent).Where({ $_ }).Count -gt 1) {
    throw 'Choisis une seule methode de configuration du Wi-Fi.'
}

function ConvertTo-IPv4Number([string]$Address) {
    if ($Address -notmatch '^([0-9]{1,3}\.){3}[0-9]{1,3}$') { throw "IPv4 invalide : $Address" }
    $bytes = $Address.Split('.') | ForEach-Object {
        $value = [int]$_
        if ($value -gt 255 -or [string]$value -ne $_) { throw "IPv4 invalide : $Address" }
        $value
    }
    return [long]$bytes[0] * 16777216 + $bytes[1] * 65536 + $bytes[2] * 256 + $bytes[3]
}

if ($Ip -notmatch '^([^/]+)/([1-9]|[12][0-9]|30)$') {
    throw 'Indique une IPv4 avec son prefixe, par exemple 10.10.2.103/24.'
}
$address = $Matches[1]
$prefix = [int]$Matches[2]
$number = ConvertTo-IPv4Number $address
$size = [long]1 -shl (32 - $prefix)
$first = $number - ($number % $size)
$last = $first + $size - 1
if ($number -eq $first -or $number -eq $last -or $address -match '^(0|127)\.|^169\.254\.' -or $number -ge 3758096384) {
    throw "IPv4 $address inutilisable sur le LAN."
}
if ($Passerelle) {
    $gateway = ConvertTo-IPv4Number $Passerelle
    if ($gateway -le $first -or $gateway -ge $last -or $gateway -eq $number) {
        throw 'La passerelle doit etre une autre adresse utilisable du meme sous-reseau.'
    }
}
if ($Dns2 -and -not $Dns) { throw 'Indique le DNS principal avec -Dns.' }
$dnsServers = @($Dns, $Dns2).Where({ $_ })
foreach ($server in $dnsServers) { $null = ConvertTo-IPv4Number $server }
foreach ($file in @($CleWifiFichier, $CleSsh, $ProfilWifiXml).Where({ $_ })) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Fichier introuvable : $file" }
}
if (-not $Regie.IsAbsoluteUri -or $Regie.Scheme -notin @('http', 'https') -or
    $Regie.AbsolutePath -ne '/' -or $Regie.UserInfo -or $Regie.Query -or $Regie.Fragment -or $Regie.Host -eq $address) {
    throw 'Indique une URL de regie distincte, par exemple http://10.10.2.99:8765.'
}

function Select-ReaderAdapter($Adapters, [int]$Type, [string]$Alias, [string]$Option) {
    $candidates = @($Adapters | Where-Object {
        $_.InterfaceType -eq $Type -and (-not $Alias -or $_.Name -eq $Alias)
    })
    if ($candidates.Count -ne 1) { throw "Indique le nom exact de la carte avec $Option (voir Get-NetAdapter)." }
    return $candidates[0]
}

$physical = @(Get-NetAdapter -Physical)
$wired = Select-ReaderAdapter $physical 6 $Ethernet '-Ethernet'
$wireless = Select-ReaderAdapter $physical 71 $InterfaceWifi '-InterfaceWifi'
foreach ($adapter in Get-NetAdapter) {
    if (($adapter.Name -eq $wiredName -and $adapter.ifIndex -ne $wired.ifIndex) -or
        ($adapter.Name -eq $wifiName -and $adapter.ifIndex -ne $wireless.ifIndex)) {
        throw "Le nom $($adapter.Name) est deja pris par une autre interface."
    }
}
Write-Host "Lecteur $Hote, IPv4 $Ip, Ethernet $($wired.Name), Wi-Fi $($wireless.Name) ($Wifi)."
if ($Verifier) { Write-Host 'Parametres valides.'; return }

$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ouvre PowerShell en administrateur sous le compte Windows du lecteur.'
}
$data = Join-Path $env:LOCALAPPDATA 'soundlive-ndi'
$legacy = Join-Path $env:LOCALAPPDATA 'SoundLive'
$app = Join-Path $data 'client'
$paired = Test-Path -LiteralPath (Join-Path $data 'config\association.json')
if (-not $paired -and (Test-Path -LiteralPath (Join-Path $legacy 'config\association.json'))) {
    $paired = $true
    $app = Join-Path $legacy 'client'
}
$prepareDesktop = (-not $paired) -or $Vnc -or $VncMsi -or $EmpreinteRegie
if (-not $paired -and -not $Code) { throw 'Renseigne le code fourni par la regie avec -Code.' }
$taskName = 'SoundLive-NDI Network'
$oldTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
$oldTaskRunning = $oldTask -and $oldTask.State -eq 'Running'
$pausedTask = $false
$staticInstalled = $false
$dhcpChanged = $false

function Restore-InstallationNetwork {
    if ($dhcpChanged) {
        Set-NetIPInterface -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -PolicyStore ActiveStore -Dhcp $previousDhcp
        if ($previousDhcp -eq 'Disabled') {
            foreach ($ip in $previousInstallIps) {
                if (-not (Get-NetIPAddress -InterfaceIndex $wired.ifIndex -IPAddress $ip.IPAddress -ErrorAction SilentlyContinue)) {
                    New-NetIPAddress -InterfaceIndex $wired.ifIndex -IPAddress $ip.IPAddress -PrefixLength $ip.PrefixLength -PolicyStore ActiveStore | Out-Null
                }
            }
            foreach ($route in $previousInstallRoutes) {
                if (-not (Get-NetRoute -InterfaceIndex $wired.ifIndex -DestinationPrefix $route.DestinationPrefix -NextHop $route.NextHop -ErrorAction SilentlyContinue)) {
                    New-NetRoute -InterfaceIndex $wired.ifIndex -DestinationPrefix $route.DestinationPrefix -NextHop $route.NextHop -RouteMetric $route.RouteMetric -PolicyStore ActiveStore | Out-Null
                }
            }
        }
        if ($previousInstallDns) {
            Set-DnsClientServerAddress -InterfaceIndex $wired.ifIndex -ServerAddresses $previousInstallDns
        } else {
            Set-DnsClientServerAddress -InterfaceIndex $wired.ifIndex -ResetServerAddresses
        }
        $script:dhcpChanged = $false
    }
}

function Install-WingetPackage([string]$Id) {
    $manager = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $manager) { throw "Installe App Installer (WinGet) pour telecharger $Id." }
    & $manager.Source install --id $Id --exact --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    if ($LASTEXITCODE -ne 0) { throw "Installation de $Id impossible (WinGet $LASTEXITCODE)." }
}

function Find-DisplayPython {
    $launchers = @(
        (Get-Command py.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Launcher\py.exe')
        (Join-Path $env:WINDIR 'py.exe')
    )
    foreach ($launcher in $launchers) {
        if ($launcher -and (Test-Path -LiteralPath $launcher)) {
            $binary = & $launcher -3 -c "import tkinter,sys,platform; assert platform.machine().lower() in ('amd64','x86_64'); print(sys.executable)" 2>$null
            if ($LASTEXITCODE -eq 0 -and $binary) { return $binary }
        }
    }
    $paths = @(
        (Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source)
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
        (Join-Path $env:ProgramFiles 'Python312\python.exe')
    )
    foreach ($binary in $paths) {
        if ($binary -and $binary -notlike '*\Microsoft\WindowsApps\*' -and (Test-Path -LiteralPath $binary)) {
            & $binary -c "import tkinter,platform; assert platform.machine().lower() in ('amd64','x86_64')" 2>$null
            if ($LASTEXITCODE -eq 0) { return $binary }
        }
    }
}

function Find-FFplay {
    $existing = Join-Path $app 'ffplay.exe'
    if (Test-Path -LiteralPath $existing) { return $existing }
    $command = Get-Command ffplay.exe -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $packages = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    $found = Get-ChildItem -Path (Join-Path $packages 'Gyan.FFmpeg_*') -Filter ffplay.exe -File -Recurse -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.FullName }
}

$player = if ($Lecteur) { (Resolve-Path -LiteralPath $Lecteur).Path } else { Join-Path $PSScriptRoot 'lecteur-ndi-windows.exe' }
if (-not (Test-Path -LiteralPath $player) -and -not (Test-Path -LiteralPath (Join-Path $app 'lecteur-ndi-windows.exe'))) {
    if (-not $SdlSdk -or -not (Test-Path -LiteralPath (Join-Path $NdiSdk 'Include\Processing.NDI.Lib.h'))) {
        throw 'Fournis le lecteur Windows x64 (-Lecteur), ou installe NDI 6 SDK et indique le dossier SDL2 de developpement Visual C++ (-SdlSdk).'
    }
    $env:SOUNDLIVE_BUILD_SCRIPT = Join-Path $PSScriptRoot 'compiler-lecteur-windows.ps1'
    $env:SOUNDLIVE_BUILD_NDI = (Resolve-Path -LiteralPath $NdiSdk).Path
    $env:SOUNDLIVE_BUILD_SDL = (Resolve-Path -LiteralPath $SdlSdk).Path
    if ($env:VSCMD_ARG_TGT_ARCH -eq 'x64' -and (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
        & $env:SOUNDLIVE_BUILD_SCRIPT -NdiSdk $env:SOUNDLIVE_BUILD_NDI -SdlSdk $env:SOUNDLIVE_BUILD_SDL
    } else {
        $vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
        if (-not (Test-Path -LiteralPath $vswhere)) { throw 'Installe Visual Studio Build Tools avec la charge de travail C++ x64.' }
        $visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
        if (-not $visualStudio) { throw 'Outils MSVC C++ x64 introuvables.' }
        $env:SOUNDLIVE_VSDEV = Join-Path $visualStudio 'Common7\Tools\VsDevCmd.bat'
        & cmd.exe /d /c 'call "%SOUNDLIVE_VSDEV%" -no_logo -arch=x64 && powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%SOUNDLIVE_BUILD_SCRIPT%" -NdiSdk "%SOUNDLIVE_BUILD_NDI%" -SdlSdk "%SOUNDLIVE_BUILD_SDL%"'
        if ($LASTEXITCODE -ne 0) { throw 'Compilation du lecteur Windows impossible.' }
    }
    if (-not (Test-Path -LiteralPath $player)) { throw 'Le binaire Windows compile est introuvable.' }
}
$installedPlayer = Join-Path $app 'lecteur-ndi-windows.exe'
if ($Lecteur -and (Test-Path -LiteralPath $player) -and ($player -ne $installedPlayer)) {
    $providedSdl = Join-Path (Split-Path -Parent $player) 'SDL2.dll'
    $kitSdl = Join-Path $PSScriptRoot 'SDL2.dll'
    if ((Test-Path -LiteralPath $providedSdl) -and $providedSdl -ne $kitSdl) {
        Copy-Item -LiteralPath $providedSdl -Destination $kitSdl -Force
    }
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'SDL2.dll')) -and
    -not (Test-Path -LiteralPath (Join-Path $app 'SDL2.dll')) -and
    -not (Get-Command SDL2.dll -ErrorAction SilentlyContinue)) {
    throw 'SDL2.dll x64 doit accompagner le binaire du lecteur.'
}
$ndiCandidates = @((Join-Path $app 'Processing.NDI.Lib.x64.dll'),
    (Join-Path $PSScriptRoot 'Processing.NDI.Lib.x64.dll'),
    (Join-Path $env:ProgramFiles 'NDI\NDI 6 Runtime\Processing.NDI.Lib.x64.dll'))
if ($env:NDI_RUNTIME_DIR_V6) {
    $ndiCandidates += Join-Path $env:NDI_RUNTIME_DIR_V6 'Processing.NDI.Lib.x64.dll'
}
if (-not ($ndiCandidates | Where-Object { Test-Path -LiteralPath $_ }) -and
    -not (Get-Command Processing.NDI.Lib.x64.dll -ErrorAction SilentlyContinue)) {
    throw 'Installe le runtime NDI 6 x64 ou place Processing.NDI.Lib.x64.dll a cote du kit.'
}
try {
    if (-not $SansDhcpInstallation) {
        if ($wired.Status -eq 'Disabled') {
            $wired | Enable-NetAdapter -Confirm:$false
            $wired = Get-NetAdapter -Physical | Where-Object { $_.ifIndex -eq $wired.ifIndex }
        }
        if ($wired.Status -eq 'Up' -or $wired.MediaConnectState -eq 1) {
            $previousDhcp = (Get-NetIPInterface -InterfaceIndex $wired.ifIndex -AddressFamily IPv4).Dhcp
            $previousInstallIps = @(Get-NetIPAddress -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object PrefixOrigin -eq 'Manual')
            $previousInstallRoutes = @(Get-NetRoute -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -PolicyStore ActiveStore -ErrorAction SilentlyContinue |
                Where-Object Protocol -eq 'NetMgmt')
            $previousInstallDns = (Get-DnsClientServerAddress -InterfaceIndex $wired.ifIndex -AddressFamily IPv4).ServerAddresses
            if ($oldTaskRunning) {
                Stop-ScheduledTask -TaskName $taskName
                $pausedTask = $true
            }
            $dhcpChanged = $true
            Get-NetIPAddress -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -PolicyStore ActiveStore -ErrorAction SilentlyContinue |
                Where-Object PrefixOrigin -eq 'Manual' | Remove-NetIPAddress -Confirm:$false
            $previousInstallRoutes | Remove-NetRoute -Confirm:$false
            Set-NetIPInterface -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -PolicyStore ActiveStore -Dhcp Enabled
            Set-DnsClientServerAddress -InterfaceIndex $wired.ifIndex -ResetServerAddresses
            Write-Host 'Phase installation sur Ethernet en DHCP.'
            $lease = $null
            for ($wait = 0; $wait -lt 20; $wait++) {
                $lease = Get-NetIPAddress -InterfaceIndex $wired.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                    Where-Object { $_.PrefixOrigin -eq 'Dhcp' -and $_.AddressState -eq 'Preferred' }
                if ($lease) { break }
                Start-Sleep -Seconds 1
            }
            if (-not $lease) {
                Restore-InstallationNetwork
                Write-Warning 'Aucun bail DHCP Ethernet. Utilisation de la connexion actuelle pour les telechargements.'
            }
        }
    }
    if ($CleSsh) {
        & (Join-Path $PSScriptRoot 'installer-ssh-windows.ps1') -CleSsh $CleSsh
    } elseif ($EmpreinteRegie) {
        & (Join-Path $PSScriptRoot 'installer-ssh-windows.ps1') -InstallerSeulement
    }
    if ($prepareDesktop) {
        & (Join-Path $PSScriptRoot 'installer-bureau-distant-windows.ps1') -VncMsi $VncMsi -ActiverRdp
    }
    $python = Find-DisplayPython
    if (-not $python) {
        Install-WingetPackage 'Python.Python.3.12'
        $python = Find-DisplayPython
        if (-not $python) { throw 'Python x64 avec Tcl/Tk est introuvable apres installation.' }
    }
    if (-not $Ffplay) { $Ffplay = Find-FFplay }
    if (-not $Ffplay) {
        Install-WingetPackage 'Gyan.FFmpeg'
        $Ffplay = Find-FFplay
    }
    if (-not $Ffplay) { throw 'FFplay introuvable. Indique le chemin de ffplay.exe avec -Ffplay.' }
    $ffplayPath = (Resolve-Path -LiteralPath $Ffplay).Path
    & $ffplayPath -version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Le binaire FFplay fourni ne fonctionne pas.' }
    New-Item -ItemType Directory -Path $app -Force | Out-Null
    $ffplayDestination = Join-Path $app 'ffplay.exe'
    if ($ffplayPath -ne $ffplayDestination) {
        Copy-Item -LiteralPath $ffplayPath -Destination $ffplayDestination -Force
    }
    & $ffplayDestination -version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Utilise une distribution FFplay Windows autonome.' }

    function Register-WifiProfile([string]$XmlFile) {
        & netsh.exe wlan add profile "filename=$XmlFile" "interface=$($wireless.Name)" user=all | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'Import du profil Wi-Fi impossible.' }
    }

    function Initialize-WifiProfile {
        $temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
        New-Item -ItemType Directory -Path $temp | Out-Null
        try {
            $source = $ProfilWifiXml
            $wifiXml = Join-Path $temp 'wifi.xml'
            if (-not $source -and -not $CleWifiFichier -and -not $SaisirCleWifi -and -not $WifiOuvert) {
                $profiles = if ($ProfilWifi) { @($ProfilWifi) } else { @($wifiName, $Wifi) }
                foreach ($profile in $profiles) {
                    & netsh.exe wlan export profile "name=$profile" "interface=$($wireless.Name)" "folder=$temp" | Out-Null
                    $saved = Get-ChildItem -LiteralPath $temp -Filter '*.xml' -File | Select-Object -First 1
                    if ($saved) {
                        [xml]$export = Get-Content -Raw -LiteralPath $saved.FullName
                        if ($export.WLANProfile.SSIDConfig.SSID.name -ceq $Wifi) {
                            $source = $saved.FullName
                            break
                        }
                        $saved | Remove-Item -Force
                    }
                }
            }
            if ($source) {
                $xmlProfile = [xml](Get-Content -Raw -LiteralPath $source)
                if ($xmlProfile.WLANProfile.SSIDConfig.SSID.name -cne $Wifi) {
                    throw 'Le profil XML ne correspond pas au SSID indique.'
                }
                $xmlProfile.DocumentElement.SelectSingleNode("*[local-name()='name']").InnerText = $wifiName
                $xmlProfile.DocumentElement.SelectSingleNode("*[local-name()='connectionMode']").InnerText = 'manual'
                $xmlProfile.Save($wifiXml)
                Register-WifiProfile $wifiXml
                return
            }
            $ssidXml = [Security.SecurityElement]::Escape($Wifi)
            $authentication = 'open'
            $encryption = 'none'
            $sharedKey = ''
            if (-not $WifiOuvert) {
                if ($CleWifiFichier) {
                    $password = Get-Content -Encoding UTF8 -LiteralPath $CleWifiFichier -TotalCount 1
                } else {
                    $secret = Read-Host "Mot de passe Wi-Fi WPA2 pour $Wifi" -AsSecureString
                    $buffer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
                    try {
                        $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($buffer)
                    } finally {
                        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($buffer)
                        $secret.Dispose()
                    }
                }
                $keyType = if ($password -match '^[0-9a-fA-F]{64}$') { 'networkKey' } else { 'passPhrase' }
                if ($keyType -eq 'passPhrase' -and $password -notmatch '^[ -~]{8,63}$') {
                    throw 'Cle WPA2 attendue : 8 a 63 caracteres ASCII ou 64 caracteres hexadecimaux.'
                }
                $authentication = 'WPA2PSK'
                $encryption = 'AES'
                $escaped = [Security.SecurityElement]::Escape($password)
                $sharedKey = "<sharedKey><keyType>$keyType</keyType><protected>false</protected><keyMaterial>$escaped</keyMaterial></sharedKey>"
            }
            $xml = @"
<?xml version="1.0" encoding="UTF-8"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>$wifiName</name><SSIDConfig><SSID><name>$ssidXml</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType><connectionMode>manual</connectionMode>
  <MSM><security><authEncryption>
    <authentication>$authentication</authentication><encryption>$encryption</encryption><useOneX>false</useOneX>
  </authEncryption>$sharedKey</security></MSM>
</WLANProfile>
"@
            Set-Content -Encoding UTF8 -LiteralPath $wifiXml -Value $xml
            Register-WifiProfile $wifiXml
        } finally {
            Remove-Item -LiteralPath $temp -Recurse -Force
        }
    }

    $wlanService = Get-Service WlanSvc
    if ($wlanService.Status -ne 'Running') { Start-Service WlanSvc }
    if ($AttendreReseauEcrans) {
        $null = Read-Host 'Branche le cable au reseau ecrans, puis appuie sur Entree'
    }
    $profileFolder = Join-Path $env:ProgramFiles 'soundlive-ndi\Network'
    New-Item -ItemType Directory -Path $profileFolder -Force | Out-Null
    if ($oldTaskRunning -and -not $pausedTask) {
        Stop-ScheduledTask -TaskName $taskName
        $pausedTask = $true
    }
    if ($wireless.Status -eq 'Disabled') { $wireless | Enable-NetAdapter -Confirm:$false }
    Initialize-WifiProfile
    if ($wired.Name -cne $wiredName) { $wired | Rename-NetAdapter -NewName $wiredName -Confirm:$false }
    if ($wireless.Name -cne $wifiName) { $wireless | Rename-NetAdapter -NewName $wifiName -Confirm:$false }
    $configPath = Join-Path $profileFolder 'configuration.json'
    $previous = if (Test-Path -LiteralPath $configPath) {
        Get-Content -Raw -LiteralPath $configPath | ConvertFrom-Json
    }
    $networkScript = Join-Path $profileFolder 'soundlive-ndi-network-windows.ps1'
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'soundlive-ndi-network-windows.ps1') -Destination $networkScript -Force
    @{ address=$address; prefix=$prefix; gateway=$Passerelle; dns=@($dnsServers); ssid=$Wifi; profile=$wifiName
       wired=[string]$wired.InterfaceGuid; wifi=[string]$wireless.InterfaceGuid } |
        ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $configPath

    # An old static configuration on these two dedicated adapters must not reappear at boot.
    foreach ($adapter in @($wired, $wireless)) {
        Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Dhcp Disabled
        foreach ($store in @('PersistentStore', 'ActiveStore')) {
            Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -PolicyStore $store -ErrorAction SilentlyContinue |
                Where-Object PrefixOrigin -eq 'Manual' | Remove-NetIPAddress -Confirm:$false
            Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -PolicyStore $store -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
                Where-Object Protocol -eq 'NetMgmt' | Remove-NetRoute -Confirm:$false
        }
        if ($dnsServers) {
            Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ServerAddresses $dnsServers
        } else {
            Set-DnsClientServerAddress -InterfaceIndex $adapter.ifIndex -ResetServerAddresses
        }
    }
    if ($wired.Status -eq 'Disabled') { $wired | Enable-NetAdapter -Confirm:$false }
    $action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$networkScript`" -Configuration `"$configPath`""
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -User SYSTEM -RunLevel Highest -Force | Out-Null
    Start-ScheduledTask -TaskName $taskName
    $staticInstalled = $true
    $ready = $false
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $active = Get-NetIPAddress -AddressFamily IPv4 -IPAddress $address -ErrorAction SilentlyContinue |
            Where-Object { $_.AddressState -eq 'Preferred' -and $_.InterfaceIndex -in @($wired.ifIndex, $wireless.ifIndex) }
        if ($active) { $ready = $true; break }
        Start-Sleep -Seconds 2
    }
    if (-not $ready) { throw "IPv4 $Ip indisponible. Verifie le cable, le SSID et $profileFolder\reseau.log." }
    if ($env:COMPUTERNAME -ine $Hote) {
        Rename-Computer -NewName $Hote -Force
    }

    $arguments = @('--lecteur', $player)
    if ($EmpreinteRegie) { $arguments += @('--empreinte-regie', $EmpreinteRegie) }
    if ($prepareDesktop) { $arguments += '--acces-deja-prepare' }
    if ($paired) { $arguments += @('--mettre-a-jour', $Regie.AbsoluteUri) }
    else { $arguments += @($Regie.AbsoluteUri, $Code) }
    & $python (Join-Path $PSScriptRoot 'installer-client-windows.py') @arguments
    if ($LASTEXITCODE -ne 0) { throw 'Installation ou association du lecteur Windows impossible.' }
    if ($previous) {
        try {
            $otherAdapters = Get-NetAdapter -IncludeHidden | Where-Object {
                [string]$_.InterfaceGuid -in @($previous.wired, $previous.wifi) -and
                $_.ifIndex -notin @($wired.ifIndex, $wireless.ifIndex)
            }
            foreach ($adapter in $otherAdapters) {
                foreach ($store in @('PersistentStore', 'ActiveStore')) {
                    if ($previous.address) {
                        Get-NetIPAddress -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -IPAddress $previous.address -PolicyStore $store -ErrorAction SilentlyContinue |
                            Where-Object PrefixOrigin -eq 'Manual' | Remove-NetIPAddress -Confirm:$false
                    }
                    if ($previous.gateway) {
                        Get-NetRoute -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -NextHop $previous.gateway -PolicyStore $store -ErrorAction SilentlyContinue |
                            Where-Object Protocol -eq 'NetMgmt' | Remove-NetRoute -Confirm:$false
                    }
                }
            }
            if ($previous.profile -match '^SOUNDLIVE[- ]WIFI$') {
                $previousWifi = Get-NetAdapter -IncludeHidden | Where-Object { [string]$_.InterfaceGuid -eq $previous.wifi }
                if ($previousWifi -and $previousWifi.Name -notmatch '[*?]' -and
                    ($previousWifi.ifIndex -ne $wireless.ifIndex -or $previous.profile -ine $wifiName)) {
                    & netsh.exe wlan delete profile "name=$($previous.profile)" "interface=$($previousWifi.Name)" | Out-Null
                    if ($LASTEXITCODE -ne 0) { Write-Warning 'La suppression de l ancien profil Wi-Fi SoundLive a echoue.' }
                }
            }
        } catch {
            Write-Warning "Nettoyage de l ancienne configuration SoundLive incomplet : $($_.Exception.Message)"
        }
    }
    if ($CleSsh -or $EmpreinteRegie) {
        Write-Host "Cible SSH : $env:USERNAME@$address"
        Write-Host 'Empreinte de ce lecteur :'
        & "$env:SystemRoot\System32\OpenSSH\ssh-keygen.exe" -lf (Join-Path $env:ProgramData 'ssh\ssh_host_ed25519_key.pub')
    }
    Write-Host 'Redemarre Windows pour activer le nouveau hostname et lancer le lecteur.'
} finally {
    if (-not $staticInstalled) {
        Restore-InstallationNetwork
        if ($pausedTask) { Start-ScheduledTask -TaskName $taskName }
    }
}
