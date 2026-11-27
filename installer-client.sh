#!/usr/bin/env bash
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
if [[ ${1:-} == --reseau-auto && $# -eq 1 ]]; then
    if [[ -e /etc/soundlive-ndi-network.conf ]]; then
        echo "La bascule avec IPv4 fixe est déjà configurée. Utilise --preparer pour la modifier."
        exit 1
    fi
    if [[ ! -x /usr/bin/nmcli ]] || ! systemctl is-active --quiet NetworkManager.service; then
        echo "La bascule Wi-Fi nécessite NetworkManager actif sur ce lecteur."
        exit 1
    fi
    temporary=$(mktemp -d)
    trap 'rm -rf -- "$temporary"' EXIT
    cat > "$temporary/soundlive-ndi-network" <<'NETWORK'
#!/bin/bash
set -eu
ethernet_ready() {
    local device name kind
    while IFS=: read -r name kind; do
        [[ $kind == ethernet ]] || continue
        device="/sys/class/net/$name"
        [[ -e "$device/device" && $(cat "$device/carrier" 2>/dev/null) == 1 ]] || continue
        if ip -4 -o address show dev "$name" scope global | grep -q .; then
            return 0
        fi
    done <<< "$devices"
    return 1
}
devices=$(LC_ALL=C /usr/bin/nmcli -t -e no -f DEVICE,TYPE device status)
if ! grep -q ':wifi$' <<< "$devices"; then
    exit 0
fi
radio=$(LC_ALL=C /usr/bin/nmcli radio wifi)
if ethernet_ready; then
    if [[ $radio == enabled ]]; then
        /usr/bin/nmcli radio wifi off
    fi
elif [[ $radio == disabled ]]; then
    /usr/bin/nmcli radio wifi on
fi
NETWORK
    cat > "$temporary/soundlive-ndi-network.service" <<'SERVICE'
[Unit]
Description=Priorité au réseau Ethernet du lecteur SoundLive-NDI
After=NetworkManager.service
Requisite=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/soundlive-ndi-network
TimeoutStartSec=6
SERVICE
    cat > "$temporary/soundlive-ndi-network.timer" <<'TIMER'
[Unit]
Description=Vérification de la connexion Ethernet du lecteur SoundLive-NDI

[Timer]
OnBootSec=2s
OnUnitInactiveSec=3s
AccuracySec=1s

[Install]
WantedBy=timers.target
TIMER
    sudo install -o root -g root -m 0755 "$temporary/soundlive-ndi-network" /usr/local/sbin/
    sudo install -o root -g root -m 0644 "$temporary/"*.service "$temporary/"*.timer /etc/systemd/system/
    sudo systemctl daemon-reload
    sudo systemctl enable --now soundlive-ndi-network.timer
    sudo systemctl start soundlive-ndi-network.service
    echo "Bascule Ethernet/Wi-Fi active."
    exit 0
fi
kind=
if [[ ${1:-} == --plateforme ]]; then
    if [[ $# -lt 2 ]]; then
        echo "Indique pi ou ubuntu après --plateforme."
        exit 2
    fi
    kind=${2:-}
    shift 2
fi
if [[ -z $kind ]]; then
    if [[ -r /proc/device-tree/model ]] && [[ "$(tr -d '\0' </proc/device-tree/model)" == *"Raspberry Pi"* ]]; then
        kind=pi
    else
        kind=ubuntu
    fi
fi
desktop="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/ndi-ecran.desktop"
if [[ ${1:-} == --activer-maintenance-os && $# -eq 1 ]]; then
    source "$base/installation-lecteur-linux.sh"
    install_os_maintenance "$base"
    echo 'Mise à jour système activée pour ce compte. Installe aussi le lecteur 1.4.38 depuis la régie.'
    exit 0
fi
if [[ ${1:-} == --reparer-identification && $# -eq 1 ]]; then
    if (( EUID == 0 )); then
        echo 'Lance cette réparation depuis le compte du lecteur, sans sudo.' >&2
        exit 1
    fi
    case "$kind" in
        pi|ubuntu) ;;
        *) echo "Plateforme attendue : pi ou ubuntu." >&2; exit 2 ;;
    esac
    if [[ ! -r ${XDG_CONFIG_HOME:-$HOME/.config}/soundlive-ndi/association.json ]]; then
        echo 'Ce compte ne possède pas d’association de lecteur.' >&2
        exit 1
    fi
    source "$base/installation-lecteur-linux.sh"
    if ! installed_sdl_directory "$HOME/.local/share" "$desktop" >/dev/null && [[ $kind == pi ]]; then
        echo 'Moteur FFplay : installation de la surcouche GTK pour Raspberry Pi OS Wayland.'
        sudo apt-get update
        sudo apt-get install -y gir1.2-gtklayershell-0.1
        exec bash "$base/installer-pi.sh" --mettre-a-jour
    fi
    echo 'Moteur SDL : mise à jour du client et du moteur d’affichage.'
    export SOUNDLIVE_DEVICE_KIND="$kind"
    export SOUNDLIVE_REPAIR_IDENTIFICATION=1
    bash "$base/installer-ubuntu.sh" --mettre-a-jour-client
    exec bash "$base/installer-ubuntu.sh" --mettre-a-jour-rendu
fi
network_options=()
reboot_after=false
fingerprint=
while (( $# )); do
    case "$1" in
        --redemarrer) reboot_after=true; shift; continue ;;
        --empreinte-regie)
            if [[ ! ${2:-} =~ ^[a-f0-9]{64}$ ]]; then
                echo "L’empreinte des clés doit être celle donnée par la régie." >&2
                exit 2
            fi
            fingerprint=$2 ;;
        --ethernet|--interface-wifi)
            if [[ -z ${2:-} || $2 == --* ]]; then
                echo "Indique le nom de l’interface après $1." >&2
                exit 2
            fi
            network_options+=("$1" "$2") ;;
        *) break ;;
    esac
    shift 2
done
if [[ -n $fingerprint ]]; then
    export SOUNDLIVE_INSTALL_FINGERPRINT="$fingerprint"
fi
if [[ ${1:-} == --preparer ]]; then
    case "$kind" in
        pi|ubuntu) exec bash "$base/preparer-lecteur-linux.sh" "$kind" "${network_options[@]}" "${@:2}" ;;
        *) echo "Plateforme attendue : pi ou ubuntu."; exit 2 ;;
    esac
fi
if (( ${#network_options[@]} )); then
    echo "Les options --ethernet et --interface-wifi nécessitent --preparer." >&2
    exit 2
fi
if [[ -n $fingerprint ]]; then
    if (( EUID == 0 )); then
        echo "Lance l’installation sous le compte du bureau, sans sudo." >&2
        exit 1
    fi
    export SOUNDLIVE_INSTALL_REGIE=${1:-}
    if [[ $SOUNDLIVE_INSTALL_REGIE == --mettre-a-jour* ]]; then
        export SOUNDLIVE_INSTALL_REGIE=${2:-}
    fi
    if [[ $SOUNDLIVE_INSTALL_REGIE != http://* ]]; then
        echo "Indique l’adresse HTTP de la régie pour récupérer ses clés." >&2
        exit 2
    fi
fi
run_installer() {
    if $reboot_after; then
        bash "$@"
        echo "Installation terminée. Redémarrage du lecteur."
        sudo systemctl reboot
        exit 0
    fi
    exec bash "$@"
}
case "$kind" in
    pi)
        if [[ ${1:-} == --mettre-a-jour* ]]; then
            source "$base/installation-lecteur-linux.sh"
            if installed_sdl_directory "$HOME/.local/share" "$desktop" >/dev/null; then
                export SOUNDLIVE_DEVICE_KIND=pi
                run_installer "$base/installer-ubuntu.sh" "$@"
            fi
        fi
        if [[ ${1:-} == --mettre-a-jour-rendu ]]; then
            echo 'Pour un Pi sous FFplay, utilise --reparer-identification.' >&2
            exit 2
        fi
        if [[ -x /opt/ndi-pi/bin/ffplay && -r /opt/ndi-pi/lib/libndi.so.6 ]]; then
            run_installer "$base/installer-pi.sh" "$@"
        fi
        export SOUNDLIVE_DEVICE_KIND=pi
        run_installer "$base/installer-ubuntu.sh" "$@"
        ;;
    ubuntu) run_installer "$base/installer-ubuntu.sh" "$@" ;;
    *) echo "Plateforme attendue : pi ou ubuntu. Sous Windows, utilise installer-client.cmd."; exit 2 ;;
esac
