# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Installation du récepteur Windows dans le profil de la session d’affichage."""
import argparse
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

from mise_a_jour import FILES, PUBLIC_KEY

ROOT = Path(__file__).resolve().parent


def locate_dll(name, runtime=None):
    locations = [ROOT, *(Path(item) for item in os.get_exec_path() if item)]
    if runtime:
        folder = os.environ.get("NDI_RUNTIME_DIR_V6")
        if folder:
            locations.append(Path(folder))
        locations.append(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "NDI/NDI 6 Runtime")
    return next((path / name for path in locations if (path / name).is_file()), None)


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("serveur", nargs="?")
    cli.add_argument("code", nargs="?")
    cli.add_argument("--mettre-a-jour", action="store_true")
    cli.add_argument("--lecteur", type=Path, default=ROOT / "lecteur-ndi-windows.exe")
    args = cli.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        cli.error("L’installateur nécessite Windows x64.")
    if not args.mettre_a_jour and not (args.serveur and args.code):
        cli.error("Indique l’adresse et le code affichés par le tableau.")
    if args.mettre_a_jour and args.code:
        cli.error("Une mise à jour conserve l’association.")
    try:
        import tkinter
    except ImportError as error:
        cli.error(f"Installe Python pour Windows avec Tcl/Tk. {error}")
    from ecran import PAIRING, WINDOWS_DATA

    app = WINDOWS_DATA / "client"
    if args.mettre_a_jour and not PAIRING.is_file():
        cli.error("Aucune association. Utilise la commande donnée par la régie.")
    if (ROOT / PUBLIC_KEY).is_file() and not shutil.which("ssh-keygen"):
        cli.error("Active le client OpenSSH de Windows avant de configurer les mises à jour signées.")
    if not args.mettre_a_jour and PAIRING.is_file():
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
    command = ["verifier"] if args.mettre_a_jour else ["associer"]
    command.extend(value for value in (args.serveur, args.code) if value)
    subprocess.run([sys.executable, "-B", str(ROOT / "ecran.py"), *command], check=True)
    for filename in (*FILES, "lancer-client.py"):
        shutil.copy2(ROOT / filename, app / filename)
    if (ROOT / PUBLIC_KEY).is_file():
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
    print("Lecteur installé. Rouvre la session Windows pour le démarrer.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.SubprocessError) as error:
        sys.exit(str(error))
