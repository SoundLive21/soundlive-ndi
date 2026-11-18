#!/usr/bin/env python3
"""Turn IPv4 NAT on and off for the display LAN of a Linux régie."""
import argparse
import fcntl
from ipaddress import IPv4Address, IPv4Interface, IPv4Network
import json
import os
from pathlib import Path
import re
import subprocess
import sys

STATE = Path("/run/soundlive-ndi-internet.json")
LOCK = "/run/soundlive-ndi-internet.lock"
FORWARD = Path("/proc/sys/net/ipv4/ip_forward")
TABLE = "soundlive_ndi_internet"
SAFE_DEVICE = re.compile(r"[\w.-]{1,15}", re.ASCII)
TOOLS = {"ip": "/usr/sbin/ip", "nft": "/usr/sbin/nft"}
PRIVATE_LANS = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def run(tool, *args, data=None, check=True):
    result = subprocess.run([TOOLS[tool], *args], input=data, text=True, capture_output=True,
                            timeout=15)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"Commande {tool} en échec.")
    return result


def network_for(gateway):
    adapters = json.loads(run("ip", "-j", "-4", "address", "show", "up").stdout)
    match = [(adapter["ifname"], address) for adapter in adapters
             for address in adapter.get("addr_info", []) if address.get("local") == str(gateway)]
    if len(match) != 1:
        raise ValueError("Choisis une IPv4 active de la carte du réseau écrans.")
    lan, address = match[0]
    network = IPv4Interface(f"{gateway}/{address['prefixlen']}").network
    routes = json.loads(run("ip", "-j", "-4", "route", "get", "1.1.1.1").stdout)
    uplink = routes[0].get("dev") if routes else None
    if not uplink or uplink == lan or str(gateway) == routes[0].get("prefsrc"):
        raise ValueError("Les réseaux écrans et Internet doivent être sur deux interfaces différentes.")
    if not SAFE_DEVICE.fullmatch(lan) or not SAFE_DEVICE.fullmatch(uplink):
        raise ValueError("Nom d’interface Linux non pris en charge.")
    if (not any(network.subnet_of(private) for private in PRIVATE_LANS) or network.prefixlen > 30 or
            gateway in (network.network_address, network.broadcast_address)):
        raise ValueError("Utilise un sous-réseau IPv4 privé dédié aux écrans.")
    for adapter in adapters:
        if adapter["ifname"] == lan:
            continue
        for other in adapter.get("addr_info", []):
            if network.overlaps(IPv4Interface(f"{other['local']}/{other['prefixlen']}").network):
                raise ValueError("Le sous-réseau écrans recouvre celui d’une autre interface.")
    return lan, uplink, network


def rules_for(lan, uplink, network, previous):
    rules = f'''
table ip {TABLE} {{
  chain forwarding {{
    type filter hook forward priority filter; policy {"drop" if previous == "0" else "accept"};
    iifname "{lan}" oifname "{uplink}" ip saddr {network} accept
    iifname "{uplink}" oifname "{lan}" ip daddr {network} ct state established,related accept
    iifname "{lan}" drop
    oifname "{lan}" drop
  }}
  chain outbound {{
    type nat hook postrouting priority srcnat; policy accept;
    iifname "{lan}" oifname "{uplink}" ip saddr {network} masquerade
  }}
}}
'''
    return rules


def disable():
    old = json.loads(STATE.read_text()) if STATE.exists() else None
    if old is None:
        return
    if old["forward"] == "0" and FORWARD.read_text().strip() == "1":
        FORWARD.write_text("0\n")
    if run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0:
        run("nft", "delete", "table", "ip", TABLE)
    STATE.unlink(missing_ok=True)


def enable(gateway):
    lan, uplink, network = network_for(gateway)
    if STATE.exists():
        old = json.loads(STATE.read_text())
        if old["gateway"] == str(gateway) and old["uplink"] == uplink and old["lan"] == lan and (
                run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0 and
                FORWARD.read_text().strip() == "1"):
            return
        disable()
    if run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0:
        raise ValueError("Une table nftables porte déjà le nom SoundLive.")
    previous = FORWARD.read_text().strip()
    payload = {"gateway": str(gateway), "lan": lan, "uplink": uplink,
               "network": str(network), "forward": previous}
    with STATE.open("x", encoding="utf-8") as output:
        json.dump(payload, output)
    STATE.chmod(0o644)
    try:
        run("nft", "-f", "-", data=rules_for(lan, uplink, network, previous))
        if previous != "1":
            FORWARD.write_text("1\n")
    except (OSError, RuntimeError, subprocess.SubprocessError):
        disable()
        raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("activer", "desactiver"))
    parser.add_argument("gateway", type=IPv4Address, nargs="?")
    args = parser.parse_args()
    if (args.operation == "activer") != bool(args.gateway):
        parser.error("Fournis l’IP locale de la régie uniquement pour activer le partage.")
    if os.geteuid() != 0:
        parser.error("Les droits d’administration sont requis.")
    if not all(Path(path).is_file() for path in TOOLS.values()):
        parser.error("Installe iproute2 et nftables avant de régler le partage.")
    try:
        with open(LOCK, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.operation == "activer":
                enable(args.gateway)
            else:
                disable()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
