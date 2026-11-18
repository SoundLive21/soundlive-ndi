# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
"""Lanceur graphique de la régie SoundLive-NDI."""
from base64 import b64encode
from datetime import datetime
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
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
import traceback
import webbrowser

from commun import APP_NAME, COPYRIGHT


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
        if os.geteuid() != 0:
            pkexec = shutil.which("pkexec")
            if not pkexec:
                raise RuntimeError("Installe pkexec et nftables, ou lance le script regie-internet.py avec sudo.")
            command.insert(0, pkexec)
        run = subprocess.run(command, capture_output=True, text=True)
        if run.returncode:
            raise RuntimeError(run.stderr.strip() or "La modification du partage Internet a échoué.")


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
        self.generate = ttk.Button(setup, text="Générer un nouveau mot de passe", command=self.generate_password)
        self.generate.grid(row=4, column=1, sticky="w", pady=(8, 0))

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
        ttk.Label(panel, text=COPYRIGHT).grid(row=8, sticky="w", pady=(8, 0))
        window.update_idletasks()
        viewport.configure(width=min(panel.winfo_reqwidth(), window.winfo_screenwidth() * 9 // 10),
                           height=min(panel.winfo_reqheight(), window.winfo_screenheight() * 4 // 5))
        self.list.bind("<<TreeviewSelect>>", lambda _event: self.moderation_controls())
        window.protocol("WM_DELETE_WINDOW", self.close)
        if not tableau.DATA.exists():
            self.status.set("Aucun écran associé dans ce dossier de régie")
        self.load_interfaces()
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
        diagnostic = None

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
            if diagnostic is not None:
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
            for button in (enable, disable, close, selector):
                button.configure(state="disabled")
            if diagnostic is not None:
                diagnostic.configure(state="disabled")
            dialog.protocol("WM_DELETE_WINDOW", lambda: None)

            def run():
                try:
                    report = change_internet(self.tableau.ROOT, operation, gateway)
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
                    if sys.platform == "win32":
                        show_report(report, failed)
                    else:
                        messagebox.showerror("Partage Internet", report, parent=dialog)

            threading.Thread(target=run, daemon=True).start()
            dialog.after(250, receive)

        enable = ttk.Button(actions, text="Activer", command=lambda: apply("activer"))
        disable = ttk.Button(actions, text="Désactiver", command=lambda: apply("desactiver"))
        close = ttk.Button(actions, text="Fermer", command=dialog.destroy)
        enable.pack(side="left", padx=(0, 8))
        disable.pack(side="left")
        if sys.platform == "win32":
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

    def controls(self):
        stopped = self.regie is None and not self.busy
        editable = stopped and self.remote.get()
        self.enabled.configure(state="normal" if stopped else "disabled")
        self.selector.configure(state="readonly" if editable else "disabled")
        for widget in (self.reload, self.entry, self.reveal, self.generate):
            widget.configure(state="normal" if editable else "disabled")
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
                regie = self.tableau.Regie(publication_status=self.publication_status)
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
            self.window.destroy()
            return
        self.refresh_remote_list()
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
        self.closing = True
        if self.regie is not None:
            self.stop()
        elif not self.busy:
            self.window.destroy()

    def open_dashboard(self):
        webbrowser.open(self.regie.local_url)

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
        config = root / "source-publications.json"
        trusted = root / "publication.pub"
        try:
            current = json.loads(config.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (OSError, ValueError):
            current = {}
        form = tk.Toplevel(self.window)
        form.title("Versions publiées")
        form.transient(self.window)
        pane = ttk.Frame(form, padding=18)
        pane.pack(fill="both", expand=True)
        repo = tk.StringVar(form, value=current.get("repository", ""))
        available = self.publication_status.read().get("version")
        outcome = tk.StringVar(form, value=f"Version installée · {self.tableau.SOFTWARE_VERSION}"
                               + (f"\nVersion {available} disponible." if available else "")
                               + ("\nArrête la régie avant d’installer une mise à jour." if self.regie else ""))
        selected = None
        answers = Queue()
        ttk.Label(pane, text="Dépôt public GitHub · propriétaire/dépôt").pack(anchor="w")
        ttk.Entry(pane, textvariable=repo, width=52).pack(fill="x", pady=(4, 10))

        def select_key():
            name = filedialog.askopenfilename(parent=form, title="Clé publique de l’éditeur",
                                              filetypes=[("Clé publique OpenSSH", "*.pub")])
            if name:
                try:
                    key = Path(name).read_bytes()
                    if len(key) > 4096 or not key.startswith(b"ssh-ed25519 "):
                        raise ValueError("Sélectionne une clé publique Ed25519.")
                    trusted.write_bytes(key)
                    key_button.configure(text="Remplacer la clé publique de l’éditeur")
                except (OSError, ValueError) as error:
                    outcome.set(str(error))

        key_button = ttk.Button(pane, command=select_key, text=(
            "Remplacer la clé publique de l’éditeur" if trusted.is_file() else "Importer la clé publique de l’éditeur"))
        key_button.pack(anchor="w")
        ttk.Label(pane, textvariable=outcome, wraplength=420).pack(anchor="w", pady=12)
        buttons = ttk.Frame(pane)
        buttons.pack(anchor="e")

        def task(callback, installing=False):
            check.configure(state="disabled")
            apply.configure(state="disabled")
            key_button.configure(state="disabled")
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
                key_button.configure(state="normal")
                form.protocol("WM_DELETE_WINDOW", form.destroy)
                selected = value if ok and not installing else None
                if not ok:
                    outcome.set(value)
                elif installing:
                    try:
                        relaunch(root)
                    except OSError as error:
                        messagebox.showwarning("Mise à jour installée",
                            f"Le lanceur n’a pas pu redémarrer. Ouvre-le manuellement.\n\n{error}", parent=form)
                    self.window.destroy()
                elif selected:
                    outcome.set(f"Version {selected[0]} disponible.")
                    if self.regie is not None:
                        outcome.set(f"Version {selected[0]} disponible. Ferme cette fenêtre et arrête la régie pour l’installer.")
                    elif trusted.is_file():
                        apply.configure(state="normal")
                    else:
                        outcome.set(f"Version {selected[0]} disponible. Importe la clé de l’éditeur puis vérifie de nouveau.")
                else:
                    outcome.set("La régie est à jour.")

            threading.Thread(target=run, daemon=True).start()
            form.after(120, receive)

        def check_release():
            try:
                name = versions.repository(repo.get())
                config.write_text(json.dumps({"repository": name}), encoding="utf-8")
            except (ValueError, OSError) as error:
                outcome.set(str(error))
                return
            outcome.set("Vérification de la dernière version…")
            task(lambda: self.publication_status.verify(name))

        def apply_release():
            if not selected or self.regie is not None:
                return
            if not messagebox.askyesno("Installer la mise à jour",
                    f"Installer la version {selected[0]} et relancer le lanceur ?", parent=form):
                return
            outcome.set("Téléchargement et vérification de la signature…")
            task(lambda: versions.install(root, selected, trusted), installing=True)

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
