# Configuration locale d'un lecteur Windows dédié SoundLive-NDI.
# Les identifiants Autologon ne sont jamais lus ni conservés par SoundLive.
[CmdletBinding()]
param(
    [switch]$Verifier,
    [switch]$SansAssistantAutologon
)
$ErrorActionPreference = 'Stop'
if ($Verifier) {
    Write-Host 'Vérification : écrans et veille désactivés; autologin guidé Microsoft Sysinternals.'
    return
}
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Exécute ce script dans PowerShell administrateur sous le compte du lecteur.'
}

# Ces paramètres s'appliquent au compte courant et au plan d'énergie actif.
$desktop = 'HKCU:\Control Panel\Desktop'
foreach ($setting in @(
    @{ Name='ScreenSaveActive'; Value='0' },
    @{ Name='ScreenSaveTimeOut'; Value='0' },
    @{ Name='ScreenSaverIsSecure'; Value='0' }
)) {
    New-ItemProperty -Path $desktop -Name $setting.Name -Value $setting.Value -PropertyType String -Force | Out-Null
}
foreach ($change in @(
    @('monitor-timeout-ac', '0'), @('monitor-timeout-dc', '0'),
    @('standby-timeout-ac', '0'), @('standby-timeout-dc', '0')
)) {
    & powercfg.exe /change $change[0] $change[1]
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Impossible d'appliquer powercfg /change $($change -join ' ')"
    }
}
Write-Host 'Lecteur Windows : économiseur, extinction de l’écran et veille automatique désactivés.'

$winlogon = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$settings = Get-ItemProperty -Path $winlogon -ErrorAction Stop
if ($settings.AutoAdminLogon -eq '1' -and $settings.DefaultUserName -ieq $env:USERNAME) {
    Write-Host "Connexion automatique déjà configurée pour $env:USERNAME."
    return
}
if ($SansAssistantAutologon) {
    Write-Warning 'Autologin non actif : lance Microsoft Sysinternals Autologon pour le compte du lecteur.'
    return
}
Write-Host 'Connexion automatique Windows : ouverture de l’assistant Microsoft Sysinternals Autologon.'
Write-Host 'Renseigne le compte Windows du lecteur, son domaine ou le nom du PC, puis son mot de passe.'
Write-Host 'Clique sur Enable. Les identifiants ne transitent pas par SoundLive-NDI.'
$folder = Join-Path $env:TEMP ('soundlive-autologon-' + [guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $archive = Join-Path $folder 'Autologon.zip'
    Invoke-WebRequest -Uri 'https://download.sysinternals.com/files/Autologon.zip' -OutFile $archive -UseBasicParsing -TimeoutSec 25
    Expand-Archive -LiteralPath $archive -DestinationPath $folder -Force
    $executable = Join-Path $folder 'Autologon64.exe'
    if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw 'Autologon64.exe introuvable dans le paquet Microsoft officiel.'
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $executable
    if ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Subject -notmatch 'Microsoft Corporation') {
        throw 'Signature de l’utilitaire Autologon non reconnue comme valide Microsoft.'
    }
    Start-Process -FilePath $executable -Wait
    Write-Host 'Vérifie dans Autologon que la connexion automatique a bien été activée.'
} catch {
    Write-Warning "Assistant Autologon indisponible : $($_.Exception.Message)"
    Write-Host 'Téléchargement manuel : https://learn.microsoft.com/fr-fr/sysinternals/downloads/autologon'
} finally {
    if (Test-Path -LiteralPath $folder) {
        Remove-Item -LiteralPath $folder -Force -Recurse -ErrorAction SilentlyContinue
    }
}
Write-Warning 'Autologin : accès physique au PC sans mot de passe. Réserve cette configuration aux lecteurs dédiés.'
