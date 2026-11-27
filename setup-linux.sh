#!/usr/bin/env bash
set -euo pipefail
base=$(cd -- "$(dirname -- "$0")" && pwd)
if (( EUID == 0 )); then echo "Lance ce script sans sudo, depuis le compte du bureau."; exit 1; fi
kind=ubuntu
if [[ -r /proc/device-tree/model ]] && [[ "$(tr -d '\0' </proc/device-tree/model)" == *"Raspberry Pi"* ]]; then kind=pi; fi
printf '\nSoundLive NDI - Installation (%s)\n1. Régie\n2. Lecteur (raccordement ultérieur)\n3. Désinstaller la régie\n4. Désinstaller le lecteur\n5. Quitter\n' "$kind"
read -rp 'Choix : ' choice
case "$choice" in
  1) [[ $kind != pi ]] || { echo "Régie sur Raspberry Pi non validée : utilise Ubuntu Desktop ou Windows."; exit 1; }; exec bash "$base/installer-regie-linux.sh";;
  2) bash "$base/installer-client.sh" --plateforme "$kind" --installer-seul
     echo "Installation terminée. Conserve ce kit. Dans la régie, ajoute un écran et exécute la commande complète générée pour configurer le réseau et raccorder le lecteur."
     ;;
  3|4)
     role=regie
     [[ $choice == 3 ]] || role=lecteur
     printf '1. Programme seulement\n2. Programme et données\n3. Programme, données et nettoyage système dédié\n4. Simulation complète\n'
     read -rp 'Choix : ' mode
     case "$mode" in
       1) exec /usr/bin/python3 "$base/desinstaller_soundlive.py" "$role";;
       2) exec /usr/bin/python3 "$base/desinstaller_soundlive.py" "$role" --purger;;
       3) exec /usr/bin/python3 "$base/desinstaller_soundlive.py" "$role" --purger --nettoyer-systeme;;
       4) exec /usr/bin/python3 "$base/desinstaller_soundlive.py" "$role" --purger --nettoyer-systeme --simulation;;
       *) exit 2;;
     esac;;
  5) exit 0;;
  *) echo "Choix invalide."; exit 2;;
esac
