#!/bin/bash
# Installed root-owned by the SoundLive-NDI Linux reader installer.
set -euo pipefail
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
umask 022
(( EUID == 0 )) || exit 1

state=/var/lib/soundlive-ndi-maintenance
if (( $# == 0 )); then
    IFS= read -r -n 33 id
elif (( $# == 2 )) && [[ $1 == --run || $1 == --finish ]]; then
    id=$2
else
    exit 2
fi
[[ $id =~ ^[0-9a-f]{32}$ ]] || exit 2

if (( $# == 0 )); then
    exec /usr/bin/systemctl --no-block start "soundlive-ndi-maintenance@$id.service"
fi

boot=$(< /proc/sys/kernel/random/boot_id)
result="$state/$id"
record() {
    local temporary
    temporary=$(mktemp "$state/.result.XXXXXX")
    printf '%s %s\n' "$boot" "$1" >"$temporary"
    chmod 0644 "$temporary"
    mv -fT -- "$temporary" "$result"
}

if [[ $1 == --finish ]]; then
    if [[ ${SERVICE_RESULT:-success} != success && (! -e $result || $(< "$result") == "$boot running") ]]; then
        record failed
    fi
    exit 0
fi

[[ ! -e $result ]] || exit 0
exec 9>"$state/lock"
if ! flock -n 9; then
    record busy
    exit 0
fi
[[ ! -e $result ]] || exit 0
find "$state" -maxdepth 1 -type f -regextype posix-extended -regex '.*/[0-9a-f]{32}' -mtime +30 -delete
record running
export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l
if ! /usr/bin/apt-get -o APT::Update::Error-Mode=any -o Acquire::Retries=2 \
        -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update ||
   ! /usr/bin/apt-get -y --no-remove --with-new-pkgs \
        -o DPkg::Lock::Timeout=120 -o Dpkg::Options::=--force-confdef \
        -o Dpkg::Options::=--force-confold upgrade; then
    record failed
    exit 1
fi
record rebooting
sync -f "$result"
sleep 6
if ! /usr/bin/systemctl --no-block reboot; then
    record reboot_failed
    exit 1
fi
