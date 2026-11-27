$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Relance setup-windows.cmd en administrateur sous le compte du lecteur.'
}
Write-Host '1. Conserver le reseau actuel'
Write-Host '2. DHCP avec priorite Ethernet (Wi-Fi deja configure dans Windows)'
Write-Host '3. IPv4 fixe avec Ethernet et Wi-Fi facultatif'
$choice = Read-Host 'Choix'
switch ($choice) {
    '1' {
        & (Join-Path $PSScriptRoot 'installer-client.cmd') --installer-seul
        exit $LASTEXITCODE
    }
    '2' {
        & (Join-Path $PSScriptRoot 'installer-client.cmd') --installer-seul
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & (Join-Path $PSScriptRoot 'installer-client.cmd') --reseau-auto
        exit $LASTEXITCODE
    }
    '3' {
        Get-NetAdapter -Physical | Format-Table Name, InterfaceDescription, Status | Out-Host
        $hostName = Read-Host "Nom du lecteur [$env:COMPUTERNAME]"
        if (-not $hostName) { $hostName = $env:COMPUTERNAME }
        $ip = Read-Host 'IPv4/prefixe (ex. 192.168.56.10/24)'
        $ethernet = Read-Host 'Interface Ethernet (vide = automatique)'
        $gateway = Read-Host 'Passerelle (facultative)'
        $dns = Read-Host 'DNS (facultatif)'
        $ssid = Read-Host 'SSID Wi-Fi (vide = Ethernet seul)'
        $params = @{ Hote=$hostName; Ip=$ip; InstallerSeul=$true }
        if ($ethernet) { $params.Ethernet = $ethernet }
        if ($gateway) { $params.Passerelle = $gateway }
        if ($dns) { $params.Dns = $dns }
        if ($ssid) {
            $params.Wifi = $ssid
            $wifi = Read-Host 'Interface Wi-Fi (vide = automatique)'
            if ($wifi) { $params.InterfaceWifi = $wifi }
            if ((Read-Host 'Reseau ouvert ? [o/N]') -eq 'o') { $params.WifiOuvert = $true }
            else { $params.SaisirCleWifi = $true }
        }
        if ((Read-Host 'Appliquer cette configuration sur ce poste dedie ? [o/N]') -ne 'o') { exit 1 }
        & (Join-Path $PSScriptRoot 'preparer-lecteur-windows.ps1') @params
        exit 0
    }
    default { throw 'Choix invalide.' }
}
