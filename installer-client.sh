#!/usr/bin/env bash
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
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
case "$kind" in
    pi)
        if [[ -x /opt/ndi-pi/bin/ffplay && -r /opt/ndi-pi/lib/libndi.so.6 ]]; then
            exec bash "$base/installer-pi.sh" "$@"
        fi
        export SOUNDLIVE_DEVICE_KIND=pi
        exec bash "$base/installer-ubuntu.sh" "$@"
        ;;
    ubuntu) exec bash "$base/installer-ubuntu.sh" "$@" ;;
    *) echo "Plateforme attendue : pi ou ubuntu. Sous Windows, utilise installer-client.cmd."; exit 2 ;;
esac
