# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Construit le ZIP d’une release et le signe avec la clé de l’éditeur."""
import argparse
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
from zipfile import ZipFile, ZIP_DEFLATED

from tableau import SOFTWARE_VERSION
from versions_regie import (ARCHIVE, FILES, LEGACY_ARCHIVE, LEGACY_LAUNCHER_NAMES, NAMESPACE,
                            archive_files, release_manifest)

ROOT = Path(__file__).resolve().parent


def publish(output, key, archive_name):
    path = output / archive_name
    folder = path.stem
    names = LEGACY_LAUNCHER_NAMES if archive_name == LEGACY_ARCHIVE else {}
    with ZipFile(path, "w", compression=ZIP_DEFLATED) as archive:
        for name in sorted(FILES):
            file = ROOT / name
            if file.is_file() and not file.is_symlink():
                archive.write(file, f"{folder}/{names.get(name, name)}")
    payload = path.read_bytes()
    archive_files(payload, SOFTWARE_VERSION, archive_name)
    signature = path.with_name(path.name + ".sig")
    with TemporaryDirectory() as temporary:
        message = Path(temporary) / "manifest"
        message.write_bytes(release_manifest(payload, archive_name))
        subprocess.run(["ssh-keygen", "-Y", "sign", "-f", str(key.resolve()),
                        "-n", NAMESPACE, str(message)], check=True)
        shutil.copyfile(message.with_suffix(".sig"), signature)
    print(f"{path}\n{signature}")


def main():
    cli = argparse.ArgumentParser()
    cli.add_argument("--cle", type=Path, required=True)
    cli.add_argument("--sortie", type=Path, default=ROOT / "dist")
    cli.add_argument("--compatibilite", action="store_true",
                     help="générer aussi les fichiers de mise à jour pour les régies 1.4.7 et antérieures")
    args = cli.parse_args()
    args.sortie.mkdir(parents=True, exist_ok=True)
    print(f"v{SOFTWARE_VERSION}")
    publish(args.sortie, args.cle, ARCHIVE)
    if args.compatibilite:
        publish(args.sortie, args.cle, LEGACY_ARCHIVE)


if __name__ == "__main__":
    main()
