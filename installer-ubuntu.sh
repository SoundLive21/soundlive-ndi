#!/usr/bin/env bash
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
set -euo pipefail
export SOUNDLIVE_DEVICE_KIND="${SOUNDLIVE_DEVICE_KIND:-ubuntu}"

if (( EUID == 0 )); then
    echo "Lance ce script depuis le compte Ubuntu qui affichera la TV, sans sudo."
    exit 1
fi

show_usage() {
    echo "Première installation : bash installer-ubuntu.sh http://IP_DU_PC:8765 CODE"
    echo "Mise à jour complète : bash installer-ubuntu.sh --mettre-a-jour [http://IP_DU_PC:8765]"
    echo "Mise à jour du client et des diagnostics : bash installer-ubuntu.sh --mettre-a-jour-client [http://IP_DU_PC:8765]"
    echo "Mise à jour du moteur d’affichage uniquement : bash installer-ubuntu.sh --mettre-a-jour-rendu"
}

update=false
client_only=false
render_only=false
if [[ ${1:-} == --mettre-a-jour-rendu && $# -eq 1 ]]; then
    update=true
    render_only=true
elif [[ ${1:-} == --mettre-a-jour || ${1:-} == --mettre-a-jour-client ]]; then
    if (( $# > 2 )); then
        show_usage
        exit 1
    fi
    update=true
    if [[ $1 == --mettre-a-jour-client ]]; then
        client_only=true
    fi
elif [[ $# -ne 2 || ${1:-} == --* ]]; then
    show_usage
    exit 1
fi

here=$(cd -- "$(dirname -- "$0")" && pwd)
for file in ecran.py commun.py diagnostic.py mise_a_jour.py lancer-client.py; do
    if [[ ! -r "$here/$file" ]]; then
        echo "Le fichier $file est absent. Décompresse le kit SoundLive-NDI complet."
        exit 1
    fi
done
app="$HOME/.local/share/soundlive-ecrans"
config_dir="${XDG_CONFIG_HOME:-$HOME/.config}"
autostart="$config_dir/autostart"
association="$config_dir/soundlive-ndi/association.json"
if $update && [[ ! -r "$association" ]]; then
    echo "Ce compte n’a pas encore d’association. Utilise le code du tableau SoundLive-NDI."
    exit 1
fi
if $render_only && [[ ! -x "$app/lecteur-ndi-ubuntu" || ! -r "$app/lib/libndi.so.6" ]]; then
    echo "Installation incomplète. Lance la mise à jour complète avec --mettre-a-jour."
    exit 1
fi

check_association() {
    if [[ $# -eq 1 ]]; then
        SOUNDLIVE_PLAYER=ubuntu /usr/bin/python3 "$here/ecran.py" verifier "$1"
    elif ! SOUNDLIVE_PLAYER=ubuntu /usr/bin/python3 "$here/ecran.py" verifier; then
        echo "Mise à jour locale possible. Vérifie ensuite l’accès au tableau depuis ce portable."
    fi
}

install_client() {
    if [[ -r "$here/regie-update.pub" ]]; then
        if ! command -v ssh-keygen >/dev/null; then
            sudo apt install -y openssh-client
        fi
        install -m 644 "$here/regie-update.pub" "$app/"
    fi
    install -m 644 "$here/commun.py" "$here/diagnostic.py" "$here/mise_a_jour.py" \
        "$here/ecran.py" "$here/lancer-client.py" "$app/"
    if [[ -d "$app/.versions" ]]; then
        touch "$app/.versions/revenir-base"
    fi
    cat >"$app/demarrer-ubuntu" <<'LAUNCHER'
#!/usr/bin/env bash
set -eu
base=$(cd -- "$(dirname -- "$0")" && pwd)
export SOUNDLIVE_PLAYER=ubuntu
if [[ -r "$base/type-recepteur" ]]; then
    export SOUNDLIVE_DEVICE_KIND="$(cat "$base/type-recepteur")"
fi
exec /usr/bin/python3 "$base/lancer-client.py"
LAUNCHER
    printf '%s\n' "$SOUNDLIVE_DEVICE_KIND" > "$app/type-recepteur"
    chmod 755 "$app/demarrer-ubuntu"
}

install_autostart() {
    mkdir -p "$autostart"
    cat >"$autostart/ndi-ecran.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive-NDI
Exec="$app/demarrer-ubuntu"
Terminal=false
DESKTOP
}

if $client_only; then
    if [[ ! -x "$app/lecteur-ndi-ubuntu" || ! -r "$app/lib/libndi.so.6" ]]; then
        echo "Installation Ubuntu incomplète. Lance la mise à jour complète avec --mettre-a-jour."
        exit 1
    fi
    check_association "${@:2}"
    install_client
    install_autostart
    echo "Client Ubuntu et diagnostic.py mis à jour. Redémarre le portable ou rouvre sa session graphique."
    exit 0
fi

case "$(dpkg --print-architecture)" in
    amd64) sdk_arch=x86_64-linux-gnu ;;
    arm64) sdk_arch=aarch64-linux-gnu ;;
    *) echo "Architecture non prise en charge. Ubuntu doit être en 64 bits."; exit 1 ;;
esac

if $render_only; then
    if ! command -v gcc >/dev/null || ! command -v pkg-config >/dev/null || ! pkg-config --exists sdl2; then
        sudo apt update
        sudo apt install -y gcc pkg-config libsdl2-dev
    fi
else
    sudo apt update
    sudo apt install -y gcc pkg-config libsdl2-dev python3 python3-gi python3-gi-cairo gir1.2-gtk-3.0 \
        avahi-daemon libavahi-client3 curl ca-certificates x11-xserver-utils ffmpeg
    sudo systemctl enable --now avahi-daemon
fi

temp=$(mktemp -d)
trap 'rm -rf -- "$temp"' EXIT
sdk="${NDI_SDK_DIR:-$app/ndi-sdk}"
if [[ ! -r "$sdk/include/Processing.NDI.Lib.h" || ! -r "$sdk/lib/$sdk_arch/libndi.so.6" ]]; then
    if [[ -n ${NDI_SDK_DIR:-} ]]; then
        echo "SDK NDI 6 introuvable pour $sdk_arch dans NDI_SDK_DIR."
        exit 1
    fi
    sdk_url=https://downloads.ndi.tv/SDK/NDI_SDK_Linux/Install_NDI_SDK_v6_Linux.tar.gz
    if [[ -n ${NDI_SDK_ARCHIVE:-} ]]; then
        cp -- "$NDI_SDK_ARCHIVE" "$temp/ndi-sdk.tar.gz"
    else
        if ! curl --fail --show-error --location --retry 2 --connect-timeout 15 \
            "$sdk_url" --output "$temp/ndi-sdk.tar.gz"; then
            echo "Télécharge le SDK Linux sur https://ndi.video/for-developers/ndi-sdk/download/"
            echo "Relance avec NDI_SDK_ARCHIVE=/chemin/vers/Install_NDI_SDK_v6_Linux.tar.gz devant la commande."
            exit 1
        fi
    fi
    (
        cd "$temp"
        tar -xzf ndi-sdk.tar.gz
        bash ./Install_NDI_SDK_v6_Linux.sh
    )
    sdk="$temp/NDI SDK for Linux"
fi

if [[ ! -r "$sdk/include/Processing.NDI.Lib.h" || ! -r "$sdk/lib/$sdk_arch/libndi.so.6" ]]; then
    echo "Le SDK NDI 6 n’a pas été extrait pour $sdk_arch."
    exit 1
fi

read -r -a sdl_flags <<< "$(pkg-config --cflags --libs sdl2)"
gcc -O2 -std=c11 -Wall -Wextra -I"$sdk/include" "$here/lecteur-ndi-ubuntu.c" \
    "${sdl_flags[@]}" -Wl,-rpath,'$ORIGIN/lib' \
    "$sdk/lib/$sdk_arch/libndi.so.6" -o "$temp/lecteur-ndi-ubuntu"
mkdir -p "$temp/lib"
install -m 644 "$sdk/lib/$sdk_arch/libndi.so.6" "$temp/lib/libndi.so.6"
if $render_only; then
    if ! /usr/bin/python3 -c 'import cairo, gi; gi.require_foreign("cairo")' >/dev/null 2>&1; then
        sudo apt install -y python3-gi-cairo
    fi
    install -m 755 "$temp/lecteur-ndi-ubuntu" "$app/.lecteur-ndi-ubuntu.new"
    if ! preview_check=$("$app/.lecteur-ndi-ubuntu.new" --apercu-interne) ||
        [[ $preview_check != apercu-interne ]]; then
        rm -f -- "$app/.lecteur-ndi-ubuntu.new"
        echo "Le nouveau moteur ne démarre pas avec le runtime NDI installé. Lance la mise à jour complète avec --mettre-a-jour."
        exit 1
    fi
    mv -fT "$app/.lecteur-ndi-ubuntu.new" "$app/lecteur-ndi-ubuntu"
    echo "Moteur d’affichage mis à jour. Pousse ensuite le nouveau client depuis la régie."
    exit 0
fi
"$temp/lecteur-ndi-ubuntu" --sources >/dev/null

account=$(id -un)
if [[ ! $account =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
    echo "Nom du compte Ubuntu non valide."
    exit 1
fi
rule="$temp/sudoers"
printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff\n' "$account" >"$rule"
sudo visudo -cf "$rule"
sudo -v
if [[ -n ${SOUNDLIVE_NETWORK_PREPARATION:-} ]]; then
    bash "$here/preparer-lecteur-linux.sh" appliquer "$SOUNDLIVE_NETWORK_PREPARATION"
fi

if ! $update; then
    SOUNDLIVE_PLAYER=ubuntu /usr/bin/python3 "$here/ecran.py" associer "$1" "$2"
else
    check_association "${@:2}"
fi

mkdir -p "$app/lib"
install_client
install -m 755 "$temp/lecteur-ndi-ubuntu" "$app/.lecteur-ndi-ubuntu.new"
install -m 644 "$temp/lib/libndi.so.6" "$app/lib/.libndi.so.6.new"
mv -fT "$app/lib/.libndi.so.6.new" "$app/lib/libndi.so.6"
mv -fT "$app/.lecteur-ndi-ubuntu.new" "$app/lecteur-ndi-ubuntu"
if [[ $sdk == "$temp/NDI SDK for Linux" ]]; then
    mkdir -p "$app/ndi-sdk/lib/$sdk_arch"
    cp -a -- "$sdk/include" "$app/ndi-sdk/"
    install -m 644 "$sdk/lib/$sdk_arch/libndi.so.6" "$app/ndi-sdk/lib/$sdk_arch/libndi.so.6"
fi
sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/soundlive-ecrans

install_autostart

echo "Installation terminée. Pour tester depuis un terminal de la session Ubuntu :"
echo "  ~/.local/share/soundlive-ecrans/demarrer-ubuntu"
echo "Le lecteur se lancera automatiquement à la prochaine ouverture de cette session."
