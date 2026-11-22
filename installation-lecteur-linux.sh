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
    bash "$kit/installer-bureau-distant-linux.sh" "$platform"
}

clean_reader_profiles() {
    local previous=$1 ethernet=$2 wifi=$3
    local -a nm=(sudo env LC_ALL=C nmcli)
    local active profiles uuid kind name managed
    local uuid_pattern='^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$'
    [[ $ethernet =~ $uuid_pattern && ( -z $wifi || $wifi =~ $uuid_pattern ) ]] || return 1
    active=$("${nm[@]}" -t -e no -f UUID connection show --active) || return 1
    profiles=$("${nm[@]}" -t -e no -f UUID,TYPE,NAME connection show) || return 1
    while IFS=: read -r uuid kind name; do
        [[ $uuid =~ $uuid_pattern && $uuid != "$ethernet" && $uuid != "$wifi" ]] || continue
        if grep -Fqx -- "$uuid" <<< "$active"; then continue; fi
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
        if $managed && ! "${nm[@]}" connection delete uuid "$uuid" >/dev/null; then
            printf 'Profil SoundLive à nettoyer manuellement : %s (%s).\n' "$name" "$uuid" >&2
        fi
    done <<< "$profiles"
}
