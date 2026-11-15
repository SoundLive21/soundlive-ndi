#!/usr/bin/python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Lance le client dans la session graphique et supervise un changement de version."""
import os
from pathlib import Path
import signal
import subprocess
import sys

from mise_a_jour import ClientUpdate, RESTART
from commun import client_lock

APP = Path(__file__).resolve().parent


def main():
    if sys.platform != "win32" and os.geteuid() == 0:
        sys.exit("Lance SoundLive-NDI avec le compte de la session graphique.")
    updater = ClientUpdate(APP, APP)
    updater.versions.mkdir(mode=0o700, exist_ok=True)
    with client_lock(updater.versions / "launcher.lock") as acquired:
        if not acquired:
            return
        stopping = False
        child = None

        def stop(*_):
            nonlocal stopping
            stopping = True
            if child is not None:
                child.terminate()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        env = {**os.environ, "SOUNDLIVE_APP": str(APP)}
        while not stopping:
            reset = updater.versions / "revenir-base"
            if reset.exists():
                updater.file.unlink(missing_ok=True)
                reset.unlink(missing_ok=True)
            entry = updater.select()
            child = subprocess.Popen([sys.executable, "-B", str(entry)], env=env)
            try:
                code = child.wait(timeout=30) if updater.read().get("trial") == "starting" else child.wait()
            except subprocess.TimeoutExpired:
                if updater.read().get("trial") != "starting":
                    code = child.wait()
                else:
                    child.terminate()
                    try:
                        code = child.wait(timeout=6)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        code = child.wait()
            child = None
            if updater.rollback():
                continue
            if code != RESTART:
                return


if __name__ == "__main__":
    main()
