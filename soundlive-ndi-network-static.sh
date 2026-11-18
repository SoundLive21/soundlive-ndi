#!/usr/bin/env bash
set -euo pipefail
export LC_ALL=C
mapfile -t settings < /etc/soundlive-ndi-network.conf
[[ ${#settings[@]} == 4 ]]
eth=${settings[0]}
wlan=${settings[1]}
eth_uuid=${settings[2]}
wifi_uuid=${settings[3]}

active_device() {
    nmcli -t -e no -f DEVICE,UUID connection show --active | grep -Fqx -- "$1:$2"
}

nmcli device set "$eth" autoconnect no
nmcli device set "$wlan" autoconnect no
ip link set dev "$eth" up
if [[ $(cat "/sys/class/net/$eth/carrier" 2>/dev/null || true) == 1 ]]; then
    if active_device "$eth" "$eth_uuid"; then
        if [[ $(nmcli radio wifi) != disabled ]]; then
            nmcli radio wifi off
        fi
        exit 0
    fi
    nmcli radio wifi off
    if nmcli --wait 12 connection up uuid "$eth_uuid" ifname "$eth"; then
        exit 0
    fi
fi

# Disconnect the wired port before assigning the same IPv4 to Wi-Fi.
if nmcli -t -e no -f DEVICE connection show --active | grep -Fqx -- "$eth"; then
    nmcli --wait 5 device disconnect "$eth"
fi
if [[ $(nmcli radio wifi) != enabled ]]; then
    nmcli radio wifi on
fi
if ! active_device "$wlan" "$wifi_uuid"; then
    nmcli --wait 20 connection up uuid "$wifi_uuid" ifname "$wlan"
fi
