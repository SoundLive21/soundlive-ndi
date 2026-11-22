#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)

usage() {
    cat <<'HELP'
Usage : bash installer-client.sh [--plateforme pi|ubuntu] --preparer \
  --hote ndi-player-03 --ip 10.10.2.103/24 [--wifi "Réseau écrans"] \
  --regie http://10.10.2.99:8765 --code CODE

Options : --ethernet INTERFACE --interface-wifi INTERFACE
          --passerelle IP --dns IP [--dns2 IP] --cle-ssh FICHIER.pub
          --cle-wifi-fichier FICHIER | --saisir-cle-wifi | --wifi-ouvert
          --bureau-distant --sans-dhcp-install --attendre-reseau-ecrans --verifier
Sans code, l'association existante est conservée.
HELP
}

# Run before enrollment, after package installation and any NDI build.
if [[ ${1:-} == appliquer ]]; then
    [[ $# == 2 ]]
    mapfile -t config < "$2"
    [[ ${#config[@]} == 9 ]]
    hote=${config[0]}
    ip=${config[1]}
    eth=${config[2]}
    wlan=${config[3]}
    wifi_uuid=${config[4]}
    gateway=${config[5]}
    dns=${config[6]}
    expected_ssid=${config[7]}
    wifi_keyfile=${config[8]}
    if [[ ${SOUNDLIVE_ATTENDRE_RESEAU:-false} == true ]]; then
        read -r -p "Branche le câble au réseau des écrans, puis appuie sur Entrée. "
    fi
    nm=(sudo env LC_ALL=C nmcli)
    # Use newly created profiles so the normal profiles and their credentials are retained.
    eth_uuid=$(cat /proc/sys/kernel/random/uuid)
    new_wifi_uuid=
    installed=false
    cleanup() {
        if ! $installed; then
            "${nm[@]}" connection delete uuid "$eth_uuid" >/dev/null 2>&1 || true
            [[ -z $new_wifi_uuid ]] || "${nm[@]}" connection delete uuid "$new_wifi_uuid" >/dev/null 2>&1 || true
        fi
    }
    trap cleanup EXIT
    profiles=$(mktemp)
    trap 'rm -f -- "$profiles"; cleanup' EXIT
    sudo cat /etc/soundlive-ndi-network.conf 2>/dev/null | tail -n 2 > "$2.anciens" || true
    eth_name="SOUNDLIVE-ETHERNET"
    wifi_name="SOUNDLIVE-WIFI"
    "${nm[@]}" connection add type ethernet ifname "$eth" con-name "$eth_name" \
        connection.uuid "$eth_uuid" connection.autoconnect no ipv4.method manual ipv4.addresses "$ip" \
        ipv4.gateway "$gateway" ipv4.dns "$dns" ipv6.method disabled >/dev/null
    if [[ -n $wlan && -n $wifi_keyfile ]]; then
        # NetworkManager only loads keyfiles owned by root with mode 0600.
        new_wifi_uuid=$(sed -n 's/^uuid=//p' "$wifi_keyfile")
        [[ $new_wifi_uuid =~ ^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$ ]]
        path="/etc/NetworkManager/system-connections/soundlive-$new_wifi_uuid.nmconnection"
        sudo install -o root -g root -m 0600 "$wifi_keyfile" "$path"
        if ! "${nm[@]}" connection load "$path"; then
            sudo rm -f -- "$path"
            exit 1
        fi
    elif [[ -n $wlan ]]; then
        [[ $("${nm[@]}" -e no -g 802-11-wireless.ssid connection show uuid "$wifi_uuid") == "$expected_ssid" ]]
        temp_name="SoundLive configuration ${eth_uuid:0:8}"
        "${nm[@]}" connection clone uuid "$wifi_uuid" "$temp_name" >/dev/null
        new_wifi_uuid=$("${nm[@]}" -g connection.uuid connection show id "$temp_name")
    fi
    if [[ -n $wlan ]]; then
        "${nm[@]}" connection modify uuid "$new_wifi_uuid" connection.interface-name "$wlan" \
            connection.id "$wifi_name" connection.autoconnect no connection.permissions '' 802-11-wireless.powersave 2 \
            ipv4.method manual ipv4.addresses "$ip" ipv4.gateway "$gateway" ipv4.dns "$dns" \
            ipv4.routes '' ipv4.never-default no ipv6.method disabled
    fi
    printf '%s\n' "$eth" "$wlan" "$eth_uuid" "$new_wifi_uuid" > "$profiles"
    sudo systemctl stop soundlive-ndi-network.timer soundlive-ndi-network.service 2>/dev/null || true
    sudo install -o root -g root -m 0755 "$base/soundlive-ndi-network-static.sh" /usr/local/sbin/soundlive-ndi-network
    sudo install -o root -g root -m 0644 "$base/soundlive-ndi-network.service" "$base/soundlive-ndi-network.timer" /etc/systemd/system/
    sudo install -o root -g root -m 0600 "$profiles" /etc/soundlive-ndi-network.conf
    installed=true
    sudo systemctl daemon-reload
    sudo systemctl enable --now soundlive-ndi-network.timer
    sudo systemctl start soundlive-ndi-network.service
    cp -- "$profiles" "$2.actifs"
    sudo hostnamectl set-hostname "$hote"
    if grep -Eq '^127\.0\.1\.1[[:space:]]' /etc/hosts; then
        sudo sed -i -E "s/^(127\.0\.1\.1[[:space:]]+)[^[:space:]]+/\\1$hote/" /etc/hosts
    else
        printf '127.0.1.1\t%s\n' "$hote" | sudo tee -a /etc/hosts >/dev/null
    fi
    exit 0
fi

kind=$1
shift
hote= ip= ssid= regie= code= eth= wlan= gateway= dns= dns2= ssh_public= wifi_file=
verify=false dhcp=true wifi_open=false wifi_prompt=false attendre=false desktop_remote=false
while (( $# )); do
    case "$1" in
        --aide|--help) usage; exit 0 ;;
        --verifier) verify=true; shift; continue ;;
        --sans-dhcp-install) dhcp=false; shift; continue ;;
        --attendre-reseau-ecrans) attendre=true; shift; continue ;;
        --wifi-ouvert) wifi_open=true; shift; continue ;;
        --bureau-distant) desktop_remote=true; shift; continue ;;
        --saisir-cle-wifi) wifi_prompt=true; shift; continue ;;
        --hote|--ip|--wifi|--regie|--code|--ethernet|--interface-wifi|--passerelle|--dns|--dns2|--cle-ssh|--cle-wifi-fichier)
            if [[ $# -lt 2 || $2 == --* ]]; then usage >&2; exit 2; fi ;;
        *) usage >&2; exit 2 ;;
    esac
    case "$1" in
        --hote) hote=$2 ;;
        --ip) ip=$2 ;;
        --wifi) ssid=$2 ;;
        --regie) regie=$2 ;;
        --code) code=$2 ;;
        --ethernet) eth=$2 ;;
        --interface-wifi) wlan=$2 ;;
        --passerelle) gateway=$2 ;;
        --dns) dns=$2 ;;
        --dns2) dns2=$2 ;;
        --cle-ssh) ssh_public=$2 ;;
        --cle-wifi-fichier) wifi_file=$2 ;;
    esac
    shift 2
done
if (( EUID == 0 )); then
    echo "Lance l'installation depuis le compte du bureau, sans sudo." >&2
    exit 1
fi
if [[ $ssid == *$'\n'* || -z $regie || -z $hote || -z $ip ]]; then
    usage >&2
    exit 2
fi
/usr/bin/python3 - "$hote" "$ip" "$gateway" "$dns" "$dns2" "$regie" "$ssid" <<'PY'
import ipaddress
import re
import sys
from urllib.parse import urlsplit

host, cidr, gateway, dns, dns2, server, ssid = sys.argv[1:]
try:
    if ssid and (not 1 <= len(ssid.encode()) <= 32 or any(ord(char) < 32 for char in ssid)):
        raise ValueError("Le SSID doit comporter 1 à 32 octets et aucun caractère de contrôle.")
    if not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", host):
        raise ValueError("Hostname invalide.")
    if "/" not in cidr:
        raise ValueError("Précise le préfixe IPv4, par exemple 10.10.2.103/24.")
    address = ipaddress.IPv4Interface(cidr)
    if address.network.prefixlen > 30 or address.ip in (address.network.network_address, address.network.broadcast_address):
        raise ValueError("L'IPv4 du lecteur doit être une adresse utilisable du sous-réseau.")
    if address.ip.is_multicast or address.ip.is_loopback or address.ip.is_unspecified or address.ip.is_link_local:
        raise ValueError("L'adresse IPv4 du lecteur doit être une adresse unicast du LAN.")
    if gateway and (ipaddress.IPv4Address(gateway) not in address.network or
                    ipaddress.IPv4Address(gateway) in (address.ip, address.network.network_address,
                                                     address.network.broadcast_address)):
        raise ValueError("La passerelle doit être une autre adresse utilisable du même sous-réseau.")
    if dns2 and not dns:
        raise ValueError("Indique le DNS principal avec --dns.")
    for item in (dns, dns2):
        if item:
            ipaddress.IPv4Address(item)
    uri = urlsplit(server)
    if (uri.scheme not in ("http", "https") or not uri.hostname or uri.username is not None or
            uri.path not in ("", "/") or uri.query or uri.fragment or uri.port == 0):
        raise ValueError("Indique l'URL de la régie, par exemple http://10.10.2.99:8765.")
    if str(address.ip) == uri.hostname:
        raise ValueError("Le lecteur et la régie doivent avoir des adresses différentes.")
except ValueError as error:
    raise SystemExit(str(error))
PY
if [[ -n $dns2 ]]; then dns="$dns,$dns2"; fi
if [[ -z $ssid && ( -n $wlan || -n $wifi_file || $wifi_open == true || $wifi_prompt == true ) ]]; then
    echo 'Sans SSID, ne renseigne aucune option Wi-Fi : le lecteur utilisera Ethernet seul.' >&2
    exit 2
fi
if { $wifi_open && { $wifi_prompt || [[ -n $wifi_file ]]; }; } || { $wifi_prompt && [[ -n $wifi_file ]]; }; then
    echo 'Choisis une seule méthode pour la clé Wi-Fi.' >&2
    exit 2
fi
for input in "$ssh_public" "$wifi_file"; do
    if [[ -n $input && ! -f $input ]]; then
        echo "Fichier introuvable : $input" >&2
        exit 2
    fi
done
if ! command -v nmcli >/dev/null || ! systemctl is-active --quiet NetworkManager.service; then
    echo "NetworkManager doit déjà gérer les interfaces de ce lecteur." >&2
    exit 1
fi
export LC_ALL=C
devices=$(nmcli -t -e no -f DEVICE,TYPE,STATE device status)
choose_interface() {
    local kind=$1 chosen=$2 item type state flag
    flag=--interface-wifi
    if [[ $kind == ethernet ]]; then flag=--ethernet; fi
    local -a matches=()
    while IFS=: read -r item type state; do
        [[ $type == "$kind" && $state != unmanaged && ( -z $chosen || $chosen == "$item" ) ]] || continue
        matches+=("$item")
    done <<< "$devices"
    case "${#matches[@]}" in
        1) printf '%s\n' "${matches[0]}"; return ;;
        0) printf 'Aucune interface %s gérée par NetworkManager ne correspond à %s.\n' \
            "$kind" "${chosen:-la détection automatique}" >&2 ;;
        *) printf 'Plusieurs interfaces %s disponibles. Choisis avec %s : %s.\n' \
            "$kind" "$flag" "${matches[*]}" >&2 ;;
    esac
    printf 'Interfaces visibles (nom:type:état) :\n%s\n' "$devices" >&2
    if [[ -z $chosen && ${#matches[@]} == 0 ]]; then
        printf 'Vérifie que NetworkManager gère cette interface avant de la sélectionner.\n' >&2
    fi
    return 1
}
eth=$(choose_interface ethernet "$eth")
if [[ -n $ssid ]]; then
    wlan=$(choose_interface wifi "$wlan")
    printf 'Lecteur %s, IPv4 %s, Ethernet %s, Wi-Fi %s (%s)\n' "$hote" "$ip" "$eth" "$wlan" "$ssid"
else
    wlan=
    printf 'Lecteur %s, IPv4 %s, Ethernet %s, Wi-Fi non configuré.\n' "$hote" "$ip" "$eth"
fi
if $verify; then
    printf 'Paramètres validés. Réseau et système inchangés.\n'
    exit 0
fi
association="${XDG_CONFIG_HOME:-$HOME/.config}/soundlive-ndi/association.json"
if [[ -z $code && ! -r $association ]]; then
    echo "Indique le code d'association donné par la régie avec --code." >&2
    exit 2
fi
sudo -v
work=$(mktemp -d)
dhcp_uuid=
previous_eth=
timer_paused=false
cleanup_preparation() {
    local status=$?
    if [[ -n $dhcp_uuid ]]; then
        local current
        current=$(nmcli -t -e no -f DEVICE,UUID connection show --active | awk -F: -v eth="$eth" '$1 == eth { print $2; exit }') || true
        sudo nmcli connection delete uuid "$dhcp_uuid" >/dev/null 2>&1 || true
        if [[ $current == "$dhcp_uuid" && -n $previous_eth ]]; then
            sudo nmcli --wait 12 connection up uuid "$previous_eth" ifname "$eth" >/dev/null 2>&1 || true
        fi
    fi
    if $timer_paused && ! systemctl is-active --quiet soundlive-ndi-network.timer; then
        sudo systemctl start soundlive-ndi-network.timer || true
    fi
    rm -rf -- "$work"
    return "$status"
}
trap cleanup_preparation EXIT

wifi_profile() {
    local uuid type key_mgmt flags
    while IFS=: read -r uuid type; do
        [[ $type == 802-11-wireless || $type == wifi ]] || continue
        [[ $(sudo nmcli -e no -g 802-11-wireless.ssid connection show uuid "$uuid") == "$ssid" ]] || continue
        key_mgmt=$(sudo nmcli -g 802-11-wireless-security.key-mgmt connection show uuid "$uuid")
        if [[ $key_mgmt == wpa-psk || $key_mgmt == sae ]]; then
            flags=$(sudo nmcli -g 802-11-wireless-security.psk-flags connection show uuid "$uuid")
            if [[ $flags != 0* || -z $(sudo nmcli --show-secrets -g 802-11-wireless-security.psk connection show uuid "$uuid") ]]; then
                continue
            fi
        fi
        printf '%s\n' "$uuid"
        return 0
    done < <(sudo nmcli -t -f UUID,TYPE connection show)
    return 1
}

wifi_uuid= wifi_keyfile=
if [[ -n $ssid ]] && { $wifi_open || $wifi_prompt || [[ -n $wifi_file ]] || ! wifi_uuid=$(wifi_profile); }; then
    wifi_keyfile="$work/wifi.nmconnection"
    if $wifi_open; then
        password=
    elif [[ -n $wifi_file ]]; then
        IFS= read -r password < "$wifi_file" || [[ -n $password ]]
        password=${password%$'\r'}
    else
        if [[ ! -t 0 ]]; then
            echo "Indique --cle-wifi-fichier pour installer sans terminal interactif." >&2
            exit 2
        fi
        read -r -s -p "Clé Wi-Fi WPA2 de $ssid : " password
        printf '\n'
    fi
    printf '%s' "$password" | /usr/bin/python3 -c '
import hashlib, os, re, sys, uuid
from pathlib import Path
ssid, open_network, output = sys.argv[1:]
secret = sys.stdin.buffer.read().removeprefix(b"\xef\xbb\xbf")
octets = ";".join(map(str, ssid.encode())) + ";"
content = (f"[connection]\nid=SOUNDLIVE-WIFI\nuuid={uuid.uuid4()}\ntype=wifi\nautoconnect=false\n"
           f"\n[wifi]\nmode=infrastructure\nssid={octets}\npowersave=2\n")
if open_network != "true":
    if not re.fullmatch(rb"[ -~]{8,63}|[0-9a-fA-F]{64}", secret):
        raise SystemExit("Clé WPA2 attendue : 8 à 63 caractères ASCII ou 64 caractères hexadécimaux.")
    psk = secret.decode() if len(secret) == 64 else hashlib.pbkdf2_hmac("sha1", secret, ssid.encode(), 4096, 32).hex()
    content += f"\n[wifi-security]\nkey-mgmt=wpa-psk\npsk={psk}\n"
content += "\n[ipv4]\nmethod=auto\n\n[ipv6]\nmethod=disabled\n"
os.umask(0o077)
Path(output).write_text(content, encoding="utf-8")
' "$ssid" "$wifi_open" "$wifi_keyfile"
    unset password
fi
if $dhcp; then
    sudo ip link set dev "$eth" up
    if [[ $(cat "/sys/class/net/$eth/carrier" 2>/dev/null || true) == 1 ]]; then
        if systemctl is-active --quiet soundlive-ndi-network.timer; then
            sudo systemctl stop soundlive-ndi-network.timer soundlive-ndi-network.service
            timer_paused=true
        fi
        previous_eth=$(nmcli -t -e no -f DEVICE,UUID connection show --active | awk -F: -v eth="$eth" '$1 == eth { print $2; exit }')
        dhcp_uuid=$(cat /proc/sys/kernel/random/uuid)
        sudo nmcli connection add type ethernet ifname "$eth" con-name "SoundLive installation ${dhcp_uuid:0:8}" \
            connection.uuid "$dhcp_uuid" connection.autoconnect no ipv4.method auto ipv4.may-fail no \
            ipv4.route-metric 25 ipv6.method disabled >/dev/null
        echo 'Phase installation sur Ethernet en DHCP.'
        if ! sudo nmcli --wait 25 connection up uuid "$dhcp_uuid" ifname "$eth"; then
            sudo nmcli connection delete uuid "$dhcp_uuid" >/dev/null
            dhcp_uuid=
            if [[ -n $previous_eth ]]; then
                sudo nmcli --wait 12 connection up uuid "$previous_eth" ifname "$eth" || true
            fi
            echo 'Aucun bail DHCP Ethernet. Utilisation de la connexion actuelle pour les téléchargements.' >&2
        fi
    else
        echo 'Aucun câble Ethernet détecté. Utilisation de la connexion actuelle pour les téléchargements.' >&2
    fi
fi

if [[ -n $code || -n ${SOUNDLIVE_INSTALL_FINGERPRINT:-} ]] || $desktop_remote; then
    source "$base/installation-lecteur-linux.sh"
    prepare_remote_access "$base" "$kind"
    export SOUNDLIVE_REMOTE_ACCESS_PREPARED=1
elif [[ -n $ssh_public ]]; then
    bash "$base/installer-ssh-linux.sh" installer
fi
if [[ -n $ssh_public ]]; then
    bash "$base/installer-ssh-linux.sh" autoriser "$ssh_public"
fi
export SOUNDLIVE_INSTALL_REGIE="$regie"

export SOUNDLIVE_NETWORK_PREPARATION="$work/preparation"
export SOUNDLIVE_ATTENDRE_RESEAU="$attendre"
printf '%s\n' "$hote" "$ip" "$eth" "$wlan" "$wifi_uuid" "$gateway" "$dns" "$ssid" "$wifi_keyfile" > "$SOUNDLIVE_NETWORK_PREPARATION"
if [[ -n $code ]]; then
    bash "$base/installer-client.sh" --plateforme "$kind" "$regie" "$code"
else
    bash "$base/installer-client.sh" --plateforme "$kind" --mettre-a-jour "$regie"
fi
source "$base/installation-lecteur-linux.sh"
mapfile -t network < "$SOUNDLIVE_NETWORK_PREPARATION.actifs"
if ! clean_reader_profiles "$(cat "$SOUNDLIVE_NETWORK_PREPARATION.anciens")" "${network[2]}" "${network[3]}"; then
    printf 'Impossible de vérifier les profils réseau à nettoyer.\n' >&2
fi
if [[ -n $ssh_public ]]; then
    printf 'Cible SSH : %s@%s\nEmpreinte de ce lecteur :\n' "$(id -un)" "${ip%/*}"
    ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
fi
