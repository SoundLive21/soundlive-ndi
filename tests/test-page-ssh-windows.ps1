$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$generated = & python -c "from deploiement_lecteur import command_windows; print(command_windows('192.168.56.1',8765,'CODE_1234','aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'))"
if ($LASTEXITCODE -ne 0) { throw 'Génération de la commande Windows impossible.' }
if ($generated -notmatch '-EncodedCommand ([A-Za-z0-9+/=]+)$') {
    throw 'La commande de déploiement n’utilise pas PowerShell EncodedCommand.'
}
$script = [Text.Encoding]::Unicode.GetString([Convert]::FromBase64String($Matches[1]))
$tokens = $null
$errors = $null
[void][Management.Automation.Language.Parser]::ParseInput($script, [ref]$tokens, [ref]$errors)
if ($errors.Count) {
    throw "Erreur de syntaxe dans le déploiement distant : $($errors -join '; ')"
}
if (-not $script.Contains("'--empreinte-regie' 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'")) {
    throw "La commande Windows distante doit transmettre l'empreinte de la régie."
}
Write-Host 'OK: commande de déploiement SSH Windows valide avec empreinte de la régie.'

$source = & python -c "from page_ssh import windows_command; print(windows_command('ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAQAB'))"
if ($LASTEXITCODE -ne 0) { throw 'Génération de la préparation SSH Windows impossible.' }
[void][Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$errors)
if ($errors.Count) {
    throw "Erreur de syntaxe dans la préparation SSH Windows : $($errors -join '; ')"
}
Write-Host 'OK: préparation SSH locale Windows valide.'
