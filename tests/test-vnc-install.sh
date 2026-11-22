#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
bash -n installer-bureau-distant-linux.sh

work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/bin" "$work/home"
cat > "$work/bin/sudo" <<'MOCK'
#!/usr/bin/env bash
[[ $* == '-v' ]]
MOCK
cat > "$work/bin/dpkg-query" <<'MOCK'
#!/usr/bin/env bash
printf 'install ok installed\n'
MOCK
cat > "$work/bin/tigervncpasswd" <<'MOCK'
#!/usr/bin/env bash
printf 'test-mock-password\n' > "$1"
MOCK
chmod +x "$work/bin/"*
export HOME="$work/home"
export PATH="$work/bin:$PATH"
export XDG_CONFIG_HOME="$HOME/.config"

export XDG_SESSION_TYPE=wayland XDG_CURRENT_DESKTOP='ubuntu:GNOME'
result=$(bash installer-bureau-distant-linux.sh ubuntu)
grep -q 'TigerVNC Server X11 ne peut pas partager' <<< "$result"
grep -q 'MSRDP' <<< "$result"
[[ ! -e "$HOME/.config/autostart/soundlive-vnc.desktop" ]]
echo 'OK: Wayland GNOME => RDP, sans faux serveur VNC.'

export XDG_SESSION_TYPE=x11 XDG_CURRENT_DESKTOP=XFCE
unset DISPLAY || true
result=$(bash installer-bureau-distant-linux.sh ubuntu)
grep -q 'prochaine ouverture de session' <<< "$result"
test -f "$HOME/.vnc/passwd"
test -x "$HOME/.local/bin/soundlive-vnc-x11"
grep -q -- '-rfbport 5900' "$HOME/.local/bin/soundlive-vnc-x11"
grep -q 'Exec=' "$HOME/.config/autostart/soundlive-vnc.desktop"
echo 'OK: X11 => mot de passe et autostart TigerVNC, aucun démarrage intempestif.'
