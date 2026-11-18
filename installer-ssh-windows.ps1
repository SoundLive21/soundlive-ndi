[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$CleSsh)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Ouvre PowerShell en administrateur sous le compte du poste.'
}
$key = (Get-Content -Raw -LiteralPath $CleSsh).Trim()
if ($key -notmatch '^(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+( [^\r\n]*)?$') {
    throw 'Fournis une seule cle publique OpenSSH (.pub).'
}
foreach ($name in @('OpenSSH.Client~~~~0.0.1.0', 'OpenSSH.Server~~~~0.0.1.0')) {
    if ((Get-WindowsCapability -Online -Name $name).State -ne 'Installed') {
        Add-WindowsCapability -Online -Name $name | Out-Null
    }
}
$keygen = "$env:SystemRoot\System32\OpenSSH\ssh-keygen.exe"
& $keygen -lf (Resolve-Path -LiteralPath $CleSsh).Path | Out-Host
if ($LASTEXITCODE -ne 0) { throw 'Cle publique OpenSSH invalide.' }
$sshFolder = Join-Path $env:ProgramData 'ssh'
New-Item -ItemType Directory -Path $sshFolder -Force | Out-Null
$authorized = Join-Path $sshFolder 'administrators_authorized_keys'
if (-not (Test-Path -LiteralPath $authorized)) {
    New-Item -ItemType File -Path $authorized | Out-Null
}
$acl = [Security.AccessControl.FileSecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
    $identity = [Security.Principal.SecurityIdentifier]::new($sid)
    $rule = [Security.AccessControl.FileSystemAccessRule]::new($identity, 'FullControl', 'Allow')
    $acl.AddAccessRule($rule)
}
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
Set-Acl -LiteralPath $authorized -AclObject $acl
$blob = ($key -split ' ')[1]
$content = [IO.File]::ReadAllText($authorized)
if (-not ($content -split '\r?\n' | Where-Object { -not $_.StartsWith('#') -and $blob -in ($_ -split '\s+') })) {
    $separator = if ($content -and -not $content.EndsWith("`n")) { "`r`n" } else { '' }
    [IO.File]::AppendAllText($authorized, $separator + $key + "`r`n", [Text.UTF8Encoding]::new($false))
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
