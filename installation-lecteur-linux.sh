#!/usr/bin/env bash
# Shared by the Raspberry Pi and Ubuntu player installers.

player_directory() {
    local parent=$1
    local current="$parent/soundlive-ndi" legacy="$parent/soundlive-ecrans"
    local staging
    if [[ ! -e $current && -d $legacy ]]; then
        staging=$(mktemp -d "$parent/.soundlive-ndi.XXXXXX") || return
        if ! cp -a -- "$legacy/." "$staging/" || ! mv -T --no-clobber -- "$staging" "$current"; then
            rm -rf -- "$staging"
            echo "Impossible de reprendre le dossier de l’ancien lecteur." >&2
            return 1
        fi
        if [[ -d $staging ]]; then rm -rf -- "$staging"; fi
    fi
    printf '%s\n' "$current"
}

installed_sdl_directory() {
    local parent=$1 autostart=$2 app
    for app in "$parent/soundlive-ndi" "$parent/soundlive-ecrans"; do
        if [[ -r $autostart ]] && grep -Fxq -e "Exec=\"$app/demarrer-ubuntu\"" -e "Exec=$app/demarrer-ubuntu" "$autostart"; then
            printf '%s\n' "$app"
            return
        fi
    done
    if [[ -r $autostart ]] && grep -Eq '^Exec=.*[/]lancer-client\.py(["[:space:]]|$)' "$autostart"; then
        return 1
    fi
    for app in "$parent/soundlive-ndi" "$parent/soundlive-ecrans"; do
        if [[ -x $app/lecteur-ndi-ubuntu && -r $app/lib/libndi.so.6 ]]; then
            printf '%s\n' "$app"
            return
        fi
    done
    return 1
}

install_os_maintenance() (
    local kit=$1 account rule
    account=$(id -un)
    if (( EUID == 0 )) || [[ ! $account =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]*$ ]]; then
        echo 'Lance l’activation sous le compte du lecteur, sans sudo.' >&2
        return 1
    fi
    if [[ ! -r "$kit/maintenance-os.sh" || ! -r "$kit/soundlive-ndi-maintenance.service" ]]; then
        echo 'Décompresse le kit SoundLive-NDI complet pour activer la maintenance.' >&2
        return 1
    fi
    rule=$(mktemp) || return
    trap 'rm -f -- "$rule"' EXIT
    printf '%s ALL=(root) NOPASSWD: /usr/local/sbin/soundlive-ndi-maintenance ""\n' "$account" >"$rule"
    sudo visudo -cf "$rule" &&
        sudo install -o root -g root -m 0755 "$kit/maintenance-os.sh" /usr/local/sbin/soundlive-ndi-maintenance &&
        sudo install -o root -g root -m 0644 "$kit/soundlive-ndi-maintenance.service" /etc/systemd/system/soundlive-ndi-maintenance@.service &&
        sudo systemctl daemon-reload &&
        sudo install -o root -g root -m 0440 "$rule" /etc/sudoers.d/soundlive-ndi-maintenance
)

prepare_remote_access() {
    local kit=$1 platform=$2
    bash "$kit/installer-ssh-linux.sh" configurer
    if [[ ${SOUNDLIVE_DEPLOY_REMOTE:-0} == 1 ]]; then
        SOUNDLIVE_REMOTE_DESKTOP_NONINTERACTIVE=1 bash "$kit/installer-bureau-distant-linux.sh" "$platform" ||
            echo 'Attention : installation du bureau distant incomplète. Relance installer-bureau-distant-linux.sh sur le lecteur.' >&2
    else
        bash "$kit/installer-bureau-distant-linux.sh" "$platform"
    fi
}

clean_reader_profiles() {
    local previous=$1 ethernet=$2 wifi=$3
    local -a nm=(sudo env LC_ALL=C nmcli)
    local profiles uuid kind name managed failed=0
    local uuid_pattern='^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
    [[ $ethernet =~ $uuid_pattern && ( -z $wifi || $wifi =~ $uuid_pattern ) ]] || return 1
    profiles=$("${nm[@]}" -t -e no -f UUID,TYPE,NAME connection show) || return 1
    while IFS=: read -r uuid kind name; do
        [[ $uuid =~ $uuid_pattern && $uuid != "$ethernet" && $uuid != "$wifi" ]] || continue
        managed=false
        if grep -Fqx -- "$uuid" <<< "$previous"; then
            managed=true
        elif [[ $kind == ethernet || $kind == 802-3-ethernet ]]; then
            if [[ $name == SOUNDLIVE-ETHERNET || $name =~ ^SoundLive\ installation\ [0-9a-f]{8}$ ]]; then
                managed=true
            fi
        elif [[ $kind == wifi || $kind == 802-11-wireless ]]; then
            if [[ $name == SOUNDLIVE-WIFI || $name =~ ^SoundLive\ configuration\ [0-9a-f]{8}$ ]]; then
                managed=true
            fi
        fi
        if $managed; then
            if "${nm[@]}" connection delete uuid "$uuid" >/dev/null; then
                printf 'Ancien profil SoundLive supprimé : %s (%s).\n' "$name" "$uuid"
            else
                printf 'Profil SoundLive à nettoyer manuellement : %s (%s).\n' "$name" "$uuid" >&2
                failed=1
            fi
        fi
    done <<< "$profiles"
    return "$failed"
}

install_pairing_launcher() {
    local kit=$1 app=$2 kind=$3 applications
    install -m 644 "$kit/raccorder-lecteur.py" "$kit/installation_lecteur.py" "$kit/installer-ssh-linux.sh" "$app/"
    printf '%s\n' "$kind" > "$app/type-recepteur"
    applications="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$applications"
    local escaped
    escaped=$(printf '%s' "$app" | sed 's/[\\`"$]/\\&/g; s/%/%%/g')
    cat > "$applications/soundlive-ndi-raccordement.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=SoundLive-NDI - Raccorder le lecteur
Exec=/usr/bin/python3 "$escaped/raccorder-lecteur.py"
Terminal=true
Categories=AudioVideo;
DESKTOP
}
