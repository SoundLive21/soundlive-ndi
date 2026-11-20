#!/usr/bin/env bash
# Install the régie in the current desktop user's profile.
set -euo pipefail

if (( EUID == 0 )); then
    echo 'Lance cet installateur depuis le compte de la régie, sans sudo.' >&2
    exit 1
fi

base=$(cd -- "$(dirname -- "$0")" && pwd)
target="$HOME/.local/lib/soundlive-ndi"
previous=$base
key=
while (( $# )); do
    case $1 in
        --depuis|--cle-ssh)
            if [[ -z ${2:-} || $2 == --* ]]; then
                printf 'Indique un chemin après %s.\n' "$1" >&2
                exit 2
            fi
            if [[ $1 == --depuis ]]; then previous=$2; else key=$2; fi
            shift 2 ;;
        *) echo 'Usage : bash installer-regie-linux.sh [--depuis DOSSIER] [--cle-ssh CLE.pub]' >&2; exit 2 ;;
    esac
done

if [[ ! -d $previous || ( -n $key && ! -r $key ) ]]; then
    echo 'Le dossier de reprise ou la clé publique SSH est introuvable.' >&2
    exit 1
fi

sudo -v
sudo apt-get update
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y python3-tk python3-cryptography \
    python3-paramiko python3-pyte openssh-client openssh-server pkexec nftables

bash "$base/installer-ssh-linux.sh" configurer
if [[ -n $key ]]; then bash "$base/installer-ssh-linux.sh" autoriser "$key"; fi
bash "$base/installer-bureau-distant-linux.sh"

manifest=$(/usr/bin/python3 -B - "$base" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root))
from versions_regie import FILES, required_files, software_version

version = software_version((root / "tableau.py").read_bytes(), "tableau.py")
missing = [name for name in required_files(version) if not (root / name).is_file()]
if missing:
    raise SystemExit("Kit incomplet : " + ", ".join(sorted(missing)))
print("\n".join(sorted(name for name in FILES if (root / name).is_file())))
PY
)

install -d -m 700 "$target"
if [[ ! $base -ef $target ]]; then
    while IFS= read -r file; do
        install -m 644 -- "$base/$file" "$target/$file"
    done <<< "$manifest"
fi
if [[ ! $previous -ef $target ]]; then
    for file in ecrans.json disposition-ecrans.json regie-update.pub source-publications.json publication.pub \
        .soundlive-update .soundlive-acces .soundlive-medias .soundlive-dhcp; do
        if [[ -e $previous/$file && ! -e $target/$file ]]; then
            cp -a -- "$previous/$file" "$target/"
        fi
    done
fi

/usr/bin/python3 -B "$target/tableau.py" --preparer-installations

applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
install -d "$applications"
desktop_path=${target//\\/\\\\}
desktop_path=${desktop_path//\"/\\\"}
desktop_path=${desktop_path//\$/\\\$}
desktop_path=${desktop_path//\`/\\\`}
desktop_path=${desktop_path//%/%%}
cat > "$applications/soundlive-ndi-regie.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive-NDI Régie
Exec=/bin/bash "$desktop_path/demarrer-regie-ubuntu.sh"
Terminal=false
Categories=AudioVideo;
DESKTOP
printf 'Régie installée dans %s. Ouvre « SoundLive-NDI Régie » dans le menu des applications.\n' "$target"
