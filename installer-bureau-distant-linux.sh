#!/usr/bin/env bash
# Configure sharing of the active desktop used by SoundLive-NDI.
set -euo pipefail

if (( EUID == 0 )); then
    echo 'Lance ce script sous le compte de la session graphique, sans sudo.' >&2
    exit 1
fi
mode=${1:-auto}
desktop=${XDG_CURRENT_DESKTOP:-}
if [[ $mode == auto ]]; then
    if [[ -r /proc/device-tree/model ]] && [[ $(tr -d '\0' < /proc/device-tree/model) == *"Raspberry Pi"* ]]; then
        mode=pi
    else
        mode=ubuntu
    fi
fi
if [[ $mode != pi && $mode != ubuntu ]]; then
    echo "Usage : bash installer-bureau-distant-linux.sh [pi|ubuntu]" >&2
    exit 2
fi
sudo -v
install_package() {
    if ! dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -Fxq 'install ok installed'; then
        sudo apt-get update
        sudo apt-get install -y "$1"
    fi
}

if [[ $mode == pi && ${XDG_SESSION_TYPE:-} != x11 ]] && command -v raspi-config >/dev/null; then
    install_package wayvnc
    sudo raspi-config nonint do_vnc 0
    echo 'VNC activé sur Raspberry Pi OS. Utilise les identifiants du Pi avec un client compatible WayVNC.'
elif [[ ${XDG_SESSION_TYPE:-} == x11 ]]; then
    install_package x11vnc
    passfile="$HOME/.vnc/passwd"
    install -d -m 700 "$HOME/.vnc"
    if [[ ! -s $passfile ]]; then
        echo 'Choisis le mot de passe VNC pour cette session.'
        x11vnc -storepasswd
    fi
    if [[ ! -s $passfile ]]; then
        echo 'Le mot de passe VNC est nécessaire.' >&2
        exit 1
    fi
    chmod 600 "$passfile"
    autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
    install -d "$autostart"
    cat > "$autostart/soundlive-vnc.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive VNC
Exec=/usr/bin/x11vnc -rfbauth "$passfile" -rfbport 5900 -forever -shared
Terminal=false
DESKTOP
    echo 'VNC démarrera à la prochaine ouverture de la session X11.'
elif [[ ${XDG_SESSION_TYPE:-} == wayland && ${desktop,,} =~ gnome|ubuntu ]]; then
    install_package gnome-remote-desktop
    echo 'Sur le bureau Ubuntu, ouvre Paramètres → Système → Bureau distant → Partage du bureau.'
    echo 'Active Partage du bureau et Contrôle à distance, puis configure les identifiants RDP.'
else
    echo 'Ouvre une session graphique locale compatible pour régler le bureau distant.' >&2
    exit 1
fi
