# Tests en mode -Verifier uniquement : n'écrit aucune configuration réseau.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

foreach ($filename in @('preparer-lecteur-windows.ps1',
                         'soundlive-ndi-network-windows.ps1',
                         'regie-internet-windows.ps1')) {
    $path = Join-Path $root $filename
    $tokens = $null
    $parseErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        $path, [ref]$tokens, [ref]$parseErrors
    )
    if ($parseErrors.Count -gt 0) {
        throw ("Erreur PowerShell dans {0} : {1}" -f $filename,
               (($parseErrors | ForEach-Object { $_.Message }) -join '; '))
    }
    Write-Host "OK: analyse syntaxique $filename"
}

# Le lecteur simulé ne possède qu'une interface Ethernet.
function Get-NetAdapter {
    param([switch]$Physical)
    [pscustomobject]@{
        InterfaceType = 6
        Name = 'Ethernet'
        ifIndex = 12
        Status = 'Up'
        InterfaceGuid = '{00000000-0000-0000-0000-000000000012}'
    }
}
$preparer = Join-Path $root 'preparer-lecteur-windows.ps1'
$parameters = @{
    Hote = 'ndi-test-01'
    Ip = '192.168.56.21/24'
    Regie = [uri]'http://192.168.56.1:8765'
    Verifier = $true
}
& $preparer @parameters
Write-Host 'OK: Windows sans Wi-Fi, mode -Verifier, uniquement Ethernet.'

$failedAsExpected = $false
try {
    & $preparer @parameters -Wifi 'Ecrans' | Out-Null
} catch {
    if ($_.Exception.Message -match 'Indique le nom exact de la carte') {
        $failedAsExpected = $true
    } else {
        throw
    }
}
if (-not $failedAsExpected) {
    throw 'Le Wi-Fi demandé sans carte Wi-Fi aurait dû provoquer une erreur.'
}
Write-Host 'OK: Wi-Fi demandé sans adaptateur => refus attendu.'

$failedAsExpected = $false
try {
    & $preparer @parameters -InterfaceWifi 'Wi-Fi' | Out-Null
} catch {
    if ($_.Exception.Message -match 'Sans SSID') {
        $failedAsExpected = $true
    } else {
        throw
    }
}
if (-not $failedAsExpected) {
    throw 'Une interface Wi-Fi sans SSID doit être refusée.'
}
Write-Host 'OK: interface Wi-Fi sans SSID => refus attendu.'
