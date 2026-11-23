"""Page de préparation SSH ; ne transmet jamais les clés privées."""
from html import escape
from pathlib import Path
from installation_lecteur import public_line
import shlex

ROOT = Path(__file__).resolve().parent


def linux_command(public):
    quoted = shlex.quote(public)
    return ("""# Sur le NOUVEAU lecteur Ubuntu ou Raspberry Pi, sous le compte du bureau :
sudo apt-get update
sudo apt-get install -y openssh-server openssh-client git
install -d -m 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
"""
            + "grep -Fxq -- " + quoted + ' "$HOME/.ssh/authorized_keys" || '
            + 'printf "%s\\n" ' + quoted + ' >> "$HOME/.ssh/authorized_keys"\n'
            + """chmod 600 "$HOME/.ssh/authorized_keys"
sudo systemctl enable --now ssh.service
printf 'Compte SSH : '; whoami
printf 'Adresses IPv4 : '; hostname -I
printf 'Empreinte du lecteur :\\n'
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
""")


def windows_command(public):
    # Le clé est réduite au type et au bloc base64, sans commentaire.
    quoted = "'" + public.replace("'", "''") + "'"
    return ("""# Sur le NOUVEAU lecteur Windows, PowerShell en administrateur :
$ErrorActionPreference = 'Stop'
$public = """ + quoted + """
foreach ($name in @('OpenSSH.Client~~~~0.0.1.0','OpenSSH.Server~~~~0.0.1.0')) {
  if ((Get-WindowsCapability -Online -Name $name).State -ne 'Installed') {
    Add-WindowsCapability -Online -Name $name | Out-Null
  }
}
$folder = Join-Path $env:ProgramData 'ssh'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$file = Join-Path $folder 'administrators_authorized_keys'
if (-not (Test-Path -LiteralPath $file)) {
  New-Item -ItemType File -Path $file | Out-Null
}
$acl = [Security.AccessControl.FileSecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
foreach ($sid in @('S-1-5-18', 'S-1-5-32-544')) {
  $identity = [Security.Principal.SecurityIdentifier]::new($sid)
  $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($identity,'FullControl','Allow'))
}
$acl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
Set-Acl -LiteralPath $file -AclObject $acl
$blob = ($public -split ' ')[1]
$content = [IO.File]::ReadAllText($file)
if (-not ($content -split '\\r?\\n' | Where-Object { -not $_.StartsWith('#') -and $blob -in ($_ -split '\\s+') })) {
  [IO.File]::AppendAllText($file, $public + "`r`n", [Text.UTF8Encoding]::new($false))
}
Set-Service -Name sshd -StartupType Automatic
Start-Service sshd
if (-not (Get-NetFirewallRule -Name 'SoundLive-NDI-SSH' -ErrorAction SilentlyContinue)) {
  New-NetFirewallRule -Name 'SoundLive-NDI-SSH' -DisplayName 'SoundLive-NDI SSH LAN' `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress LocalSubnet | Out-Null
}
Write-Host "Compte : $env:USERNAME"
Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '169.254*' } |
  Select-Object InterfaceAlias,IPAddress
& (Join-Path $env:SystemRoot 'System32\\OpenSSH\\ssh-keygen.exe') -lf (Join-Path $folder 'ssh_host_ed25519_key.pub')
""")


def render(public):
    public = public_line(public)
    text = (ROOT / "addsshkey.html").read_text(encoding="utf-8")
    for marker, value in (
        ("__SSH_PUBLIC_KEY__", public),
        ("__LINUX_COMMAND__", linux_command(public)),
        ("__WINDOWS_COMMAND__", windows_command(public)),
    ):
        text = text.replace(marker, escape(value, quote=False))
    return text.encode("utf-8")
