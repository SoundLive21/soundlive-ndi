#!/usr/bin/env bash
# Configure la session d'un lecteur NDI dédié : autologin + écran toujours actif.
set -euo pipefail

mode=$\{1:-ubuntu}
verify=false
if [[ $\{2:-} == --verifier ]]; then verify=true; fi
if [[ $mode != ubuntu && $mode != pi ]]; then
    echo 'Usage : bash configurer-session-lecteur-linux.sh ubuntu|pi [--verifier]' >&2
    exit 2
fi
if (( EUID == 0 )); then
    echo 'Exécute ce script avec le compte de la session graphique, sans sudo.' >&2
    exit 1
fi
account=$(id -un)
if [[ ! $account =~ ^[a-zA-Z_][a-zA-Z0-9_.-]*$ ]]; then
    echo "Compte utilisateur non pris en charge : $account" >&2
    exit 1
fi
autostart="$\{XDG_CONFIG_HOME:-$HOME/.config}/autostart"
launcher="$HOME/.local/bin/soundlive-ndi-ecran-actif"
if $verify; then
    echo "Vérification : mode $mode, compte $account, sans modification."
    echo "Autologin via GDM/LightDM/raspi-config, désactivation du verrouillage GNOME et de la veille X11."
    exit 0
fi

install -d -m 700 "$(dirname -- "$launcher")" "$autostart"
cat > "$launcher" <<'SCRIPT'
#!/usr/bin/env bash
# Application à chaque ouverture de session, même si l'installation était via SSH.
set -u
if command -v gsettings >/dev/null 2>&1; then
    set_gnome() {
        local schema=$1 key=$2 value=$3
        if gsettings list-keys "$schema" 2>/dev/null | grep -Fxq -- "$key"; then
            gsettings set "$schema" "$key" "$value" || true
        fi
    }
    set_gnome org.gnome.desktop.session idle-delay 0
    set_gnome org.gnome.desktop.screensaver lock-enabled false
    set_gnome org.gnome.settings-daemon.plugins.power idle-dim false
    set_gnome org.gnome.settings-daemon.plugins.power sleep-inactive-ac-type nothing
    set_gnome org.gnome.settings-daemon.plugins.power sleep-inactive-battery-type nothing
fi
if [[ $\{XDG_SESSION_TYPE:-} == x11 && -n $\{DISPLAY:-} ]] && command -v xset >/dev/null 2>&1; then
    xset s off || true
    xset s noblank || true
    xset -dpms || true
fi
SCRIPT
chmod 700 "$launcher"
cat > "$autostart/soundlive-ecran-actif.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive-NDI - Écran actif
Exec=$launcher
Terminal=false
X-GNOME-Autostart-enabled=true
DESKTOP
# Appliquer immédiatement dans la session courante lorsqu'elle est accessible.
"$launcher"

if [[ $\{SOUNDLIVE_SKIP_AUTOLOGIN:-0} == 1 ]]; then
    echo 'Connexion automatique ignorée (SOUNDLIVE_SKIP_AUTOLOGIN=1).'
    exit 0
fi
sudo -v

if [[ $mode == pi ]] && command -v raspi-config >/dev/null 2>&1; then
    # B4 : démarrage sur bureau avec connexion automatique du compte Pi.
    # do_blanking 1 : désactivation de l'écran noir sur inactivité.
    if sudo raspi-config nonint do_boot_behaviour B4; then
        sudo raspi-config nonint do_blanking 1 || echo 'Vérifie le réglage Screen Blanking du Raspberry Pi.' >&2
        echo "Raspberry Pi : démarrage graphique automatique configuré pour $account."
        exit 0
    fi
    echo 'raspi-config n’a pas activé l’autologin : recherche de GDM/LightDM.' >&2
fi

dm=''
if [[ -e /etc/systemd/system/display-manager.service ]]; then
    dm=$(basename -- "$(readlink -f /etc/systemd/system/display-manager.service)")
fi
if [[ $dm == *gdm* || ( -z $dm && -d /etc/gdm3 ) ]]; then
    conf=/etc/gdm3/custom.conf
    if [[ -d /etc/gdm && ! -d /etc/gdm3 ]]; then conf=/etc/gdm/custom.conf; fi
    # Modifier la section [daemon] sans effacer les autres sections existantes.
    sudo python3 - "$conf" "$account" <<'PY'
from pathlib import Path
import os
import re
import sys
from tempfile import NamedTemporaryFile

target = Path(sys.argv[1])
user = sys.argv[2]
original = target.read_text(encoding="utf-8") if target.is_file() else ""
lines = original.splitlines()
result = []
in_daemon = False
found_daemon = False
for line in lines:
    match = re.match(r"^\s*\[([^\]]+)\]\s*$", line)
    if match:
        if in_daemon:
            result.extend(("AutomaticLoginEnable=true", f"AutomaticLogin={user}"))
        in_daemon = match.group(1).strip().lower() == "daemon"
        found_daemon |= in_daemon
    if in_daemon and re.match(r"^\s*[#;]?\s*AutomaticLogin(?:Enable)?\s*=", line):
        continue
    result.append(line)
if in_daemon:
    result.extend(("AutomaticLoginEnable=true", f"AutomaticLogin={user}"))
if not found_daemon:
    result.extend(("", "[daemon]", "AutomaticLoginEnable=true", f"AutomaticLogin={user}"))
if original and not target.with_suffix(target.suffix + ".soundlive-avant").exists():
    target.with_suffix(target.suffix + ".soundlive-avant").write_text(original, encoding="utf-8")
target.parent.mkdir(parents=True, exist_ok=True)
with NamedTemporaryFile(mode="w", dir=target.parent, encoding="utf-8", delete=False) as stream:
    stream.write("\n".join(result) + "\n")
    temp = Path(stream.name)
os.chmod(temp, 0o644)
os.replace(temp, target)
PY
    echo "GDM : ouverture de session automatique configurée pour $account."
elif [[ $dm == *lightdm* || ( -z $dm && -d /etc/lightdm ) ]]; then
    sudo install -d -m 755 /etc/lightdm/lightdm.conf.d
    printf '[Seat:*]\nautologin-user=%s\nautologin-user-timeout=0\n' "$account" |
        sudo tee /etc/lightdm/lightdm.conf.d/60-soundlive-autologin.conf >/dev/null
    echo "LightDM : ouverture de session automatique configurée pour $account."
else
    echo "Gestionnaire de connexion inconnu ($\{dm:-absent}) : configure l’autologin manuellement." >&2
fi
echo 'Écran actif et verrouillage automatique désactivés pour la session du lecteur.'
