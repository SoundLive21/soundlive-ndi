#!/usr/bin/env python3
"""Désinstallation SoundLive NDI sous le compte de l'installation.

Exécuter depuis le kit décompressé, après arrêt du logiciel.
Les dépendances et réglages système partagés sont conservés.
"""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

WINDOWS = sys.platform == "win32"
HOME = Path.home()


def locations(role):
    if WINDOWS:
        local = Path(os.environ["LOCALAPPDATA"])
        roaming = Path(os.environ["APPDATA"])
        programs = roaming / "Microsoft/Windows/Start Menu/Programs"
        if role == "regie":
            root = local / "Programs/soundlive-ndi"
            links = [programs / "SoundLive-NDI Régie.lnk",
                     HOME / "Desktop/SoundLive-NDI Régie.lnk"]
            data = [root / name for name in ("ecrans.json", "disposition-ecrans.json",
                    "regie-update.pub", "source-publications.json", "publication.pub",
                    ".soundlive-acces", ".soundlive-update", ".soundlive-medias",
                    ".soundlive-dhcp", ".soundlive-backups")]
            return root, links, data
        root = local / "soundlive-ndi/client"
        links = [programs / "Startup" / name for name in
                 ("SoundLive-NDI.lnk", "SoundLive-Ecran.lnk")]
        links.append(programs / "SoundLive-NDI Lecteur.lnk")
        return root, links, [root.parent / "config", root.parent / "state"]
    config = Path(os.environ.get("XDG_CONFIG_HOME", HOME / ".config"))
    data_home = Path(os.environ.get("XDG_DATA_HOME", HOME / ".local/share"))
    state = Path(os.environ.get("XDG_STATE_HOME", HOME / ".local/state"))
    if role == "regie":
        root = HOME / ".local/lib/soundlive-ndi"
        links = [data_home / "applications/soundlive-ndi-regie.desktop"]
        data = [root / name for name in ("ecrans.json", "disposition-ecrans.json",
                "regie-update.pub", "source-publications.json", "publication.pub",
                ".soundlive-acces", ".soundlive-update", ".soundlive-medias",
                ".soundlive-dhcp", ".soundlive-backups")]
        return root, links, data
    root = HOME / ".local/share/soundlive-ndi"
    links = [config / "autostart/ndi-ecran.desktop",
             config / "autostart/soundlive-ecran-actif.desktop",
             HOME / ".local/bin/soundlive-ndi-ecran-actif"]
    return root, links, [config / "soundlive-ndi", state / "soundlive-ndi"]


def assert_safe(path):
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ValueError("Lien ou jonction non autorisé : " + str(part))


def remove(path):
    assert_safe(path)
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()
    else:
        return
    print("Supprimé :", path, flush=True)


def active_processes(root):
    """Ne jamais tuer un processus générique Python, ffplay ou PowerShell."""
    if WINDOWS:
        script = (
            "$ErrorActionPreference='Stop'; "
            "$root=$env:SOUNDLIVE_UNINSTALL_ROOT; "
            "Get-CimInstance Win32_Process | Where-Object { "
            "$_.ProcessId -ne $PID -and $_.ProcessId -ne "
            "$env:SOUNDLIVE_UNINSTALL_PID -and "
            "($_.ExecutablePath -and $_.ExecutablePath.StartsWith($root+'\\', "
            "[StringComparison]::OrdinalIgnoreCase) -or "
            "$_.CommandLine -and $_.CommandLine.IndexOf($root, "
            "[StringComparison]::OrdinalIgnoreCase) -ge 0 -and "
            "$_.CommandLine -match '(regie-windows|lancer-client|ecran|tableau|lancer-regie)\\.py[w]?') "
            "} | ForEach-Object { $_.ProcessId }")
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            env={**os.environ, "SOUNDLIVE_UNINSTALL_ROOT": str(root),
                 "SOUNDLIVE_UNINSTALL_PID": str(os.getpid())},
            capture_output=True, text=True, check=True, timeout=20)
        return result.stdout.split()
    found = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit() or int(proc.name) == os.getpid():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            args = (proc / "cmdline").read_bytes().split(b"\0")
            cwd = (proc / "cwd").resolve(strict=True)
            exe = (proc / "exe").resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        names = {"regie-windows.py", "tableau.py", "lancer-client.py",
                 "ecran.py", "demarrer-ubuntu", "lecteur-ndi-ubuntu"}
        if root in exe.parents or any(
                Path(os.fsdecode(arg)).name in names and
                (root in (cwd / os.fsdecode(arg)).resolve().parents)
                for arg in args if arg):
            found.append(proc.name)
    return found


def uninstall(role, purge=False, dry_run=False):
    root, links, data = locations(role)
    for path in [root, *links, *data]:
        assert_safe(path)
    if role == "regie":
        # Le manifeste de publication évite d'effacer des fichiers personnels.
        from versions_regie import FILES
        program = [root / name for name in sorted(FILES)]
        program += [root / name for name in ("soundlive.ico", "soundlive.png",
                    "__pycache__", ".installation")]
    else:
        # Le répertoire client ne contient que le programme installé.
        program = [root]
    if not root.is_dir():
        raise ValueError("Installation introuvable : " + str(root))
    if role == "regie" and not (root / "tableau.py").is_file():
        raise ValueError("Ce dossier ne contient pas une régie SoundLive.")
    if role == "lecteur" and not any((root / name).is_file() for name in
                                    ("ecran.py", "lancer-client.py", "demarrer-ubuntu")):
        raise ValueError("Ce dossier ne contient pas un lecteur SoundLive.")
    paths = links + program + (data if purge else [])
    for path in paths:
        assert_safe(path)
    if dry_run:
        for path in paths:
            if path.exists():
                print("À supprimer :", path)
        return
    processes = active_processes(root)
    if processes:
        raise ValueError("Arrête SoundLive NDI avant de désinstaller (PID : "
                         + ", ".join(processes) + ").")
    if root in Path(__file__).resolve().parents:
        raise ValueError("Exécute le désinstallateur depuis un kit séparé.")
    for path in paths:
        remove(path)
    if WINDOWS and role == "regie":
        import winreg
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\SoundLive-NDI-Regie")
        except FileNotFoundError:
            pass
    print("Désinstallation locale terminée.", flush=True)
    print("Les dépendances, réglages système et autorisations SSH sont conservés.")
    if not purge:
        print("Les données d'association et de configuration sont conservées.")
    else:
        print("Les données locales sélectionnées ont été supprimées.")
    print("Retire l'association du lecteur dans la régie si nécessaire.")
    print("Les éventuels services réseau système doivent être vérifiés séparément.")


def main():
    if sys.platform not in ("win32", "linux"):
        raise ValueError("Système non pris en charge.")
    if not WINDOWS and os.geteuid() == 0:
        raise ValueError("Utilise le compte de l'installation, sans sudo.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("regie", "lecteur"))
    parser.add_argument("--purger", action="store_true", help="Supprimer également les données locales")
    parser.add_argument("--simulation", action="store_true", help="Afficher les fichiers sans les supprimer")
    args = parser.parse_args()
    if not args.simulation:
        expected = "SUPPRIMER" if args.purger else "DESINSTALLER"
        if input("Confirme en saisissant " + expected + " : ") != expected:
            print("Annulé.")
            return
    uninstall(args.role, args.purger, args.simulation)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
