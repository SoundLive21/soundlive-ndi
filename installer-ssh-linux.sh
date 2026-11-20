#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)

install_ssh() {
    if ! command -v ssh-keygen >/dev/null ||
        ! dpkg-query -W -f='${Status}' openssh-server 2>/dev/null | grep -Fxq 'install ok installed'; then
        sudo apt-get update
        sudo apt-get install -y openssh-server openssh-client
    fi
}

authorize_key() {
    /usr/bin/python3 - "$1" <<'PY'
import os
from pathlib import Path
import re
import subprocess
import sys

key = Path(sys.argv[1]).read_text(encoding="utf-8").strip()
if not re.fullmatch(r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)) [A-Za-z0-9+/=]+(?: [^\r\n]*)?", key):
    raise SystemExit("Fournis une seule clé publique OpenSSH (.pub).")
subprocess.run(["ssh-keygen", "-lf", "-"], input=key + "\n", text=True, check=True)
identity = key.split()[1]
os.umask(0o077)
directory = Path.home() / ".ssh"
directory.mkdir(exist_ok=True)
directory.chmod(0o700)
authorized = directory / "authorized_keys"
with authorized.open("a+", encoding="utf-8") as output:
    authorized.chmod(0o600)
    output.seek(0)
    contents = output.read()
    if not any(identity in line.split() for line in contents.splitlines() if not line.startswith("#")):
        output.write(("" if not contents or contents.endswith("\n") else "\n") + key + "\n")
PY
    sudo systemctl enable --now ssh.service
}

case "$1" in
    installer) install_ssh ;;
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
