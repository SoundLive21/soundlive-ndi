"""Raccordement interactif d'un lecteur déjà installé, sans réinstaller le moteur."""
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from installation_lecteur import fetch_keys, SSH_PUBLIC
from mise_a_jour import PUBLIC_KEY, ssh_keygen

ROOT = Path(__file__).resolve().parent


def main():
    windows = sys.platform == "win32"
    if windows:
        import ctypes
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise ValueError("Relance Raccorder le lecteur en tant qu’administrateur, sous le même compte.")
    else:
        if os.geteuid() == 0:
            raise ValueError("Lance le raccordement sous le compte du bureau, sans sudo.")
        kind_file = ROOT / "type-recepteur"
        kind = kind_file.read_text().strip() if kind_file.is_file() else "ubuntu"
        os.environ["SOUNDLIVE_DEVICE_KIND"] = kind
        if (ROOT / "lecteur-ndi-ubuntu").is_file():
            os.environ["SOUNDLIVE_PLAYER"] = "ubuntu"
    from ecran import PAIRING, pair, server_address
    if PAIRING.exists():
        raise ValueError("Ce lecteur possède déjà une association. Elle est conservée.")
    print("SoundLive NDI — Raccorder le lecteur")
    print("Dans la régie, ajoute un écran du type correspondant.")
    server = server_address(input("Adresse HTTP de la régie (avec le port) : ").strip())
    code = input("Code d’association : ").strip()
    fingerprint = input("Empreinte des clés (64 caractères, affichée par la régie) : ").strip()
    if not code or not re.fullmatch(r"[a-f0-9]{64}", fingerprint):
        raise ValueError("Code ou empreinte manquants.")
    with TemporaryDirectory() as folder:
        keys = Path(folder)
        fetch_keys(server, fingerprint, keys)
        ssh_keygen("-lf", keys / PUBLIC_KEY)
        if input("Autoriser cette régie à administrer le lecteur et signer ses mises à jour ? [o/N] ").strip().lower() != "o":
            return
        if windows:
            subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                            str(ROOT / "installer-ssh-windows.ps1"), "-CleSsh", str(keys / SSH_PUBLIC)], check=True)
        else:
            subprocess.run(["bash", str(ROOT / "installer-ssh-linux.sh"), "autoriser", str(keys / SSH_PUBLIC)], check=True)
        shutil.copy2(keys / PUBLIC_KEY, ROOT / PUBLIC_KEY)
        os.environ["SOUNDLIVE_INSTALL_FINGERPRINT"] = fingerprint
        pair(server, code)
    print("Raccordement réussi. Redémarre le lecteur pour démarrer la connexion.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        print("Raccordement interrompu :", error)
    finally:
        try:
            input("Entrée pour fermer…")
        except EOFError:
            pass
