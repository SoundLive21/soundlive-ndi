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
UFW_CHAIN = "ufw-user-forward"
FILTER_CHAIN = ("ip", "filter", "FORWARD")
SAFE_DEVICE = re.compile(r"[\w.-]{1,15}", re.ASCII)
TOOLS = {"ip": "/usr/sbin/ip", "nft": "/usr/sbin/nft",
         "ufw": "/usr/sbin/ufw", "iptables": "/usr/sbin/iptables",
         "iptables-nft": "/usr/sbin/iptables-nft"}
PRIVATE_LANS = tuple(map(IPv4Network, ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))


def run(tool, *args, data=None, check=True):
    result = subprocess.run([TOOLS[tool], *args], input=data, text=True, capture_output=True,
                            timeout=15, env={**os.environ, "LC_ALL": "C"})
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
    iifname "{lan}" oifname "{uplink}" ip saddr {network} counter accept
    iifname "{uplink}" oifname "{lan}" ip daddr {network} ct state established,related counter accept
    iifname "{lan}" drop
    oifname "{lan}" drop
  }}
  chain outbound {{
    type nat hook postrouting priority srcnat; policy accept;
    iifname "{lan}" oifname "{uplink}" ip saddr {network} counter masquerade
  }}
}}
'''
    return rules


def ufw_active():
    return (Path(TOOLS["ufw"]).is_file() and
            "Status: active" in run("ufw", "status").stdout.splitlines())


def forward_chains():
    entries = json.loads(run("nft", "-j", "list", "chains").stdout)["nftables"]
    chains = (entry.get("chain", {}) for entry in entries)
    return [chain for chain in chains if chain.get("hook") == "forward" and
            chain.get("family") in ("ip", "inet") and chain.get("table") != TABLE]


def default_forward_drops(chains):
    return any((chain["family"], chain["table"], chain["name"]) == FILTER_CHAIN and
               chain.get("policy") == "drop" for chain in chains)


def choose_firewall():
    if ufw_active():
        if not ufw_has_chain():
            raise RuntimeError("UFW est actif mais sa chaîne de routage IPv4 est introuvable.")
        return "ufw"
    if default_forward_drops(forward_chains()):
        if Path(TOOLS["iptables-nft"]).is_file():
            rules = run("iptables-nft", "-w", "5", "-S", FILTER_CHAIN[-1], check=False)
            if rules.returncode == 0 and "-P FORWARD DROP" in rules.stdout.splitlines():
                return "iptables-nft"
        return "nft"
    return None


def firewall_type(state):
    return state.get("firewall", "ufw" if state.get("ufw") else None)


def xtables_rules(state):
    lan, uplink, network = state["lan"], state["uplink"], state["network"]
    mark = ("-m", "comment", "--comment", TABLE, "-j", "ACCEPT")
    return (
        ("-i", lan, "-o", uplink, "-s", network, *mark),
        ("-i", uplink, "-o", lan, "-d", network,
         "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", *mark),
    )


def xtables_rule_exists(tool, chain, rule):
    result = run(tool, "-w", "5", "-C", chain, *rule, check=False)
    if result.returncode not in (0, 1):
        raise RuntimeError(result.stderr.strip() or f"Impossible de vérifier la règle {chain}.")
    return result.returncode == 0


def ufw_has_chain():
    chains = run("iptables", "-w", "5", "-S").stdout.splitlines()
    return f"-N {UFW_CHAIN}" in chains


def native_handles():
    result = run("nft", "-j", "-a", "list", "chain", *FILTER_CHAIN, check=False)
    if result.returncode:
        if "No such file or directory" in result.stderr:
            return []
        raise RuntimeError(result.stderr.strip() or "Impossible de lire la chaîne FORWARD.")
    rules = (entry.get("rule", {}) for entry in json.loads(result.stdout)["nftables"])
    return [rule["handle"] for rule in rules if rule.get("comment") == TABLE]


def configure_native_forward(state, enabled):
    chain = " ".join(FILTER_CHAIN)
    commands = [f"delete rule {chain} handle {handle}" for handle in native_handles()]
    if enabled:
        lan, uplink, network = state["lan"], state["uplink"], state["network"]
        commands.extend((
            f'add rule {chain} iifname "{lan}" oifname "{uplink}" '
            f'ip saddr {network} counter accept comment "{TABLE}"',
            f'add rule {chain} iifname "{uplink}" oifname "{lan}" '
            f'ip daddr {network} ct state established,related counter accept comment "{TABLE}"',
        ))
    if commands:
        run("nft", "-f", "-", data="\n".join(commands) + "\n")


def firewall_rules_present(state):
    mode = firewall_type(state)
    if mode is None:
        return True
    if mode == "nft":
        return len(native_handles()) == 2
    tool, chain = ("iptables", UFW_CHAIN) if mode == "ufw" else ("iptables-nft", FILTER_CHAIN[-1])
    return all(xtables_rule_exists(tool, chain, rule) for rule in xtables_rules(state))


def configure_firewall(state, enabled):
    mode = firewall_type(state)
    if mode is None:
        return
    if mode == "nft":
        configure_native_forward(state, enabled)
        return
    if mode == "ufw" and not ufw_has_chain():
        if enabled:
            raise RuntimeError("UFW est actif mais sa chaîne de routage IPv4 est introuvable.")
        return
    tool, chain = ("iptables", UFW_CHAIN) if mode == "ufw" else ("iptables-nft", FILTER_CHAIN[-1])
    # L'ajout en fin de chaîne respecte les verdicts des règles déjà présentes.
    for rule in xtables_rules(state):
        if xtables_rule_exists(tool, chain, rule) != enabled:
            run(tool, "-w", "5", "-A" if enabled else "-D", chain, *rule)


def disable():
    old = json.loads(STATE.read_text()) if STATE.exists() else None
    if old is None:
        return
    configure_firewall(old, False)
    if old["forward"] == "0" and FORWARD.read_text().strip() == "1":
        FORWARD.write_text("0\n")
    if run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0:
        run("nft", "delete", "table", "ip", TABLE)
    STATE.unlink(missing_ok=True)


def enable(gateway):
    lan, uplink, network = network_for(gateway)
    firewall = choose_firewall()
    if STATE.exists():
        old = json.loads(STATE.read_text())
        if (old["gateway"] == str(gateway) and old["uplink"] == uplink and old["lan"] == lan and
                old["network"] == str(network) and firewall_type(old) == firewall and
                run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0 and
                FORWARD.read_text().strip() == "1" and
                firewall_rules_present(old)):
            return
        disable()
    if run("nft", "list", "table", "ip", TABLE, check=False).returncode == 0:
        raise ValueError("Une table nftables porte déjà le nom SoundLive.")
    previous = FORWARD.read_text().strip()
    payload = {"gateway": str(gateway), "lan": lan, "uplink": uplink,
               "network": str(network), "forward": previous, "firewall": firewall}
    with STATE.open("x", encoding="utf-8") as output:
        json.dump(payload, output)
    STATE.chmod(0o644)
    try:
        run("nft", "-f", "-", data=rules_for(lan, uplink, network, previous))
        configure_firewall(payload, True)
        if FORWARD.read_text().strip() != "1":
            FORWARD.write_text("1\n")
        if FORWARD.read_text().strip() != "1":
            raise RuntimeError("Le noyau n’a pas activé net.ipv4.ip_forward.")
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        try:
            disable()
        except (OSError, RuntimeError, subprocess.SubprocessError) as rollback:
            raise RuntimeError(f"{error}\nRestauration incomplète : {rollback}") from error
        raise


def diagnostic(gateway):
    state = json.loads(STATE.read_text()) if STATE.exists() else None
    print(f"Droits d’administration : UID {os.geteuid()}")
    print(f"net.ipv4.ip_forward = {FORWARD.read_text().strip()}")
    if state:
        print(f"Passerelle configurée : {state['gateway']}")
        gateway = gateway or IPv4Address(state["gateway"])
    if gateway:
        lan, uplink, network = network_for(gateway)
        print(f"Réseau écrans : {network} via {lan} (passerelle {gateway})")
        print(f"Sortie Internet : {uplink}")
        for interface in (lan, uplink):
            setting = FORWARD.parent / "conf" / interface / "forwarding"
            print(f"Routage IPv4 sur {interface} : {setting.read_text().strip()}")
        if state and (state["lan"], state["uplink"], state["network"]) != (lan, uplink, str(network)):
            print("Les interfaces ou le sous-réseau ont changé. Désactive puis réactive le partage.")
    else:
        print("Sélectionne la carte du réseau écrans pour vérifier sa configuration.")
    active_ufw = ufw_active()
    chains = forward_chains()
    print("UFW : actif" if active_ufw else "UFW : inactif ou absent.")
    if state:
        mode = firewall_type(state)
        if mode and firewall_rules_present(state):
            chain = UFW_CHAIN if mode == "ufw" else "ip filter FORWARD"
            print(f"Pare-feu : règles temporaires SoundLive présentes dans {chain}.")
        elif mode or active_ufw or default_forward_drops(chains):
            print("Pare-feu : règles SoundLive manquantes. Désactive puis réactive le partage.")
    rules = run("nft", "list", "table", "ip", TABLE, check=False)
    if rules.returncode == 0:
        print("\nRègles et compteurs SoundLive :")
        print(rules.stdout.rstrip())
    else:
        print(f"Table NAT SoundLive indisponible : {rules.stderr.strip()}")
    for chain in chains:
        print(f"Autre chaîne de routage : {chain['family']} {chain['table']} "
              f"{chain['name']} (politique {chain.get('policy', '?')})")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("activer", "desactiver", "diagnostic"))
    parser.add_argument("gateway", type=IPv4Address, nargs="?")
    args = parser.parse_args()
    if args.operation == "activer" and not args.gateway:
        parser.error("Fournis l’IP locale de la régie pour activer le partage.")
    if args.operation == "desactiver" and args.gateway:
        parser.error("La désactivation utilise la passerelle déjà configurée.")
    if os.geteuid() != 0:
        parser.error("Les droits d’administration sont requis.")
    if not all(Path(TOOLS[tool]).is_file() for tool in ("ip", "nft")):
        parser.error("Installe iproute2 et nftables avant de régler le partage.")
    try:
        with open(LOCK, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args.operation == "activer":
                enable(args.gateway)
            elif args.operation == "diagnostic":
                diagnostic(args.gateway)
            else:
                disable()
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
        sys.exit(str(error))


if __name__ == "__main__":
    main()
