[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][ValidateSet('activer', 'desactiver', 'diagnostic')][string]$Action,
    [string]$Gateway
)
$ErrorActionPreference = 'Stop'
$name = 'SoundLive-NDI Internet'
$rootKey = 'HKLM:\SOFTWARE\SoundLive'
$stateKey = Join-Path $rootKey 'Internet'
$stage = 'Initialisation'
$notes = [System.Collections.Generic.List[string]]::new()

function Write-SoundLiveSetting([string]$Path, [string]$Property, $Value) {
    if (-not (Test-Path -LiteralPath $rootKey)) {
        New-Item -Path $rootKey | Out-Null
    }
    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -Path $Path | Out-Null
    }
    Set-ItemProperty -LiteralPath $Path -Name $Property -Value ($Value | ConvertTo-Json -Compress -Depth 6)
}

function Get-SoundLiveState {
    if (Test-Path -LiteralPath $stateKey) {
        $settings = (Get-ItemProperty -LiteralPath $stateKey).Settings
        if ($settings) { return $settings | ConvertFrom-Json }
    }
}

function Restore-SoundLiveForwarding($State) {
    foreach ($entry in @($State.lan, $State.uplink)) {
        $adapter = Get-NetAdapter -IncludeHidden | Where-Object { [string]$_.InterfaceGuid -eq $entry.guid }
        if ($adapter) {
            Set-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -Forwarding $entry.forwarding
        }
    }
}

function Get-NetworkPrefix([byte[]]$Bytes, [int]$Length) {
    for ($i = 0; $i -lt 4; $i++) {
        $bits = [Math]::Max(0, [Math]::Min(8, $Length - 8 * $i))
        $Bytes[$i] -band (256 - (1 -shl (8 - $bits)))
    }
}

function Invoke-SoundLiveSharing {
    $script:stage = 'Vérification de WinNAT'
    if (-not (Get-Command Get-NetNat -ErrorAction SilentlyContinue) -or
        -not (Get-Command New-NetNat -ErrorAction SilentlyContinue)) {
        throw 'Les commandes WinNAT sont indisponibles sur ce poste.'
    }
    $nats = @(Get-NetNat)
    $current = $nats | Where-Object Name -eq $name
    foreach ($nat in $nats) {
        $notes.Add("NAT existant : $($nat.Name) · $($nat.InternalIPInterfaceAddressPrefix)")
    }
    if (-not $nats.Count) { $notes.Add('WinNAT répond. Aucun NAT recensé.') }

    $script:stage = 'Lecture de la configuration SoundLive'
    $own = Get-SoundLiveState
    if ($Action -eq 'desactiver') {
        if ($own) {
            $script:stage = 'Retrait du NAT SoundLive'
            if ($current) { $current | Remove-NetNat -Confirm:$false }
            $script:stage = 'Restauration du routage IPv4'
            Restore-SoundLiveForwarding $own
            Remove-Item -LiteralPath $stateKey -Force
        }
        $notes.Add('Partage SoundLive désactivé.')
        return
    }

    $script:stage = 'Détection de la sortie Internet'
    $route = Find-NetRoute -RemoteIPAddress '1.1.1.1' |
        Where-Object { $_.PSObject.Properties['DestinationPrefix'] } | Select-Object -First 1
    if (-not $route) { throw 'Aucune route IPv4 vers Internet trouvée.' }
    $adapters = @(Get-NetAdapter -IncludeHidden)
    $uplink = $adapters | Where-Object ifIndex -eq $route.InterfaceIndex
    if (-not $uplink) { throw 'Carte de sortie Internet introuvable.' }
    $notes.Add("Sortie Internet : $($uplink.Name) · prochain saut $($route.NextHop)")

    $script:stage = 'Sélection de la carte du réseau écrans'
    if (-not $Gateway) { throw "Sélectionne l'adresse IPv4 de la carte reliée aux écrans." }
    $ipv4 = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred)
    $lanIp = @($ipv4 | Where-Object IPAddress -eq $Gateway)
    if ($lanIp.Count -ne 1) { throw "L'adresse choisie doit être active sur une seule carte de la régie." }
    $lanIp = $lanIp[0]
    $lan = $adapters | Where-Object ifIndex -eq $lanIp.InterfaceIndex
    if (-not $lan) { throw 'Carte du réseau écrans introuvable.' }
    $notes.Add("Réseau écrans : $($lan.Name) · $Gateway/$($lanIp.PrefixLength)")
    if ($route.InterfaceIndex -eq $lanIp.InterfaceIndex) {
        throw 'La carte choisie est aussi celle de la sortie Internet. Choisis la carte du réseau écrans.'
    }

    $script:stage = 'Vérification des conflits NAT'
    if ($current -and $own -and $own.gateway -eq $Gateway -and $own.uplink.guid -eq [string]$uplink.InterfaceGuid) {
        $notes.Add('Un partage SoundLive existe déjà avec ces interfaces.')
        return
    }
    if ($nats.Count -or $own) {
        throw 'Un NAT ou une configuration SoundLive existe déjà. Consulte la liste du diagnostic.'
    }

    $script:stage = 'Vérification du partage de connexion Windows'
    $icsService = Get-Service -Name SharedAccess -ErrorAction SilentlyContinue
    if ($icsService -and $icsService.Status -eq 'Running') {
        $ics = New-Object -ComObject HNetCfg.HNetShare
        foreach ($connection in $ics.EnumEveryConnection) {
            $configuration = $ics.INetSharingConfigurationForINetConnection.Invoke($connection)
            if ($configuration.SharingEnabled) {
                $interface = $ics.NetConnectionProps.Invoke($connection).Name
                throw "Le partage de connexion Windows est déjà actif sur $interface."
            }
        }
    }

    $script:stage = 'Vérification du sous-réseau écrans'
    [byte[]]$bytes = ([Net.IPAddress]$Gateway).GetAddressBytes()
    if (-not ($bytes[0] -eq 10 -or ($bytes[0] -eq 192 -and $bytes[1] -eq 168) -or
              ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31))) {
        throw 'Utilise une adresse IPv4 privée sur le réseau écrans.'
    }
    $privateLength = if ($bytes[0] -eq 192) { 16 } elseif ($bytes[0] -eq 172) { 12 } else { 8 }
    if ($lanIp.PrefixLength -lt $privateLength -or $lanIp.PrefixLength -gt 30) {
        throw 'Le préfixe du réseau écrans doit rester privé et avoir entre 2 et 24 bits hôte.'
    }
    $prefix = ( (Get-NetworkPrefix $bytes $lanIp.PrefixLength) -join '.' ) + '/' + $lanIp.PrefixLength
    foreach ($other in ($ipv4 | Where-Object InterfaceIndex -ne $lan.ifIndex)) {
        [byte[]]$peer = ([Net.IPAddress]$other.IPAddress).GetAddressBytes()
        $common = [Math]::Min([int]$lanIp.PrefixLength, [int]$other.PrefixLength)
        if (((Get-NetworkPrefix $bytes $common) -join '.') -eq ((Get-NetworkPrefix $peer $common) -join '.')) {
            throw "Le réseau $prefix recouvre $($other.InterfaceAlias) ($($other.IPAddress)/$($other.PrefixLength))."
        }
    }
    $notes.Add("Sous-réseau NAT prévu : $prefix")
    if ($Action -eq 'diagnostic') {
        $notes.Add("Vérifications terminées. L'activation peut être essayée.")
        return
    }

    $script:stage = 'Lecture du routage IPv4'
    $state = @{
        gateway=$Gateway; network=$prefix
        lan=@{ guid=[string]$lan.InterfaceGuid; forwarding=[string](Get-NetIPInterface -InterfaceIndex $lan.ifIndex -AddressFamily IPv4).Forwarding }
        uplink=@{ guid=[string]$uplink.InterfaceGuid; forwarding=[string](Get-NetIPInterface -InterfaceIndex $uplink.ifIndex -AddressFamily IPv4).Forwarding }
    }
    $created = $false
    $routingAttempted = $false
    try {
        $script:stage = 'Enregistrement de la configuration SoundLive'
        Write-SoundLiveSetting $stateKey 'Settings' $state
        $script:stage = 'Création du NAT (New-NetNat)'
        New-NetNat -Name $name -InternalIPInterfaceAddressPrefix $prefix | Out-Null
        $created = $true
        $routingAttempted = $true
        $script:stage = "Activation du routage IPv4 · $($lan.Name)"
        Set-NetIPInterface -InterfaceIndex $lan.ifIndex -AddressFamily IPv4 -Forwarding Enabled
        $script:stage = "Activation du routage IPv4 · $($uplink.Name)"
        Set-NetIPInterface -InterfaceIndex $uplink.ifIndex -AddressFamily IPv4 -Forwarding Enabled
    } catch {
        $failure = $_
        $restored = $true
        if ($created) {
            try { Remove-NetNat -Name $name -Confirm:$false }
            catch { $notes.Add("Retrait du NAT après échec : $($_.Exception.Message)"); $restored = $false }
        }
        if ($routingAttempted) {
            try { Restore-SoundLiveForwarding ([pscustomobject]$state) }
            catch { $notes.Add("Restauration du routage après échec : $($_.Exception.Message)"); $restored = $false }
        }
        if ($restored -and (Test-Path -LiteralPath $stateKey)) {
            try { Remove-Item -LiteralPath $stateKey -Force }
            catch { $notes.Add("Nettoyage de l'état SoundLive : $($_.Exception.Message)") }
        }
        throw $failure
    }
    $notes.Add("Partage SoundLive activé via $Gateway.")
}

$report = @{ success=$false }
try {
    $edition = Get-ItemProperty -LiteralPath 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion'
    $notes.Add("Windows : $($edition.EditionID) · build $($edition.CurrentBuild).$($edition.UBR)")
    Invoke-SoundLiveSharing
    $report.success = $true
} catch {
    $report.error = $_.Exception.Message
    $report.code = [string]$_.FullyQualifiedErrorId
    $report.hresult = '0x{0:X8}' -f $_.Exception.HResult
}
$notes | Write-Output
if (-not $report.success) {
    Write-Output "Étape : $stage"
    Write-Output "Erreur : $($report.error)"
    Write-Output "Code : $($report.code) · HRESULT $($report.hresult)"
    exit 1
}
exit 0
