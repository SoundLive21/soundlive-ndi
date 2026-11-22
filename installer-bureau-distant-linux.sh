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
        sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$1"
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
elif [[ $mode == ubuntu && $session == wayland &&
        "${XDG_CURRENT_DESKTOP,,}" == *gnome* || $mode == ubuntu && $session == wayland &&
        "${XDG_CURRENT_DESKTOP,,}" == *ubuntu* ]]; then
    # GNOME/Wayland (Ubuntu récent) partage l'écran via GNOME Remote Desktop (RDP).
    # Aucun mot de passe RDP ne doit être inventé ou enregistré par l'installateur.
    install_package gnome-remote-desktop
    rm -f -- "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/soundlive-vnc.desktop"
    echo 'Ubuntu GNOME / Wayland détecté : TigerVNC Server X11 ne peut pas partager ce bureau.'
    echo 'Active Paramètres > Système > Bureau à distance > Partage du bureau (et Contrôle à distance).'
    echo 'Définis les identifiants RDP dans les paramètres Ubuntu, puis utilise le bouton MSRDP de la régie.'
    echo 'La session utilisateur doit être ouverte pour le partage du bureau.'
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
    bindir="$HOME/.local/bin"
    install -d -m 700 "$bindir"
    launcher="$bindir/soundlive-vnc-x11"
    cat > "$launcher" <<'LAUNCHER'
#!/usr/bin/env bash
set -euo pipefail
exec /usr/bin/x0tigervncserver -fg -localhost no -SecurityTypes TLSVnc \
    -PasswordFile "$HOME/.vnc/passwd" -rfbport 5900
LAUNCHER
    chmod 700 "$launcher"
    install -d "$autostart"
    cat > "$autostart/soundlive-vnc.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive VNC
Exec=$launcher
Terminal=false
DESKTOP
    if [[ -n ${DISPLAY:-} ]]; then
        if [[ -n $(ss -H -ltn '( sport = :5900 )' 2>/dev/null || true) ]]; then
            echo 'Le port TCP 5900 est déjà occupé : vérifie le serveur VNC existant.'
        else
            mkdir -p "${XDG_STATE_HOME:-$HOME/.local/state}/soundlive-ndi"
            logfile="${XDG_STATE_HOME:-$HOME/.local/state}/soundlive-ndi/vnc.log"
            nohup "$launcher" > "$logfile" 2>&1 </dev/null &
            sleep 2
            if [[ -n $(ss -H -ltn '( sport = :5900 )' 2>/dev/null || true) ]]; then
                echo 'TigerVNC actif sur TCP 5900 pour la session X11 courante.'
            else
                echo "TigerVNC n'écoute pas encore sur TCP 5900. Diagnostic : $logfile" >&2
            fi
        fi
    else
        echo 'Pas de DISPLAY X11 actif : TigerVNC sera lancé à la prochaine ouverture de session graphique.'
    fi
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
Exec=$viewer -PasswordFile=$passfile -rfbport=5900
Terminal=false
DESKTOP
    echo 'Serveur VNC Wayland configuré. Rouvre ta session et vérifie TCP 5900.'
else
    echo "Aucun serveur VNC compatible n'a été configuré pour la session ${session:-inconnue}." >&2
    echo 'Sur Ubuntu GNOME/Wayland, utilise le partage RDP intégré de GNOME.' >&2
fi
