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
  2) printf 'Réseau du lecteur\n1. Conserver le réseau actuel\n2. DHCP avec priorité Ethernet (Wi-Fi déjà configuré dans le système)\n3. IPv4 fixe avec Ethernet et Wi-Fi facultatif\n'
     read -rp 'Choix : ' network
     case "$network" in
       1|2) bash "$base/installer-client.sh" --plateforme "$kind" --installer-seul
            if [[ $network == 2 ]]; then bash "$base/installer-client.sh" --reseau-auto; fi;;
       3) nmcli device status
          read -rp "Nom du lecteur [$(hostname)] : " host
          read -rp 'IPv4/préfixe (ex. 192.168.56.10/24) : ' ip
          read -rp 'Interface Ethernet (vide = automatique) : ' eth
          read -rp 'Passerelle (facultative) : ' gateway
          read -rp 'DNS (facultatif) : ' dns
          read -rp 'SSID Wi-Fi (vide = Ethernet seul) : ' ssid
          args=(--installer-seul --hote "${host:-$(hostname)}" --ip "$ip")
          [[ -z $eth ]] || args+=(--ethernet "$eth")
          [[ -z $gateway ]] || args+=(--passerelle "$gateway")
          [[ -z $dns ]] || args+=(--dns "$dns")
          if [[ -n $ssid ]]; then
              read -rp 'Interface Wi-Fi (vide = automatique) : ' wlan
              read -rp 'Réseau ouvert ? [o/N] : ' opened
              args+=(--wifi "$ssid")
              [[ -z $wlan ]] || args+=(--interface-wifi "$wlan")
              if [[ $opened == o ]]; then args+=(--wifi-ouvert); else args+=(--saisir-cle-wifi); fi
          fi
          read -rp 'Appliquer cette configuration sur ce poste dédié ? [o/N] : ' confirm
          [[ $confirm == o ]] || exit 0
          bash "$base/preparer-lecteur-linux.sh" "$kind" "${args[@]}";;
       *) exit 2;;
     esac
     echo "Installation terminée. Ouvre SoundLive-NDI - Raccorder le lecteur depuis le menu des applications."
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
