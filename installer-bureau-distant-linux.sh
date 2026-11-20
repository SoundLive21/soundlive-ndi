#!/usr/bin/env bash
# Configure sharing of the active desktop used by SoundLive-NDI.
set -euo pipefail

if (( EUID == 0 )); then
    echo 'Lance ce script sous le compte de la session graphique, sans sudo.' >&2
    exit 1
fi
mode=${1:-auto}
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

install_package tigervnc-viewer
session=${XDG_SESSION_TYPE:-}
if [[ $session != x11 && $session != wayland ]]; then
    while read -r id _; do
        [[ $(loginctl show-session "$id" -p Name --value 2>/dev/null) == "$(id -un)" ]] || continue
        type=$(loginctl show-session "$id" -p Type --value 2>/dev/null || true)
        if [[ $type == x11 || $type == wayland ]]; then session=$type; break; fi
    done < <(loginctl list-sessions --no-legend 2>/dev/null)
fi
if [[ $mode == pi && $session != x11 ]] && command -v raspi-config >/dev/null; then
    install_package wayvnc
    sudo raspi-config nonint do_vnc 0
    echo 'VNC activé sur Raspberry Pi OS. Utilise les identifiants du Pi avec un client compatible WayVNC.'
elif [[ $session == x11 ]]; then
    install_package tigervnc-scraping-server
    install_package tigervnc-tools
    passfile="$HOME/.vnc/passwd"
    install -d -m 700 "$HOME/.vnc"
    if [[ ! -s $passfile ]]; then
        echo 'Choisis le mot de passe VNC pour cette session.'
        tigervncpasswd "$passfile"
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
Exec=/usr/bin/x0tigervncserver -fg -localhost no -PasswordFile "$passfile" -rfbport 5900
Terminal=false
DESKTOP
    echo 'Le serveur TigerVNC démarrera à la prochaine ouverture de la session X11.'
elif [[ $session == wayland ]] && viewer=$(command -v w0vncserver || command -v w0tigervncserver); then
    install_package tigervnc-tools
    passfile="$HOME/.config/tigervnc/passwd"
    install -d -m 700 "$(dirname -- "$passfile")"
    if [[ ! -s $passfile ]]; then
        tigervncpasswd "$passfile"
    fi
    chmod 600 "$passfile"
    autostart="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
    install -d "$autostart"
    cat > "$autostart/soundlive-vnc.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive VNC
Exec="$viewer" -PasswordFile="$passfile" -rfbport=5900
Terminal=false
DESKTOP
    echo 'VNC démarrera à la prochaine ouverture de la session Wayland. Autorise le partage si le bureau le demande.'
else
    echo 'TigerVNC Viewer est installé. Pour partager ce bureau, relance ce script depuis une session X11 ou un environnement avec w0vncserver compatible.' >&2
fi
