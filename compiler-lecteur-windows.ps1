# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
param(
    [string]$NdiSdk = "$env:ProgramFiles\NDI\NDI 6 SDK",
    [Parameter(Mandatory=$true)][string]$SdlSdk
)
$ErrorActionPreference = 'Stop'
if (-not (Get-Command cl.exe -ErrorAction SilentlyContinue)) {
    throw 'Ouvre le terminal x64 Native Tools de Visual Studio Build Tools.'
}
if ($env:VSCMD_ARG_TGT_ARCH -ne 'x64') {
    throw 'Sélectionne la chaîne de compilation Visual Studio x64.'
}
$ndi = Get-ChildItem -LiteralPath $NdiSdk -Recurse -Filter 'Processing.NDI.Lib.x64.lib' -File |
    Select-Object -First 1
$sdl = Join-Path $SdlSdk 'lib\x64\SDL2.lib'
$runtime = Join-Path $SdlSdk 'lib\x64\SDL2.dll'
if (-not $ndi -or -not (Test-Path -LiteralPath $sdl) -or -not (Test-Path -LiteralPath $runtime)) {
    throw 'Vérifie les dossiers du SDK NDI 6 et du paquet de développement SDL2 pour Visual C++.'
}
$source = Join-Path $PSScriptRoot 'lecteur-ndi-ubuntu.c'
$destination = Join-Path $PSScriptRoot 'lecteur-ndi-windows.exe'
$work = Join-Path ([System.IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString())
New-Item -ItemType Directory -Path $work | Out-Null
Push-Location $work
try {
    $flags = @('/nologo', '/std:c11', '/O2', '/utf-8', '/MT',
        "/I$NdiSdk\Include", "/I$SdlSdk\include", $source, '/link', "/OUT:$destination", $ndi.FullName, $sdl)
    & cl.exe @flags
    if ($LASTEXITCODE -ne 0) { throw 'La compilation du lecteur a échoué.' }
    Copy-Item -LiteralPath $runtime -Destination $PSScriptRoot -Force
}
finally {
    Pop-Location
    Remove-Item -LiteralPath $work -Recurse -Force
}
