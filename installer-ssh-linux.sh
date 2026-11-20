#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)

install_ssh() {
    if ! command -v ssh-keygen >/dev/null ||
        ! dpkg-query -W -f='${Status}' openssh-server 2>/dev/null | grep -Fxq 'install ok installed'; then
        sudo apt-get update
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y openssh-server openssh-client
    fi
}

authorize_key() {
    /usr/bin/python3 - "$base" "$1" <<'PYTHON'
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from mise_a_jour import authorize_ssh_key

try:
    authorize_ssh_key(Path(sys.argv[2]).read_text(encoding="utf-8").strip())
except (OSError, RuntimeError, ValueError) as error:
    sys.exit(str(error))
PYTHON
    sudo systemctl enable --now ssh.service
}

case "$1" in
    installer) install_ssh ;;
    configurer)
        install_ssh
        sudo systemctl enable --now ssh.service
        ;;
    autoriser) authorize_key "$2" ;;
    recuperer)
        directory=$(mktemp -d)
        trap 'rm -rf -- "$directory"' EXIT
        /usr/bin/python3 "$base/installation_lecteur.py" "$2" "$3" "$directory"
        ssh-keygen -lf "$directory/regie-update.pub" >/dev/null
        authorize_key "$directory/id_soundlive.pub"
        install -m 0644 "$directory/regie-update.pub" "$4/"
        printf 'Empreinte de ce lecteur SSH :\n'
        for key in /etc/ssh/ssh_host_*.pub; do
            if [[ -r $key ]]; then ssh-keygen -lf "$key"; fi
        done
        ;;
    *) exit 2 ;;
esac
