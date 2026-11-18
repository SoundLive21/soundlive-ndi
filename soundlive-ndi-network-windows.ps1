# Started by Task Scheduler as SYSTEM at boot.
param([Parameter(Mandatory=$true)][string]$Configuration)
$ErrorActionPreference = 'Stop'
$config = Get-Content -Raw -LiteralPath $Configuration | ConvertFrom-Json

function Remove-ReaderAddress($Index) {
    Get-NetIPAddress -InterfaceIndex $Index -AddressFamily IPv4 -IPAddress $config.address -ErrorAction SilentlyContinue |
        Remove-NetIPAddress -Confirm:$false
    if ($config.gateway) {
        Get-NetRoute -InterfaceIndex $Index -AddressFamily IPv4 -DestinationPrefix '0.0.0.0/0' -NextHop $config.gateway -ErrorAction SilentlyContinue |
            Remove-NetRoute -Confirm:$false
    }
}

function Set-ReaderAddress($Index) {
    $ip = Get-NetIPAddress -InterfaceIndex $Index -AddressFamily IPv4 -IPAddress $config.address -ErrorAction SilentlyContinue
    if ($ip -and $ip.PrefixLength -ne $config.prefix) {
        Remove-ReaderAddress $Index
        $ip = $null
    }
    if (-not $ip) {
        $parameters = @{
            InterfaceIndex = $Index
            IPAddress = $config.address
            PrefixLength = $config.prefix
            PolicyStore = 'ActiveStore'
        }
        if ($config.gateway) { $parameters.DefaultGateway = $config.gateway }
        New-NetIPAddress @parameters | Out-Null
        if ($config.dns) {
            Set-DnsClientServerAddress -InterfaceIndex $Index -ServerAddresses $config.dns
        }
    } elseif ($ip.AddressState -eq 'Duplicate') {
        throw "IPv4 $($config.address) en conflit sur le reseau."
    }
}

function Test-ReaderWifi($Adapter) {
    $connection = Get-NetConnectionProfile -InterfaceIndex $Adapter.ifIndex -ErrorAction SilentlyContinue
    if ($connection -and $connection.Name -ceq $config.ssid) { return $true }
    try {
        $network = [Windows.Networking.Connectivity.NetworkInformation,Windows,ContentType=WindowsRuntime]
        foreach ($profile in $network::GetConnectionProfiles()) {
            if ($profile.IsWlanConnectionProfile -and $profile.NetworkAdapter.NetworkAdapterId -eq [guid]$Adapter.InterfaceGuid) {
                return $profile.WlanConnectionProfileDetails.GetConnectedSsid() -ceq $config.ssid
            }
        }
    } catch { Write-Verbose $_.Exception.Message }
    $status = (& netsh.exe wlan show interfaces 2>$null) -join "`n"
    $guid = [regex]::Escape(([string]$Adapter.InterfaceGuid).Trim('{}'))
    $ssid = [regex]::Escape($config.ssid)
    # The localized output retains the GUID and SSID field names.
    return $status -cmatch "(?m)^\s*GUID\s*:\s*\{?(?i:$guid)\}?[^\r\n]*(?:(?!^\s*GUID\s*:)[\s\S])*?^\s*SSID\s*:\s*$ssid\s*$"
}

$lastError = ''
while ($true) {
    try {
        $adapters = Get-NetAdapter -Physical
        $wired = $adapters | Where-Object { [string]$_.InterfaceGuid -eq $config.wired }
        $wifi = $adapters | Where-Object { [string]$_.InterfaceGuid -eq $config.wifi }
        if (-not $wired -or -not $wifi) {
            throw 'Adaptateur Ethernet ou Wi-Fi introuvable.'
        }
        if ($wired.MediaConnectState -eq 1 -or $wired.Status -eq 'Up') {
            Remove-ReaderAddress $wifi.ifIndex
            if ($wifi.Status -ne 'Disabled') {
                $wifi | Disable-NetAdapter -Confirm:$false
            }
            Set-ReaderAddress $wired.ifIndex
        } else {
            Remove-ReaderAddress $wired.ifIndex
            if ($wifi.Status -eq 'Disabled') {
                $wifi | Enable-NetAdapter -Confirm:$false
            }
            if ($wifi.Status -ne 'Up') {
                & netsh.exe wlan connect "name=$($config.profile)" "ssid=$($config.ssid)" "interface=$($wifi.Name)" | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    throw "Connexion au profil Wi-Fi $($config.profile) impossible."
                }
            } else {
                # Refuse to assign the reader IPv4 on another saved Wi-Fi network.
                if (Test-ReaderWifi $wifi) {
                    Set-ReaderAddress $wifi.ifIndex
                } else {
                    Remove-ReaderAddress $wifi.ifIndex
                    & netsh.exe wlan connect "name=$($config.profile)" "ssid=$($config.ssid)" "interface=$($wifi.Name)" | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw 'Connexion au SSID configure impossible.' }
                }
            }
        }
        $lastError = ''
    } catch {
        $message = $_.Exception.Message
        if ($message -ne $lastError) {
            try {
                Set-Content -Encoding UTF8 -LiteralPath (Join-Path $PSScriptRoot 'reseau.log') -Value "$(Get-Date -Format s) $message"
            } catch { Write-Warning $_.Exception.Message }
            $lastError = $message
        }
    }
    Start-Sleep -Seconds 3
}
