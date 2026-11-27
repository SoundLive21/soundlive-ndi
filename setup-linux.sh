#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
if (( EUID == 0 )); then echo "Lance ce script sans sudo, depuis le compte du bureau."; exit 1; fi
kind=ubuntu
if [[ -r /proc/device-tree/model ]] && [[ "$(tr -d '\0' </proc/device-tree/model)" == *"Raspberry Pi"* ]]; then kind=pi; fi
printf '\nSoundLive NDI - Installation (%s)\n1. Régie\n2. Lecteur (raccordement ultérieur)\n3. Quitter\n' "$kind"
read -rp 'Choix : ' choice
case "$choice" in
  1) [[ $kind != pi ]] || { echo "Régie sur Raspberry Pi non validée : utilise Ubuntu Desktop ou Windows."; exit 1; }; exec bash "$base/installer-regie-linux.sh";;
  2) bash "$base/installer-client.sh" --plateforme "$kind" --installer-seul
     echo "Installation terminée. Ouvre SoundLive-NDI - Raccorder le lecteur depuis le menu des applications."
     ;;
  3) exit 0;;
  *) echo "Choix invalide."; exit 2;;
esac
