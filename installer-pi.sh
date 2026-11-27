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
install_only=false
if [[ ${1:-} == --installer-seul && $# -eq 1 ]]; then
    install_only=true
elif [[ ${1:-} == --mettre-a-jour && $# -le 2 ]]; then
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
packages=()
command -v ffplay >/dev/null || packages+=(ffmpeg)
command -v xtigervncviewer >/dev/null || packages+=(tigervnc-viewer)
if apt-cache show gir1.2-gtklayershell-0.1 >/dev/null 2>&1 &&
    ! dpkg-query -W -f='${Status}' gir1.2-gtklayershell-0.1 2>/dev/null | grep -Fxq 'install ok installed'; then
    packages+=(gir1.2-gtklayershell-0.1)
fi
if (( ${#packages[@]} )); then
    sudo apt update
    sudo apt install -y "${packages[@]}"
fi
if [[ ${XDG_SESSION_TYPE:-} == wayland ]] && ! command -v wlr-randr >/dev/null; then
    sudo apt install -y wlr-randr
elif [[ ${XDG_SESSION_TYPE:-} == x11 ]] && ! command -v xrandr >/dev/null; then
    sudo apt install -y x11-xserver-utils
fi
here=$(cd -- "$(dirname -- "$0")" && pwd)
for file in ecran.py commun.py diagnostic.py mise_a_jour.py lancer-client.py; do
    if [[ ! -r "$here/$file" ]]; then
        echo "Le fichier $file est absent. Décompresse le kit SoundLive-NDI complet."
        exit 1
    fi
done
source "$here/installation-lecteur-linux.sh"
app=$(player_directory "$HOME/.local/share")
autostart="$config_dir/autostart"
mkdir -p "$app" "$autostart"
rule=$(mktemp)
trap 'rm -f -- "$rule"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\n' "$(id -un)" > "$rule"
sudo visudo -cf "$rule"
sudo -v
if [[ ${SOUNDLIVE_REMOTE_ACCESS_PREPARED:-} != 1 ]] && { ! $update || [[ -n ${SOUNDLIVE_INSTALL_FINGERPRINT:-} ]]; }; then
    prepare_remote_access "$here" pi
fi
if [[ -n ${SOUNDLIVE_NETWORK_PREPARATION:-} ]]; then
    bash "$here/preparer-lecteur-linux.sh" appliquer "$SOUNDLIVE_NETWORK_PREPARATION"
fi
if [[ -n ${SOUNDLIVE_INSTALL_FINGERPRINT:-} ]]; then
    bash "$here/installer-ssh-linux.sh" recuperer "$SOUNDLIVE_INSTALL_REGIE" "$SOUNDLIVE_INSTALL_FINGERPRINT" "$app"
fi
if $install_only; then
    echo "Installation sans association."
elif ! $update; then
    /usr/bin/python3 "$here/ecran.py" associer "$1" "$2"
elif [[ $# -eq 2 ]]; then
    /usr/bin/python3 "$here/ecran.py" verifier "$2"
elif ! /usr/bin/python3 "$here/ecran.py" verifier; then
    echo "Mise à jour locale possible. Pour rétablir la connexion, vérifie le tableau et l’adresse du PC."
fi
if [[ -z ${SOUNDLIVE_INSTALL_FINGERPRINT:-} && -r "$here/regie-update.pub" ]]; then
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
install_os_maintenance "$here"
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
bash "$here/configurer-session-lecteur-linux.sh" pi
if $install_only; then install_pairing_launcher "$here" "$app" pi; fi
echo "Configuration terminée. Redémarre avec sudo reboot pour charger le client."
