# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Lanceur graphique de la régie SoundLive-NDI."""
from base64 import b64encode
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from ipaddress import IPv4Address
import json
import os
from pathlib import Path
from queue import Empty, Queue
import secrets
import shutil
import subprocess
import sys
import threading
from time import monotonic
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk
import traceback
import webbrowser

from commun import APP_NAME, COPYRIGHT
from regie_dhcp import Service as DhcpService, bind_listener, candidates
from regie_reseau import INTERNET_OPERATIONS, NetworkAuthorization


def relaunch(root):
    if sys.platform != "win32":
        subprocess.Popen([sys.executable, "-B", str(root / "regie-windows.py")], cwd=root)
        return

    for filename in ("SoundLive-NDI.exe", "SoundLive-Regie.exe"):
        executable = root / filename
        if executable.is_file():
            subprocess.Popen([str(executable)], cwd=root)
            return
    raise FileNotFoundError("Le lanceur SoundLive-NDI.exe est introuvable dans le dossier de la régie.")


def qr_bitmap(modules):
    scale = 4
    width = (len(modules) + 8) * scale
    white = b"\xff" * (3 * scale)
    black = b"\0" * (3 * scale)
    border = b"\xff" * (width * 3 * 4 * scale)
    rows = [(white * 4 + b"".join(black if dark == "1" else white for dark in row) + white * 4) * scale
            for row in modules]
    return f"P6\n{width} {width}\n255\n".encode("ascii") + border + b"".join(rows) + border


def internet_registry(section, field):
    import winreg
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\SoundLive\{section}",
                        access=winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
        return json.loads(winreg.QueryValueEx(key, field)[0])


def internet_state():
    try:
        if sys.platform == "win32":
            return internet_registry("Internet", "Settings")
        return json.loads(Path("/run/soundlive-ndi-internet.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def internet_worker(path, operation, gateway, request):
    job = b64encode(json.dumps(dict(path=str(path), action=operation, gateway=gateway,
                                    request=request)).encode("utf-8")).decode("ascii")
    # Keep the script path and arguments out of the elevated process command-line parser.
    return "$jobData = '" + job + "'\n" + r"""
$ErrorActionPreference = 'Stop'
$job = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($jobData)) | ConvertFrom-Json
$lines = [System.Collections.Generic.List[string]]::new()
$code = 0
try {
    $parameters = @{ Action = $job.action }
    if ($job.gateway) { $parameters.Gateway = $job.gateway }
    $LASTEXITCODE = 0
    & $job.path @parameters *>&1 | ForEach-Object { $lines.Add($_.ToString()) }
    $code = $LASTEXITCODE
} catch {
    $lines.Add($_.ToString())
    if ($_.FullyQualifiedErrorId) { $lines.Add("Code PowerShell : $($_.FullyQualifiedErrorId)") }
    if ($_.InvocationInfo.PositionMessage) { $lines.Add($_.InvocationInfo.PositionMessage.Trim()) }
    $code = 1
}
if (-not $lines.Count) { $lines.Add("Script terminé avec le code $code.") }
$report = @{ request=$job.request; success=($code -eq 0); output=($lines -join [Environment]::NewLine) }
try {
    $hive = [Microsoft.Win32.RegistryKey]::OpenBaseKey('LocalMachine', 'Registry64')
    try {
        $key = $hive.CreateSubKey('SOFTWARE\SoundLive\InternetDiagnostic')
        try { $key.SetValue('LastReport', ($report | ConvertTo-Json -Compress)) }
        finally { $key.Dispose() }
    } finally { $hive.Dispose() }
} catch {
    $message = "Enregistrement du diagnostic impossible : $($_.Exception.Message)" +
        [Environment]::NewLine + [Environment]::NewLine + $report.output
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($message, 'Diagnostic SoundLive-NDI') | Out-Null
    exit 3
}
exit $code
"""


def change_internet(root, operation, gateway=None):
    args = [operation, str(IPv4Address(gateway))] if gateway else [operation]
    if sys.platform == "win32":
        system = Path(os.environ["SystemRoot"])
        executable = "WindowsPowerShell/v1.0/powershell.exe"
        powershell = str(system / "System32" / executable)
        sysnative = system / "Sysnative" / executable
        helper = root / "regie-internet-windows.ps1"
        if not helper.is_file():
            raise RuntimeError(f"Script du partage Internet introuvable : {helper}")
        request = secrets.token_hex(16)
        worker = internet_worker(helper, operation, args[1] if gateway else None, request)
        encoded = b64encode(worker.encode("utf-16-le")).decode("ascii")
        script = ("$ErrorActionPreference = 'Stop'; "
                  "try { [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false) } catch {}; "
                  "try { $process = Start-Process -FilePath $env:SOUNDLIVE_POWERSHELL "
                  "-ArgumentList $env:SOUNDLIVE_INTERNET_ARGS -Verb RunAs -Wait -PassThru -WindowStyle Hidden; "
                  "exit $process.ExitCode } catch { "
                  "$cause = $_.Exception; while ($cause) { "
                  "if ($cause -is [ComponentModel.Win32Exception] -and $cause.NativeErrorCode -eq 1223) { exit 1223 }; "
                  "$cause = $cause.InnerException }; [Console]::Error.WriteLine($_.Exception.Message); exit 2 }")
        run = subprocess.run([str(sysnative) if sysnative.is_file() else powershell,
                              "-NoProfile", "-NonInteractive", "-Command", script],
                             capture_output=True, encoding="utf-8", errors="replace",
                             creationflags=subprocess.CREATE_NO_WINDOW,
                             env={**os.environ, "SOUNDLIVE_POWERSHELL": powershell,
                                  "SOUNDLIVE_INTERNET_ARGS":
                                      f"-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand {encoded}"})
        if run.returncode == 1223:
            raise RuntimeError("La demande d’autorisation administrateur a été annulée.")
        try:
            report = internet_registry("InternetDiagnostic", "LastReport")
        except (OSError, ValueError):
            report = None
        if isinstance(report, dict) and report.get("request") == request and isinstance(report.get("output"), str):
            if run.returncode or not report.get("success"):
                raise RuntimeError(report["output"])
            return report["output"] if operation == "diagnostic" else None
        if run.returncode == 3:
            raise RuntimeError("Windows n’a pas pu enregistrer le rapport. Le résultat est dans la fenêtre « Diagnostic SoundLive-NDI ».")
        raise RuntimeError(run.stderr.strip() or
                           f"Le processus PowerShell élevé a quitté avant de transmettre son rapport (code {run.returncode}). "
                           "Relance le diagnostic depuis PowerShell en administrateur (voir README.md).")
    else:
        command = ["/usr/bin/python3", "-I", str(root / "regie-internet.py"), *args]
        elevate = os.geteuid() != 0
        if elevate:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise RuntimeError("Installe pkexec et nftables, ou lance le script regie-internet.py avec sudo.")
            command.insert(0, pkexec)
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode:
            details = "\n".join(part.strip() for part in (run.stdout, run.stderr) if part.strip())
            if elevate and run.returncode in (126, 127):
                raise RuntimeError("L’autorisation administrateur Ubuntu n’a pas été obtenue."
                                   + (f"\n{details}" if details else ""))
            raise RuntimeError(details or f"Le script de partage Internet a échoué (code {run.returncode}).")
        return run.stdout.strip() if operation == "diagnostic" else None


class NetworkControl:
    def __init__(self, tableau):
        self.root = tableau.ROOT
        self.interfaces = tableau.dhcp_interfaces
        self.dhcp = DhcpService(self.root / ".soundlive-dhcp", self.interfaces)
        self.authorization = NetworkAuthorization()
        self.lock = threading.Lock()

    def status(self):
        dhcp = self.dhcp.status()
        try:
            rows = self.interfaces()
            dhcp["interfaces"] = candidates(rows)
            interfaces = [{"label": row["label"], "ip": row["ip"]} for row in rows]
            interface_error = ""
        except (RuntimeError, OSError, ValueError) as error:
            dhcp["interfaces"] = interfaces = []
            interface_error = str(error)
        return {"available": True, "authorized": self.authorization.ready, "interfaces": interfaces,
                "internet": internet_state().get("gateway"), "dhcp": dhcp, "interface_error": interface_error}

    def open_dhcp_socket(self, settings):
        interface = settings.adapter["label"]
        try:
            return bind_listener(interface, settings.adapter["index"], str(settings.server))
        except PermissionError:
            if not sys.platform.startswith("linux"):
                raise
        return self.authorization.request("dhcp", interface=interface)

    def change(self, kind, operation, data=None, *, web=False):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError("Une modification réseau est déjà en cours.")
        try:
            authorized = self.authorization.ready
            if web and not authorized:
                raise PermissionError("Autorise la gestion réseau web dans le lanceur de la régie.")
            if kind == "internet" and operation in INTERNET_OPERATIONS:
                gateway = data
                if gateway is not None and (not isinstance(gateway, str) or not gateway.strip()):
                    raise ValueError("Adresse IPv4 invalide.")
                if authorized:
                    return self.authorization.request("internet", operation=operation, gateway=gateway)
                return change_internet(self.root, operation, gateway)
            if kind == "dhcp" and operation == "stop":
                self.dhcp.stop()
                return None
            if kind == "dhcp" and operation == "start":
                if not isinstance(data, dict) or data.get("dedicated") is not True:
                    raise ValueError("Confirme qu’aucun autre serveur DHCP n’est actif sur le réseau écrans.")
                self.dhcp.start(data, listener_factory=self.open_dhcp_socket if authorized else None)
                return None
            raise ValueError("Action réseau inconnue.")
        finally:
            self.lock.release()

    def close(self):
        with self.lock:
            self.authorization.revoke(closing=True)
            self.dhcp.stop(disable=False)


class DhcpPanel:
    def __init__(self, parent, network, tasks):
        self.network = network
        self.service = network.dhcp
        self.tasks = tasks
        self.pending = None
        self.adapters = []
        self.leases = None
        self.state = None
        self.dialog = dialog = tk.Toplevel(parent)
        dialog.title("Serveur DHCP · Réseau écrans")
        dialog.transient(parent)
        dialog.protocol("WM_DELETE_WINDOW", dialog.withdraw)
        pane = ttk.Frame(dialog, padding=16)
        pane.pack(fill="both", expand=True)
        pane.columnconfigure(1, weight=1)
        pane.rowconfigure(10, weight=1)

        self.status = tk.StringVar(dialog)
        self.error = tk.StringVar(dialog)
        ttk.Label(pane, textvariable=self.status, wraplength=560).grid(row=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(pane, text="Interface du réseau écrans").grid(row=1, column=0, sticky="w", padx=(0, 10))
        self.selector = ttk.Combobox(pane, width=45, state="disabled")
        self.selector.grid(row=1, column=1, sticky="ew")
        self.reload = ttk.Button(pane, text="Actualiser", command=self.load_adapters)
        self.reload.grid(row=1, column=2, padx=(8, 0))
        self.fields = {}
        self.entries = []
        for row, (name, label, default) in enumerate((
            ("first", "Début de plage IPv4", ""), ("last", "Fin de plage IPv4", ""),
            ("minutes", "Durée du bail (minutes)", "480"), ("router", "Passerelle (facultative)", ""),
            ("dns1", "DNS principal (facultatif)", ""), ("dns2", "DNS secondaire (facultatif)", ""),
        ), start=2):
            self.fields[name] = field = tk.StringVar(dialog, value=default)
            ttk.Label(pane, text=label).grid(row=row, column=0, sticky="w", pady=2)
            entry = ttk.Entry(pane, textvariable=field, width=23)
            entry.grid(row=row, column=1, sticky="w", pady=2)
            self.entries.append(entry)
        self.dedicated = tk.BooleanVar(dialog)
        ttk.Label(pane, text="Choisis une plage libre hors des adresses fixes.").grid(
            row=8, columnspan=3, sticky="w", pady=(8, 0))
        self.confirm = ttk.Checkbutton(pane, variable=self.dedicated, command=self.controls,
            text="Ce réseau écrans n’a pas d’autre serveur DHCP actif.")
        self.confirm.grid(row=9, columnspan=3, sticky="w", pady=(4, 5))

        leases = ttk.LabelFrame(pane, text="Baux en cours", padding=6)
        leases.grid(row=10, columnspan=3, sticky="nsew", pady=(4, 8))
        leases.columnconfigure(0, weight=1)
        leases.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(leases, columns=("ip", "hostname", "mac", "expires"), show="headings", height=4)
        for name, label, width in (("ip", "Adresse IP", 115), ("hostname", "Nom", 145),
                                 ("mac", "Adresse MAC", 145), ("expires", "Expiration", 150)):
            self.table.heading(name, text=label)
            self.table.column(name, width=width, stretch=name == "hostname")
        self.table.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(leases, orient="vertical", command=self.table.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scroll.set)
        ttk.Label(pane, textvariable=self.error, wraplength=560).grid(row=11, columnspan=3, sticky="w")

        actions = ttk.Frame(pane)
        actions.grid(row=12, columnspan=3, sticky="ew", pady=(8, 0))
        self.start = ttk.Button(actions, text="Démarrer le DHCP", command=self.start_service)
        self.stop = ttk.Button(actions, text="Arrêter le DHCP", command=self.stop_service)
        self.start.pack(side="left", padx=(0, 8))
        self.stop.pack(side="left")
        ttk.Button(actions, text="Fermer", command=dialog.withdraw).pack(side="right")
        self.selector.bind("<<ComboboxSelected>>", lambda _event: self.controls())
        self.load_adapters()
        self.poll()

    def show(self):
        self.dialog.deiconify()
        self.dialog.lift()

    def controls(self):
        running = self.state and self.state["running"]
        busy = self.pending is not None
        editable = not (busy or running)
        self.selector.configure(state="readonly" if editable else "disabled")
        for field in (*self.entries, self.confirm):
            field.configure(state="normal" if editable else "disabled")
        self.reload.configure(state="disabled" if busy else "normal")
        self.start.configure(state="normal" if editable and self.selector.current() >= 0
                             and self.dedicated.get() else "disabled")
        enabled = self.state and self.state["config"].get("enabled")
        self.stop.configure(state="normal" if not busy and (running or enabled) else "disabled")

    def run(self, message, operation, *args, **kwargs):
        if self.pending is not None:
            return
        self.error.set("")
        self.status.set(message)
        self.pending = self.tasks.submit(operation, *args, **kwargs)
        self.controls()

    def load_adapters(self):
        self.run("Recherche des interfaces réseau…", self.service.status, include_interfaces=True)

    def start_service(self):
        index = self.selector.current()
        if index < 0 or not self.dedicated.get():
            return
        values = {name: field.get().strip() for name, field in self.fields.items()}
        try:
            values["minutes"] = int(values["minutes"])
        except ValueError:
            self.error.set("Indique une durée de bail en minutes entre 1 et 10 080.")
            return
        dns = [value for value in (values.pop("dns1"), values.pop("dns2")) if value]
        row = self.adapters[index]
        data = dict(values, adapter=row["id"], ip=row["ip"], dns=dns, dedicated=True)
        self.run("Démarrage du DHCP…", self.network.change, "dhcp", "start", data)

    def stop_service(self):
        self.run("Arrêt du DHCP…", self.network.change, "dhcp", "stop")

    def configure_adapters(self, status):
        index = self.selector.current()
        previous = self.adapters[index] if index >= 0 else {}
        config = status["config"]
        selected = (previous.get("id", config.get("adapter")), previous.get("ip", config.get("ip")))
        self.adapters = status["interfaces"]
        self.selector.configure(values=[f"{row['label']} · {row['ip']} · {row['network']}" for row in self.adapters])
        index = next((i for i, row in enumerate(self.adapters) if (row["id"], row["ip"]) == selected),
                     0 if len(self.adapters) == 1 else -1)
        if index >= 0:
            self.selector.current(index)
        else:
            self.selector.set("")

    def poll(self):
        if self.pending is not None and not self.pending.done():
            self.dialog.after(150, self.poll)
            return
        if self.pending is not None:
            try:
                status = self.pending.result()
                if status is not None:
                    self.configure_adapters(status)
            except (OSError, RuntimeError, ValueError) as error:
                self.error.set(str(error))
            self.pending = None
        status = self.service.status()
        config = status["config"]
        if self.state is None or config != self.state["config"]:
            for name in ("first", "last", "minutes", "router"):
                if name in config:
                    self.fields[name].set(config[name])
            dns = config.get("dns", [])
            for index, name in enumerate(("dns1", "dns2")):
                self.fields[name].set(dns[index] if index < len(dns) else "")
            match = next((index for index, row in enumerate(self.adapters)
                          if (row["id"], row["ip"]) == (config.get("adapter"), config.get("ip"))), None)
            if match is not None:
                self.selector.current(match)
        self.state = status
        label = f"DHCP actif · {config.get('ip')} · UDP 67" if status["running"] else "DHCP arrêté"
        if not self.adapters and not status["running"]:
            label += " · Aucune interface dédiée à IPv4 fixe détectée."
        self.status.set(label + ("\n" + status["error"] if status["error"] else ""))
        if self.leases != status["leases"]:
            self.leases = status["leases"]
            children = self.table.get_children()
            if children:
                self.table.delete(*children)
            for row in self.leases:
                expiry = datetime.fromtimestamp(row["expires"]).strftime("%d/%m/%Y %H:%M:%S")
                self.table.insert("", "end", values=(row["ip"], row["hostname"], row["mac"], expiry))
        self.controls()
        self.dialog.after(1200, self.poll)


class Launcher:
    def __init__(self, window, tableau, access):
        self.window = window
        self.tableau = tableau
        self.access = access
        self.publication_status = tableau.PublicationStatus(
            tableau.ROOT / "source-publications.json", tableau.SOFTWARE_VERSION)
        self.regie = None
        self.busy = False
        self.closing = False
        self.results = Queue()
        self.interfaces = []
        self.remote = tk.BooleanVar(value=(tableau.REMOTE_ACCESS / "acces.json").is_file())
        self.visible = tk.BooleanVar()
        self.password = tk.StringVar()
        self.interface = tk.StringVar()
        self.status = tk.StringVar(value="Prêt à démarrer")
        self.remote_address = tk.StringVar(value="—")
        self.connection_status = tk.StringVar(value="Accès distant arrêté")
        self.internet = None
        self.network = NetworkControl(tableau)
        self.dhcp_panel = None
        self.dhcp_tasks = ThreadPoolExecutor(max_workers=1)
        self.dhcp_shutdown = None

        window.title(f"{APP_NAME} · Régie {tableau.SOFTWARE_VERSION}")
        window.minsize(620, 510)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)
        viewport = tk.Canvas(window, highlightthickness=0, background=ttk.Style(window).lookup("TFrame", "background"))
        viewport.grid(row=0, column=0, sticky="nsew")
        page_scrollbar = ttk.Scrollbar(window, orient="vertical", command=viewport.yview)
        page_scrollbar.grid(row=0, column=1, sticky="ns")
        viewport.configure(yscrollcommand=page_scrollbar.set)
        panel = ttk.Frame(viewport, padding=(22, 16))
        panel_id = viewport.create_window(0, 0, anchor="nw", window=panel)
        panel.bind("<Configure>", lambda _event: viewport.configure(scrollregion=viewport.bbox(panel_id)))
        viewport.bind("<Configure>", lambda event: viewport.itemconfigure(panel_id, width=event.width))
        panel.columnconfigure(0, weight=1)
        ttk.Label(panel, text=f"{APP_NAME} · Régie", font=("Segoe UI", 19, "bold")).grid(row=0, sticky="w")
        ttk.Label(panel, textvariable=self.status).grid(row=1, sticky="w", pady=(2, 14))

        setup = ttk.LabelFrame(panel, text="Accès distant HTTPS", padding=10)
        setup.grid(row=2, sticky="ew")
        setup.columnconfigure(1, weight=1)
        self.enabled = ttk.Checkbutton(setup, text="Activer l’accès distant", variable=self.remote, command=self.controls)
        self.enabled.grid(row=0, columnspan=3, sticky="w")
        ttk.Label(setup, text="Interface réseau").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        self.selector = ttk.Combobox(setup, textvariable=self.interface, state="readonly")
        self.selector.grid(row=1, column=1, sticky="ew")
        self.reload = ttk.Button(setup, text="Actualiser", command=self.load_interfaces)
        self.reload.grid(row=1, column=2, padx=(8, 0))
        ttk.Label(setup, text="Mot de passe").grid(row=2, column=0, sticky="w", pady=6)
        self.entry = ttk.Entry(setup, textvariable=self.password, show="•")
        self.entry.grid(row=2, column=1, sticky="ew")
        self.reveal = ttk.Checkbutton(setup, text="Afficher", variable=self.visible, command=self.reveal_password)
        self.reveal.grid(row=2, column=2, sticky="w", padx=(8, 0))
        ttk.Label(setup, text="Compte regie. À IP identique, laisse le champ vide pour conserver le mot de passe.",
                  wraplength=550).grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(2, 0))
        setup_actions = ttk.Frame(setup)
        setup_actions.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        self.generate = ttk.Button(setup_actions, text="Générer un nouveau mot de passe", command=self.generate_password)
        self.generate.pack(side="left", padx=(0, 8))
        self.acme_button = ttk.Button(setup_actions, text="Certificat ACME",
                                      command=self.configure_acme_dialog)
        self.acme_button.pack(side="left", padx=(0, 10))
        self.acme_summary = tk.StringVar(value="Aucun certificat ACME")
        self.acme_summary_label = ttk.Label(setup_actions, textvariable=self.acme_summary,
                                            justify="left", wraplength=300)
        self.acme_summary_label.pack(side="left", fill="x", expand=True)
        setup_actions.bind("<Configure>", lambda event: self.acme_summary_label.configure(
            wraplength=max(100, event.width - self.generate.winfo_reqwidth()
                           - self.acme_button.winfo_reqwidth() - 30)))
        self.acme_next_refresh = 0.0

        actions = ttk.Frame(panel)
        actions.grid(row=3, sticky="ew", pady=12)
        self.start_button = ttk.Button(actions, text="Démarrer la régie", command=self.start)
        self.stop_button = ttk.Button(actions, text="Arrêter la régie", command=self.stop)
        self.open_button = ttk.Button(actions, text="Ouvrir la console", command=self.open_dashboard)
        self.release_button = ttk.Button(actions, text="Versions publiées", command=self.publications)
        for button in (self.start_button, self.stop_button, self.open_button):
            button.pack(side="left", padx=(0, 8))
        self.release_button.pack(side="left")
        address = ttk.Frame(panel)
        address.grid(row=4, sticky="ew", pady=(0, 10))
        ttk.Label(address, textvariable=self.remote_address).pack(side="left")
        self.copy_button = ttk.Button(address, text="Copier l’adresse", command=self.copy_address)
        self.copy_button.pack(side="right")
        self.certificate_button = ttk.Button(address, text="Dossier du certificat", command=self.open_certificate_folder)
        self.certificate_button.pack(side="right", padx=(0, 8))
        connections = ttk.LabelFrame(panel, text="Connexions à distance", padding=10)
        connections.grid(row=5, sticky="ew")
        connections.columnconfigure(0, weight=1)
        connections.rowconfigure(1, weight=1)
        ttk.Label(connections, textvariable=self.connection_status).grid(sticky="w", pady=(0, 6))
        self.list = ttk.Treeview(connections, columns=("ip", "seen", "browser"), show="headings", height=4,
                                 selectmode="browse")
        for key, label, width in (("ip", "Adresse IP", 130), ("seen", "Dernière activité", 120), ("browser", "Navigateur", 260)):
            self.list.heading(key, text=label)
            self.list.column(key, width=width, stretch=key == "browser")
        self.list.grid(row=1, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(connections, orient="vertical", command=self.list.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.list.configure(yscrollcommand=scrollbar.set)
        moderation = ttk.Frame(connections)
        moderation.grid(row=2, column=0, columnspan=2, sticky="w", pady=(8, 0))
        self.disconnect_button = ttk.Button(moderation, text="Déconnecter", command=self.disconnect_remote)
        self.block_button = ttk.Button(moderation, text="Bloquer l’IP", command=self.block_remote)
        self.blocked_button = ttk.Button(moderation, text="Adresses bloquées", command=self.show_blocked)
        for button in (self.disconnect_button, self.block_button, self.blocked_button):
            button.pack(side="left", padx=(0, 8))

        self.qr_panel = ttk.LabelFrame(panel, text="QR code · Gestion des écrans", padding=8)
        self.qr_panel.grid(row=6, sticky="ew", pady=(10, 0))
        self.qr_panel.grid_remove()
        self.qr = ttk.Label(self.qr_panel)
        self.qr.pack(side="left", padx=(0, 12))
        ttk.Label(self.qr_panel, text="Scanne ce code depuis un appareil connecté au réseau de la régie.",
                  wraplength=290).pack(side="left", fill="x", expand=True)

        share = ttk.Frame(panel)
        share.grid(row=7, sticky="ew", pady=(10, 0))
        ttk.Button(share, text="Partage Internet", command=self.internet_dialog).pack(side="left")
        ttk.Button(share, text="Serveur DHCP", command=self.open_dhcp).pack(side="left", padx=(8, 0))
        self.network_button = ttk.Button(share, text="Autoriser la gestion réseau web", command=self.authorize_network)
        self.network_button.pack(side="left", padx=(8, 0))
        backups = ttk.Frame(panel)
        backups.grid(row=8, sticky="ew", pady=(10, 0))
        self.backup_button = ttk.Button(backups, text="Sauvegarder la régie", command=self.backup_dialog)
        self.restore_button = ttk.Button(backups, text="Restaurer la régie", command=self.restore_dialog)
        self.backup_button.pack(side="left", padx=(0, 8))
        self.restore_button.pack(side="left")
        ttk.Label(panel, text=COPYRIGHT).grid(row=9, sticky="w", pady=(8, 0))
        window.update_idletasks()
        viewport.configure(width=min(panel.winfo_reqwidth(), window.winfo_screenwidth() * 9 // 10),
                           height=min(panel.winfo_reqheight(), window.winfo_screenheight() * 4 // 5))
        self.list.bind("<<TreeviewSelect>>", lambda _event: self.moderation_controls())
        window.protocol("WM_DELETE_WINDOW", self.close)
        if not tableau.DATA.exists():
            self.status.set("Aucun écran associé dans ce dossier de régie")
        self.load_interfaces()
        self.dhcp_tasks.submit(self.network.dhcp.restore)
        self.tick()

    def load_interfaces(self):
        current = self.interface.get()
        try:
            settings = json.loads((self.tableau.REMOTE_ACCESS / "acces.json").read_text(encoding="utf-8"))
            configured_ip = settings.get("ip")
        except (OSError, ValueError, AttributeError):
            configured_ip = None
        self.interfaces = [(f"{row['label']} — {row['ip']}", row["ip"])
                           for row in self.tableau.local_addresses() if self.access.is_local_ip(row["ip"])]
        self.selector.configure(values=[label for label, _ in self.interfaces])
        labels = dict(self.interfaces)
        preferred = next((label for label, ip in self.interfaces if ip == configured_ip), "")
        self.interface.set(current if current in labels else preferred or next(iter(labels), ""))
        self.controls()

    def internet_dialog(self):
        if self.internet is not None and self.internet.winfo_exists():
            self.internet.lift()
            return
        self.load_interfaces()
        dialog = tk.Toplevel(self.window)
        self.internet = dialog
        dialog.title("Partage Internet · Réseau écrans")
        dialog.transient(self.window)
        content = ttk.Frame(dialog, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Interface du réseau écrans").pack(anchor="w")
        addresses = dict(self.interfaces)
        selected = tk.StringVar()
        selector = ttk.Combobox(content, width=48, textvariable=selected, values=list(addresses), state="readonly")
        selector.pack(fill="x", pady=(3, 10))
        if len(addresses) == 1:
            selected.set(next(iter(addresses)))
        ttk.Label(content, wraplength=440,
                  text="La régie partage sa connexion Internet vers ce réseau. Utilise son IP comme passerelle sur les lecteurs."
                  ).pack(anchor="w", pady=(0, 8))
        status = tk.StringVar()
        ttk.Label(content, textvariable=status, wraplength=440).pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(content)
        actions.pack(fill="x")
        results = Queue()

        def show_report(text, failed):
            report = tk.Toplevel(dialog)
            report.title("Échec du partage Internet" if failed else "Diagnostic du partage Internet")
            report.transient(dialog)
            pane = ttk.Frame(report, padding=14)
            pane.pack(fill="both", expand=True)
            field = tk.Text(pane, width=75, height=12, wrap="word")
            scrollbar = ttk.Scrollbar(pane, orient="vertical", command=field.yview)
            field.configure(yscrollcommand=scrollbar.set)
            field.grid(row=0, column=0, sticky="nsew")
            scrollbar.grid(row=0, column=1, sticky="ns")
            pane.columnconfigure(0, weight=1)
            pane.rowconfigure(0, weight=1)
            field.insert("1.0", text)
            field.configure(state="disabled")

            def copy_report():
                report.clipboard_clear()
                report.clipboard_append(text)

            buttons = ttk.Frame(pane)
            buttons.grid(row=1, column=0, sticky="ew", pady=(10, 0))
            ttk.Button(buttons, text="Copier le diagnostic", command=copy_report).pack(side="left")
            ttk.Button(buttons, text="Fermer", command=report.destroy).pack(side="right")

        def refresh():
            state = internet_state()
            gateway = state.get("gateway")
            status.set(f"Partage configuré · {gateway}" if gateway else "Partage désactivé")
            if gateway:
                label = next((label for label, ip in addresses.items() if ip == gateway), "")
                if label:
                    selected.set(label)
            selector.configure(state="disabled" if gateway else "readonly")
            enable.configure(state="normal" if selected.get() in addresses and not gateway else "disabled")
            disable.configure(state="normal" if gateway else "disabled")
            close.configure(state="normal")
            diagnostic.configure(state="normal")
            dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)

        def apply(operation):
            gateway = addresses.get(selected.get()) if operation != "desactiver" else None
            if operation == "activer" and not gateway:
                return
            if operation != "diagnostic" and not messagebox.askyesno("Partage Internet",
                                       f"Activer le partage via {gateway} ?" if gateway else "Couper le partage Internet ?",
                                       parent=dialog):
                return
            status.set("Diagnostic en cours…" if operation == "diagnostic" else "Modification en cours…")
            for button in (enable, disable, close, selector, diagnostic):
                button.configure(state="disabled")
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)

            def run():
                try:
                    report = self.network.change("internet", operation, gateway)
                    results.put((report, False))
                except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as error:
                    results.put((str(error), True))

            def receive():
                try:
                    report, failed = results.get_nowait()
                except Empty:
                    dialog.after(250, receive)
                    return
                refresh()
                if report:
                    show_report(report, failed)

            threading.Thread(target=run, daemon=True).start()
            dialog.after(250, receive)

        enable = ttk.Button(actions, text="Activer", command=lambda: apply("activer"))
        disable = ttk.Button(actions, text="Désactiver", command=lambda: apply("desactiver"))
        close = ttk.Button(actions, text="Fermer", command=dialog.destroy)
        enable.pack(side="left", padx=(0, 8))
        disable.pack(side="left")
        diagnostic = ttk.Button(actions, text="Diagnostic", command=lambda: apply("diagnostic"))
        diagnostic.pack(side="left", padx=(8, 0))
        close.pack(side="right")
        selector.bind("<<ComboboxSelected>>", lambda _event: refresh())
        refresh()

    def reveal_password(self):
        self.entry.configure(show="" if self.visible.get() else "•")

    def generate_password(self):
        self.password.set(secrets.token_urlsafe(18))
        self.visible.set(True)
        self.reveal_password()

    def backup_dialog(self):
        if self.regie is not None or self.busy:
            return
        from sauvegarde_regie import create_backup
        destination = filedialog.asksaveasfilename(parent=self.window,
            title="Sauvegarder la régie", defaultextension=".slbackup",
            initialfile="SoundLive-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".slbackup",
            filetypes=[("Sauvegarde SoundLive chiffrée", "*.slbackup")])
        if not destination:
            return
        password = simpledialog.askstring("Sauvegarde chiffrée", "Mot de passe (12 caractères minimum) :",
                                          show="•", parent=self.window)
        if not password:
            return
        confirm = simpledialog.askstring("Sauvegarde chiffrée", "Confirmer le mot de passe :",
                                         show="•", parent=self.window)
        if password != confirm:
            messagebox.showerror("Sauvegarde", "Les mots de passe diffèrent.", parent=self.window)
            return
        try:
            count = create_backup(self.tableau.ROOT, destination, password)
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("Sauvegarde", str(error), parent=self.window)
            return
        messagebox.showinfo("Sauvegarde", f"{count} fichiers sauvegardés dans une archive chiffrée.\n"
                            "Conserve son mot de passe séparément.", parent=self.window)

    def restore_dialog(self):
        if self.regie is not None or self.busy:
            return
        if self.network.dhcp.status().get("running"):
            messagebox.showwarning("Restauration", "Arrête d'abord le serveur DHCP.", parent=self.window)
            return
        from sauvegarde_regie import restore_backup
        source = filedialog.askopenfilename(parent=self.window, title="Restaurer la régie",
            filetypes=[("Sauvegarde SoundLive chiffrée", "*.slbackup")])
        if not source:
            return
        password = simpledialog.askstring("Restauration", "Mot de passe de l'archive :",
                                          show="•", parent=self.window)
        if not password:
            return
        if not messagebox.askyesno("Restaurer la régie",
            "Remplacer les configurations présentes dans la sauvegarde ?\n"
            "Une sauvegarde de sécurité sera créée avant les modifications.\n"
            "Le DHCP restauré restera désactivé. Le lanceur redémarrera.",
            default=messagebox.NO, parent=self.window):
            return
        ip = simpledialog.askstring("Adresse HTTPS",
            "Nouvelle IPv4 locale de la régie (laisser vide pour conserver celle de la sauvegarde) :",
            initialvalue=dict(self.interfaces).get(self.interface.get(), ""), parent=self.window)
        if ip is None:
            return
        try:
            count, safety = restore_backup(self.tableau.ROOT, source, password, ip.strip() or None)
        except (OSError, ValueError, KeyError, TypeError) as error:
            messagebox.showerror("Restauration", str(error), parent=self.window)
            return
        messagebox.showinfo("Restauration", f"{count} fichiers restaurés.\n"
                            f"Sauvegarde de sécurité : {safety or 'aucune'}", parent=self.window)
        self.finish_close(restart=True)

    def refresh_acme_summary(self, force=False):
        if not force and monotonic() < self.acme_next_refresh:
            return
        self.acme_next_refresh = monotonic() + 10
        from certificats_acme import certificate_configuration, check_certificate
        from gestion_acme_powerdns import get_config, RENEW_BEFORE, CERT, KEY
        folder = self.tableau.REMOTE_ACCESS
        try:
            config = certificate_configuration(folder)
            if not config:
                self.acme_summary.set("Aucun certificat ACME")
                return
            name = config["identifier"]
            host = f"[{name}]" if ":" in name else name
            url = f"https://{host}:{self.access.PORT}"
            try:
                _, expiry = check_certificate(name, config["fullchain"], config["private_key"])
            except (OSError, ValueError):
                self.acme_summary.set(url + "\nCertificat invalide ou expiré")
                return
            automatic = get_config(folder)
            managed = (automatic and automatic["domain"] == name
                       and Path(config["fullchain"]).resolve() == (folder / CERT).resolve()
                       and Path(config["private_key"]).resolve() == (folder / KEY).resolve())
            expiry_text = expiry.astimezone().strftime("%d/%m/%Y")
            if managed:
                renewal = expiry - RENEW_BEFORE
                date = renewal.astimezone().strftime("%d/%m/%Y")
                detail = ("Renouvellement dès le " + date if renewal > datetime.now(timezone.utc)
                          else "Renouvellement à effectuer")
                if automatic["environment"] == "staging":
                    detail += " · TEST"
            else:
                detail = "Renouvellement externe"
            self.acme_summary.set(f"{url}\n{detail} · Expire le {expiry_text}")
        except (OSError, ValueError, KeyError, TypeError):
            self.acme_summary.set("Configuration ACME illisible")

    def controls(self):
        stopped = self.regie is None and not self.busy
        editable = stopped and self.remote.get()
        self.enabled.configure(state="normal" if stopped else "disabled")
        self.selector.configure(state="readonly" if editable else "disabled")
        for widget in (self.reload, self.entry, self.reveal, self.generate, self.acme_button):
            widget.configure(state="normal" if editable else "disabled")
        self.backup_button.configure(state="normal" if stopped else "disabled")
        self.restore_button.configure(state="normal" if stopped else "disabled")
        self.start_button.configure(state="normal" if stopped else "disabled")
        self.stop_button.configure(state="normal" if self.regie and not self.busy else "disabled")
        self.open_button.configure(state="normal" if self.regie and not self.busy else "disabled")
        remote = self.regie and self.regie.remote_url and not self.busy
        self.copy_button.configure(state="normal" if remote else "disabled")
        self.certificate_button.configure(state="normal" if remote else "disabled")
        self.release_button.configure(state="normal" if not self.busy else "disabled")
        self.moderation_controls()

    def remote_connections(self):
        if self.regie and self.regie.remote_url and not self.busy:
            return self.regie.connections
        return None

    def moderation_controls(self):
        connections = self.remote_connections()
        selected = bool(connections and self.list.selection())
        self.disconnect_button.configure(state="normal" if selected else "disabled")
        self.block_button.configure(state="normal" if selected else "disabled")
        self.blocked_button.configure(state="normal" if connections else "disabled")

    def disconnect_remote(self):
        connections = self.remote_connections()
        selection = self.list.selection()
        if not connections or not selection:
            return
        address = self.list.set(selection[0], "ip")
        if messagebox.askyesno("Déconnecter cette session",
                f"Fermer la session du navigateur à l’adresse {address} ?",
                default=messagebox.NO, parent=self.window):
            connections.disconnect(selection[0])
            self.refresh_remote_list()

    def block_remote(self):
        connections = self.remote_connections()
        selection = self.list.selection()
        if not connections or not selection:
            return
        address = self.list.set(selection[0], "ip")
        if not messagebox.askyesno("Bloquer l’accès distant",
                f"Bloquer {address} ? Tous les navigateurs utilisant cette adresse seront déconnectés.",
                parent=self.window):
            return
        try:
            connections.change_block(address, True)
        except (OSError, ValueError) as error:
            messagebox.showerror("Blocage impossible", str(error), parent=self.window)
        self.refresh_remote_list()

    def show_blocked(self):
        connections = self.remote_connections()
        if not connections:
            return
        dialog = tk.Toplevel(self.window)
        dialog.title("Adresses bloquées")
        dialog.transient(self.window)
        content = ttk.Frame(dialog, padding=14)
        content.pack(fill="both", expand=True)
        addresses = tk.Listbox(content, height=8, width=36, exportselection=False)
        addresses.pack(fill="both", expand=True)

        def refresh():
            addresses.delete(0, tk.END)
            for address in connections.blocked_addresses():
                addresses.insert(tk.END, address)
            unblock.configure(state="disabled")

        def release_address():
            selection = addresses.curselection()
            if not selection:
                return
            try:
                connections.change_block(addresses.get(selection[0]), False)
            except (OSError, ValueError) as error:
                messagebox.showerror("Déblocage impossible", str(error), parent=dialog)
            refresh()

        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(8, 0))
        unblock = ttk.Button(actions, text="Débloquer l’IP", command=release_address)
        unblock.pack(side="left")
        ttk.Button(actions, text="Fermer", command=dialog.destroy).pack(side="right")
        addresses.bind("<<ListboxSelect>>", lambda _event:
                       unblock.configure(state="normal" if addresses.curselection() else "disabled"))
        refresh()
        dialog.grab_set()

    def start(self):
        enabled = self.remote.get()
        address = dict(self.interfaces).get(self.interface.get())
        if enabled and not address:
            messagebox.showerror("Interface introuvable", "Choisis l’interface du réseau des écrans.", parent=self.window)
            return
        password = self.password.get() or None
        self.busy = True
        self.status.set("Démarrage de la régie…")
        self.controls()

        def launch():
            try:
                regie = self.tableau.Regie(publication_status=self.publication_status, network_control=self.network)
                try:
                    generated, certificate = (
                        self.access.prepare_access(self.tableau.REMOTE_ACCESS, address, password)
                        if enabled else (None, None)
                    )
                except Exception:
                    regie.server.server_close()
                    raise
                regie.start(enabled)
                self.results.put(("started", regie, generated, certificate))
            except Exception as error:
                self.results.put(("error", str(error)))

        threading.Thread(target=launch, daemon=True).start()

    def stop(self):
        if self.regie is None or self.busy:
            return
        self.busy = True
        self.status.set("Arrêt de la régie…")
        self.controls()
        regie = self.regie

        def close_servers():
            try:
                regie.close()
            finally:
                self.results.put(("stopped",))

        threading.Thread(target=close_servers, daemon=True).start()

    def tick(self):
        try:
            result = self.results.get_nowait()
        except Empty:
            result = None
        if result:
            self.busy = False
            if result[0] == "started":
                _, self.regie, generated, certificate = result
                self.status.set(f"Régie en cours · {self.regie.local_url}")
                self.remote_address.set(self.regie.remote_url or "Accès distant désactivé")
                if self.regie.remote_url:
                    self.show_qr(self.regie.server.waiting_qr)
                if certificate and (generated or self.password.get()):
                    if generated:
                        self.generated_credentials(generated)
                    else:
                        messagebox.showinfo("Accès distant",
                            "Le mot de passe du compte regie a été enregistré.",
                            parent=self.window)
                    self.password.set("")
                    self.visible.set(False)
                    self.reveal_password()
                if self.closing:
                    self.stop()
            elif result[0] == "stopped":
                self.regie = None
                self.status.set("Régie arrêtée")
                self.remote_address.set("—")
                self.qr_panel.grid_remove()
            else:
                self.status.set("La régie n’a pas démarré")
                messagebox.showerror("Démarrage impossible", result[1], parent=self.window)
            self.controls()
        if self.closing and not self.busy and self.regie is None:
            self.finish_close()
            return
        self.refresh_remote_list()
        self.refresh_acme_summary()
        self.show_publication(self.publication_status.read())
        self.window.after(1500, self.tick)

    def refresh_remote_list(self):
        connections = self.remote_connections()
        rows = connections.snapshot() if connections else []
        self.connection_status.set(f"{len(rows)} navigateur(s) actif(s)" if self.regie and self.regie.remote_url
                                   else "Accès distant arrêté")
        current = {row["id"] for row in rows}
        obsolete = [identifier for identifier in self.list.get_children() if identifier not in current]
        if obsolete:
            self.list.delete(*obsolete)
        for index, row in enumerate(rows):
            identifier = row["id"]
            last_seen = datetime.fromtimestamp(row["seen"]).strftime("%H:%M:%S")
            values = (row["ip"], last_seen, row["agent"])
            if self.list.exists(identifier):
                self.list.item(identifier, values=values)
                if self.list.index(identifier) != index:
                    self.list.move(identifier, "", index)
            else:
                self.list.insert("", index, iid=identifier, values=values)
        self.moderation_controls()

    def close(self):
        if self.closing:
            return
        self.closing = True
        if self.regie is not None:
            self.stop()
        elif not self.busy:
            self.finish_close()

    def finish_close(self, restart=False):
        if self.dhcp_shutdown is not None:
            return
        self.closing = True
        self.window.withdraw()
        if self.dhcp_panel is not None:
            self.dhcp_panel.dialog.withdraw()
        self.dhcp_shutdown = self.dhcp_tasks.submit(self.network.close)
        self.dhcp_tasks.shutdown(wait=False)

        def finish():
            if not self.dhcp_shutdown.done():
                self.window.after(120, finish)
                return
            if restart:
                try:
                    relaunch(self.tableau.ROOT)
                except OSError as error:
                    messagebox.showwarning("Mise à jour installée",
                        f"Le lanceur n’a pas pu redémarrer. Ouvre-le manuellement.\n\n{error}", parent=self.window)
            self.window.destroy()

        finish()

    def open_dashboard(self):
        webbrowser.open(self.regie.local_url)

    def open_dhcp(self):
        if self.dhcp_panel is None:
            self.dhcp_panel = DhcpPanel(self.window, self.network, self.dhcp_tasks)
        self.dhcp_panel.show()

    def authorize_network(self):
        authorization = self.network.authorization
        enabled = authorization.ready
        prompt = "Retirer l’autorisation de gestion réseau web ?" if enabled else (
            "Autoriser les sessions web de la régie à gérer le partage Internet et le DHCP "
            "jusqu’à la fermeture du lanceur ?")
        if not messagebox.askyesno("Gestion réseau web", prompt, parent=self.window):
            return
        self.network_button.configure(state="disabled", text="Modification de l’autorisation…")
        task = self.dhcp_tasks.submit(authorization.revoke if enabled else authorization.open)

        def receive():
            if not task.done():
                self.window.after(150, receive)
                return
            self.network_button.configure(state="normal", text="Retirer l’autorisation réseau web" if authorization.ready
                                          else "Autoriser la gestion réseau web")
            try:
                task.result()
            except (OSError, ValueError, RuntimeError) as error:
                messagebox.showerror("Gestion réseau web", str(error), parent=self.window)

        receive()

    def show_publication(self, status):
        state = status["state"]
        label = {"checking": "Vérification des versions…",
                 "current": "Versions publiées · À jour",
                 "unavailable": "Versions publiées · Vérification indisponible"}
        self.release_button.configure(text=f"Version {status['version']} disponible" if state == "available"
                                      else label.get(state, "Versions publiées"))

    def publications(self):
        import versions_regie as versions

        root = self.tableau.ROOT
        form = tk.Toplevel(self.window)
        form.title("Versions publiées")
        form.transient(self.window)
        pane = ttk.Frame(form, padding=18)
        pane.pack(fill="both", expand=True)

        # Le dépôt est officiel et fixe : aucune saisie ni clé à importer.
        repo = tk.StringVar(form, value=versions.DEFAULT_REPOSITORY)
        available = self.publication_status.read().get("version")
        outcome = tk.StringVar(form, value=f"Version installée · {self.tableau.SOFTWARE_VERSION}"
                               + (f"\nVersion {available} disponible." if available else "")
                               + ("\nArrête la régie avant d’installer une mise à jour." if self.regie else ""))
        selected = None
        answers = Queue()
        ttk.Label(pane, text="Dépôt officiel GitHub").pack(anchor="w")
        # Un champ réellement non modifiable, sans l'aspect grisé de ttk sous Windows.
        tk.Entry(pane, textvariable=repo, width=52, state="readonly",
                 readonlybackground="white", background="white", foreground="black",
                 insertbackground="black", relief="solid", borderwidth=1
                 ).pack(fill="x", pady=(4, 10), ipady=3)
        ttk.Label(pane, textvariable=outcome, wraplength=420).pack(anchor="w", pady=12)
        buttons = ttk.Frame(pane)
        buttons.pack(anchor="e")

        def task(callback, installing=False):
            check.configure(state="disabled")
            apply.configure(state="disabled")
            close.configure(state="disabled")
            form.protocol("WM_DELETE_WINDOW", lambda: None)

            def run():
                try:
                    answers.put((True, callback()))
                except Exception as error:
                    answers.put((False, str(error)))

            def receive():
                nonlocal selected
                try:
                    ok, value = answers.get_nowait()
                except Empty:
                    form.after(120, receive)
                    return
                check.configure(state="normal")
                close.configure(state="normal")
                form.protocol("WM_DELETE_WINDOW", form.destroy)
                selected = value if ok and not installing else None
                if not ok:
                    outcome.set(value)
                elif installing:
                    form.destroy()
                    self.finish_close(restart=True)
                elif selected:
                    outcome.set(f"Version {selected[0]} disponible.")
                    if self.regie is not None:
                        outcome.set(f"Version {selected[0]} disponible. Ferme cette fenêtre et arrête la régie pour l’installer.")
                    else:
                        apply.configure(state="normal")
                else:
                    outcome.set("La régie est à jour.")

            threading.Thread(target=run, daemon=True).start()
            form.after(120, receive)

        def check_release():
            outcome.set("Vérification de la dernière version…")
            task(lambda: self.publication_status.verify(versions.DEFAULT_REPOSITORY))

        def apply_release():
            if not selected or self.regie is not None:
                return
            if not messagebox.askyesno("Installer la mise à jour",
                    f"Installer la version {selected[0]} et relancer le lanceur ?", parent=form):
                return
            outcome.set("Téléchargement et vérification de la signature…")
            task(lambda: versions.install(root, selected), installing=True)

        check = ttk.Button(buttons, text="Vérifier", command=check_release)
        apply = ttk.Button(buttons, text="Installer", command=apply_release, state="disabled")
        close = ttk.Button(buttons, text="Fermer", command=form.destroy)
        for button in (check, apply, close):
            button.pack(side="left", padx=3)
        form.grab_set()

    def copy_address(self):
        self.window.clipboard_clear()
        self.window.clipboard_append(self.regie.remote_url)

    def generated_credentials(self, generated):
        box = tk.Toplevel(self.window)
        box.title("Mot de passe généré")
        box.transient(self.window)
        panel = ttk.Frame(box, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Compte regie · conserve ce mot de passe").pack(anchor="w")
        entry = ttk.Entry(panel, width=34)
        entry.insert(0, generated)
        entry.configure(state="readonly")
        entry.pack(fill="x", pady=8)

        def copy():
            self.window.clipboard_clear()
            self.window.clipboard_append(generated)

        ttk.Button(panel, text="Copier le mot de passe", command=copy).pack(side="left")
        ttk.Button(panel, text="Fermer", command=box.destroy).pack(side="right")
        box.grab_set()
        self.window.wait_window(box)

    def configure_acme_dialog(self):
        """Configurer le chargement des PEM obtenus par un client ACME externe."""
        if self.regie is not None or self.busy:
            return
        from certificats_acme import (certificate_configuration, configure_certificate,
                                       disable_certificate)
        folder = self.tableau.REMOTE_ACCESS
        try:
            current = certificate_configuration(folder) or {}
        except (OSError, ValueError) as error:
            messagebox.showerror("Certificat ACME", str(error), parent=self.window)
            return
        dialog = tk.Toplevel(self.window)
        dialog.title("Certificat public ACME · accès régie")
        dialog.transient(self.window)
        panel = ttk.Frame(dialog, padding=18)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Certificat délivré et renouvelé par un client ACME externe.",
                  wraplength=600).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        ttk.Label(panel, text="Le serveur reste accessible uniquement sur le LAN (port 8766).",
                  wraplength=600).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        identifier = tk.StringVar(value=current.get("identifier", ""))
        chain = tk.StringVar(value=current.get("fullchain", ""))
        key = tk.StringVar(value=current.get("private_key", ""))
        panel.columnconfigure(1, weight=1)
        for row, label, var in (
                (2, "Nom DNS public ou IPv4/IPv6 publique", identifier),
                (3, "Chaîne certificat fullchain.pem", chain),
                (4, "Clé privée privkey.pem", key)):
            ttk.Label(panel, text=label).grid(row=row, column=0, sticky="w", pady=5, padx=(0, 8))
            ttk.Entry(panel, width=58, textvariable=var).grid(row=row, column=1, sticky="ew", pady=5)
            if row in (3, 4):
                def browse(target=var):
                    filename = filedialog.askopenfilename(parent=dialog, title="Choisir le PEM",
                                      filetypes=[("Certificats PEM", "*.pem"), ("Tous les fichiers", "*")])
                    if filename:
                        target.set(filename)
                ttk.Button(panel, text="Parcourir…", command=browse).grid(
                    row=row, column=2, padx=(8, 0), pady=5)
        ttk.Label(panel, text="Pour les IP privées (192.168.x.x / 10.x.x.x), utilise plutôt un nom DNS public validé par DNS-01. "
                  "Le certificat doit déjà être émis et ses PEM renouvelés automatiquement.",
                  wraplength=620).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 12))
        actions = ttk.Frame(panel)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew")
        def enable():
            try:
                name = configure_certificate(folder, identifier.get().strip(),
                                             chain.get().strip(), key.get().strip())
                from gestion_acme_powerdns import disable_automatic
                disable_automatic(folder)
            except (OSError, ValueError) as error:
                messagebox.showerror("Certificat ACME", str(error), parent=dialog)
                return
            self.refresh_acme_summary(force=True)
            dialog.destroy()
            messagebox.showinfo("Certificat ACME",
                f"Certificat configuré pour {name}. Démarre la régie pour l'activer. "
                "Les renouvellements de ces PEM seront ensuite chargés automatiquement.",
                parent=self.window)
        def disable():
            if not messagebox.askyesno("Revenir au certificat local",
                        "Revenir à l’autorité locale SoundLive au prochain démarrage ?",
                        parent=dialog):
                return
            try:
                disable_certificate(folder)
                from gestion_acme_powerdns import disable_automatic
                disable_automatic(folder)
            except OSError as error:
                messagebox.showerror("Certificat ACME", str(error), parent=dialog)
                return
            self.refresh_acme_summary(force=True)
            dialog.destroy()
        ttk.Button(actions, text="Appliquer", command=enable).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Automatiser via PowerDNS…",
                   command=lambda: self.configure_powerdns_dialog(dialog)).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Certificat local", command=disable).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="Fermer", command=dialog.destroy).pack(side="right")
        dialog.grab_set()

    def configure_powerdns_dialog(self, parent=None):
        """Assistant d'obtention et de renouvellement Let's Encrypt par DNS PowerDNS."""
        from gestion_acme_powerdns import (configure, get_config, public_config, read_status,
                                           renew_once)
        folder = self.tableau.REMOTE_ACCESS
        current = public_config(folder) or {}
        dialog = tk.Toplevel(self.window)
        dialog.title("Let's Encrypt · PowerDNS API · certificat automatique")
        dialog.transient(parent or self.window)
        # Réserver le pied de fenêtre avant le contenu défilant.
        buttons = ttk.Frame(dialog, padding=(18, 8, 18, 12))
        buttons.pack(side="bottom", fill="x")
        viewport = ttk.Frame(dialog)
        viewport.pack(fill="both", expand=True)
        canvas = tk.Canvas(viewport, highlightthickness=0, borderwidth=0)
        scrollbar = ttk.Scrollbar(viewport, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        content = ttk.Frame(canvas, padding=18)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(content_id, width=event.width))
        content.columnconfigure(1, weight=1)
        info = ("SoundLive-NDI peut obtenir et renouveler un certificat Let's Encrypt "
                "en créant un enregistrement TXT temporaire dans PowerDNS. "
                "L'interface HTTPS reste sur le réseau privé TCP 8766.")
        ttk.Label(content, text=info, wraplength=600).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, 12))

        fields = (
            ("Nom DNS du certificat", "domain", "regie.exemple.fr"),
            ("Zone PowerDNS", "zone", "exemple.fr"),
            ("Adresse API PowerDNS", "api_url", "https://dns.exemple.fr:8081"),
            ("Identifiant serveur PowerDNS", "server_id", "localhost"),
            ("Adresse e-mail Let's Encrypt", "email", "admin@exemple.fr"),
        )
        variables = {}
        for index, (label, key, placeholder) in enumerate(fields, start=1):
            variables[key] = tk.StringVar(value=current.get(key, "localhost" if key == "server_id" else ""))
            ttk.Label(content, text=label).grid(row=index, column=0, sticky="w", pady=5, padx=(0, 8))
            ttk.Entry(content, textvariable=variables[key], width=58).grid(
                row=index, column=1, sticky="ew", pady=5)
        api_key = tk.StringVar()
        ttk.Label(content, text="Clé API PowerDNS (X-API-Key)").grid(
            row=6, column=0, sticky="w", pady=5, padx=(0, 8))
        ttk.Entry(content, textvariable=api_key, show="•", width=58).grid(
            row=6, column=1, sticky="ew", pady=5)
        ttk.Label(content, text=("La clé n'est jamais transmise au navigateur de la régie. "
                "Sur Windows, elle est chiffrée avec DPAPI pour ce compte utilisateur. "
                "Si la configuration existe, laisse ce champ vide pour conserver la clé."),
                  wraplength=605).grid(row=7, column=0, columnspan=2, sticky="w", pady=(0, 10))
        staging = tk.BooleanVar(value=current.get("environment") == "staging")
        ttk.Checkbutton(content, text="Environnement de test Let's Encrypt (certificat NON reconnu)",
                        variable=staging).grid(row=8, column=0, columnspan=2, sticky="w")
        terms = tk.BooleanVar()
        ttk.Checkbutton(content,
            text="J'accepte les conditions d'utilisation de Let's Encrypt (letsencrypt.org/repository/)",
            variable=terms).grid(row=9, column=0, columnspan=2, sticky="w", pady=(4, 10))
        status = tk.StringVar(value=(read_status(folder).get("message") or "Configuration non démarrée."))
        ttk.Label(content, textvariable=status, wraplength=605).grid(
            row=10, column=0, columnspan=2, sticky="w", pady=10)
        running = [False]

        def begin():
            if running[0]:
                return
            if not terms.get():
                status.set("Accepte les conditions de Let's Encrypt pour continuer.")
                return
            params = {name: entry.get().strip() for name, entry in variables.items()}
            secret = api_key.get()
            environment = "staging" if staging.get() else "production"
            running[0] = True
            launch_button.configure(state="disabled")
            status.set("Vérification de l'API PowerDNS, puis demande de certificat…")
            output = Queue()
            events = Queue()
            log_window = tk.Toplevel(dialog)
            log_window.title("SoundLive NDI — Journal DNS / ACME")
            log_window.geometry(f"{min(850, log_window.winfo_screenwidth() - 80)}x"
                                f"{min(440, log_window.winfo_screenheight() - 100)}")
            log_window.transient(dialog)
            log_actions = ttk.Frame(log_window, padding=(12, 8, 12, 12))
            log_actions.pack(side="bottom", fill="x")
            log_frame = ttk.Frame(log_window, padding=12)
            log_frame.pack(fill="both", expand=True)
            log_text = tk.Text(log_frame, wrap="word", state="disabled")
            scroll = ttk.Scrollbar(log_frame, command=log_text.yview)
            log_text.configure(yscrollcommand=scroll.set)
            scroll.pack(side="right", fill="y")
            log_text.pack(side="left", fill="both", expand=True)
            def append_log(message):
                if not log_window.winfo_exists():
                    return
                from datetime import datetime
                log_text.configure(state="normal")
                log_text.insert("end", datetime.now().strftime("[%H:%M:%S] ") + message + "\n")
                log_text.see("end")
                log_text.configure(state="disabled")
            def close_log():
                if not running[0]:
                    log_window.destroy()
                    if dialog.winfo_exists():
                        dialog.grab_set()
            log_window.protocol("WM_DELETE_WINDOW", close_log)
            def copy_log():
                log_window.clipboard_clear()
                log_window.clipboard_append(log_text.get("1.0", "end-1c"))
            ttk.Button(log_actions, text="Copier le journal", command=copy_log).pack(side="left")
            ttk.Button(log_actions, text="Fermer", command=close_log).pack(side="right")
            log_window.update_idletasks()
            log_window.minsize(min(log_window.winfo_screenwidth() - 80,
                                   log_actions.winfo_reqwidth() + 24), log_actions.winfo_reqheight() + 100)
            log_window.grab_set()
            append_log("Début de la vérification PowerDNS et ACME.")

            def work():
                try:
                    configure(folder, params["domain"], params["zone"], params["api_url"],
                              secret, params["email"], params["server_id"], environment,
                              accept_terms=True)
                    events.put("Configuration PowerDNS validée.")
                    renew_once(folder, progress=events.put, force=True)
                    output.put((True, "Certificat obtenu et installé. Le renouvellement automatique "
                                "sera actif lorsque la régie fonctionne."))
                except Exception as error:
                    # Les erreurs système non prévues ne doivent pas exposer une clé API
                    # dans les dialogues et journaux.
                    from acme_powerdns import AcmeFailure
                    output.put((False, str(error) if isinstance(error, (ValueError, AcmeFailure))
                                else "Émission impossible. Vérifie PowerDNS, les DNS publics "
                                     "et le statut de l'opération."))

            threading.Thread(target=work, daemon=True, name="acme-powerdns-setup").start()

            def update():
                while True:
                    try:
                        append_log(events.get_nowait())
                    except Empty:
                        break
                state = read_status(folder)
                if running[0] and state.get("state") in ("running", "failed"):
                    status.set(state.get("message", ""))
                try:
                    ok, message = output.get_nowait()
                except Empty:
                    if dialog.winfo_exists():
                        dialog.after(500, update)
                    return
                running[0] = False
                while True:
                    try:
                        append_log(events.get_nowait())
                    except Empty:
                        break
                self.refresh_acme_summary(force=True)
                append_log(("SUCCÈS : " if ok else "ERREUR : ") + message)
                if dialog.winfo_exists():
                    launch_button.configure(state="normal")
                    status.set(message if ok else state.get("message") or message)
                    if ok:
                        messagebox.showinfo("Let's Encrypt",
                            "Certificat obtenu. Démarre ou redémarre la régie pour afficher "
                            "le nom DNS dans son QR code. Les renouvellements seront automatiques.",
                            parent=log_window)
            dialog.after(500, update)

        launch_button = ttk.Button(buttons, text="Vérifier PowerDNS et obtenir le certificat",
                                   command=begin)
        launch_button.pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="Fermer", command=dialog.destroy).pack(side="right")
        dialog.update_idletasks()
        width = min(dialog.winfo_screenwidth() - 80,
                    max(720, content.winfo_reqwidth() + scrollbar.winfo_reqwidth()))
        height = min(dialog.winfo_screenheight() - 120,
                     content.winfo_reqheight() + buttons.winfo_reqheight() + 12)
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(min(width, buttons.winfo_reqwidth() + 24),
                       buttons.winfo_reqheight() + 100)
        dialog.grab_set()

    def open_certificate_folder(self):
        folder = self.tableau.REMOTE_ACCESS
        try:
            if sys.platform == "win32":
                os.startfile(folder)
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError as error:
            messagebox.showerror("Dossier du certificat", f"{folder}\n{error}", parent=self.window)

    def show_qr(self, modules):
        self.qr_image = tk.PhotoImage(master=self.window, data=qr_bitmap(modules), format="PPM")
        self.qr.configure(image=self.qr_image)
        self.qr_panel.grid()


def main():
    try:
        window = tk.Tk()
    except tk.TclError as error:
        raise SystemExit(f"Ouvre le lanceur depuis une session graphique. {error}") from error
    try:
        import acces_regie
        import tableau
        Launcher(window, tableau, acces_regie)
    except Exception as error:
        window.withdraw()
        Path(__file__).with_name("regie-demarrage.log").write_text(traceback.format_exc(), encoding="utf-8")
        messagebox.showerror("Démarrage impossible", str(error), parent=window)
        window.destroy()
        return
    window.mainloop()


if __name__ == "__main__":
    main()
