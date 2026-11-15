#!/usr/bin/env bash
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
set -euo pipefail
if (( EUID == 0 )); then
    echo "Lance le script avec le compte du bureau (soundlive), sans sudo."
    exit 1
fi
update=false
if [[ ${1:-} == --mettre-a-jour && $# -le 2 ]]; then
    update=true
elif [[ $# -ne 2 || ${1:-} == --* ]]; then
    echo "Usage : bash installer-pi.sh http://IP_DU_PC:8765 CODE"
    echo "Pi déjà associé : bash installer-pi.sh --mettre-a-jour [http://IP_ETHERNET_DU_PC:8765]"
    exit 1
fi
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
if $update && [[ ! -r "$config_dir/soundlive-ndi/association.json" ]]; then
    echo "Ce compte n’a pas d’association enregistrée. Utilise un code d’association."
    exit 1
fi
if [[ ! -x /opt/ndi-pi/bin/ffplay || ! -r /opt/ndi-pi/lib/libndi.so.6 ]]; then
    echo "Le lecteur NDI doit être présent. Utilise une microSD clonée du Pi qui fonctionne."
    exit 1
fi
if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk'; then
    sudo apt update
    sudo apt install -y python3-gi gir1.2-gtk-3.0
fi
here=$(cd -- "$(dirname -- "$0")" && pwd)
for file in ecran.py commun.py diagnostic.py mise_a_jour.py lancer-client.py; do
    if [[ ! -r "$here/$file" ]]; then
        echo "Le fichier $file est absent. Décompresse le kit SoundLive-NDI complet."
        exit 1
    fi
done
app="$HOME/.local/share/soundlive-ecrans"
autostart="$config_dir/autostart"
mkdir -p "$app" "$autostart"
rule=$(mktemp)
trap 'rm -f -- "$rule"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\n' "$(id -un)" > "$rule"
sudo visudo -cf "$rule"
sudo -v
if ! $update; then
    /usr/bin/python3 "$here/ecran.py" associer "$1" "$2"
elif [[ $# -eq 2 ]]; then
    /usr/bin/python3 "$here/ecran.py" verifier "$2"
elif ! /usr/bin/python3 "$here/ecran.py" verifier; then
    echo "Mise à jour locale possible. Pour rétablir la connexion, vérifie le tableau et l’adresse du PC."
fi
if [[ -r "$here/regie-update.pub" ]]; then
    if ! command -v ssh-keygen >/dev/null; then
        sudo apt install -y openssh-client
    fi
    install -m 644 "$here/regie-update.pub" "$app/"
fi
install -m 644 "$here/commun.py" "$here/diagnostic.py" "$here/mise_a_jour.py" \
    "$here/ecran.py" "$here/lancer-client.py" "$app/"
if [[ -d "$app/.versions" ]]; then
    touch "$app/.versions/revenir-base"
fi
sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/soundlive-ecrans
desktop_file="$autostart/ndi-ecran.desktop"
if [[ -f "$desktop_file" && ! -f "$desktop_file.avant-tableau" ]]; then
    cp -p -- "$desktop_file" "$desktop_file.avant-tableau"
fi
cat > "$desktop_file" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive-NDI
Exec=/usr/bin/python3 "$app/lancer-client.py"
Terminal=false
DESKTOP
echo "Configuration terminée. Redémarre avec sudo reboot pour charger le client."
