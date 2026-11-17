#!/usr/bin/python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Lance le client dans la session graphique et supervise un changement de version."""
from contextlib import contextmanager
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

APP = Path(__file__).resolve().parent


@contextmanager
def launcher_lock(path):
    # Le lanceur est mis à jour indépendamment du commun.py de l’installation initiale.
    with path.open("a+b") as handle:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True


def main():
    if sys.platform != "win32" and os.geteuid() == 0:
        sys.exit("Lance SoundLive-NDI avec le compte de la session graphique.")
    from mise_a_jour import ClientUpdate, RESTART

    updater = ClientUpdate(APP, APP)
    updater.versions.mkdir(mode=0o700, exist_ok=True)
    with launcher_lock(updater.versions / "launcher.lock") as acquired:
        if not acquired:
            return
        stopping = False
        child = None
        failures = 0

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
            print(f"Ouverture du client {entry}", flush=True)
            started = time.monotonic()
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
            if stopping:
                return
            if updater.rollback():
                failures = 0
                continue
            if code == RESTART:
                failures = 0
                continue
            if code == 0:
                return
            if time.monotonic() - started >= 30:
                failures = 0
            failures += 1
            print(f"Le client a quitté avec le code {code}. Échec {failures}/3.", flush=True)
            if failures >= 3:
                return code
            time.sleep(3)


if __name__ == "__main__":
    if sys.platform == "win32" or os.geteuid() == 0:
        sys.exit(main())
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "soundlive-ndi"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "lancement.log").open("a", encoding="utf-8", buffering=1) as log:
        # Le descripteur est aussi hérité par le processus graphique.
        os.dup2(log.fileno(), 1)
        os.dup2(log.fileno(), 2)
        print(time.strftime("%Y-%m-%d %H:%M:%S · Démarrage du lanceur"), flush=True)
        print(f"Session graphique : {os.environ.get('XDG_SESSION_TYPE') or 'indéterminée'}"
              f" · DISPLAY={os.environ.get('DISPLAY', '')}"
              f" · WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY', '')}", flush=True)
        sys.exit(main())
