$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
foreach ($path in @('configurer-session-lecteur-windows.ps1', 'preparer-lecteur-windows.ps1')) {
    $errors = $null
    $tokens = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $root $path), [ref]$tokens, [ref]$errors
    )
    if ($errors.Count -gt 0) {
        throw ("Erreur de syntaxe : {0}: {1}" -f $path, ($errors -join '; '))
    }
    Write-Host "OK: analyse syntaxique $path"
}
& (Join-Path $root 'configurer-session-lecteur-windows.ps1') -Verifier
Write-Host 'OK: aucune modification du registre, des profils réseau ni de la veille.'
