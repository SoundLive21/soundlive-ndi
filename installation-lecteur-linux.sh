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
    [[ $ethernet =~ $uuid_pattern && $wifi =~ $uuid_pattern ]] || return 1
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
