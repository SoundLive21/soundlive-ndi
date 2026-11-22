#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."

# Ne jamais modifier les interfaces du runner : vérification de syntaxe et
# simulation de la seule phase --verifier du préparateur.
for file in preparer-lecteur-linux.sh soundlive-ndi-network-static.sh \
  installer-client.sh installation-lecteur-linux.sh; do
  bash -n "$file"
done

mock=$(mktemp -d)
trap 'rm -rf -- "$mock"' EXIT
cat > "$mock/nmcli" <<'EOF'
#!/usr/bin/env bash
if [[ $* == '-t -e no -f DEVICE,TYPE,STATE device status' ]]; then
  printf 'eth0:ethernet:connected\n'
  exit 0
fi
echo "Unexpected nmcli test call: $*" >&2
exit 1
EOF
cat > "$mock/systemctl" <<'EOF'
#!/usr/bin/env bash
[[ $* == 'is-active --quiet NetworkManager.service' ]]
EOF
chmod +x "$mock/nmcli" "$mock/systemctl"
export PATH="$mock:$PATH"

opts=(--verifier --hote ndi-test-01 --ip 192.168.56.21/24
  --regie http://192.168.56.1:8765)
output=$(bash preparer-lecteur-linux.sh ubuntu "${opts[@]}")
grep -q 'Wi-Fi non configuré' <<< "$output"
grep -q 'Paramètres validés' <<< "$output"
echo 'OK: Linux/Ubuntu Ethernet seul avec aucune carte Wi-Fi.'

if bash preparer-lecteur-linux.sh ubuntu "${opts[@]}" --wifi Ecrans 2>"$mock/error"; then
  echo 'ERREUR: le mode Wi-Fi aurait dû échouer sans adaptateur Wi-Fi.' >&2
  exit 1
fi
grep -q 'Aucune interface wifi' "$mock/error"
echo 'OK: Linux/Ubuntu avec SSID signale correctement la carte Wi-Fi absente.'

if bash preparer-lecteur-linux.sh ubuntu "${opts[@]}" --interface-wifi wlan0 2>"$mock/error"; then
  echo 'ERREUR: interface Wi-Fi sans SSID aurait dû être refusée.' >&2
  exit 1
fi
grep -q 'Sans SSID' "$mock/error"
echo 'OK: interface Wi-Fi sans SSID correctement refusée.'
