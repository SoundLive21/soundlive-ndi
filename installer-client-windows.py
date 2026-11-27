# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Installation du récepteur Windows dans le profil de la session d’affichage."""
import argparse
from base64 import b64encode
import ctypes
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from installation_lecteur import SSH_PUBLIC, fetch_keys
from mise_a_jour import FILES, PUBLIC_KEY, ssh_keygen, ssh_keygen_path

ROOT = Path(__file__).resolve().parent


def install_network_priority():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise OSError("Ouvre un terminal en tant qu’administrateur pour activer la bascule Ethernet/Wi-Fi.")
    monitor = r"""
$ErrorActionPreference = 'Stop'
while ($true) {
    try {
        $physical = Get-CimInstance -Namespace 'root/StandardCimv2' -ClassName 'MSFT_NetAdapter' |
            Where-Object HardwareInterface
        $wifi = @($physical | Where-Object InterfaceType -eq 71)
        if ($wifi.Count) {
            $wired = $false
            foreach ($adapter in ($physical | Where-Object {
                $_.InterfaceType -eq 6 -and $_.MediaConnectState -eq 1
            })) {
                $addresses = Get-NetIPAddress -InterfaceIndex $adapter.InterfaceIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                    Where-Object AddressState -eq 'Preferred'
                if ($addresses | Where-Object { $_.IPAddress -notlike '169.254.*' }) {
                    $wired = $true
                    break
                }
            }
            $changed = if ($wired) {
                $wifi | Where-Object InterfaceAdminStatus -eq 1
            } else {
                $wifi | Where-Object InterfaceAdminStatus -eq 2
            }
            if ($changed) {
                $indices = @($changed | ForEach-Object InterfaceIndex)
                $targets = Get-NetAdapter -IncludeHidden | Where-Object { $_.ifIndex -in $indices }
                if ($targets -and $wired) {
                    $targets | Disable-NetAdapter -Confirm:$false
                } elseif ($targets) {
                    $targets | Enable-NetAdapter -Confirm:$false
                }
            }
        }
    } catch {
        Write-Warning $_.Exception.Message
    }
    Start-Sleep -Seconds 3
}
"""
    encoded = b64encode(monitor.encode("utf-16le")).decode("ascii")
    register = rf"""
$ErrorActionPreference = 'Stop'
$name = 'SoundLive-NDI Network'
$static = Join-Path $env:ProgramFiles 'soundlive-ndi\Network\configuration.json'
if (Test-Path -LiteralPath $static) {{ throw 'La bascule IPv4 fixe est deja configuree.' }}
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($existing -and $existing.State -eq 'Running') {{ Stop-ScheduledTask -TaskName $name }}
$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Argument '-NoProfile -NonInteractive -EncodedCommand {encoded}'
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName $name
"""
    command = b64encode(register.encode("utf-16le")).decode("ascii")
    # Read the registration script from standard input to stay below command-line limits.
    runner = "& ([ScriptBlock]::Create([Text.Encoding]::Unicode.GetString([Convert]::FromBase64String([Console]::In.ReadToEnd()))))"
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", runner],
                   input=command, text=True, check=True, timeout=30)
    print("Bascule Ethernet/Wi-Fi active.")


def locate_dll(name, runtime=None):
    locations = [ROOT, *(Path(item) for item in os.get_exec_path() if item)]
    if runtime:
        folder = os.environ.get("NDI_RUNTIME_DIR_V6")
        if folder:
            locations.append(Path(folder))
        locations.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NDI/NDI 6 Runtime")
    return next((path / name for path in locations if (path / name).is_file()), None)


def migrate_reader_data(current, legacy):
    pairing = "config/association.json"
    if (current / pairing).is_file() or not (legacy / pairing).is_file():
        return
    if current.exists():
        raise OSError(f"Le dossier {current} existe sans association. Sauvegarde-le et déplace-le avant de reprendre l’ancien lecteur.")
    with TemporaryDirectory(dir=current.parent, prefix="soundlive-ndi-migration-") as folder:
        staging = Path(folder) / "soundlive-ndi"
        staging.mkdir()
        for name in ("client", "config", "state"):
            if (legacy / name).is_dir():
                shutil.copytree(legacy / name, staging / name)
        staging.rename(current)


def remote_access_command():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise OSError("Ouvre PowerShell en administrateur sous le compte du lecteur.")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    return [str(powershell), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File"]


def install_regie_access(server, fingerprint, app):
    command = remote_access_command()
    with TemporaryDirectory() as folder:
        keys = Path(folder)
        fetch_keys(server, fingerprint, keys)
        subprocess.run([*command, str(ROOT / "installer-ssh-windows.ps1"), "-CleSsh", str(keys / SSH_PUBLIC)],
                       check=True)
        ssh_keygen("-lf", keys / PUBLIC_KEY)
        shutil.copy2(keys / PUBLIC_KEY, app / PUBLIC_KEY)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("serveur", nargs="?")
    cli.add_argument("code", nargs="?")
    cli.add_argument("--mettre-a-jour", action="store_true")
    cli.add_argument("--installer-seul", action="store_true")
    cli.add_argument("--reseau-auto", action="store_true")
    cli.add_argument("--empreinte-regie")
    cli.add_argument("--acces-deja-prepare", action="store_true", help=argparse.SUPPRESS)
    cli.add_argument("--lecteur", type=Path, default=ROOT / "lecteur-ndi-windows.exe")
    args = cli.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        cli.error("L’installateur nécessite Windows x64.")
    if args.reseau_auto:
        if args.serveur or args.code or args.mettre_a_jour or args.empreinte_regie:
            cli.error("Utilise --reseau-auto seul.")
        install_network_priority()
        return
    if args.installer_seul and (args.serveur or args.code or args.mettre_a_jour or args.empreinte_regie):
        cli.error("Utilise --installer-seul sans paramètres d’association.")
    if not args.installer_seul and not args.mettre_a_jour and not (args.serveur and args.code):
        cli.error("Indique l’adresse et le code affichés par le tableau.")
    if args.mettre_a_jour and args.code:
        cli.error("Une mise à jour conserve l’association.")
    if args.empreinte_regie and not args.serveur:
        cli.error("Indique l’adresse HTTP de la régie pour récupérer ses clés.")
    try:
        import tkinter
    except ImportError as error:
        cli.error(f"Installe Python pour Windows avec Tcl/Tk. {error}")
    from ecran import WINDOWS_CURRENT_DATA, WINDOWS_LEGACY_DATA

    migrate_reader_data(WINDOWS_CURRENT_DATA, WINDOWS_LEGACY_DATA)
    pairing = WINDOWS_CURRENT_DATA / "config/association.json"
    app = WINDOWS_CURRENT_DATA / "client"
    if args.mettre_a_jour and not pairing.is_file():
        cli.error("Aucune association. Utilise la commande donnée par la régie.")
    if not args.empreinte_regie and (ROOT / PUBLIC_KEY).is_file() and not ssh_keygen_path():
        cli.error("Active le client OpenSSH de Windows avant de configurer les mises à jour signées.")
    if not args.installer_seul and not args.mettre_a_jour and pairing.is_file():
        cli.error("Ce lecteur est déjà associé. Utilise --mettre-a-jour.")
    installed = app / "lecteur-ndi-windows.exe"
    player = args.lecteur if args.lecteur.is_file() else installed
    if not player.is_file():
        cli.error("Lecteur Windows manquant. Compile-le avec compiler-lecteur-windows.ps1 ou récupère le binaire de la release.")
    app.mkdir(parents=True, exist_ok=True)
    dlls = (("Processing.NDI.Lib.x64.dll", True), ("SDL2.dll", False))
    for name, ndi in dlls:
        if (app / name).is_file():
            continue
        dll = locate_dll(name, ndi)
        if dll is None:
            cli.error(f"{name} introuvable. Installe le runtime NDI 6 ou place SDL2.dll x64 à côté du kit.")
        shutil.copy2(dll, app / name)
    if player != installed:
        shutil.copy2(player, installed)
    subprocess.run([str(installed), "--sources"], check=True, timeout=15)
    remote_deploy = os.environ.get("SOUNDLIVE_DEPLOY_REMOTE") == "1"
    if not remote_deploy and not args.acces_deja_prepare and (not args.mettre_a_jour or args.empreinte_regie):
        command = remote_access_command()
        subprocess.run([*command, str(ROOT / "installer-bureau-distant-windows.ps1"), "-ActiverRdp"], check=True)
    elif remote_deploy:
        print("Déploiement SSH : configuration interactive VNC/RDP à finaliser localement si nécessaire.")
    if args.empreinte_regie:
        install_regie_access(args.serveur, args.empreinte_regie, app)
    command = ["verifier"] if args.mettre_a_jour else ["associer"]
    command.extend(value for value in (args.serveur, args.code) if value)
    if not args.installer_seul:
        subprocess.run([sys.executable, "-B", str(ROOT / "ecran.py"), *command], check=True)
    for filename in (*FILES, "lancer-client.py"):
        shutil.copy2(ROOT / filename, app / filename)
    if not args.empreinte_regie and (ROOT / PUBLIC_KEY).is_file():
        shutil.copy2(ROOT / PUBLIC_KEY, app / PUBLIC_KEY)
    versions = app / ".versions"
    if versions.is_dir():
        (versions / "revenir-base").touch()
    startup = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    startup.mkdir(parents=True, exist_ok=True)
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.is_file() else Path(sys.executable)
    # The shortcut avoids cmd.exe parsing profile paths or displaying a console.
    script = ("$w=New-Object -ComObject WScript.Shell; "
              "$s=$w.CreateShortcut($env:SOUNDLIVE_SHORTCUT); "
              "$s.TargetPath=$env:SOUNDLIVE_PYTHON; $s.Arguments=$env:SOUNDLIVE_ARGS; "
              "$s.WorkingDirectory=$env:SOUNDLIVE_WORK; $s.Save()")
    subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                   env={**os.environ, "SOUNDLIVE_SHORTCUT":str(startup / "SoundLive-NDI.lnk"),
                        "SOUNDLIVE_PYTHON":str(interpreter),
                        "SOUNDLIVE_ARGS":f'-B "{app / "lancer-client.py"}"', "SOUNDLIVE_WORK":str(app)},
                   check=True, timeout=10)
    (startup / "SoundLive-Ecran.lnk").unlink(missing_ok=True)
    if args.installer_seul:
        for filename in ("raccorder-lecteur.py", "installation_lecteur.py", "installer-ssh-windows.ps1"):
            shutil.copy2(ROOT / filename, app / filename)
        programs = startup.parent
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                       env={**os.environ, "SOUNDLIVE_SHORTCUT":str(programs / "SoundLive-NDI - Raccorder le lecteur.lnk"),
                            "SOUNDLIVE_PYTHON":sys.executable,
                            "SOUNDLIVE_ARGS":f'-B "{app / "raccorder-lecteur.py"}"', "SOUNDLIVE_WORK":str(app)},
                       check=True, timeout=10)
    # Désactiver la veille et le verrouillage de la session lecteur.
    # La configuration de l'autologin utilise l'outil Microsoft officiel :
    # aucun mot de passe n'est lu ni conservé par SoundLive-NDI.
    session = ROOT / "configurer-session-lecteur-windows.ps1"
    if not session.is_file():
        raise ValueError("Script de session manquant : configurer-session-lecteur-windows.ps1")
    command = remote_access_command()
    options = ["-SansAssistantAutologon"] if args.mettre_a_jour or remote_deploy else []
    subprocess.run([*command, str(session), *options], check=True)
    print("Lecteur installé. Redémarre Windows après configuration de l'autologin.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
