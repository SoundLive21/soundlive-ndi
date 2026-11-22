#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

bash -n configurer-session-lecteur-linux.sh
bash -n installer-pi.sh
bash -n installer-ubuntu.sh
bash -n installation-lecteur-linux.sh

output=$(bash configurer-session-lecteur-linux.sh ubuntu --verifier)
grep -q 'sans modification' <<< "$output"
echo 'OK: mode vérification Ubuntu (aucun fichier système modifié).'
output=$(bash configurer-session-lecteur-linux.sh pi --verifier)
grep -q 'sans modification' <<< "$output"
echo 'OK: mode vérification Raspberry Pi (aucun fichier système modifié).'

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/bin" "$work/home"
cat > "$work/bin/gsettings" <<'MOCK'
#!/usr/bin/env bash
if [[ $1 == list-keys ]]; then
    case $2 in
        org.gnome.desktop.session) echo idle-delay ;;
        org.gnome.desktop.screensaver) echo lock-enabled ;;
        org.gnome.settings-daemon.plugins.power)
            printf 'idle-dim\nsleep-inactive-ac-type\nsleep-inactive-battery-type\n' ;;
    esac
elif [[ $1 == set ]]; then
    printf '%s\n' "$*" >> "$HOME/gsettings-applied"
fi
MOCK
cat > "$work/bin/xset" <<'MOCK'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HOME/xset-applied"
MOCK
chmod +x "$work/bin/gsettings" "$work/bin/xset"

SOUNDLIVE_SKIP_AUTOLOGIN=1 HOME="$work/home" XDG_CONFIG_HOME="$work/home/.config" \
    XDG_SESSION_TYPE=x11 DISPLAY=:1 PATH="$work/bin:$PATH" \
    bash configurer-session-lecteur-linux.sh ubuntu

test -x "$work/home/.local/bin/soundlive-ndi-ecran-actif"
test -f "$work/home/.config/autostart/soundlive-ecran-actif.desktop"
grep -q 'org.gnome.desktop.session idle-delay 0' "$work/home/gsettings-applied"
grep -q 'org.gnome.desktop.screensaver lock-enabled false' "$work/home/gsettings-applied"
grep -q 'sleep-inactive-ac-type nothing' "$work/home/gsettings-applied"
grep -q 's off' "$work/home/xset-applied"
grep -q '\-dpms' "$work/home/xset-applied"
echo 'OK: GNOME et X11 gardent la session active ; réglages réappliqués à chaque connexion.'

# La possibilité d'éviter l'autologin est délibérément limitée à la variable
# d'environnement, afin de ne pas modifier l'accès d'une station non dédiée.
