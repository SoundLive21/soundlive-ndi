#!/usr/bin/env python3
# SoundLive-NDI
# Auteur : DUVERNOY Matthieu & ChatGPT
# contact@soundlive.fr
# Société SoundLive
import argparse
from base64 import b64decode
from contextlib import contextmanager
from getpass import getuser
from hashlib import sha256
import hmac
from html import escape
from ipaddress import IPv4Address
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
from tempfile import NamedTemporaryFile
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

from acces_regie import (PORT as REMOTE_PORT, SESSION_COOKIE, SESSION_WARNING, USERNAME,
                         RemoteConnections, RemoteDashboard, prepare_access)
from commun import (APP_NAME, MEDIA_CHUNK, MEDIA_COUNT_LIMIT, MEDIA_DISK_PERCENT, MEDIA_FILE_LIMIT, MEDIA_FOLDER_LIMIT,
                    MEDIA_LIBRARY_LIMIT, MEDIA_TOTAL_LIMIT,
                    ORIENTATIONS, PREVIEW_LIMIT, SOFTWARE_VERSION as CLIENT_VERSION, SSH_PAIRING_PREFIX,
                    media_available, media_config, media_disk, media_name, media_revision, media_timing,
                    options, release_number, ssh_fingerprints, ssh_pairing_proof)
from diagnostic import Diagnostic, NetworkRates, OsUpdateCheck, desktop_protocol, desktop_services, network_info, os_update_report
from certificats_acme import configure_certificate, disable_certificate
from gestion_acme_powerdns import RenewalService, get_config, disable_automatic
from deploiement_lecteur import Deployments, validate_ipv4
from page_ssh import render as render_ssh_page
from installation_lecteur import KEYS_PATH, installation_keys, prepare_installations, ssh_access_public
from mise_a_jour import (CLIENT_UPDATE_ISSUES, ENGINE_ASSETS, OS_NAMESPACE, SAFE_ID, OpenSSHError, contents,
                         digest, engine_name, full_contents, maintenance_manifest, package, prepare_key, sign_manifest)
from qrencoder import QRCode, QRErrorCorrectLevel
from terminal_ssh import GROUP_LIMIT, USER as SSH_USER, HostKeyCheck, Terminals
from versions_regie import PublicationStatus

ROOT = Path(__file__).resolve().parent
SOFTWARE_VERSION = "1.4.151"
# Publication : arrêt indépendant des sorties vidéo.
RESOLUTION_CONTROLS_ENABLED = False
PREVIEW_AUTO_INTERVAL = 30
DATA = ROOT / "ecrans.json"
SCREEN_ORDER_FILE = ROOT / "disposition-ecrans.json"
MEDIA_ROOT = ROOT / ".soundlive-medias"
MEDIA_WRITE_LOCK = threading.Lock()
MEDIA_DEFAULT_FOLDER = "general"
MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".mp4": "video/mp4",
    ".m4v": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm", ".mkv": "video/x-matroska",
}
REMOTE_ACCESS = ROOT / ".soundlive-acces"
PAGE = ((ROOT / "tableau.html").read_bytes()
        .replace(b"__SOFTWARE_VERSION__", SOFTWARE_VERSION.encode("ascii"))
        .replace(b"__CLIENT_VERSION__", CLIENT_VERSION.encode("ascii"))
        .replace(b"__MEDIA_DISK_PERCENT__", str(MEDIA_DISK_PERCENT).encode("ascii"))
        .replace(b"__RESOLUTION_CONTROLS_ENABLED__", str(RESOLUTION_CONTROLS_ENABLED).lower().encode("ascii"))
        .replace(b"__PREVIEW_INTERVAL__", str(PREVIEW_AUTO_INTERVAL).encode("ascii")))
LOGIN_PAGE = (ROOT / "acces-refuse.html").read_text(encoding="utf-8")
PERSONAL_ROOT = ROOT / ".soundlive-personal-medias"

def personal_directory(owner):
    if not isinstance(owner, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,32}", owner):
        raise ValueError("Utilisateur invalide.")
    return PERSONAL_ROOT / sha256(owner.encode("utf-8")).hexdigest()

def personal_catalog(owner):
    directory = personal_directory(owner)
    path = directory / "catalog.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("owner") != owner:
            raise ValueError("Espace média invalide.")
        return directory, data
    return directory, {"owner":owner,"folders":{"general":{"name":"Général","items":[]}}}

def save_personal(directory, data):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = directory / "catalog.tmp"
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(directory / "catalog.json")

def personal_copy_prefix(owner):
    return "personal-" + sha256(owner.encode("utf-8")).hexdigest()[:32] + "-"

PERSONAL_MEDIA_PAGE = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Médias personnels · SoundLive NDI</title><style>__SHARED_STYLE__</style><header><span class="mark"></span><strong>SoundLive-NDI</strong><span id="count"></span><span class="connected-user">Connecté : <strong>__CONNECTED_USER__</strong></span><a href="/">Retour à la régie</a></header><main><div class="heading"><h1>Médias personnels</h1></div><article><label>Utilisateur<select id="owner"></select></label><label>Dossier<select id="folder"></select></label><div class="actions"><button id="create">Créer un dossier</button><button id="rename">Renommer le dossier</button><button id="remove">Supprimer le dossier</button></div><label>Images et vidéos<input type="file" id="files" multiple accept=".jpg,.jpeg,.png,.mp4,.m4v,.mov,.webm,.mkv"></label><button class="primary" id="upload">Déposer les fichiers</button><p id="message" role="status"></p><div id="items"></div><label>Écran de destination<select id="screen"></select></label><button class="primary" id="play">Diffuser ce dossier</button><p class="small">La diffusion transfère une copie du dossier vers le lecteur.</p></article></main><script>
const q=id=>document.getElementById(id);let state,busy=false;
async function api(path,data,raw=false){const r=await fetch(path,data===undefined?{}:{method:"POST",headers:{"Content-Type":raw?"application/octet-stream":"application/json"},body:raw?data:JSON.stringify(data)});if(r.status===401){location.href="/connexion";throw Error("Session expirée")}const v=await r.json();if(!r.ok)throw Error(v.error||r.status);return v}
const query=(extra={})=>new URLSearchParams({owner:q("owner").value,folder:q("folder").value,...extra});
const act=(action,extra={})=>api("/api/personal-media",{owner:q("owner").value,folder:q("folder").value,action,...extra});
const run=f=>async()=>{if(busy)return;busy=true;const controls=[...document.querySelectorAll("button,select,input")];controls.forEach(x=>x.disabled=true);try{q("message").textContent="Traitement en cours…";await f();await load();q("message").textContent="Terminé."}catch(e){q("message").textContent=e.message}finally{busy=false;controls.forEach(x=>x.disabled=false)}};
function options(node,items,previous){node.replaceChildren(...items.map(([v,n])=>new Option(n,v)));if(items.some(x=>x[0]===previous))node.value=previous}
async function load(){const owner=q("owner").value,folder=q("folder").value,screen=q("screen").value;state=await api("/api/personal-media?"+new URLSearchParams({owner}));options(q("owner"),state.owners.map(x=>[x,x]),state.owner);options(q("folder"),Object.entries(state.folders).map(([id,f])=>[id,f.name]),folder);options(q("screen"),Object.entries(state.screens),screen);draw()}
function draw(){const box=q("items");box.replaceChildren();for(const item of state.folders[q("folder").value]?.items||[]){const row=document.createElement("div");row.className="row";const name=document.createElement("a");name.textContent=item.name;name.href="/api/personal-media/file?"+query({file:item.id});name.target="_blank";name.rel="noopener";const rename=document.createElement("button"),remove=document.createElement("button");rename.textContent="Renommer";remove.textContent="Supprimer";rename.onclick=run(async()=>{const n=prompt("Nouveau nom",item.name);if(n)await act("rename-file",{file:item.id,name:n})});remove.onclick=run(async()=>{if(confirm("Supprimer "+item.name+" ?"))await act("remove-file",{file:item.id})});row.append(name,rename,remove);box.append(row)}}
q("owner").onchange=()=>load().catch(e=>q("message").textContent=e.message);q("folder").onchange=draw;
q("create").onclick=run(async()=>{const name=prompt("Nom du dossier");if(name)await act("create",{name})});q("rename").onclick=run(async()=>{const name=prompt("Nouveau nom",state.folders[q("folder").value]?.name);if(name)await act("rename",{name})});q("remove").onclick=run(async()=>{if(confirm("Supprimer le dossier et ses fichiers ?"))await act("remove-folder")});
q("upload").onclick=run(async()=>{for(const file of q("files").files){q("message").textContent="Envoi : "+file.name;await api("/api/personal-media/upload?"+query({name:file.name}),file,true)}q("files").value=""});
q("play").onclick=run(async()=>{if(!q("screen").value)throw Error("Aucun écran autorisé.");await act("play",{screen:q("screen").value})});load().catch(e=>q("message").textContent=e.message);
</script></html>"""
SHARED_ACTION_DIALOG = """<style>#sl-action[data-state="success"] .dialog-heading svg{background:#20362a;color:var(--green)}#sl-action .dialog-heading path{display:none}#sl-action[data-state="confirm"] .icon-confirm,#sl-action[data-state="success"] .icon-success,#sl-action[data-state="error"] .icon-error{display:inline}</style><dialog id="sl-action" aria-labelledby="sl-title" aria-describedby="sl-detail">
  <form class="dialog-body" method="dialog">
    <div class="dialog-heading">
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path class="icon-confirm" d="M12 3 2.8 20h18.4L12 3Z M12 9v4 m0 3h.01"/>
        <path class="icon-busy" d="M21 12a9 9 0 1 1-9-9"/>
        <path class="icon-success" d="m5 12 4 4L19 6"/>
        <path class="icon-error" d="M6 6l12 12M18 6 6 18"/>
      </svg>
      <h2 id="sl-title"></h2>
    </div>
    <p id="sl-detail" role="status"></p>
    <label id="sl-field" hidden>Nom<input id="sl-value" maxlength="180" autocomplete="off"></label>
    <div class="actions">
      <button id="sl-cancel" name="cancel" value="cancel" autofocus>Annuler</button>
      <button value="accept" class="primary" id="sl-accept"></button>
    </div>
  </form>
</dialog><script>{
 const d=document.getElementById("sl-action"),form=d.querySelector("form"),title=d.querySelector("#sl-title"),detail=d.querySelector("#sl-detail"),field=d.querySelector("#sl-field"),input=d.querySelector("#sl-value"),accept=d.querySelector("#sl-accept"),cancel=d.querySelector("#sl-cancel");
 let queue=Promise.resolve();
 function show(t,message,mode,value){const task=()=>new Promise(resolve=>{d.dataset.state=mode;title.textContent=t;detail.textContent=message||"";field.hidden=value===undefined;input.required=value!==undefined;input.value=value??"";accept.textContent=mode==="confirm"?(value===undefined?"Confirmer":"Enregistrer"):"Fermer";cancel.hidden=mode!=="confirm";d.returnValue="cancel";d.addEventListener("close",()=>resolve(d.returnValue==="accept"?(value===undefined?true:input.value.trim()):null),{once:true});d.showModal();if(value!==undefined){input.focus();input.select()}else if(mode!=="confirm")accept.focus();else cancel.focus()});const next=queue.then(task);queue=next.catch(()=>{});return next}
 window.slConfirm=(message,title="Confirmation")=>show(title,message,"confirm");
 window.slPrompt=(message,value="")=>show(message,"Renseigne le nom souhaité.","confirm",value);
 window.slResult=(message,error=false)=>{const notify=typeof showToast==="function"?showToast:window.showToast;notify(error?"Action impossible":"Action effectuée",error?"error":"success",message)};
}</script>"""
PERSONAL_MEDIA_DIALOG = """<dialog id="personal-media-dialog" aria-labelledby="pm-title" style="width:min(920px,94vw)"><div class="dialog-body"><div class="media-heading"><h2 id="pm-title">Images et vidéos · Médias personnels</h2></div><label>Utilisateur<select id="pm-owner"></select></label><div class="media-disks" id="pm-disks"></div><section><h3>Diffusion actuelle par écran</h3><div class="media-output-cards" id="pm-outputs"></div></section><h3>Bibliothèque de médias</h3><label>Dossier<select id="pm-folder"></select></label><div class="actions"><button id="pm-create">Créer un nouveau dossier</button><button id="pm-rename">Renommer le dossier</button><button id="pm-empty">Vider le dossier en cours</button><button id="pm-remove" class="remove">Supprimer le dossier</button></div><label>Écran de destination<select id="pm-screen"></select></label><div class="actions"><button class="primary" id="pm-play">Lancer ce dossier</button><button id="pm-ndi">Revenir à la diffusion NDI</button></div><div class="actions"><label>Durée des images (s)<input id="pm-images" type="number" min="1" max="3600" value="10"></label><label>Durée max. des vidéos (s)<input id="pm-videos" type="number" min="0" max="3600" value="0"></label><button id="pm-timing">Enregistrer les durées</button></div><p class="small">0 = lire la vidéo jusqu’à la fin.</p><div class="media-grid"><div><label>Images et vidéos<input type="file" id="pm-files" multiple accept=".jpg,.jpeg,.png,.mp4,.m4v,.mov,.webm,.mkv"></label><button id="pm-upload">Envoyer les fichiers</button><div class="media-progress" id="pm-progress" hidden><span id="pm-progress-text" class="small"></span><progress max="100" value="0"></progress></div><div class="media-bulk"><label class="check"><input type="checkbox" id="pm-all"> Tout sélectionner</label><button class="button-red" id="pm-bulk-remove">Supprimer</button></div><ol class="media-list" id="pm-items"></ol></div><section class="media-preview"><p id="pm-hint" class="small">Choisis un fichier pour l’afficher.</p><div id="pm-player"></div><div id="pm-file-actions" hidden><div class="action-row"><a class="media-download" id="pm-download" download>Télécharger</a><button id="pm-file-rename">Renommer</button><button id="pm-file-remove" class="button-red">Supprimer</button></div><label>Déplacer vers<select id="pm-move"></select></label><button id="pm-move-go">Déplacer</button></div></section></div><p id="pm-message" role="status" class="small"></p><div class="actions"><button class="primary" id="pm-close">Fermer</button></div></div></dialog><script>{
const dialog=document.getElementById("personal-media-dialog"),q=id=>document.getElementById("pm-"+id);let state,busy=false,selected="",checked=new Set();
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});if(r.status===401){location.href="/connexion";throw Error("Session expirée")}const v=await r.json();if(!r.ok)throw Error(v.error||r.status);return v}
const query=(extra={})=>new URLSearchParams({owner:q("owner").value,folder:q("folder").value,...extra});
const act=(action,extra={})=>api("/api/personal-media",{owner:q("owner").value,folder:q("folder").value,action,...extra});
const run=f=>async()=>{if(busy)return;busy=true;const controls=[...dialog.querySelectorAll("button,select,input")];controls.forEach(x=>x.disabled=true);try{const result=await f();if(result!==false){await load();slResult("Modifications enregistrées.")}}catch(e){q("message").textContent=e.message;slResult(e.message,true)}finally{busy=false;controls.forEach(x=>x.disabled=false);q("progress").hidden=true;sync()}};
function options(node,items,previous){node.replaceChildren(...items.map(([v,n])=>new Option(n,v)));if(items.some(x=>x[0]===previous))node.value=previous}
const items=()=>state?.folders[q("folder").value]?.items||[];
const current=()=>items().find(i=>i.id===selected);
const size=n=>(n/1048576).toLocaleString("fr-FR",{maximumFractionDigits:1})+" Mio";
async function load(){const owner=q("owner").value,folder=q("folder").value,screen=q("screen").value;state=await api("/api/personal-media?"+new URLSearchParams({owner}));options(q("owner"),state.owners.map(x=>[x,x]),state.owner);options(q("folder"),Object.entries(state.folders).map(([id,f])=>[id,f.name]),folder);options(q("screen"),Object.entries(state.screens),screen);q("disks").textContent=state.disk?"Espace disponible sur la régie : "+size(state.disk):"";q("outputs").replaceChildren();for(const [id,o] of Object.entries(state.outputs||{})){const card=document.createElement("div");card.className="disk-card";const title=document.createElement("strong");title.textContent=state.screens[id];const p=document.createElement("p");p.textContent=o.mode;const b=document.createElement("button");b.textContent="Revenir au NDI";b.disabled=!o.local;b.onclick=run(async()=>{if(!await slConfirm("Revenir au NDI sur "+title.textContent+" ?"))return false;await act("ndi",{screen:id})});card.append(title,p,b);q("outputs").append(card)}draw()}
function sync(){const ids=items().map(i=>i.id);q("all").checked=!!ids.length&&ids.every(id=>checked.has(id));q("all").indeterminate=!q("all").checked&&ids.some(id=>checked.has(id));q("bulk-remove").disabled=!checked.size;q("play").disabled=!ids.length||!q("screen").value;q("ndi").disabled=!q("screen").value;q("remove").disabled=q("folder").value==="general"}
function preview(){const item=current();q("player").replaceChildren();q("file-actions").hidden=!item;q("hint").hidden=!!item;if(!item)return;const url="/api/personal-media/file?"+query({file:item.id}),video=/[.](mp4|m4v|mov|webm|mkv)$/i.test(item.name);const el=document.createElement(video?"video":"img");if(video){el.controls=true;el.preload="metadata"}else el.alt=item.name;el.src=url;const name=document.createElement("h3");name.textContent=item.name;const meta=document.createElement("p");meta.className="small media-preview-meta";meta.textContent=size(item.size);q("player").append(name,meta,el);q("download").href=url;q("download").download=item.name;options(q("move"),Object.entries(state.folders).filter(([id])=>id!==q("folder").value).map(([id,f])=>[id,f.name]));q("move-go").disabled=!q("move").value}
function draw(){const folder=state.folders[q("folder").value];q("images").value=folder?.timing?.image_seconds??10;q("videos").value=folder?.timing?.video_seconds??0;checked=new Set([...checked].filter(id=>items().some(i=>i.id===id)));if(!current())selected="";const box=q("items");box.replaceChildren();for(const item of items()){const row=document.createElement("li"),check=document.createElement("input"),icon=document.createElement("span"),handle=document.createElement("button"),name=document.createElement("button");check.type="checkbox";check.checked=checked.has(item.id);check.onchange=()=>{if(check.checked)checked.add(item.id);else checked.delete(item.id);sync()};icon.textContent=/[.](jpg|jpeg|png)$/i.test(item.name)?"▧":"▷";handle.textContent="⠿";handle.className="drag-handle";handle.draggable=true;handle.ondragstart=e=>e.dataTransfer.setData("text/plain",item.id);row.ondragover=e=>e.preventDefault();row.ondrop=e=>{e.preventDefault();const file=e.dataTransfer.getData("text/plain");if(file&&file!==item.id)run(()=>act("reorder",{file,anchor:item.id}))()};name.textContent=item.name;name.style.whiteSpace="normal";name.style.textAlign="left";name.setAttribute("aria-pressed",String(selected===item.id));name.onclick=()=>{selected=item.id;draw()};row.append(check,icon,handle,name);box.append(row)}preview();sync()}
q("owner").onchange=()=>{selected="";checked.clear();load().catch(e=>slResult(e.message,true))};q("folder").onchange=()=>{selected="";checked.clear();draw()};q("screen").onchange=sync;q("all").onchange=()=>{checked=new Set(q("all").checked?items().map(i=>i.id):[]);draw()};
q("create").onclick=run(async()=>{const name=await slPrompt("Nom du dossier");if(!name)return false;await act("create",{name})});q("rename").onclick=run(async()=>{const name=await slPrompt("Nouveau nom",state.folders[q("folder").value]?.name);if(!name)return false;await act("rename",{name})});q("remove").onclick=run(async()=>{if(!await slConfirm("Supprimer le dossier et ses fichiers ?"))return false;await act("remove-folder")});q("empty").onclick=run(async()=>{if(!await slConfirm("Vider ce dossier ?"))return false;await act("empty-folder")});q("timing").onclick=run(()=>act("timing",{image_seconds:Number(q("images").value),video_seconds:Number(q("videos").value)}));
q("file-rename").onclick=run(async()=>{const item=current();if(!item)return false;const name=await slPrompt("Nouveau nom",item.name);if(!name)return false;await act("rename-file",{file:item.id,name})});q("file-remove").onclick=run(async()=>{if(!current()||!await slConfirm("Supprimer "+current().name+" ?"))return false;await act("remove-file",{file:selected})});q("bulk-remove").onclick=run(async()=>{if(!checked.size||!await slConfirm("Supprimer "+checked.size+" fichier(s) ?"))return false;await act("remove-files",{files:[...checked]});checked.clear()});q("move-go").onclick=run(()=>act("move",{file:selected,destination:q("move").value}));
q("upload").onclick=run(async()=>{const files=[...q("files").files];if(!files.length)return false;q("progress").hidden=false;const bar=q("progress").querySelector("progress");let done=0,total=files.reduce((n,f)=>n+f.size,0);for(const file of files){await new Promise((resolve,reject)=>{const xhr=new XMLHttpRequest();xhr.open("POST","/api/personal-media/upload?"+query({name:file.name}));xhr.setRequestHeader("Content-Type","application/octet-stream");xhr.upload.onprogress=e=>{bar.value=100*(done+e.loaded)/total;q("progress-text").textContent=file.name+" · "+Math.round(bar.value)+" %"};xhr.onload=()=>{if(xhr.status>=200&&xhr.status<300)resolve();else{let msg="Envoi impossible";try{msg=JSON.parse(xhr.responseText).error||msg}catch{}reject(Error(msg))}};xhr.onerror=()=>reject(Error("Transfert interrompu"));xhr.send(file)});done+=file.size}q("files").value=""});
q("play").onclick=run(async()=>{if(!await slConfirm("Diffuser ce dossier sur l’écran sélectionné ?"))return false;await act("play",{screen:q("screen").value})});q("ndi").onclick=run(async()=>{if(!await slConfirm("Revenir à la diffusion NDI ?"))return false;await act("ndi",{screen:q("screen").value})});
const open=()=>{if(dialog.open)return;dialog.showModal();load().catch(e=>slResult(e.message,true))};document.querySelectorAll("[data-personal-media]").forEach(b=>b.addEventListener("click",open));q("close").onclick=()=>{if(!busy)dialog.close()};dialog.addEventListener("cancel",e=>{if(busy)e.preventDefault()});dialog.addEventListener("close",()=>q("player").replaceChildren());
}</script>"""


USERS_PAGE = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Utilisateurs · SoundLive NDI</title><style>body{background:#171319;color:#fff;font:16px system-ui;margin:24px}button,input,select{font:inherit;padding:8px;margin:4px;background:#302631;color:white;border:1px solid #806070;border-radius:6px}a{color:#ffc0cd}fieldset{margin:12px 0;border:1px solid #604a59}label{display:inline-block;margin:4px}table{border-collapse:collapse}td,th{padding:6px;text-align:left}#matrix{overflow:auto}button{cursor:pointer}</style><a href="/">← Régie</a><h1>Utilisateurs et groupes</h1><p>Les droits directs et ceux des groupes se cumulent. Le compte regie reste administrateur.</p><select id="kind"><option value="users">Utilisateurs</option><option value="groups">Groupes</option></select><select id="existing"></select><form id="editor"><label>Identifiant <input id="name" required pattern="[A-Za-z0-9_.-]{1,32}"></label><section id="userfields"><label>Mot de passe <input id="password" type="password" autocomplete="new-password"></label><label><input id="enabled" type="checkbox" checked> Actif</label><div id="members"></div></section><h2>Droits par écran</h2><div id="matrix"></div><button>Enregistrer</button><button type="button" id="remove">Supprimer</button></form><p id="message" role="status"></p><script>
const q=id=>document.getElementById(id), rights={view:"Voir",preview:"Aperçu",play:"Lecture/arrêt",source:"Source/qualité",audio:"Audio",media:"Diffuser médias",identify:"Identifier",maintenance:"Maintenance",configuration:"Orientation"};let db;
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});const v=await r.json();if(!r.ok)throw Error(v.error||r.status);return v}
function draw(){const kind=q("kind").value,n=q("existing").value,entry=db[kind][n]||{};q("name").value=n;q("name").readOnly=!!n;q("password").value="";q("enabled").checked=entry.enabled!==false;q("userfields").hidden=kind!=="users";q("members").replaceChildren();for(const g of Object.keys(db.groups)){const l=document.createElement("label"),i=document.createElement("input");i.type="checkbox";i.value=g;i.checked=(entry.groups||[]).includes(g);l.append(i,g);q("members").append(l)}const table=document.createElement("table"),head=table.insertRow();for(const title of ["Écran",...Object.values(rights)]){const th=document.createElement("th");th.textContent=title;head.append(th)}for(const [id,name] of Object.entries(db.screens)){const row=table.insertRow();row.insertCell().textContent=name;for(const p of Object.keys(rights)){const i=document.createElement("input");i.type="checkbox";i.dataset.screen=id;i.value=p;i.checked=(entry.grants?.[id]||[]).includes(p);i.setAttribute("aria-label",name+" "+rights[p]);row.insertCell().append(i)}}q("matrix").replaceChildren(table);q("remove").disabled=!n}
async function load(){db=await api("/api/users");q("existing").replaceChildren(new Option("Nouveau",""),...Object.keys(db[q("kind").value]).map(x=>new Option(x,x)));draw()}
q("kind").onchange=()=>load().catch(e=>q("message").textContent=e.message);q("existing").onchange=draw;
q("editor").onsubmit=async e=>{e.preventDefault();try{const grants={};for(const i of q("matrix").querySelectorAll("input:checked"))(grants[i.dataset.screen]??=[]).push(i.value);await api("/api/users",{kind:q("kind").value,name:q("name").value,password:q("password").value,enabled:q("enabled").checked,groups:[...q("members").querySelectorAll("input:checked")].map(i=>i.value),grants});await load();q("message").textContent="Enregistré."}catch(e){q("message").textContent=e.message}};
q("remove").onclick=async()=>{if(!confirm("Supprimer cet élément ?"))return;try{await api("/api/users",{kind:q("kind").value,name:q("existing").value,delete:true});await load()}catch(e){q("message").textContent=e.message}};load().catch(e=>q("message").textContent=e.message);
</script></html>"""
OPERATOR_PAGE = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>SoundLive NDI · Opérateur</title><style>__SHARED_STYLE__
.operator-controls{display:grid;gap:8px}.operator-controls label{margin:8px 0}.operator-preview img{display:block;width:100%;max-height:280px;object-fit:contain;border-radius:8px;background:#100d12}.operator-preview img[hidden]{display:none}.operator-output{margin-top:16px}.operator-output legend{font-weight:600}.operator-output .actions{margin-top:8px}.operator-header-statuses{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0}.operator-preview{margin:8px 0}.operator-preview button{width:100%}.operator-card h2{min-width:0}.operator-card .badge{white-space:nowrap}.operator-controls button{white-space:normal}
</style><header><span class="mark" aria-hidden="true"></span><strong>SoundLive-NDI</strong><span class="release">v__OP_VERSION__</span><span id="count"></span><button type="button" data-personal-media>Mes médias</button><span class="connected-user">Connecté : <strong>__CONNECTED_USER__</strong></span><button id="logout">Me déconnecter</button></header><main><div class="heading"><h1>Gestion des écrans NDI</h1><div class="heading-actions"><button id="organize">Organiser les écrans</button><button id="group-action">Actions de groupe</button><button type="button" id="identify-manager-open" aria-haspopup="dialog" disabled>Identifier les écrans…</button></div></div><details class="preview-section" id="source-previews" open hidden><summary>Aperçu des sources NDI disponibles</summary><label class="check"><input type="checkbox" id="auto-previews" checked> Actualiser automatiquement les aperçus visibles</label><p id="source-empty" class="muted" hidden>Aucune source NDI détectée par les lecteurs connectés.</p><div class="preview-grid" id="source-grid"></div></details><p id="message" role="status"></p><div id="screens"></div></main><footer class="project-credits">© 2026 Société SoundLive · Auteur : DUVERNOY Matthieu &amp; ChatGPT · <a href="mailto:contact@soundlive.fr">contact@soundlive.fr</a></footer><dialog id="organizer" style="width:min(640px,92vw)"><div class="dialog-body"><h2>Organiser les écrans</h2><p>Personnalise l’ordre des groupes et des encarts dans ton navigateur.</p><div id="organizer-list"></div><div class="actions"><button id="reset-layout">Réinitialiser</button><button id="close-organizer" class="primary">Fermer</button></div></div></dialog><dialog id="group-dialog" style="width:min(650px,92vw)"><div class="dialog-body"><h2>Actions de groupe</h2><label>Action<select id="group-operation"><option value="start">Lecture</option><option value="stop">Arrêter</option><option value="mute">Couper le son</option><option value="unmute">Activer le son</option><option value="volume">Volume</option><option value="source">Source NDI</option><option value="quality">Qualité</option><option value="orientation">Orientation</option></select></label><label id="group-value-label" hidden>Valeur<select id="group-value"></select></label><label id="group-volume-label" hidden>Volume en %<input id="group-volume" type="number" min="0" max="100" value="50"></label><div class="actions"><button id="group-all">Tout sélectionner</button><button id="group-none">Tout désélectionner</button></div><div id="group-choices"></div><p id="group-error" role="status"></p><div class="actions"><button id="close-group">Fermer</button><button id="apply-group" class="primary">Appliquer</button></div></div></dialog><dialog id="identify-manager" aria-labelledby="identify-manager-title">
  <div class="dialog-body">
    <h2 id="identify-manager-title">Identifier les écrans</h2>
    <p>Affiche le nom et le groupe sur les écrans physiques pour les repérer dans la salle.
       L’identification reste visible tant qu’elle est activée.</p>
    <div class="actions">
      <button type="button" id="identify-select-all">Tout sélectionner</button>
      <button type="button" id="identify-unselect-all">Tout désélectionner</button>
    </div>
    <div id="identify-manager-list" role="group" aria-label="Liste des écrans"
         style="display:grid;gap:8px;max-height:45vh;overflow:auto;margin:12px 0"></div>
    <p id="identify-manager-message" class="small" role="status"></p>
    <div class="actions">
      <button type="button" class="primary" id="identify-selected-show">Afficher sur la sélection</button>
      <button type="button" id="identify-selected-hide">Masquer sur la sélection</button>
      <button type="button" id="identify-hide-all">Tout masquer</button>
      <button type="button" id="identify-manager-close">Fermer</button>
    </div>
  </div>
</dialog>__SHARED_TEMPLATES__<script>
const q=id=>document.getElementById(id);let cards=new Map(),latestRows=[];
async function api(path,data){const r=await fetch(path,data===undefined?{}:{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(data)});if(r.status===401){location.href="/connexion";throw Error("Session expirée")}const v=await r.json();if(!r.ok)throw Error(v.error||r.status);return v}
const run=f=>async()=>{try{const result=await f();q("message").textContent="";if(result!==undefined)await slResult("La commande a été transmise.")}catch(e){q("message").textContent=e.message;await slResult(e.message,true)}};
function build(s){
 const a=q("card").content.querySelector(".primary-output").cloneNode(true),title=a.querySelector("legend"),status=document.createElement("span"),caption=document.createElement("span");
 caption.className="output-title";title.replaceChildren(caption,status);status.className="badge";
 const has=p=>s.rights.includes(p),act=(action,data={})=>api("/api/operator/action",{id:s.id,output:s.output,action,...data}),patch=changes=>act("settings",{changes}),get=c=>a.querySelector(c),hide=c=>get(c)?.remove();
 const values=(node,items,current)=>{node.replaceChildren(...items.map(x=>new Option(x[1],x[0])));node.value=String(current??"")};
 const extraButton=(label,fn)=>{const b=document.createElement("button");b.type="button";b.className="full-action";b.textContent=label;b.onclick=run(fn);a.append(b);return b};
 hide(".resolution-controls");
 if(has("source")){const source=get(".source"),detected=get(".detected-sources");source.value=s.settings.source||"";source.required=false;values(detected,[["","Choisir une source"],...s.sources.map(x=>[x,x])],source.value);detected.onchange=()=>{source.value=detected.value};get(".bandwidth").value=String(s.settings.bandwidth??"0")}else{hide(".ndi-sources");get(".bandwidth").closest("label").remove()}
 if(has("configuration"))get(".orientation").value=s.settings.orientation||"landscape";else get(".orientation").closest("label").remove();
 if(has("audio")){
  const label=document.createElement("label"),route=document.createElement("select");label.className="audio-route";label.append("Sortie audio",route);values(route,[["","Automatique"],...s.audio_devices.map(d=>[d.id,d.label])],s.audio_route);route.onchange=run(()=>act("audio",{target:route.value}));
  const vol=get(".volume");vol.closest("label").before(label);vol.value=s.settings.volume??0;const update=()=>get("output").textContent=vol.value+" %";vol.oninput=update;update();get(".muted").checked=!!s.settings.muted;
 }else{get(".volume").closest("label").remove();get(".muted").closest("label").remove()}
 const apply=get(".action-row .primary");apply.type="button";apply.onclick=run(async()=>{const changes={};if(has("source")){changes.source=get(".source").value;changes.bandwidth=get(".bandwidth").value}if(has("configuration"))changes.orientation=get(".orientation").value;if(has("audio")){changes.volume=Number(get(".volume").value);changes.muted=get(".muted").checked}if(Object.keys(changes).length&&await slConfirm("Appliquer les réglages à "+s.name+" ?","Modifier les réglages"))return patch(changes)});
 if(!["source","configuration","audio"].some(has))apply.remove();
 const toggle=get(".toggle");if(has("play")){toggle.textContent=s.settings.enabled?"Stop":"Reprendre";toggle.onclick=run(async()=>{if(await slConfirm((s.settings.enabled?"Arrêter":"Reprendre")+" la diffusion sur "+s.name+" ?"))return patch({enabled:!s.settings.enabled})})}else toggle.remove();
 if(!get(".action-row").children.length)hide(".action-row");
 if(has("media")){const label=document.createElement("label"),folder=document.createElement("select");label.append("Dossier média",folder);values(folder,Object.entries(s.folders),Object.keys(s.folders)[0]);a.append(label);extraButton("Diffuser le dossier",async()=>{if(await slConfirm("Diffuser le dossier sur "+s.name+" ?"))return act("media",{folder:folder.value,enabled:true})});const ret=get(".return-ndi");ret.hidden=false;ret.onclick=run(async()=>{if(await slConfirm("Revenir à la diffusion NDI sur "+s.name+" ?"))return act("media",{folder:folder.value,enabled:false})});a.append(ret)}else hide(".return-ndi");
 if(has("preview")){const v=makeOperatorPreview("Capture de l’écran",{screen:s.id,output:s.output},s.display_capture,s.capture_consent);title.after(v.el)}

 if(s.maintenance){extraButton("Redémarrer",async()=>{if(await slConfirm("Redémarrer le lecteur ?"))return act("reboot")});extraButton("Éteindre",async()=>{if(await slConfirm("Éteindre le lecteur ?"))return act("poweroff")})}
 return {a,title:caption,status}
}
function makeOperatorPreview(title,target,available=true,consent=false){
 const el=q("preview-template").content.firstElementChild.cloneNode(true),img=el.querySelector("img"),button=el.querySelector("button"),status=el.querySelector("p"),placeholder=el.querySelector(".preview-placeholder");
 el.querySelector("strong").textContent=title;button.disabled=!available;button.textContent=consent?"Autoriser la capture":"Capturer un aperçu";if(!available)placeholder.querySelector("span").textContent="Aperçu indisponible";
 const v={el,img,button,status,placeholder,target,available,consent,next:0,pending:false};button.onclick=()=>captureSource(v);return v
}
const previewViews=new Map();
function sourceGallery(rows){
 const names=new Set(rows.flatMap(s=>s.preview_sources||[])),canPreview=rows.some(s=>s.rights.includes("preview"));q("source-previews").hidden=false;q("source-empty").hidden=names.size>0;
 for(const [name,v] of previewViews)if(!names.has(name)||v.available!==canPreview){v.el.remove();previewViews.delete(name)}
 for(const name of [...names].sort())if(!previewViews.has(name)){
  const v=makeOperatorPreview(name,{source:name},canPreview);if(!canPreview)v.placeholder.querySelector("span").textContent="Droit d’aperçu requis";previewViews.set(name,v);q("source-grid").append(v.el);
 }
}
async function captureSource(v,automatic=false){if(v.pending||!v.available||automatic&&v.consent)return;v.pending=true;v.button.disabled=true;v.status.textContent="Capture en cours…";try{let p=await api("/api/preview",{...v.target,previous:v.previous,automatic});const end=Date.now()+(p.consent?92000:18000);while(p.state==="pending"&&Date.now()<end){await new Promise(r=>setTimeout(r,950));p=await api("/api/preview?"+new URLSearchParams(v.target))}if(p.state!=="ready"||!p.image)throw Error(p.error||"Capture indisponible");v.previous=p.id;v.img.src="data:image/png;base64,"+p.image;v.img.hidden=false;v.placeholder.hidden=true;v.status.textContent="Image capturée à "+new Date(p.captured*1000).toLocaleTimeString("fr-FR");v.button.textContent="Actualiser l’aperçu";v.next=Date.now()+10000}catch(e){v.status.textContent=e.message;v.next=Date.now()+30000}finally{v.pending=false;v.button.disabled=!v.available}}
setInterval(()=>{if(!q("source-previews").open||!q("auto-previews").checked||[...previewViews.values()].some(v=>v.pending))return;const v=[...previewViews.values()].find(v=>v.available&&v.next<=Date.now()&&v.el.getBoundingClientRect().bottom>0&&v.el.getBoundingClientRect().top<innerHeight);if(v)captureSource(v,true)},3000);
function badge(node,state,label=""){node.className="badge"+(state==="Lecture"?" online":state==="Arrêté"?" stopped":(state==="En attente"||state==="Démarrage")?" waiting":"");node.textContent=label+state}
const groups=new Map(), readerCards=new Map();
let layout={groups:[],screens:{}};try{layout=JSON.parse(localStorage.getItem("soundlive-operator-layout")||"null")||layout}catch{}
const saveLayout=()=>{try{localStorage.setItem("soundlive-operator-layout",JSON.stringify(layout))}catch{}};
function rank(order,key){const i=order.indexOf(key);return i<0?1e9:i}
function reorder(){for(const [name,g] of [...groups].sort((a,b)=>rank(layout.groups,a[0])-rank(layout.groups,b[0])))q("screens").append(g.section);for(const [name,g] of groups){const order=layout.screens[name]||[];for(const r of [...readerCards.values()].filter(r=>r.group===name).sort((a,b)=>rank(order,a.key)-rank(order,b.key)))g.grid.append(r.el)}}
function move(order,source,dest){const a=order.indexOf(source),b=order.indexOf(dest);if(a<0||b<0)return order;const copy=[...order];copy.splice(a,1);copy.splice(b,0,source);return copy}
let dragging=null;
function dragHandle(target,key,scope,group=""){
 const b=document.createElement("button");b.type="button";b.className="drag-handle";b.textContent="⠿";b.title="Déplacer · glisser-déposer ou flèches";b.draggable=true;
 const keys=()=>scope==="groups"?[...q("screens").children].map(e=>e.dataset.orderKey):[...groups.get(group).grid.children].map(e=>e.dataset.orderKey);
 const apply=(from,to)=>{const list=move(keys(),from,to);if(scope==="groups")layout.groups=list;else layout.screens[group]=list;saveLayout();reorder();if(q("organizer").open)drawOrganizer()};
 b.ondragstart=e=>{dragging={key,scope,group};e.dataTransfer.setData("text/plain",key);e.dataTransfer.effectAllowed="move"};b.ondragend=()=>{dragging=null;target.classList.remove("drop-target")};
 target.dataset.orderKey=key;target.ondragover=e=>{if(dragging&&dragging.scope===scope&&dragging.group===group){e.preventDefault();target.classList.add("drop-target")}};target.ondragleave=()=>target.classList.remove("drop-target");target.ondrop=e=>{if(dragging&&dragging.scope===scope&&dragging.group===group){e.preventDefault();e.stopPropagation();apply(dragging.key,key);dragging=null}target.classList.remove("drop-target")};
 b.onkeydown=e=>{if(!["ArrowUp","ArrowDown","ArrowLeft","ArrowRight"].includes(e.key))return;e.preventDefault();const k=keys(),i=k.indexOf(key)+(e.key==="ArrowUp"||e.key==="ArrowLeft"?-1:1);if(k[i]!==undefined)apply(key,k[i])};return b;
}
function drawOrganizer(){const box=q("organizer-list");box.replaceChildren();for(const section of q("screens").children){const name=section.dataset.orderKey,g=groups.get(name),p=document.createElement("p");p.textContent=name||"Sans groupe";box.append(p);for(const el of g.grid.children){const r=readerCards.get(el.dataset.orderKey),line=document.createElement("div");line.className="row";line.textContent=r.title.textContent;for(const [symbol,delta] of [["↑",-1],["↓",1]]){const b=document.createElement("button");b.textContent=symbol;b.onclick=()=>{const k=[...g.grid.children].map(x=>x.dataset.orderKey),i=k.indexOf(r.key)+delta;if(k[i]!==undefined){layout.screens[name]=move(k,r.key,k[i]);saveLayout();reorder();drawOrganizer()}};line.append(b)}box.append(line)}}}
q("organize").onclick=()=>{drawOrganizer();q("organizer").showModal()};q("close-organizer").onclick=()=>q("organizer").close();q("reset-layout").onclick=()=>{layout={groups:[],screens:{}};saveLayout();for(const [name,g] of [...groups].sort((a,b)=>a[0].localeCompare(b[0])))q("screens").append(g.section);for(const g of groups.values())for(const el of [...g.grid.children].sort((a,b)=>readerCards.get(a.dataset.orderKey).title.textContent.localeCompare(readerCards.get(b.dataset.orderKey).title.textContent)))g.grid.append(el);drawOrganizer()};
const groupRights={start:"play",stop:"play",mute:"audio",unmute:"audio",volume:"audio",source:"source",quality:"source",orientation:"configuration"};
function groupChoices(){const op=q("group-operation").value,eligible=latestRows.filter(s=>s.rights.includes(groupRights[op]));q("group-choices").replaceChildren();for(const s of eligible){const l=document.createElement("label"),i=document.createElement("input");l.className="check";i.type="checkbox";i.value=s.id+":"+s.output;l.append(i,s.name);q("group-choices").append(l)}q("group-volume-label").hidden=op!=="volume";q("group-value-label").hidden=!["source","quality","orientation"].includes(op);const choices=op==="source"?[...new Set(eligible.flatMap(s=>s.sources))].map(x=>[x,x]):op==="quality"?[["0","High"],["1","Low"]]:[["landscape","Paysage"],["portrait-left","Portrait gauche"],["portrait-right","Portrait droite"]];q("group-value").replaceChildren(...choices.map(x=>new Option(x[1],x[0])));q("group-error").textContent=""}
q("group-action").onclick=()=>{const opts=[...q("group-operation").options];for(const o of opts)o.disabled=!latestRows.some(s=>s.rights.includes(groupRights[o.value]));if(q("group-operation").selectedOptions[0]?.disabled)q("group-operation").value=opts.find(o=>!o.disabled)?.value||"";groupChoices();q("group-dialog").showModal()};q("group-operation").onchange=groupChoices;q("close-group").onclick=()=>q("group-dialog").close();q("group-all").onclick=()=>q("group-choices").querySelectorAll("input").forEach(i=>i.checked=true);q("group-none").onclick=()=>q("group-choices").querySelectorAll("input").forEach(i=>i.checked=false);
q("apply-group").onclick=async()=>{const op=q("group-operation").value,ids=[...q("group-choices").querySelectorAll("input:checked")].map(i=>i.value),v=q("group-value").value;const changes=op==="start"?{enabled:true}:op==="stop"?{enabled:false}:op==="mute"?{muted:true}:op==="unmute"?{muted:false}:op==="volume"?{volume:Number(q("group-volume").value)}:op==="source"?{source:v}:op==="quality"?{bandwidth:v}:{orientation:v};if(!ids.length){q("group-error").textContent="Sélectionne au moins un écran.";slResult("Sélectionne au moins un écran.",true);return}if(!await slConfirm("Appliquer à "+ids.length+" écran(s) ?","Actions de groupe"))return;try{await api("/api/operator/group",{ids,changes});q("group-dialog").close();await refresh();await slResult("Les réglages de groupe ont été transmis.")}catch(e){q("group-error").textContent=e.message;await slResult(e.message,true)}};

function getGroup(name){let g=groups.get(name);if(!g){const section=document.createElement("section"),title=document.createElement("h2"),grid=document.createElement("div");section.className="screen-group";title.className="screen-group-title";title.textContent=name||"Sans groupe";grid.className="grid";section.append(title,grid);q("screens").append(section);g={section,grid};groups.set(name,g);title.append(dragHandle(section,name,"groups"))}return g}
function renderReaderHealth(r,s){
 const el=r.el, h=s.health||{}, d=h.diagnostic||{}, online=h.online;
 const set=(selector,value,level)=>{const n=el.querySelector(selector);n.dataset.level=level;n.querySelector("strong").textContent=value};
 const finite=Number.isFinite, severity=(v,a,b)=>!finite(v)?"unknown":v<a?"good":v<b?"warn":"bad", pct=v=>finite(v)?Math.round(v)+" %":"—";
 const load=(used,total)=>finite(used)&&total>0?100*used/total:null;
 el.querySelector(".device-kind").textContent=({pi:"Raspberry Pi",ubuntu:"PC Ubuntu",windows:"PC Windows"})[h.kind]||"";
 el.querySelector(".address").textContent=h.ip||"";
 el.querySelector(".client-release").textContent=h.version?"Lecteur v"+h.version:"";
 el.querySelector(".metrics").hidden=false;
 set(".connection",online?(finite(h.latency)?Math.round(h.latency)+" ms":"—"):"Hors ligne",online?severity(h.latency,30,100):"bad");
 const cpu=online?d.cpu_pct:null,ram=online?load(d.ram_used,d.ram_total):null,disk=online?load(d.disk_used,d.disk_total):null;
 set(".cpu",pct(cpu),severity(cpu,65,85));set(".ram",pct(ram),severity(ram,65,85));set(".disk",pct(disk),severity(disk,h.disk_limit-10,h.disk_limit));
 el.querySelector(".disk-free").textContent=online&&finite(d.disk_free)?(d.disk_free/2**30).toLocaleString("fr-FR",{maximumFractionDigits:1})+" Gio libres":"";
 const links=online?(d.interfaces||[]):[],link=links.find(i=>i.to_regie)||links[0];
 el.querySelector(".screen-traffic").hidden=false;
 for(const [cls,field,dir] of [[".rate-rx","rx_mbps","rx"],[".rate-tx","tx_mbps","tx"]]){const v=link?.[field];set(cls,finite(v)?v.toLocaleString("fr-FR",{maximumFractionDigits:2})+" Mbit/s":"—",finite(v)?dir:"unknown")}
 const caption=el.querySelector(".traffic-interface-label");caption.dataset.kind=link?.kind||"";caption.querySelector("span").textContent=!online?"Hors ligne":link?[link.name,link.kind==="wifi"?"Wi-Fi":link.kind==="ethernet"?"Ethernet":"",link.ssid].filter(Boolean).join(" · "):"Mesure indisponible";
}
function getReader(s){
 const key=JSON.stringify([s.group||"",s.id]);let r=readerCards.get(key);
 if(!r){
  const el=q("card").content.firstElementChild.cloneNode(true),head=el.querySelector(".card-heading"),title=head.querySelector("h2"),badge=head.querySelector(".badge"),statuses=document.createElement("div"),details=el.querySelector(".screen-controls"),body=el.querySelector(".config");
  el.classList.add("operator-card");head.querySelector(".rename-reader").remove();statuses.className="header-statuses";head.append(statuses);
  for(const child of [...el.children])if(![head,details].includes(child)&&!child.matches(".device-meta,.metrics,.screen-traffic"))child.remove();
   const target=el.querySelector(".identify");target.hidden=!s.identify;target.onclick=async()=>{if(identifyBusy)return;const current=latestRows.find(x=>x.id===s.id&&x.identify);if(!current)return;identifyBusy=true;target.disabled=true;try{await api("/api/operator/identify",{ids:[s.id],enabled:!current.identified});slResult(current.identified?"Identification masquée.":"Identification activée.");await refresh()}catch(e){slResult(e.message,true)}finally{identifyBusy=false;target.disabled=false;await refresh().catch(()=>{})}};for(const b of el.querySelectorAll(".metrics button,.screen-traffic button")){const d=document.createElement("div");d.className=b.className;d.innerHTML=b.innerHTML;b.replaceWith(d)}
  for(const child of [...details.children])if(child!==body&&child.tagName!=="SUMMARY")child.remove();
  details.open=true;body.replaceChildren();body.onsubmit=e=>e.preventDefault();
  getGroup(s.group||"").grid.append(el);r={el,title,badge,statuses,body,key,group:s.group||""};readerCards.set(key,r);head.insertBefore(dragHandle(el,key,"screens",r.group),badge)
 }return r
}
async function refresh(){const rows=await api("/api/operator/screens"),keys=new Set(),activeReaders=new Map();latestRows=rows;sourceGallery(rows);q("identify-manager-open").disabled=identifyBusy||!identifyRows().length;if(q("identify-manager").open&&!identifyBusy)renderIdentifyManager();q("group-action").disabled=!rows.some(s=>s.rights.some(p=>Object.values(groupRights).includes(p)));q("organize").disabled=!rows.length;q("count").textContent=rows.filter(s=>s.status!=="Hors ligne").length+" / "+rows.length+" écrans connectés";for(const s of rows){const k=s.id+":"+s.output;keys.add(k);const r=getReader(s);if(!activeReaders.has(r.key))activeReaders.set(r.key,[]);activeReaders.get(r.key).push(s);const signature=JSON.stringify([s.rights,s.sources,s.audio_devices,s.folders,s.identify,s.identified,s.maintenance,s.settings,s.audio_route,s.display_capture,s.capture_consent]);let c=cards.get(k);if(!c||c.signature!==signature&&!c.a.contains(document.activeElement)){const n=build(s);n.signature=signature;if(c)c.a.replaceWith(n.a);cards.set(k,n);c=n}if(c.a.parentElement!==r.body)r.body.append(c.a);c.title.textContent="Écran "+(Number(s.output)+1)+(s.output_name?" · "+s.output_name:"")+(s.width&&s.height?" · "+s.width+" × "+s.height:"")+" ";badge(c.status,s.status)}
for(const [k,c] of cards)if(!keys.has(k)){c.a.remove();cards.delete(k)}
for(const [k,r] of readerCards){const outputs=activeReaders.get(k);if(!outputs){r.el.remove();readerCards.delete(k);continue}r.title.textContent=outputs[0].reader_name;const target=r.el.querySelector(".identify"),ident=outputs.find(s=>s.identify);target.hidden=!ident;target.disabled=identifyBusy||!ident;target.setAttribute("aria-pressed",String(!!ident?.identified));target.title=(ident?.identified?"Masquer l’identification de ":"Identifier ")+r.title.textContent;target.setAttribute("aria-label",target.title);renderReaderHealth(r,outputs[0]);r.badge.hidden=outputs.length>1;r.statuses.hidden=outputs.length===1;badge(r.badge,outputs[0].status);r.statuses.replaceChildren(...outputs.map(s=>{const b=document.createElement("span");badge(b,s.status,"Écran "+(Number(s.output)+1)+" : ");return b}))}
for(const [name,g] of groups)if(!g.grid.children.length){g.section.remove();groups.delete(name)}
if(!dragging&&!document.activeElement?.closest(".operator-card"))reorder();
}
let identifyBusy=false;
function identifyRows(){const readers=new Map();for(const s of latestRows){if(!s.identify)continue;const row=readers.get(s.id);if(!row||s.output==="0")readers.set(s.id,s)}return [...readers.values()]}
function renderIdentifyManager(){const rows=identifyRows(),list=q("identify-manager-list"),selected=new Set([...list.querySelectorAll("input:checked")].map(x=>x.value)),first=!list.hasChildNodes();list.replaceChildren(...rows.map(s=>{const wrapper=document.createElement("label");wrapper.className="check";wrapper.style.cssText="display:flex;justify-content:space-between;align-items:center;gap:14px;padding:8px 12px;border:1px solid #526076;border-radius:7px";const left=document.createElement("span"),box=document.createElement("input"),name=document.createElement("strong"),status=document.createElement("small");box.type="checkbox";box.value=s.id;box.checked=first||selected.has(s.id);box.disabled=identifyBusy;name.textContent=s.reader_name;left.append(box," ",name);status.textContent=s.status==="Hors ligne"?"Hors ligne":s.identified?"Identification activée":"En ligne";status.style.color=s.identified?"#a1e8b4":"#c5d1df";wrapper.append(left,status);return wrapper}));q("identify-manager").querySelectorAll("button").forEach(b=>b.disabled=identifyBusy);q("identify-manager-open").disabled=identifyBusy||!rows.length}
q("identify-manager-open").onclick=()=>{q("identify-manager-list").replaceChildren();q("identify-manager-message").textContent="";q("identify-manager").showModal();renderIdentifyManager()};
q("identify-manager-close").onclick=()=>q("identify-manager").close();
q("identify-manager").addEventListener("cancel",e=>{if(identifyBusy)e.preventDefault()});
for(const [id,on] of [["identify-select-all",true],["identify-unselect-all",false]])q(id).onclick=()=>q("identify-manager-list").querySelectorAll("input").forEach(x=>x.checked=on);
async function identifySelection(enabled,all=false){if(identifyBusy)return;const ids=all?identifyRows().map(s=>s.id):[...q("identify-manager-list").querySelectorAll("input:checked")].map(x=>x.value);if(!ids.length){slResult("Sélectionne au moins un écran.",true);return}identifyBusy=true;renderIdentifyManager();try{const result=await api("/api/operator/identify",{ids,enabled});q("identify-manager-message").textContent=result.count+" lecteur(s) modifié(s).";slResult(result.count+" lecteur(s) modifié(s).");await refresh()}catch(e){q("identify-manager-message").textContent=e.message;slResult(e.message,true)}finally{identifyBusy=false;renderIdentifyManager()}}
q("identify-selected-show").onclick=()=>identifySelection(true);q("identify-selected-hide").onclick=()=>identifySelection(false);q("identify-hide-all").onclick=()=>identifySelection(false,true);
q("logout").onclick=run(async()=>{if(!await slConfirm("Tu reviendras à la page de connexion de la régie.","Me déconnecter"))return;await api("/api/session/deconnexion",{});location.href="/connexion?etat=fermee"});refresh().catch(e=>q("message").textContent=e.message);setInterval(()=>refresh().catch(e=>q("message").textContent=e.message),3000);
</script></html>"""
_dashboard_markup = (ROOT / "tableau.html").read_text(encoding="utf-8")
def shared_dashboard_template(name):
    start = _dashboard_markup.index('<template id="' + name + '">')
    end = _dashboard_markup.index("</template>", start) + len("</template>")
    return _dashboard_markup[start:end]
OPERATOR_TOASTS = """<div id="toasts" popover="manual" hidden></div>
<template id="toast-template">
  <div class="toast">
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path class="toast-warning" d="M12 3 2.8 20h18.4L12 3Zm0 6v4m0 3h.01"/>
      <path class="toast-check" d="m5 12 4 4L19 6"/>
    </svg>
    <div><strong class="toast-title"></strong><p class="toast-detail"></p></div>
  </div>
</template><script>{const select=(node,selector)=>node.querySelector(selector);const toastStack = document.querySelector("#toasts");
const toastTemplate = document.querySelector("#toast-template");
const visibleToasts = new Map();
function dismissToast(key) {
  const toast = visibleToasts.get(key);
  if (!toast) return;
  clearTimeout(toast.timer);
  toast.element.remove();
  visibleToasts.delete(key);
  if (!visibleToasts.size) {
    if (toastStack.hidePopover && toastStack.matches(":popover-open")) toastStack.hidePopover();
    toastStack.hidden = true;
  }
}
function showToast(title, tone, detail = "") {
  const key = JSON.stringify([title, tone, detail]);
  let toast = visibleToasts.get(key);
  if (!toast) {
    if (visibleToasts.size >= 4) dismissToast(visibleToasts.keys().next().value);
    const element = toastTemplate.content.firstElementChild.cloneNode(true);
    element.dataset.tone = tone;
    element.setAttribute("role", tone === "success" ? "status" : "alert");
    select(element,".toast-title").textContent = title;
    select(element,".toast-detail").textContent = detail;
    toast = {element};
    visibleToasts.set(key, toast);
    toastStack.append(element);
    toastStack.hidden = false;
    if (toastStack.showPopover && toastStack.matches(":popover-open")) toastStack.hidePopover();
    toastStack.showPopover?.();
  }
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => dismissToast(key), 7000);
}
window.showToast=showToast;}</script>"""
OPERATOR_PAGE = OPERATOR_PAGE.replace("</html>", OPERATOR_TOASTS + SHARED_ACTION_DIALOG + PERSONAL_MEDIA_DIALOG + "</html>").replace("__SHARED_STYLE__", _dashboard_markup.split("<style>", 1)[1].split("</style>", 1)[0]).replace("__OP_VERSION__", SOFTWARE_VERSION).replace("__SHARED_TEMPLATES__", shared_dashboard_template("card") + shared_dashboard_template("preview-template"))
TERMINAL_PAGE = (ROOT / "terminal.html").read_bytes()
BLOCKED_MESSAGE = "Cette adresse IP est bloquée sur la régie. Contacte la régie pour demander le déblocage."
LOGIN_NOTICES = {
    "fermee": "Tu as été déconnecté de la régie.",
    "expiree": "La session a expiré ou a été fermée. Reconnecte-toi pour reprendre.",
}
LOCK = threading.Lock()
INSTALLATION_KEYS_LOCK = threading.Lock()
DEPLOYMENTS = Deployments()
POWER_REQUEST_AGE = 25
ONLINE_AGE = 12
RESOLUTIONS = ("360p", "720p", "1080p", "4K")
RESOLUTION_SUFFIX = re.compile(
    r"(?:^|\s)(?:\[(360p|720p|1080p|4K)\]|(360p|720p|1080p|4K))$", re.IGNORECASE)
LATENCY_TEST_AGE = 15
DEVICE_KINDS = ("pi", "ubuntu", "windows")
SCREENS = {}
SCREEN_ORDER = {"groups": [], "screens": {}}
MEDIA_EXPORTS = {}
LATENCY_TESTS = {}
CLIENT_UPDATES = {}
UPDATE_AGE = 120
OS_STATES = ("queued", "waiting", "starting", "running", "rebooting", "updated", "failed")
UPDATE_KEY = ROOT / ".soundlive-update" / "regie"
UPDATE_FILES = contents(ROOT)
UPDATE_DIGEST = digest(UPDATE_FILES)
def build_full_updates(ssh_public=""):
    updates = {}
    for target in ENGINE_ASSETS:
        try:
            files = full_contents(ROOT, target, release=True, ssh_public=ssh_public)
        except (OSError, ValueError) as error:
            print(f"Paquet client {target} : {error}", file=sys.stderr)
            continue
        updates[target] = (files, digest(files))
    return updates


try:
    FULL_UPDATES = build_full_updates(ssh_access_public(ROOT))
except (OSError, ValueError) as error:
    print(f"Clé publique SSH de la régie : {error}", file=sys.stderr)
    FULL_UPDATES = build_full_updates()

RECEIVER_PATHS = {"/api/enroll", "/api/poll", "/api/client-package", "/api/preview-result"}
PREVIEWS = {}
PREVIEW_WAIT = 16
PREVIEW_CONSENT_WAIT = 90
PREVIEW_FRESH = 40
PREVIEW_CLIENT = (1, 4, 7)
DISPLAY_PREVIEW_CLIENT = (1, 4, 8)


def source_resolution(source):
    host, opening, name = source.partition(" (")
    if host and opening and name.endswith(")"):
        source = name[:-1]
    match = RESOLUTION_SUFFIX.search(source.rstrip())
    if not match:
        return None
    return (match[1] or match[2]).lower().replace("4k", "4K")


def resolution_sources(row, now):
    limit = row.get("resolution_limit")
    report = row.get("report", {})
    available = {resolution: [] for resolution in RESOLUTIONS}
    if limit in RESOLUTIONS and now - report.get("seen", 0) < ONLINE_AGE:
        for source in dict.fromkeys(report.get("sources", [])):
            resolution = source_resolution(source)
            if resolution and RESOLUTIONS.index(resolution) <= RESOLUTIONS.index(limit):
                available[resolution].append(source)
    return available


def resolution_rejection(row, settings, now, requested):
    if not RESOLUTION_CONTROLS_ENABLED:
        return "Les réglages de résolution sont temporairement désactivés." if requested is not None else None
    resolution = source_resolution(settings["source"])
    if requested is not None and (requested not in RESOLUTIONS or requested != resolution):
        raise ValueError("La source ne correspond pas à la résolution choisie.")
    if requested is not None and settings["bandwidth"] != "0":
        raise ValueError("Choisis High pour recevoir la résolution annoncée par la source.")
    changed = (settings["source"] != row["options"]["source"]
               or settings["bandwidth"] != row["options"]["bandwidth"])
    if resolution and (changed or requested is not None):
        if settings["source"] not in resolution_sources(row, now)[resolution]:
            return f"« {row['name']} » ne détecte plus cette source ou sa limite validée est insuffisante."
    return None


def preview_prune(now):
    for source in [source for source, job in PREVIEWS.items() if job["until"] <= now]:
        del PREVIEWS[source]


def preview_status(job):
    public = {"id": job["id"], "state": job["state"]}
    for field in ("image", "error", "captured", "consent"):
        if field in job:
            public[field] = job[field]
    return public


def preview_target(data):
    if "screen" in data:
        output = data.get("output", "0")
        if output not in tuple(map(str, range(8))):
            raise ValueError("Sortie vidéo invalide.")
        return ("screen", safe_text(data["screen"])) + ((output,) if output != "0" else ()), None
    source = safe_text(data["source"], 256)
    return ("source", source), source


def request_preview(data, now):
    preview_prune(now)
    target, source = preview_target(data)
    screen = target[0] == "screen"
    online = [
        (key, row["report"]) for key, row in SCREENS.items()
        if row.get("token") and now - row.get("report", {}).get("seen", 0) < ONLINE_AGE
        and (key == target[1] if screen else
             source in row["report"].get("sources", []) or source == row["report"].get("active_source"))
    ]
    existing = PREVIEWS.get(target)
    if not online:
        return 409, {"error": "Ce lecteur est hors ligne." if screen else "Ce flux n’est plus détecté par les lecteurs."}
    output = target[2] if len(target) == 3 else "0"
    if screen and output != "0" and not any(o["id"] == output for o in online[0][1].get("outputs", [])):
        return 409, {"error": "Écran supplémentaire indisponible."}
    if screen and output == "0" and online[0][1].get("display_capture") is not True:
        return 409, {"error": "La capture de l’affichage est indisponible sur ce lecteur."}
    automatic = data.get("automatic") is True
    consent = screen and online[0][1].get("capture_needs_consent") is True
    if automatic and consent:
        return 409, {"error": "Autorise le partage sur le lecteur Ubuntu avant de lancer la capture automatique."}
    if existing:
        if existing["state"] != "ready":
            return 200, preview_status(existing)
        if automatic and now - existing["captured"] < PREVIEW_AUTO_INTERVAL:
            return 200, preview_status(existing)
        if not automatic and not consent and existing["id"] != data.get("previous"):
            return 200, preview_status(existing)
    minimum = release_number("1.4.118") if screen and output != "0" else DISPLAY_PREVIEW_CLIENT if screen else PREVIEW_CLIENT
    supported = [(key, report) for key, report in online
                 if release_number(report.get("client_release")) >= minimum]
    if not supported:
        return 409, {"error": "Mets à jour un lecteur qui détecte ce flux pour afficher son aperçu."}
    busy = {job["reader"] for job in PREVIEWS.values() if job["state"] == "pending"}
    available = [(key, report) for key, report in supported if key not in busy]
    auto_busy = automatic and any(job["state"] == "pending" and job.get("automatic") for job in PREVIEWS.values())
    if not available or len(busy) >= 4 or auto_busy:
        return 429, {"error": "Captures en cours. Réessaie dans quelques secondes."}
    device, _ = min(available, key=lambda item: item[1].get("active_source") != source)
    if existing is None and len(PREVIEWS) >= 24:
        expired_first = min((name for name in PREVIEWS if PREVIEWS[name]["state"] != "pending"),
                            key=lambda name: PREVIEWS[name]["until"])
        del PREVIEWS[expired_first]
    job = dict(id=secrets.token_hex(12), reader=device, source=source, screen=screen, output=output, state="pending",
               automatic=automatic, consent=consent, until=now + (PREVIEW_CONSENT_WAIT if consent else PREVIEW_WAIT))
    PREVIEWS[target] = job
    return 200, preview_status(job)


def finish_preview(reader, data, now):
    preview_prune(now)
    job = next((item for item in PREVIEWS.values() if item["id"] == data.get("job")
                and item["reader"] == reader and item["state"] == "pending"), None)
    if job is None:
        return 409, {"error": "Cette capture n’est plus demandée."}
    image = data.get("image")
    if isinstance(image, str) and 0 < len(image) <= 4 * ((PREVIEW_LIMIT + 2) // 3):
        try:
            raw = b64decode(image, validate=True)
        except ValueError:
            raw = b""
        if (33 <= len(raw) <= PREVIEW_LIMIT and raw.startswith(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
                and 1 <= int.from_bytes(raw[16:20], "big") <= 240
                and 1 <= int.from_bytes(raw[20:24], "big") <= 135
                and raw[24:29] == b"\x08\x02\x00\x00\x00"):
            job.update(state="ready", image=image, captured=now, until=now + PREVIEW_FRESH)
            return 200, {"ok": True}
    error = data.get("error")
    if not isinstance(error, str) or not error or not job["screen"]:
        error = ("Ce lecteur n’a pas pu capturer son affichage." if job["screen"] else
                 "Ce lecteur n’a pas pu capturer une image NDI.")
    job.update(state="error", error=error[:240], until=now + 4)
    return 200, {"ok": True}


def waiting_access(address, port):
    url = f"https://{address}:{port}"
    code = QRCode(None, QRErrorCorrectLevel.M)
    code.addData(url)
    code.make()
    return ["".join("1" if dark else "0" for dark in row) for row in code.modules]


def update_payload(report):
    if report.get("update_target") is None:
        return UPDATE_FILES, UPDATE_DIGEST
    return FULL_UPDATES.get(report["update_target"])


def update_availability(report, now, key_exists):
    if now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if release_number(report.get("client_release")) > release_number(CLIENT_VERSION):
        return "newer"
    payload = update_payload(report)
    if payload and report.get("client_version") == payload[1]:
        return "current"
    if not report.get("can_update"):
        issue = report.get("update_issue")
        return issue if issue in CLIENT_UPDATE_ISSUES else "unsupported"
    if payload is None or (report.get("update_has_engine") and engine_name(report.get("update_target")) not in payload[0]):
        return "package_missing"
    return "available" if key_exists else "key_required"


def update_view(key, report, now):
    job = CLIENT_UPDATES.get(key)
    if not job:
        return None
    result = report.get("update_result")
    if result and result["id"] == job["id"]:
        if result["state"] == "failed":
            return result
        if result["state"] == "updated":
            if report.get("client_version") == job["version"]:
                return result
            return dict(id=job["id"], state="failed",
                        message="Le lecteur a répondu, mais son empreinte diffère du paquet envoyé.")
    if now >= job["until"]:
        return dict(id=job["id"], state="expired", message="Pas de confirmation du récepteur.")
    if result and result["id"] == job["id"] and result["state"] == "installing":
        return result
    return dict(id=job["id"], state="queued")


def remove_screens(targets):
    for key in targets:
        del SCREENS[key]
    try:
        save()
    except OSError:
        SCREENS.update(targets)
        raise
    for key in targets:
        LATENCY_TESTS.pop(key, None)
        CLIENT_UPDATES.pop(key, None)
        if targets[key].get("media"):
            shutil.rmtree(MEDIA_ROOT / key, ignore_errors=True)
    for source in [source for source, job in PREVIEWS.items() if job["reader"] in targets]:
        del PREVIEWS[source]


def os_status(row, now):
    job = row.get("os_update")
    if job and job["state"] == "queued" and now >= job["until"]:
        return {**job, "state": "failed", "message": "Le lecteur n’a pas reçu la demande."}
    return job


def os_busy(row, now):
    return (os_status(row, now) or {}).get("state") in OS_STATES[:5]


def os_updates(targets, now):
    before = {}
    results = []
    for key, row in targets.items():
        report = row.get("report", {})
        state = report.get("os_capability", "unsupported")
        current = row.get("power", {})
        client_update = update_view(key, report, now) or {}
        if not report.get("boot") or now - report.get("seen", 0) >= ONLINE_AGE:
            state = "offline"
        elif os_busy(row, now) or client_update.get("state") in ("queued", "installing") or (
                current.get("boot") == report["boot"] and now - current.get("time", 0) < POWER_REQUEST_AGE):
            state = "maintenance"
        elif state == "ready":
            if UPDATE_KEY.is_file():
                before[key] = row.get("os_update")
                row["os_update"] = dict(id=secrets.token_hex(16), boot=report["boot"],
                                        until=now + UPDATE_AGE, state="queued", message="")
                state = "queued"
            else:
                state = "key_required"
        results.append(dict(id=key, name=row["name"], state=state))
    if before:
        try:
            save()
        except OSError:
            for key, job in before.items():
                if job is None:
                    targets[key].pop("os_update", None)
                else:
                    targets[key]["os_update"] = job
            raise
    return results


def os_poll(row, data, boot, now):
    job = row.get("os_update")
    if not job or job["state"] in ("updated", "failed"):
        return None
    result = data.get("os_result")
    if isinstance(result, dict) and result.get("id") == job["id"] and result.get("state") in OS_STATES[1:]:
        state = result["state"]
        if state == "updated" and boot == job["boot"]:
            return None
        if state in OS_STATES[1:5] and boot != job["boot"]:
            state = "failed"
            result = {"message": "Le lecteur a redémarré avant la fin de la mise à jour."}
        update = {**job, "state": state, "message": str(result.get("message") or "")[:240]}
        if update != job:
            row["os_update"] = update
            try:
                save()
            except OSError:
                row["os_update"] = job
                raise
        return None
    if (job["state"] == "queued" and now < job["until"] and job["boot"] == boot
            and data.get("os_capability") == "ready"):
        nonce = data.get("os_nonce")
        if isinstance(nonce, str) and SAFE_ID.fullmatch(nonce):
            return {"id": job["id"]}
    return None


def queue_power(row, operation, now):
    report = row.get("report", {})
    if not report.get("boot") or now - report.get("seen", 0) >= ONLINE_AGE:
        return "offline"
    if os_busy(row, now):
        return "maintenance"
    if operation == "poweroff" and not report.get("can_poweroff"):
        return "unsupported"
    if operation == "reboot" and report.get("can_reboot") is False:
        return "unsupported"
    current = row.get("power", {})
    if (current.get("operation") != operation or current.get("boot") != report["boot"]
            or now - current.get("time", 0) >= POWER_REQUEST_AGE):
        row["power"] = dict(boot=report["boot"], time=now, id=secrets.token_hex(8),
                            operation=operation)
    return "requested"


def group_device_action(targets, operation, now):
    results = []
    if operation == "remove":
        remove_screens(targets)
        return [dict(id=key, name=row["name"], state="removed") for key, row in targets.items()]
    previous = {key: row.get("power") for key, row in targets.items()}
    for key, row in targets.items():
        state = queue_power(row, operation, now)
        results.append(dict(id=key, name=row["name"], state=state))
    try:
        if any(row.get("power") != previous[key] for key, row in targets.items()):
            save()
    except OSError:
        for key, row in targets.items():
            if previous[key] is None:
                row.pop("power", None)
            else:
                row["power"] = previous[key]
        raise
    return results


def physical_outputs(row):
    return ["0", *sorted({o["id"] for o in row.get("report", {}).get("outputs", [])
                          if o.get("id") in tuple(map(str, range(1, 8)))}
                         | set(row.get("output_groups", {})), key=int)]


def physical_group(row, index):
    return row.get("group") if index == "0" else row.get("output_groups", {}).get(index, row.get("group"))


def physical_selection(devices):
    selected = {}
    for key in devices:
        reader, sep, index = key.partition(":")
        index = index if sep else "0"
        row = SCREENS.get(reader)
        if row is None or index not in physical_outputs(row):
            raise ValueError("Écran physique inconnu.")
        selected[(reader, index)] = row
    return selected


def physical_batch(devices, data, now):
    selected = physical_selection(devices)
    patch = data.get("changes")
    fields = {"source", "muted", "enabled", "bandwidth", "volume", "orientation"}
    if not isinstance(patch, dict) or not patch or not set(patch) <= fields:
        raise ValueError("Aucun réglage de groupe valide.")
    staged = {}
    results = []
    for (key, index), row in selected.items():
        if "options" not in row:
            raise ValueError("Lecteur non associé.")
        if key not in staged:
            staged[key] = dict(options=dict(row["options"]), extra_outputs=dict(row.get("extra_outputs", {})),
                               media=dict(row.get("media", {})), extra_media=dict(row.get("extra_media", {})))
        state = staged[key]
        if index == "0":
            changes = options({**state["options"], **patch})
            error = resolution_rejection(row, changes, now, data.get("resolution"))
            if error:
                return 409, {"error": error}
            # A source change on the primary must not change the secondary outputs following it.
            if "source" in patch and changes["source"] != state["options"]["source"]:
                for other in physical_outputs(row):
                    if other == "0" or (key, other) in selected:
                        continue
                    existing = dict(state["extra_outputs"].get(other, {}))
                    if existing.get("mode") == "same":
                        existing.update(mode="independent", source=state["options"]["source"])
                        state["extra_outputs"][other] = existing
            state["options"] = changes
            if "source" in patch:
                state["media"]["enabled"] = False
        else:
            current = dict(state["extra_outputs"].get(index, {}))
            defaults = dict(source=state["options"]["source"] if current.get("mode") == "same" else current.get("source") or state["options"]["source"],
                            enabled=current.get("enabled", False), bandwidth=current.get("bandwidth", state["options"]["bandwidth"]),
                            volume=current.get("volume", 0), muted=current.get("muted", True),
                            orientation=current.get("orientation", "landscape"))
            changes = options({**defaults, **patch})
            if "source" in patch:
                current["mode"] = "independent"
                state["extra_media"].pop(index, None)
            current.update(changes)
            current.setdefault("mode", "independent")
            state["extra_outputs"][index] = current
        results.append(dict(id=key + ":" + index, name=row["name"] + " · Écran " + str(int(index)+1),
                            state="saved", online=now-row.get("report", {}).get("seen", 0)<ONLINE_AGE))
    previous = {key: {field: SCREENS[key].get(field) for field in (*state, "version")} for key,state in staged.items()}
    try:
        for key,state in staged.items():
            SCREENS[key].update(state, version=SCREENS[key].get("version",0)+1)
        save()
    except OSError:
        for key,old in previous.items():
            for field,value in old.items():
                if value is None:
                    SCREENS[key].pop(field,None)
                else:
                    SCREENS[key][field]=value
        raise
    return 200, {"results": results}


def batch(path, data, now):
    devices = data.get("ids")
    if not isinstance(devices, list) or not devices or len(devices) > 250 or any(not isinstance(key, str) for key in devices):
        raise ValueError("Sélectionne au moins un écran.")
    if path == "/api/group" and "changes" in data and any(":" in key for key in devices):
        return physical_batch(devices, data, now)
    if path != "/api/screen-groups" and any(":" in key for key in devices):
        devices = list(dict.fromkeys(key for key, _ in physical_selection(devices)))
    targets = {key: SCREENS.get(key) for key in devices}
    if path == "/api/screen-groups":
        if any(":" in key for key in devices):
            selected = []
            for key in devices:
                reader, separator, index = key.partition(":")
                row = SCREENS.get(reader)
                index = index if separator else "0"
                if not row or index not in tuple(map(str, range(8))) or index not in physical_outputs(row):
                    raise ValueError("Écran physique inconnu.")
                selected.append((reader, row, index))
            name = data.get("group")
            if not isinstance(name, str) or any(not char.isprintable() for char in name):
                raise ValueError("Nom de groupe invalide.")
            name = safe_text(name) if name.strip() else None
            previous = {key: (row.get("group"), dict(row.get("output_groups", {}))) for key, row, _ in selected}
            for _, row, index in selected:
                if index == "0":
                    # Preserve the previous inherited assignment of other connected outputs.
                    groups = row.setdefault("output_groups", {})
                    for other in physical_outputs(row):
                        if other != "0":
                            groups.setdefault(other, row.get("group"))
                    if name:
                        row["group"] = name
                    else:
                        row.pop("group", None)
                else:
                    row.setdefault("output_groups", {})[index] = name
            try:
                save()
            except OSError:
                for key, (group, groups) in previous.items():
                    if group is None:
                        SCREENS[key].pop("group", None)
                    else:
                        SCREENS[key]["group"] = group
                    SCREENS[key]["output_groups"] = groups
                raise
            return 200, {"group": name, "count": len(selected)}
        if any(row is None for row in targets.values()):
            return 409, {"error": "Un écran sélectionné a été retiré du tableau."}
        name = data.get("group")
        if not isinstance(name, str) or any(not char.isprintable() for char in name):
            raise ValueError("Nom de groupe invalide.")
        name = safe_text(name) if name.strip() else None
        if name:
            name = next((row["group"] for row in SCREENS.values()
                         if row.get("group") and row["group"].casefold() == name.casefold()), name)
        previous = {key: row.get("group") for key, row in targets.items()}
        for row in targets.values():
            if name:
                row["group"] = name
            else:
                row.pop("group", None)
        try:
            save()
        except OSError:
            for key, group in previous.items():
                if group is None:
                    SCREENS[key].pop("group", None)
                else:
                    SCREENS[key]["group"] = group
            raise
        return 200, {"group": name, "count": len(targets)}
    if any(not row or "options" not in row for row in targets.values()):
        return 409, {"error": "La sélection contient un écran qui n’est plus associé."}
    results = []
    if path == "/api/os-updates":
        return 200, {"results": os_updates(targets, now)}
    if path == "/api/group":
        operation = data.get("operation")
        if operation is not None:
            if operation not in ("reboot", "poweroff", "remove") or "changes" in data:
                raise ValueError("Action de groupe invalide.")
            return 200, {"results": group_device_action(targets, operation, now)}
        patch = data.get("changes")
        fields = {"source", "muted", "enabled", "bandwidth", "volume", "orientation"}
        if not isinstance(patch, dict) or not patch or not set(patch) <= fields:
            raise ValueError("Aucun réglage de groupe valide.")
        changes = {key: options({**row["options"], **patch}) for key, row in targets.items()}
        for key, row in targets.items():
            error = resolution_rejection(row, changes[key], now, data.get("resolution"))
            if error:
                return 409, {"error": error}
        previous = {}
        for key, row in targets.items():
            if changes[key] != row["options"]:
                previous[key] = row["options"], row["version"]
                row.update(options=changes[key], version=row["version"] + 1)
            results.append(dict(id=key, name=row["name"], state="saved",
                                online=now - row.get("report", {}).get("seen", 0) < ONLINE_AGE))
        try:
            if previous:
                save()
        except OSError:
            for key, (settings, version) in previous.items():
                SCREENS[key].update(options=settings, version=version)
            raise
    else:
        if not UPDATE_KEY.is_file():
            return 409, {"error": "Crée la clé de régie avec python tableau.py --preparer-mises-a-jour."}
        for key, row in targets.items():
            report = row.get("report", {})
            state = "maintenance" if os_busy(row, now) else update_availability(report, now, key_exists=True)
            if state == "available":
                state = "queued"
                existing = update_view(key, report, now)
                if not existing or existing["state"] not in ("queued", "installing"):
                    files, version = update_payload(report)
                    CLIENT_UPDATES[key] = dict(id=secrets.token_hex(16), until=now + UPDATE_AGE,
                                               version=version, files=files)
            job = CLIENT_UPDATES.get(key)
            results.append(dict(id=key, name=row["name"], state=state,
                                job=job["id"] if state == "queued" else None))
    return 200, {"results": results}

def enroll_reader(data, now):
    code = data.get("code")
    proof = data.get("proof")
    nonce = data.get("nonce")
    request = {name: value for name, value in data.items() if name != "proof"}
    protected_request = (code is None and isinstance(proof, str) and SAFE_ID.fullmatch(proof)
                         and isinstance(nonce, str) and SAFE_ID.fullmatch(nonce))
    for key, row in SCREENS.items():
        if now >= row.get("pairing_until", 0):
            continue
        secret = row["pairing"]
        protected_code = row.get("exchange_ssh") is True
        if protected_code:
            if token_matches(code, secret):
                # An older kit sent the protected code itself. Invalidate it immediately.
                updated = row.copy()
                updated["pairing_until"] = 0
                SCREENS[key] = updated
                try:
                    save()
                except OSError:
                    SCREENS[key] = row
                    raise
                return 409, {"error": "Utilise le kit 1.4.77 puis génère un nouveau code d’association."}
            if not protected_request or not hmac.compare_digest(proof, ssh_pairing_proof(secret, "request", request)):
                continue
            fingerprints = ssh_fingerprints(data.get("ssh_host_fingerprints"))
            ssh_user = data.get("ssh_user")
            if not isinstance(ssh_user, str) or not SSH_USER.fullmatch(ssh_user):
                raise ValueError("Nom du compte SSH du lecteur invalide.")
        else:
            if not token_matches(code, secret):
                continue
            fingerprints = None
        settings = options(data["options"])
        serial = safe_text(data["serial"])
        kind = data.get("kind")
        if kind is None:
            kind = "ubuntu" if serial.startswith("pc-") else "pi"
        if kind not in DEVICE_KINDS or row.get("kind") not in (None, kind):
            return 409, {"error": "Le type de récepteur ne correspond pas au code créé."}
        if any(existing.get("serial") == serial for existing in SCREENS.values()):
            return 409, {"error": "Ce récepteur est déjà associé. Retire d’abord son ancienne fiche."}
        token = secrets.token_urlsafe(32)
        updated = {**row, "options": settings, "token": token, "serial": serial, "version": 1, "kind": kind}
        for field in ("pairing", "pairing_until", "installation_fingerprint", "exchange_ssh"):
            updated.pop(field, None)
        if fingerprints:
            updated.update(ssh_host_fingerprints=fingerprints, ssh_user=ssh_user)
        result = dict(id=key, token=token, name=row["name"])
        if protected_code:
            result["proof"] = ssh_pairing_proof(secret, "response", {**result, "nonce": nonce})
        SCREENS[key] = updated
        try:
            save()
        except OSError:
            SCREENS[key] = row
            raise
        return 200, result
    return 403, {"error": "Code expiré ou inconnu."}


def save():
    temporary = DATA.with_suffix(".tmp")
    temporary.write_text(json.dumps(SCREENS, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(DATA)


def order_screens(data):
    scope, order = data.get("scope"), data.get("order")
    if not isinstance(order, list) or any(not isinstance(value, str) for value in order):
        raise ValueError("Ordre d’affichage invalide.")
    if scope == "groups":
        expected = {physical_group(row,index) or "" for row in SCREENS.values() for index in physical_outputs(row)}
        layout = {**SCREEN_ORDER, "groups": order}
    elif scope == "screens" and isinstance(data.get("group"), str):
        group = data["group"]
        expected = {key if index == "0" else key + ":" + index for key,row in SCREENS.items() for index in physical_outputs(row) if (physical_group(row,index) or "") == group and (index == "0" or (physical_group(row,index) or "") != (row.get("group") or ""))}
        layout = {**SCREEN_ORDER, "screens": {**SCREEN_ORDER["screens"], group: order}}
    else:
        raise ValueError("Disposition invalide.")
    if not order or len(order) != len(expected) or set(order) != expected:
        return 409, {"error": "La liste des écrans a changé. Réessaie avec la liste à jour."}
    temporary = SCREEN_ORDER_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(layout, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(SCREEN_ORDER_FILE)
    SCREEN_ORDER.update(layout)
    return 200, {"ok": True}


def load_screen_order():
    try:
        layout = json.loads(SCREEN_ORDER_FILE.read_text(encoding="utf-8"))
        groups = layout["groups"]
        screens = layout["screens"]
        if not isinstance(screens, dict):
            raise ValueError("Disposition invalide.")
        if not all(isinstance(order, list) and all(isinstance(key, str) for key in order)
                   for order in (groups, *screens.values())):
            raise ValueError("Disposition invalide.")
        return {"groups": groups, "screens": screens}
    except FileNotFoundError:
        return {"groups": [], "screens": {}}
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Disposition des écrans : {error}", file=sys.stderr)
        return {"groups": [], "screens": {}}


def screen_media(row):
    state = row.get("media", {"enabled": False, "items": []})
    folder = media_folders(row)[row.get("media_folder", MEDIA_DEFAULT_FOLDER)]
    return dict(enabled=state["enabled"], items=folder["items"], **folder.get("timing", {}))


def media_folders(row):
    return row.get("media_folders") or {MEDIA_DEFAULT_FOLDER: dict(
        name="Général", items=row.get("media", {}).get("items", []))}


def extra_media_folders(row, folders=None):
    folders = media_folders(row) if folders is None else folders
    selected = row.get("extra_media") or {}
    return {index: folder for index, folder in selected.items()
            if index in tuple(map(str, range(1, 8))) and isinstance(folder, str)
            and folder in folders and folders[folder]["items"]}


def extra_media_configs(row):
    folders = media_folders(row)
    return {index: media_config(dict(enabled=True, items=folders[folder]["items"], **folders[folder].get("timing", {})))
            for index, folder in extra_media_folders(row, folders).items()}


def catalog_items(row):
    return [item for folder in media_folders(row).values() for item in folder["items"]]


def save_media(row, folders, active, enabled, *, resume=False, empty=False, extra_media=None, extra_outputs=None):
    fields = ("media", "media_folders", "media_folder", "media_generation", "extra_media", "extra_outputs")
    previous = {field: row[field] for field in fields if field in row}
    settings, version = row["options"], row["version"]
    row.update(media_folders=folders, media_folder=active,
               media=media_config(dict(enabled=enabled, items=folders[active]["items"], **folders[active].get("timing", {}))))
    if extra_media is not None:
        row["extra_media"] = extra_media
    row["extra_media"] = extra_media_folders(row, folders)
    if extra_outputs is not None:
        row["extra_outputs"] = extra_outputs
    if empty:
        row["media_generation"] = row.get("media_generation", 0) + 1
    if resume and not settings["enabled"]:
        row.update(options={**settings, "enabled": True}, version=version + 1)
    try:
        save()
    except OSError:
        row.update(options=settings, version=version)
        for field in fields:
            row.pop(field, None)
        row.update(previous)
        raise


def media_capacity(row):
    report = row.get("report", {}).get("media_status") or {}
    disk = report.get("disk")
    if not disk:
        return None
    cached = set(report.get("cached", []))
    pending = sum(item["size"] for item in catalog_items(row) if item["id"] not in cached)
    return {**disk, "available": max(0, media_available(disk) - pending), "pending": pending}


def media_quota(row, folder, length):
    items = folder["items"]
    if len(items) >= MEDIA_COUNT_LIMIT or sum(item["size"] for item in items) + length > MEDIA_TOTAL_LIMIT:
        raise ValueError("Un dossier peut contenir jusqu’à 20 fichiers et 1 Gio.")
    if len(catalog_items(row)) >= MEDIA_LIBRARY_LIMIT:
        raise ValueError("Ce lecteur a atteint la limite de 128 fichiers.")
    disk = media_capacity(row)
    if disk is None:
        raise ValueError("Espace disque du lecteur inconnu. Connecte un lecteur à jour avant d’ajouter des fichiers.")
    if length > disk["available"]:
        raise ValueError(f"Ce fichier ferait dépasser le seuil de {MEDIA_DISK_PERCENT} % sur le lecteur.")


def media_location(folders, identifier):
    for folder, data in folders.items():
        for index, item in enumerate(data["items"]):
            if item["id"] == identifier:
                return folder, index, item
    raise ValueError("Ce média ne figure plus dans la médiathèque.")


@contextmanager
def media_stream(stream, key, identifier):
    try:
        with stream:
            yield
    finally:
        # Windows peut refuser l’effacement tant qu’un aperçu tient le fichier ouvert.
        with LOCK:
            row = SCREENS.get(key)
            if row is None or not any(item["id"] == identifier for item in catalog_items(row)):
                try:
                    (MEDIA_ROOT / key / identifier).unlink(missing_ok=True)
                except OSError:
                    pass


def media_bytes_range(value, size):
    if value is None:
        return 0, size - 1
    match = re.fullmatch(r"bytes=(\d{0,15})-(\d{0,15})", value)
    if not match or not any(match.groups()):
        raise ValueError("Plage invalide.")
    lo, hi = match.groups()
    if not lo and int(hi) > 0:
        return max(0, size - int(hi)), size - 1
    if lo and int(lo) < size and (not hi or int(hi) >= int(lo)):
        return int(lo), min(int(hi), size - 1) if hi else size - 1
    raise ValueError("Plage invalide.")

def token_matches(candidate, expected):
    return isinstance(candidate, str) and hmac.compare_digest(candidate.encode(), expected.encode())

def safe_text(value, limit=80):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Texte invalide.")
    return value.strip()

def bounded_number(value, maximum):
    if type(value) in (float, int) and 0 <= value <= maximum:
        return value
    return None


def diagnostic_report(payload):
    if not isinstance(payload, dict):
        return None
    report = {}
    if payload.get("desktop_protocol") in ("vnc", "rdp"):
        report["desktop_protocol"] = payload["desktop_protocol"]
    services = payload.get("desktop_services")
    if isinstance(services, dict):
        report["desktop_services"] = {
            protocol: port for protocol, port in services.items()
            if (protocol == "rdp" and type(port) is int and port in (3389, 3390))
            or (protocol == "vnc" and type(port) is int and port == 5900)
        }
    for field in ("hostname", "os", "architecture", "model", "cpu"):
        value = payload.get(field)
        if isinstance(value, str):
            report[field] = value[:160]
    for field, maximum in (("cpu_pct", 100), ("cores", 4096), ("ram_total", 2**60),
                           ("ram_used", 2**60), ("disk_total", 2**60), ("disk_free", 2**60),
                           ("disk_used", 2**60)):
        report[field] = bounded_number(payload.get(field), maximum)
    interfaces = payload.get("interfaces")
    if isinstance(interfaces, list):
        report["interfaces"] = [
            dict(id=str(item.get("id", item.get("name", "")))[:80],
                 name=str(item.get("name", ""))[:64],
                 kind=item.get("kind") if item.get("kind") in ("wifi", "ethernet") else "other",
                 connected=item.get("connected") is True, to_regie=item.get("to_regie") is True,
                 ssid="".join(char if char.isprintable() else "�" for char in item["ssid"][:64])
                 if item.get("kind") == "wifi" and item.get("connected") is True
                 and isinstance(item.get("ssid"), str) and item["ssid"] else None,
                 addresses=[ip[:45] for ip in item["addresses"][:4] if isinstance(ip, str)]
                 if isinstance(item.get("addresses"), list) else [],
                 rx_mbps=bounded_number(item.get("rx_mbps"), 1_000_000),
                 tx_mbps=bounded_number(item.get("tx_mbps"), 1_000_000))
            for item in interfaces[:12] if isinstance(item, dict)
        ]
    return report


def display_report(payload):
    if not isinstance(payload, dict):
        return None
    width, height = payload.get("width"), payload.get("height")
    if not all(type(value) is int and 1 <= value <= 32768 for value in (width, height)):
        return None
    report = dict(width=width, height=height, estimated=payload.get("estimated") is True)
    for field in ("model", "connector"):
        value = payload.get(field)
        if isinstance(value, str):
            report[field] = value.strip()[:160]
    return report


def interface_addresses():
    if sys.platform == "win32":
        # Enumerate IPv4 addresses on each active adapter, including isolated LANs.
        script = (
            "[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false); "
            "$rows = foreach ($adapter in "
            "[System.Net.NetworkInformation.NetworkInterface]::GetAllNetworkInterfaces()) { "
            "if ($adapter.OperationalStatus -ne 'Up' -or "
            "$adapter.NetworkInterfaceType -eq 'Loopback') { continue }; "
            "$properties = $adapter.GetIPProperties(); "
            "try { $index = $properties.GetIPv4Properties().Index } catch { continue }; "
            "foreach ($entry in $properties.UnicastAddresses) { "
            "if ($entry.Address.AddressFamily -eq 'InterNetwork' -and "
            "$entry.DuplicateAddressDetectionState -in @('Preferred', 'Deprecated')) { "
            "[PSCustomObject]@{ip=$entry.Address.IPAddressToString; label=$adapter.Name; id=$adapter.Id; "
            "index=$index; prefix=$entry.IPv4Mask.ToString(); static=$entry.PrefixOrigin -eq 'Manual'} "
            "} } }; ConvertTo-Json -InputObject @($rows | Where-Object { $_ }) -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, check=True, encoding="utf-8-sig", timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        rows = json.loads(result.stdout or "[]")
        return rows if isinstance(rows, list) else [rows]

    result = subprocess.run(["ip", "-j", "-4", "address", "show", "up"],
                            capture_output=True, check=True, text=True, timeout=2)
    return [
        dict(ip=entry["local"], label=interface["ifname"], id=interface["ifname"],
             index=interface["ifindex"], prefix=entry["prefixlen"], static=not entry.get("dynamic", False))
        for interface in json.loads(result.stdout)
        for entry in interface.get("addr_info", [])
        if entry.get("scope") in {"global", "link"}
    ]


def dhcp_interfaces():
    try:
        return interface_addresses()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        raise RuntimeError("Impossible d’identifier les interfaces du réseau écrans.") from error


class InterfaceTraffic:
    def __init__(self):
        self.lock = threading.Lock()
        self.rates = NetworkRates()
        self.system = Diagnostic()
        self.refresh_at = 0
        self.result = {"interfaces": [], "available": False}

    def read(self):
        with self.lock:
            if time.monotonic() >= self.refresh_at:
                try:
                    counters = network_info()
                except (OSError, KeyError, ValueError, subprocess.SubprocessError):
                    counters = None
                rows = self.rates.sample(counters)
                stats = self.system.sample_system(MEDIA_ROOT if MEDIA_ROOT.is_dir() else ROOT)
                self.result = {"interfaces": [row for row in rows or [] if row["connected"]],
                               "available": rows is not None, "diagnostic": diagnostic_report(stats)}
                self.refresh_at = time.monotonic() + 2
            return self.result


def local_addresses():
    try:
        rows = interface_addresses()
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = []
    # Connecting an unbound UDP socket selects the local route without sending data.
    preferred = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as route:
            route.connect(("192.0.2.1", 9))
            preferred = route.getsockname()[0]
    except OSError:
        pass
    if not rows:
        try:
            rows = [dict(ip=ip, label=socket.gethostname())
                    for ip in socket.gethostbyname_ex(socket.gethostname())[2]]
        except OSError:
            pass
    if preferred:
        rows.append(dict(ip=preferred, label="Route par défaut"))

    found = {}
    for row in rows:
        try:
            address = IPv4Address(row["ip"])
        except (KeyError, ValueError):
            continue
        if address.is_loopback or address.is_unspecified or address.is_multicast or address.packed[0] in (0, 255):
            continue
        ip = str(address)
        found.setdefault(ip, dict(ip=ip, label=row["label"]))
    ordered = sorted(found.values(), key=lambda item: (
        IPv4Address(item["ip"]).is_link_local, item["ip"] != preferred, item["label"], item["ip"]
    ))
    return ordered


def desktop_command(address, protocol, port=None):
    if protocol not in ("rdp", "vnc"):
        return None
    if port is None:
        port = 3389 if protocol == "rdp" else 5900
    if type(port) is not int or not 1 <= port <= 65535:
        return None
    if sys.platform != "win32":
        # Remmina prend en charge RDP et VNC directement depuis une URI.
        if remmina := shutil.which("remmina"):
            return [remmina, "-c", f"{protocol}://{address}:{port}"]
        if protocol == "rdp":
            return None
        if program := shutil.which("xtigervncviewer") or shutil.which("tigervncviewer"):
            return [program, f"{address}::{port}"]
        return None
    if protocol == "rdp":
        return ["mstsc.exe", f"/v:{address}:{port}"]
    for folder in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        if programs := os.environ.get(folder):
            viewer = Path(programs) / "TigerVNC" / "vncviewer.exe"
            if viewer.is_file():
                return [str(viewer), f"{address}::{port}"]
    if viewer := shutil.which("vncviewer.exe"):
        if "tigervnc" in {part.casefold() for part in Path(viewer).parts}:
            return [viewer, f"{address}::{port}"]
    return None


def support_methods(kind, desktop=None, services=None):
    if kind == "windows":
        return ("rdp", "vnc")
    if kind == "ubuntu":
        # Les clients récents indiquent les ports réellement ouverts.
        # Un dictionnaire vide signifie qu'aucun serveur ne fonctionne.
        if isinstance(services, dict):
            return ("ssh", *(name for name in ("rdp", "vnc") if name in services))
        # Compatibilité avec les lecteurs qui n'ont pas encore été mis à jour.
        return ("ssh", "rdp") if desktop == "rdp" else ("ssh", "vnc")
    if kind == "pi":
        return ("ssh", "vnc")
    return ()


def regie_support_methods():
    return support_methods("windows" if sys.platform == "win32" else "ubuntu",
                           desktop_protocol(), desktop_services())


def support_target(key):
    if key == "regie":
        ports = desktop_services() if sys.platform != "win32" else {}
        return dict(address="127.0.0.1", name="Régie", user=getuser(),
                    support=support_methods("windows" if sys.platform == "win32" else "ubuntu",
                                            desktop_protocol(), ports),
                    desktop_ports=ports)
    with LOCK:
        row = SCREENS.get(key) if isinstance(key, str) else None
        report = row.get("report", {}) if row and row.get("token") else {}
        if time.time() - report.get("seen", 0) >= ONLINE_AGE:
            raise ValueError("Le lecteur doit être associé et en ligne.")
        address = IPv4Address(report["ip"])
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError("Adresse du lecteur invalide.")
        diagnostics = report.get("diagnostic") or {}
        ports = diagnostics.get("desktop_services")
        return dict(address=str(address), name=row["name"], user=row.get("ssh_user", "soundlive"),
                    support=support_methods(row.get("kind"), diagnostics.get("desktop_protocol"), ports),
                    desktop_ports=ports if isinstance(ports, dict) else {},
                    ssh_host_fingerprints=row.get("ssh_host_fingerprints", []))


class Request(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(8)
        super().setup()

    def log_message(self, *_):
        pass

    def reply(self, status, value, *, location=None, session=None, content_type=None):
        if isinstance(value, bytes):
            body = value
            content_type = content_type or "text/html; charset=utf-8"
        else:
            body = json.dumps(value, ensure_ascii=False).encode()
            content_type = "application/json; charset=utf-8"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        if getattr(self.server, "remote_dashboard", False):
            self.send_header("Referrer-Policy", "same-origin")
        if session is not None:
            max_age = "" if session else "; Max-Age=0"
            self.send_header("Set-Cookie",
                             f"{SESSION_COOKIE}={session}; Path=/; Secure; HttpOnly; SameSite=Strict{max_age}")
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def refuse_blocked(self, page):
        if page:
            self.login_page(403, blocked=True)
        else:
            self.reply(403, {"code": "remote_blocked", "error": BLOCKED_MESSAGE})

    def dashboard(self, path, write=False):
        port = self.server.server_port
        remote = getattr(self.server, "remote_dashboard", False)
        if remote:
            hosts = {f"{self.server.server_address[0]}:{port}"}
            # N'autoriser que le nom du certificat effectivement chargé.
            certificates = getattr(self.server, "certificates", None)
            identifier = getattr(certificates, "identifier", None)
            if identifier:
                authority = f"[{identifier}]" if ":" in identifier else identifier
                hosts.add(f"{authority}:{port}")
            scheme = "https"
        else:
            hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if self.client_address[0] != "127.0.0.1":
                self.reply(403, {"error": "Utilise l’adresse HTTPS affichée sur le PC de régie."})
                return False
            scheme = "http"
        host = self.headers.get("Host")
        if host not in hosts or (write and self.headers.get("Origin") != f"{scheme}://{host}"):
            self.reply(403, {"error": "Adresse d’accès au tableau non autorisée."})
            return False
        if not remote:
            return True
        address = self.client_address[0]
        connections = self.server.remote_connections
        page = path in ("/", "/connexion", "/terminal", "/addsshkey", "/setup")
        if connections.is_blocked(address):
            self.refuse_blocked(page)
            return False
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            cookies.clear()
        cookie = cookies.get(SESSION_COOKIE)
        self.remote_session = cookie.value if cookie else None
        if path == "/connexion":
            return True
        reason, self.session_remaining = connections.observe(address, self.remote_session)
        if not reason:
            self.principal = connections.principal(self.remote_session)
            self.grants = None if self.principal == USERNAME else self.server.grants(self.principal)
            if self.principal == USERNAME or self.grants is not None:
                return True
            reason = "remote_disconnected"
        if reason == "remote_blocked":
            self.refuse_blocked(page)
        elif page:
            destination = "/connexion?etat=expiree" if cookie else "/connexion"
            self.reply(303, b"", location=destination)
        else:
            self.reply(401, {"code": reason, "error": LOGIN_NOTICES["expiree"]})
        return False

    def login_page(self, status=200, notice="", error="", blocked=False):
        description = BLOCKED_MESSAGE if blocked else "Accède au tableau de régie pour piloter tes écrans."
        self.reply(status, LOGIN_PAGE.replace("__VERSION__", SOFTWARE_VERSION)
                   .replace("__USERNAME__", escape(USERNAME))
                   .replace("__TITLE__", "Accès bloqué" if blocked else "Connexion")
                   .replace("__DESCRIPTION__", escape(description))
                   .replace("__FORM_HIDDEN__", "hidden" if blocked else "")
                   .replace("__NOTICE__", escape(notice))
                   .replace("__NOTICE_HIDDEN__", "" if notice else "hidden")
                   .replace("__ERROR__", escape(error))
                   .replace("__ERROR_HIDDEN__", "" if error else "hidden").encode("utf-8"))

    def login(self, payload):
        if self.headers.get_content_type() != "application/x-www-form-urlencoded":
            self.login_page(400, error="Utilise le formulaire de connexion.")
            return
        try:
            values = parse_qs(payload.decode("utf-8"), max_num_fields=2, strict_parsing=True,
                              errors="strict")
            username, = values["identifiant"]
            password, = values["mot_de_passe"]
        except (KeyError, ValueError):
            self.login_page(400, error="Renseigne ton identifiant et ton mot de passe.")
            return
        if not self.server.authenticate(username, password):
            self.login_page(401, error="Identifiant ou mot de passe incorrect.")
            return
        connections = self.server.remote_connections
        session = connections.issue(self.client_address[0], self.headers.get("User-Agent", ""), username)
        if session is None:
            self.refuse_blocked(True)
            return
        if self.remote_session:
            connections.disconnect_if_owned(self.client_address[0], self.remote_session)
        self.reply(303, b"", location="/", session=session)

    def session_status(self):
        self.reply(200, {"remaining_ms": self.session_remaining, "warning_ms": SESSION_WARNING * 1000, "username": getattr(self,"principal",USERNAME)})

    def do_GET(self):
        url = urlsplit(self.path)
        path = url.path
        remote = getattr(self.server, "remote_dashboard", False)
        if path == KEYS_PATH and not remote:
            try:
                with INSTALLATION_KEYS_LOCK:
                    keys = installation_keys(ROOT)
            except (OSError, ValueError):
                self.reply(404, {"error": "Prépare les clés d’installation depuis la régie."})
                return
            self.reply(200, keys, content_type="application/json; charset=utf-8")
            return
        if path == "/api/client-media":
            self.send_media(url.query, preview=False)
            return
        if not self.dashboard(path):
            return
        if path == "/medias-personnels":
            style = _dashboard_markup.split("<style>",1)[1].split("</style>",1)[0]
            self.reply(200,PERSONAL_MEDIA_PAGE.replace("__SHARED_STYLE__",style).replace("__CONNECTED_USER__",escape(getattr(self,"principal",USERNAME))).encode("utf-8"))
            return
        if path in ("/api/personal-media","/api/personal-media/file"):
            self.personal_get(path,url.query)
            return
        if remote and getattr(self,"grants",None) is not None:
            if path == "/":
                self.reply(200, OPERATOR_PAGE.replace("__CONNECTED_USER__", escape(self.principal)).encode("utf-8"))
            elif path == "/api/operator/screens":
                with LOCK:
                    self.reply(200,self.operator_screens())
            elif path == "/api/session":
                self.session_status()
            elif path == "/api/preview":
                try:
                    query = {k:v[0] for k,v in parse_qs(url.query,max_num_fields=4).items()}
                    target,_ = preview_target(query)
                    with LOCK:
                        if not self.operator_preview_allowed(target):
                            raise PermissionError()
                        preview_prune(time.time())
                        job = PREVIEWS.get(target)
                        self.reply(200,preview_status(job) if job else {"state":"empty"})
                except PermissionError:
                    self.reply(403,{"error":"Accès refusé."})
                except (KeyError,ValueError):
                    self.reply(400,{"error":"Aperçu invalide."})
            else:
                self.reply(403,{"error":"Accès réservé à l’administration."})
            return
        if path == "/utilisateurs":
            self.reply(200, USERS_PAGE.encode("utf-8"))
        elif path == "/api/users":
            with LOCK:
                screens = {key+":"+i:row["name"]+" · Écran "+str(int(i)+1) for key,row in SCREENS.items() if row.get("token") for i in physical_outputs(row)}
            self.reply(200,{**self.server.public_accounts(),"screens":screens})
        elif path == "/api/media-preview":
            self.send_media(url.query, preview=True)
        elif path == "/api/media-download":
            self.send_media_archive(url.query)
        elif path == "/":
            self.reply(200, PAGE.replace(b"</body>", (SHARED_ACTION_DIALOG + PERSONAL_MEDIA_DIALOG).encode("utf-8") + b"</body>").replace(b"onclick=\"location.href=\'/medias-personnels\'\"", b"data-personal-media").replace(b"__REMOTE_ONLY__", b"" if remote else b"hidden").replace(b"__CONNECTED_USER__", escape(getattr(self,"principal",USERNAME)).encode("utf-8")))
        elif path == "/terminal":
            self.reply(200, TERMINAL_PAGE)
        elif path == "/setup":
            self.reply(200, (ROOT / "setup.html").read_bytes(), content_type="text/html; charset=utf-8")
        elif path == "/addsshkey":
            try:
                with INSTALLATION_KEYS_LOCK:
                    prepare_installations(ROOT)
                    public = ssh_access_public(ROOT)
                self.reply(200, render_ssh_page(public), content_type="text/html; charset=utf-8")
            except (OSError, RuntimeError, ValueError) as error:
                self.reply(409, {"error": "Clé SSH de la régie indisponible. Vérifie les clés d'installation."})
        elif remote and path == "/connexion":
            notice = LOGIN_NOTICES.get(url.query.removeprefix("etat="), "")
            self.login_page(notice=notice)
        elif remote and path == "/api/session":
            self.session_status()
        elif path == "/api/network":
            self.reply(200, {"addresses": local_addresses(),
                             "port": getattr(self.server, "receiver_port", self.server.server_port),
                             "desktop_protocol": desktop_protocol(), "support": regie_support_methods(),
                             "desktop_ports": desktop_services() if sys.platform != "win32" else {}})
        elif path == "/api/interface-traffic":
            self.reply(200, self.server.interface_traffic.read())
        elif path == "/api/regie-network":
            network = self.server.network_control
            self.reply(200, network.status() if network else {"available": False})
        elif path == "/api/regie-latency":
            self.reply(200, {"ok": True})
        elif path == "/api/os-health":
            manager = "windows" if sys.platform == "win32" else "linux"
            can_open = not remote and (manager == "windows" or bool(shutil.which("update-manager")))
            self.reply(200, {**self.server.os_update_check.report(), "manager": manager, "can_open": can_open})
        elif path == "/api/regie-version":
            self.reply(200, self.server.publication_status.read())
        elif path == "/api/preview":
            try:
                query = {name: values[0] for name, values in parse_qs(url.query, max_num_fields=4).items()}
                target, _ = preview_target(query)
            except (KeyError, ValueError):
                self.reply(400, {"error": "Aperçu demandé invalide."})
                return
            with LOCK:
                now = time.time()
                preview_prune(now)
                job = PREVIEWS.get(target)
                if target[0] == "screen" and now - SCREENS.get(target[1], {}).get("report", {}).get("seen", 0) >= ONLINE_AGE:
                    job = None
                value = preview_status(job) if job else {"state": "empty"}
            self.reply(200, value)
        elif path == "/api/remote-connections":
            connections = getattr(self.server, "remote_connections", None)
            self.reply(200, dict(enabled=connections is not None,
                                 connections=connections.snapshot() if connections else [],
                                 users={name: {"enabled": account.get("enabled",False)} for name,account in self.server.accounts()["users"].items()}))
        elif path == "/api/screens":
            now = time.time()
            key_exists = UPDATE_KEY.is_file()
            with LOCK:
                rows = []
                group_order = {name: index for index, name in enumerate(SCREEN_ORDER["groups"])}
                screen_order = {name: {key: index for index, key in enumerate(order)}
                                for name, order in SCREEN_ORDER["screens"].items()}
                for key, row in SCREENS.items():
                    report = row.get("report", {})
                    group = row.get("group") or ""
                    rows.append(dict(
                        id=key, name=row["name"], options=row.get("options"),
                        report=report, extra_outputs=row.get("extra_outputs", {}), online=now - report.get("seen", 0) < ONLINE_AGE,
                        pairing=row.get("pairing") if now < row.get("pairing_until", 0) else None,
                        installation_fingerprint=row.get("installation_fingerprint"),
                        audio_routes=row.get("audio_routes", {}),
                        installation_network=row.get("installation_network", {}),
                        paired="token" in row, version=row.get("version", 0), kind=row.get("kind"),
                        support=support_methods(row.get("kind"),
                                                (report.get("diagnostic") or {}).get("desktop_protocol"),
                                                (report.get("diagnostic") or {}).get("desktop_services")),
                        desktop_ports=(report.get("diagnostic") or {}).get("desktop_services", {}),
                        group=row.get("group"), output_groups=row.get("output_groups", {}), output_names=row.get("output_names", {}), physical_outputs=physical_outputs(row),
                        physical_order={index: dict(group=group_order.get(physical_group(row,index) or ""), screen=screen_order.get(physical_group(row,index) or "", {}).get(key if index=="0" else key+":"+index)) for index in physical_outputs(row)}, identify=row.get("identify") is True,
                        resolution_limit=row.get("resolution_limit"),
                        group_order=group_order.get(group), screen_order=screen_order.get(group, {}).get(key),
                        resolution_sources=resolution_sources(row, now),
                        source_resolutions={source: resolution for source in report.get("sources", [])
                                            if (resolution := source_resolution(source))},
                        client_update=update_view(key, report, now),
                        update_availability="maintenance" if os_busy(row, now) else update_availability(report, now, key_exists),
                        os_update=os_status(row, now), os_busy=os_busy(row, now),
                        media=screen_media(row), media_revision=media_revision(screen_media(row)),
                        media_folders=media_folders(row), media_folder=row.get("media_folder", MEDIA_DEFAULT_FOLDER),
                        extra_media=extra_media_folders(row),
                        media_disk=media_capacity(row),
                    ))
            self.reply(200, rows)
        elif path == "/api/media-storage":
            disk = media_disk(MEDIA_ROOT if MEDIA_ROOT.is_dir() else ROOT)
            self.reply(200, {**disk, "available": media_available(disk)})
        else:
            self.reply(404, {"error": "Page introuvable."})

    def do_POST(self):
        url = urlsplit(self.path)
        path = url.path
        remote = getattr(self.server, "remote_dashboard", False)
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if path == "/api/personal-media/upload":
            if self.dashboard(path,write=True):
                try:
                    self.personal_upload(url.query,length)
                except PermissionError as error:
                    self.reply(403,{"error":str(error)})
                except (ValueError,KeyError) as error:
                    self.reply(400,{"error":str(error)})
                except OSError:
                    self.reply(500,{"error":"Enregistrement impossible."})
            return
        if path == "/api/media-upload":
            if self.dashboard(path, write=True):
                if getattr(self,"grants",None) is not None:
                    self.reply(403,{"error":"Import réservé à l’administration."})
                    return
                try:
                    self.upload_media(url.query, length)
                except (ValueError, KeyError) as error:
                    self.reply(400, {"error": str(error)})
                except OSError as error:
                    print(error, flush=True)
                    self.reply(500, {"error": "Le fichier n’a pas pu être reçu ou enregistré par la régie."})
            return
        limit = 16384
        if path == "/api/preview-result":
            limit = 4 * ((PREVIEW_LIMIT + 2) // 3) + 1024
        elif path == "/api/media-export":
            limit = 512 * 1024
        if not 0 < length <= limit:
            self.reply(400, {"error": "Requête invalide."})
            return
        payload = self.rfile.read(length)
        if path in RECEIVER_PATHS:
            if remote:
                self.reply(404, {"error": "Les récepteurs utilisent le port d’association habituel."})
                return
        elif not self.dashboard(path, write=True):
            return
        if remote and path == "/connexion":
            self.login(payload)
            return
        try:
            value = json.loads(payload)
            if not isinstance(value, dict):
                raise ValueError("Requête invalide.")
            if path == "/api/personal-media":
                try:
                    with LOCK:
                        status,result=self.personal_change(value)
                except PermissionError as error:
                    status,result=403,{"error":str(error)}
                self.reply(status,result)
                return
            if path == "/api/remote-connections/disconnect":
                if getattr(self,"grants",None) is not None:
                    self.reply(403,{"error":"Accès refusé."})
                    return
                identifier=value.get("id")
                if not isinstance(identifier,str) or len(identifier)!=64 or any(c not in "0123456789abcdef" for c in identifier):
                    raise ValueError("Session invalide.")
                connections=getattr(self.server,"remote_connections",None)
                if connections:
                    connections.revoke_session(identifier)
                self.reply(200,{"ok":True})
                return
            if path == "/api/users":
                if getattr(self,"grants",None) is not None:
                    self.reply(403,{"error":"Accès refusé."})
                else:
                    with LOCK:
                        valid = {key+":"+i for key,row in SCREENS.items() if row.get("token") for i in physical_outputs(row)}
                    self.server.edit_accounts(value,valid)
                    self.reply(200,{"ok":True})
                return
            if remote and getattr(self,"grants",None) is not None and path not in ("/api/session/prolonger","/api/session/deconnexion"):
                with LOCK:
                    status,result = self.operator_action(path,value)
                self.reply(status,result)
                return
            if path == "/api/regie-network":
                self.change_network(value)
                return
            if path == "/api/pair":
                value.pop("installation_fingerprint", None)
                if value.get("prepare_installation") is True:
                    try:
                        with INSTALLATION_KEYS_LOCK:
                            value["installation_fingerprint"] = prepare_installations(ROOT)
                    except (OSError, RuntimeError, ValueError) as error:
                        self.reply(409, {"error": f"Impossible de préparer les clés d’installation. {error}"})
                        return
            if path.startswith("/api/ssh/"):
                self.terminal_action(path.removeprefix("/api/ssh/"), value)
                return
            if path == "/api/deploy/start":
                try:
                    identifier = value.get("id")
                    with LOCK:
                        row = SCREENS.get(identifier) if isinstance(identifier, str) else None
                        if not row or "token" in row or time.time() >= row.get("pairing_until", 0):
                            raise ValueError("Ajoute un nouvel écran avec un code d'association encore valide.")
                        kind, code = row.get("kind"), row.get("pairing")
                        install_fingerprint = row.get("installation_fingerprint")
                    if not isinstance(install_fingerprint, str) or not SAFE_ID.fullmatch(install_fingerprint):
                        raise ValueError("Empreinte de la régie absente. Recrée le code d'association.")
                    with INSTALLATION_KEYS_LOCK:
                        live_fingerprint = sha256(installation_keys(ROOT)).hexdigest()
                    if not hmac.compare_digest(install_fingerprint, live_fingerprint):
                        raise ValueError("Les clés d'installation ont changé. Recrée le code d'association.")
                    host = validate_ipv4(value.get("ip"))
                    regie = validate_ipv4(value.get("regie"))
                    if regie not in {a["ip"] for a in local_addresses()}:
                        raise ValueError("Sélectionne une adresse IPv4 de ce PC de régie.")
                    if host in {a["ip"] for a in local_addresses()}:
                        raise ValueError("L'adresse cible appartient au PC de régie.")
                    result = DEPLOYMENTS.start(
                        (self.client_address[0], getattr(self, "remote_session", None)),
                        identifier, host, value.get("user"), value.get("fingerprint"),
                        kind, regie, self.server.receiver_port, code,
                        install_fingerprint, value.get("sudo_password", ""))
                    # Un installateur Linux peut télécharger et compiler le SDK NDI.
                    # Le code reste valable pendant le déploiement, puis expirera.
                    with LOCK:
                        current = SCREENS.get(identifier)
                        if current and current.get("pairing") == code and "token" not in current:
                            current["pairing_until"] = time.time() + 36 * 60
                            save()
                    self.reply(200, result)
                except (OSError, ValueError, RuntimeError) as error:
                    self.reply(400, {"error": str(error)})
                return
            if path == "/api/deploy/status":
                try:
                    self.reply(200, DEPLOYMENTS.status(
                        (self.client_address[0], getattr(self, "remote_session", None)),
                        value.get("job")))
                except ValueError as error:
                    self.reply(400, {"error": str(error)})
                return
            if path == "/api/os-health/open":
                if remote:
                    self.reply(403, {"error": "Ouvre le gestionnaire de mises à jour depuis le PC de régie."})
                    return
                if sys.platform == "win32":
                    os.startfile("ms-settings:windowsupdate")
                elif manager := shutil.which("update-manager"):
                    subprocess.Popen([manager], stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    self.reply(409, {"error": "Utilise le gestionnaire de paquets sur le PC de régie."})
                    return
                self.reply(200, {"ok": True})
                return
            if path == "/api/desktop/open":
                if remote:
                    self.reply(403, {"error": "Le lancement d’un client sur la régie est réservé à la console locale."})
                    return
                key = value.get("id")
                target = support_target(key)
                protocol = value.get("protocol")
                if protocol not in ("vnc", "rdp") or protocol not in target["support"]:
                    raise ValueError("Ce type de bureau distant est indisponible pour ce poste.")
                address = target["address"]
                if key == "regie":
                    address = str(IPv4Address(value.get("address", "")))
                    if address not in {item["ip"] for item in local_addresses()}:
                        raise ValueError("Choisis une adresse du PC de régie.")
                port = target.get("desktop_ports", {}).get(protocol)
                command = desktop_command(address, protocol, port)
                if command:
                    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                self.reply(200, {"opened": command is not None})
                return
            if remote and path == "/api/session/prolonger":
                reason, self.session_remaining = self.server.remote_connections.observe(
                    self.client_address[0], self.remote_session, renew=True)
                if reason == "remote_blocked":
                    self.refuse_blocked(False)
                elif reason:
                    self.reply(401, {"code": reason, "error": LOGIN_NOTICES["expiree"]})
                else:
                    self.session_status()
                return
            if remote and path == "/api/session/deconnexion":
                self.server.remote_connections.disconnect(self.remote_session)
                self.reply(200, {"ok": True}, session="")
                return
            with LOCK:
                status, result = self.apply(path, value)
            if path == "/api/poll" and status == 200 and result.get("os_job"):
                job = result["os_job"]
                try:
                    job["signature"] = sign_manifest(UPDATE_KEY, maintenance_manifest(
                        value["id"], job["id"], safe_text(value["boot"]), value["os_nonce"]), OS_NAMESPACE)
                except (OSError, RuntimeError) as error:
                    print(f"Signature de la maintenance système : {error}", flush=True)
                    result["os_job"] = None
                    with LOCK:
                        row = SCREENS.get(value["id"], {})
                        previous = row.get("os_update")
                        if previous and previous["id"] == job["id"] and previous["state"] == "queued":
                            row["os_update"] = {**previous, "state": "failed",
                                                "message": "La régie n’a pas pu signer la demande."}
                            try:
                                save()
                            except OSError:
                                row["os_update"] = previous
                                raise
            if path == "/api/client-package" and status == 200:
                try:
                    result = package(UPDATE_KEY, **result)
                except (OSError, RuntimeError) as error:
                    print(f"Signature des mises à jour : {error}", flush=True)
                    reason = error.reason if isinstance(error, OpenSSHError) else "failed"
                    message = {
                        "missing": "Le client OpenSSH (ssh-keygen) est introuvable sur la régie.",
                        "timeout": "OpenSSH ne répond pas pendant la signature sur la régie.",
                        "permissions": "La régie n’a pas pu sécuriser sa clé privée de mise à jour.",
                    }.get(reason, "La régie n’a pas pu signer le paquet.")
                    status, result = 503, {"code": "update_signing_failed",
                                           "error": message + " Sur la régie, lance tableau.py --tester-mises-a-jour."}
        except (KeyError, ValueError, TypeError) as error:
            status, result = 400, {"error": str(error)}
        except (OSError, RuntimeError) as error:
            print(error, flush=True)
            status, result = 500, {"error": "Le serveur n’a pas pu terminer la demande."}
        self.reply(status, result)

    def allowed(self, key, index, right):
        return isinstance(key,str) and isinstance(index,str) and right in self.grants.get(key+":"+index,set())

    def available_ndi_sources(self):
        now = time.time()
        names = set()
        for row in SCREENS.values():
            report = row.get("report", {})
            if row.get("token") and now - report.get("seen", 0) < ONLINE_AGE:
                names.update(source for source in report.get("sources", []) if source)
                if report.get("active_source"):
                    names.add(report["active_source"])
        return sorted(names, key=str.casefold)

    def operator_screens(self):
        rows=[]
        available_sources = self.available_ndi_sources()
        for key,row in SCREENS.items():
            if not row.get("token"):
                continue
            report=row.get("report",{})
            online=time.time()-report.get("seen",0)<ONLINE_AGE
            for index in physical_outputs(row):
                if not self.allowed(key,index,"view"):
                    continue
                settings=dict(row.get("options",{}) if index=="0" else row.get("extra_outputs",{}).get(index,{}))
                if settings.get("mode")=="same":
                    settings["source"]=row.get("options",{}).get("source","")
                out=next((o for o in report.get("outputs",[]) if o["id"]==index),{})
                playing=out.get("playing",report.get("state")=="Lecture" if index=="0" else False)
                perms=sorted(self.grants[key+":"+index])
                whole=lambda right: all(self.allowed(key,i,right) for i in physical_outputs(row))
                diagnostic=report.get("diagnostic") or {}
                health=dict(online=online,kind=row.get("kind"),ip=report.get("ip"),version=report.get("client_release"),latency=report.get("latency_ms"),disk_limit=MEDIA_DISK_PERCENT,
                            diagnostic={**{k:diagnostic.get(k) for k in ("cpu_pct","ram_used","ram_total","disk_used","disk_total","disk_free")},
                                        "interfaces":[{k:link.get(k) for k in ("name","kind","ssid","to_regie","rx_mbps","tx_mbps")} for link in diagnostic.get("interfaces",[]) if link.get("connected")]})
                rows.append(dict(health=health,id=key,output=index,name=row.get("output_names",{}).get(index) or row["name"]+" · Écran "+str(int(index)+1),
                                 reader_name=row["name"], output_name=row.get("output_names",{}).get(index,""), group=physical_group(row,index) or "",
                                 status="Hors ligne" if not online or index!="0" and not out else "Lecture" if playing else "Démarrage" if index=="0" and settings.get("enabled") and report.get("state")=="Démarrage" else "En attente" if settings.get("enabled") else "Arrêté",
                                 settings={k:v for k,v in settings.items() if k in {"enabled"} or k in {"source","bandwidth","mode"} and "source" in perms or k in {"volume","muted"} and "audio" in perms or k=="orientation" and "configuration" in perms},rights=perms,sources=available_sources if "source" in perms else [],
                                 preview_sources=available_sources,
                                 audio_devices=report.get("audio_devices",[]) if "audio" in perms else [],
                                 audio_route=row.get("audio_routes",{}).get(index,"") if "audio" in perms else "",
                                 folders={k:v["name"] for k,v in media_folders(row).items() if not k.startswith("personal-") or k.startswith(personal_copy_prefix(self.principal))} if "media" in perms else {},
                                 maintenance=whole("maintenance"),identify=whole("identify"),identified=row.get("identify") is True,
                                 connector=out.get("connector",""), width=out.get("width"),height=out.get("height"),
                                 display_capture=report.get("display_capture") is True if index=="0" else bool(out), capture_consent=report.get("capture_needs_consent") is True))
        return rows

    def operator_preview_allowed(self,target):
        if target[0]=="screen":
            return self.allowed(target[1],target[2] if len(target)==3 else "0","preview")
        return target[1] in self.available_ndi_sources() and any(
            self.allowed(key,i,"view") and self.allowed(key,i,"preview")
            for key,row in SCREENS.items() if row.get("token") for i in physical_outputs(row))

    def operator_action(self,path,data):
        denied=(403,{"error":"Action non autorisée."})
        if path == "/api/preview":
            target,_=preview_target(data)
            if not self.operator_preview_allowed(target):
                return denied
            return request_preview(data,time.time())
        if path == "/api/operator/identify":
            ids,enabled=data.get("ids"),data.get("enabled")
            if type(enabled) is not bool or not isinstance(ids,list) or not ids or len(ids)>250 or any(not isinstance(key,str) for key in ids):
                return denied
            ids=list(dict.fromkeys(ids))
            for key in ids:
                row=SCREENS.get(key)
                if not row or not row.get("token") or not all(self.allowed(key,i,"view") and self.allowed(key,i,"identify") for i in physical_outputs(row)):
                    return denied
            return self.apply("/api/identify",{"ids":ids,"enabled":enabled})
        if path == "/api/operator/group":
            ids,patch=data.get("ids"),data.get("changes")
            fields={"enabled":"play","source":"source","bandwidth":"source","volume":"audio","muted":"audio","orientation":"configuration"}
            if not isinstance(ids,list) or not 0<len(ids)<=250 or not isinstance(patch,dict) or not patch or any(k not in fields for k in patch):
                return denied
            for item in ids:
                if not isinstance(item,str) or ":" not in item:
                    return denied
                key,index=item.split(":",1)
                if not self.allowed(key,index,"view") or any(not self.allowed(key,index,fields[k]) for k in patch):
                    return denied
            return physical_batch(list(dict.fromkeys(ids)),{"changes":patch},time.time())
        if path != "/api/operator/action":
            return denied
        key,index,action=data.get("id"),data.get("output"),data.get("action")
        if not self.allowed(key,index,"view"):
            return denied
        row=SCREENS.get(key)
        if not row or index not in physical_outputs(row) or not row.get("token"):
            return 404,{"error":"Écran indisponible."}
        if action=="settings":
            patch=data.get("changes")
            fields={"enabled":"play","source":"source","bandwidth":"source","volume":"audio","muted":"audio","orientation":"configuration"}
            if not isinstance(patch,dict) or not patch or any(k not in fields or not self.allowed(key,index,fields[k]) for k in patch):
                return denied
            return physical_batch([key+":"+index],{"changes":patch},time.time())
        if action=="audio" and self.allowed(key,index,"audio"):
            return self.apply("/api/audio-route",{"id":key,"output":index,"target":data.get("target")})
        if action in ("reboot","poweroff","identify"):
            right="identify" if action=="identify" else "maintenance"
            if not all(self.allowed(key,i,right) for i in physical_outputs(row)):
                return denied
            return self.apply("/api/"+action,{"id":key,"enabled":data.get("enabled")})
        if action=="media" and self.allowed(key,index,"media"):
            # The reader library is shared; operators may only select existing folders.
            if type(data.get("enabled")) is not bool:
                return 400,{"error":"État média invalide."}
            folder=data.get("folder")
            if not data["enabled"]:
                folder=MEDIA_DEFAULT_FOLDER
            elif not isinstance(folder,str) or (folder.startswith("personal-") and not folder.startswith(personal_copy_prefix(self.principal))):
                return denied
            return self.change_media(key,row,{"action":"play","folder":folder,"enabled":data["enabled"],"outputs":[index]})
        return denied

    def change_network(self, value):
        network = self.server.network_control
        if network is None:
            self.reply(409, {"error": "Démarre la régie avec son lanceur graphique pour gérer le réseau."})
            return
        try:
            report = network.change(value.get("kind"), value.get("operation"), value.get("data"), web=True)
        except PermissionError as error:
            self.reply(403, {"error": str(error)})
        except (OSError, RuntimeError, ValueError, TypeError, subprocess.SubprocessError) as error:
            self.reply(409, {"error": str(error)})
        else:
            self.reply(200, {"report": report or ""})

    def prepare_ssh_readers(self, ids):
        with INSTALLATION_KEYS_LOCK:
            prepare_installations(ROOT)
            packages = build_full_updates(ssh_access_public(ROOT))
            with LOCK:
                FULL_UPDATES.clear()
                FULL_UPDATES.update(packages)
                now = time.time()
                for key in ids:
                    report = SCREENS.get(key, {}).get("report", {})
                    current = CLIENT_UPDATES.get(key)
                    pending = update_view(key, report, now) or {}
                    desired = update_payload(report)
                    if (current and desired and current["version"] != desired[1]
                            and pending.get("state") in ("queued", "installing")):
                        return 409, {"error": "Attends la fin de la mise à jour client en cours, puis prépare l’accès par clé."}
                return batch("/api/client-updates", {"ids": ids}, now)

    def terminal_action(self, operation, value):
        owner = (self.client_address[0], getattr(self, "remote_session", None))
        terminals = self.server.terminals
        try:
            if operation in ("target", "start"):
                key = value.get("id")
                target = support_target(key)
                if "ssh" not in target["support"]:
                    raise ValueError("Le terminal web SSH est disponible pour les postes Linux.")
                result = target if operation == "target" else terminals.start(
                    owner, key, target["address"], value, target.get("ssh_host_fingerprints", ()))
            elif operation in ("group-start", "group-prepare"):
                ids = value.get("ids")
                if (not isinstance(ids, list) or not ids or len(ids) > GROUP_LIMIT
                        or any(not isinstance(key, str) or key == "regie" for key in ids)):
                    raise ValueError(f"Sélectionne entre 1 et {GROUP_LIMIT} lecteurs Raspberry Pi ou Ubuntu.")
                with LOCK:
                    if any(key not in SCREENS or not SCREENS[key].get("token")
                           or SCREENS[key].get("kind") not in ("pi", "ubuntu") for key in ids):
                        raise ValueError("Sélectionne uniquement des lecteurs Linux associés.")
                    names = {key: SCREENS[key]["name"] for key in ids}
                if operation == "group-prepare":
                    self.reply(*self.prepare_ssh_readers(list(names)))
                    return
                targets = {}
                for key, name in names.items():
                    try:
                        targets[key] = support_target(key)
                    except ValueError as error:
                        targets[key] = dict(name=name, error=str(error))
                result = terminals.start_group(owner, targets, value)
            elif operation == "group-status":
                result = terminals.group_status(owner, value.get("job"))
            elif operation == "sync":
                result = terminals.sync(owner, value.get("session"), value.get("input"))
            elif operation == "end":
                terminals.end(owner, value.get("session"))
                result = {"ok": True}
            else:
                self.reply(404, {"error": "Action terminal inconnue."})
                return
        except HostKeyCheck as error:
            self.reply(409, {"error": str(error), "code": "ssh_host_key",
                             "presented": error.presented, "expected": error.expected})
            return
        except (ValueError, RuntimeError) as error:
            self.reply(400, {"error": str(error)})
            return
        self.reply(200, result)

    def personal_owners(self):
        if getattr(self,"grants",None) is not None:
            return [self.principal]
        owners = {USERNAME}
        for path in PERSONAL_ROOT.glob("*/catalog.json"):
            try:
                owner = json.loads(path.read_text(encoding="utf-8"))["owner"]
                if personal_directory(owner) == path.parent:
                    owners.add(owner)
            except (OSError, ValueError, KeyError):
                continue
        if hasattr(self.server,"public_accounts"):
            owners.update(self.server.public_accounts()["users"])
        return sorted(owners, key=str.casefold)

    def personal_owner(self, owner):
        owners = self.personal_owners()
        owner = owner or (self.principal if getattr(self,"grants",None) is not None else USERNAME)
        if owner not in owners:
            raise PermissionError("Accès à ce dossier refusé.")
        return owner

    def personal_screens(self):
        return {key+":"+i:row["name"]+" · Écran "+str(int(i)+1)
                for key,row in SCREENS.items() if row.get("token")
                for i in physical_outputs(row) if getattr(self,"grants",None) is None
                or (self.allowed(key,i,"view") and self.allowed(key,i,"media"))}

    def personal_get(self, path, raw_query):
        try:
            query = parse_qs(raw_query, max_num_fields=4)
            with LOCK:
                owner = self.personal_owner(query.get("owner",[""])[0])
                directory, data = personal_catalog(owner)
                if path == "/api/personal-media":
                    save_personal(directory,data)
                    screens=self.personal_screens()
                    outputs={}
                    for target in screens:
                        key,index=target.split(":",1)
                        row=SCREENS[key]
                        local=screen_media(row)["enabled"] if index=="0" else index in extra_media_folders(row)
                        folder_id=row.get("media_folder",MEDIA_DEFAULT_FOLDER) if index=="0" else extra_media_folders(row).get(index)
                        folder=media_folders(row).get(folder_id,{})
                        visible=getattr(self,"grants",None) is None or not str(folder_id).startswith("personal-") or str(folder_id).startswith(personal_copy_prefix(self.principal))
                        outputs[target]=dict(local=local,mode=("Médias · "+folder.get("name","") if visible else "Médias en cours") if local else "Diffusion NDI")
                    self.reply(200,dict(owners=self.personal_owners(),owner=owner,folders=data["folders"],screens=screens,outputs=outputs,disk=media_available(media_disk(directory))))
                    return
                folder = data["folders"][query["folder"][0]]
                item = next(i for i in folder["items"] if i["id"] == query["file"][0])
                stream = (directory / item["id"]).open("rb")
            with stream:
                self.send_response(200)
                self.send_header("Content-Type", MEDIA_TYPES[Path(item["name"]).suffix.lower()])
                self.send_header("Content-Length",str(item["size"]))
                self.send_header("Cache-Control","no-store")
                self.send_header("X-Content-Type-Options","nosniff")
                self.send_header("Content-Security-Policy","default-src 'none'; sandbox")
                self.end_headers()
                shutil.copyfileobj(stream,self.wfile,MEDIA_CHUNK)
        except PermissionError as error:
            self.reply(403,{"error":str(error)})
        except (KeyError,ValueError,StopIteration,FileNotFoundError):
            self.reply(404,{"error":"Média ou dossier indisponible."})

    def personal_upload(self, raw_query, length):
        if self.headers.get_content_type() != "application/octet-stream" or not 0 < length <= MEDIA_FILE_LIMIT:
            raise ValueError("Fichier non vide de 256 Mio maximum.")
        query = parse_qs(raw_query,max_num_fields=3)
        owner = self.personal_owner(query["owner"][0])
        folder_id = query["folder"][0]
        name = media_name(query["name"][0])
        with LOCK:
            directory,data = personal_catalog(owner)
            if folder_id not in data["folders"]:
                raise ValueError("Dossier indisponible.")
            save_personal(directory,data)
        with NamedTemporaryFile(dir=directory,delete=False) as handle:
            staged=Path(handle.name)
            checksum=sha256()
            try:
                remaining=length
                while remaining:
                    chunk=self.rfile.read(min(remaining,MEDIA_CHUNK))
                    if not chunk:
                        raise ValueError("Transfert interrompu.")
                    with MEDIA_WRITE_LOCK:
                        if len(chunk)>media_available(media_disk(directory)):
                            raise ValueError("Espace disque insuffisant.")
                        handle.write(chunk)
                    checksum.update(chunk)
                    remaining-=len(chunk)
                handle.close()
                with LOCK:
                    directory,data=personal_catalog(owner)
                    folder=data["folders"][folder_id]
                    if len(folder["items"])>=MEDIA_COUNT_LIMIT or sum(i["size"] for i in folder["items"])+length>MEDIA_TOTAL_LIMIT:
                        raise ValueError("Limite du dossier : 20 fichiers et 1 Gio.")
                    identifier=secrets.token_hex(16)
                    destination=directory / identifier
                    staged.replace(destination)
                    item=dict(id=identifier,name=name,size=length,sha256=checksum.hexdigest())
                    folder["items"].append(item)
                    try:
                        save_personal(directory,data)
                    except OSError:
                        destination.unlink(missing_ok=True)
                        raise
            finally:
                handle.close()
                staged.unlink(missing_ok=True)
        self.reply(201,item)

    def personal_change(self, data):
        owner=self.personal_owner(data.get("owner"))
        directory,catalog=personal_catalog(owner)
        folders=catalog["folders"]
        action=data.get("action")
        folder_id=data.get("folder")
        removed=[]
        if action=="ndi":
            target=data.get("screen")
            if target not in self.personal_screens():
                raise PermissionError("Écran non autorisé.")
            key,index=target.split(":",1)
            return self.change_media(key,SCREENS[key],dict(action="play",folder=MEDIA_DEFAULT_FOLDER,enabled=False,outputs=[index]))
        if action=="create":
            if len(folders)>=MEDIA_FOLDER_LIMIT:
                raise ValueError("Limite de dossiers atteinte.")
            folders[secrets.token_hex(8)]=dict(name=safe_text(data["name"],64),items=[])
        else:
            folder=folders[folder_id]
            if action=="rename":
                folder["name"]=safe_text(data["name"],64)
            elif action=="remove-folder":
                if folder_id=="general":
                    raise ValueError("Le dossier général ne peut pas être supprimé.")
                removed=folder["items"]
                del folders[folder_id]
            elif action=="timing":
                folder["timing"]=media_timing(dict(image_seconds=data["image_seconds"],video_seconds=data["video_seconds"]))
            elif action=="empty-folder":
                removed=folder["items"]
                folder["items"]=[]
            elif action=="remove-files":
                ids=data.get("files")
                if not isinstance(ids,list) or not ids or any(not isinstance(i,str) for i in ids):
                    raise ValueError("Sélection invalide.")
                removed=[i for i in folder["items"] if i["id"] in ids]
                if len(removed)!=len(set(ids)):
                    raise ValueError("Fichier indisponible.")
                folder["items"]=[i for i in folder["items"] if i["id"] not in ids]
            elif action in ("move","reorder"):
                item=next((i for i in folder["items"] if i["id"]==data.get("file")),None)
                if item is None:
                    raise ValueError("Fichier indisponible.")
                if action=="move":
                    destination=folders[data["destination"]]
                    if destination is folder:
                        raise ValueError("Choisis un autre dossier.")
                    media_config(dict(enabled=False,items=[*destination["items"],item]))
                    folder["items"].remove(item)
                    destination["items"].append(item)
                else:
                    anchor=next((i for i in folder["items"] if i["id"]==data.get("anchor")),None)
                    if anchor is None or anchor is item:
                        raise ValueError("Déplacement invalide.")
                    folder["items"].remove(item)
                    folder["items"].insert(folder["items"].index(anchor),item)
            elif action in ("remove-file","rename-file"):
                item=next((i for i in folder["items"] if i["id"]==data.get("file")),None)
                if item is None:
                    raise ValueError("Fichier indisponible.")
                if action=="remove-file":
                    folder["items"].remove(item)
                    removed=[item]
                else:
                    name=media_name(data["name"])
                    if Path(name).suffix.lower()!=Path(item["name"]).suffix.lower():
                        raise ValueError("Conserve l’extension du fichier.")
                    item["name"]=name
            elif action=="play":
                target=data.get("screen")
                if target not in self.personal_screens():
                    raise PermissionError("Écran non autorisé.")
                key,index=target.split(":",1)
                row=SCREENS[key]
                if not folder["items"]:
                    raise ValueError("Dossier vide.")
                current=media_folders(row).copy()
                if len(current)>=MEDIA_FOLDER_LIMIT or len(catalog_items(row))+len(folder["items"])>MEDIA_LIBRARY_LIMIT:
                    raise ValueError("Bibliothèque du lecteur pleine.")
                capacity=media_capacity(row)
                total=sum(i["size"] for i in folder["items"])
                if capacity is None or total>capacity["available"]:
                    raise ValueError("Espace disponible du lecteur insuffisant ou inconnu.")
                if total>media_available(media_disk(directory)):
                    raise ValueError("Espace disque de la régie insuffisant.")
                copy_id=personal_copy_prefix(owner)+secrets.token_hex(8)
                target_dir=MEDIA_ROOT / key
                target_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
                copied=[]
                previous=dict(row)
                try:
                    for item in folder["items"]:
                        new={**item,"id":secrets.token_hex(16)}
                        copied.append(new)
                        shutil.copyfile(directory / item["id"],target_dir / new["id"])
                    current[copy_id]=dict(name=("Personnel · "+owner+" · "+folder["name"])[:64],items=copied,timing=folder.get("timing",{}))
                    save_media(row,current,row.get("media_folder",MEDIA_DEFAULT_FOLDER),screen_media(row)["enabled"])
                    status,result=self.change_media(key,row,dict(action="play",folder=copy_id,enabled=True,outputs=[index]))
                    if status!=200:
                        raise ValueError(result.get("error","Diffusion impossible."))
                except (OSError,ValueError,KeyError):
                    row.clear()
                    row.update(previous)
                    save()
                    for item in copied:
                        (target_dir / item["id"]).unlink(missing_ok=True)
                    raise
                return 200,result
            else:
                raise ValueError("Action inconnue.")
        save_personal(directory,catalog)
        for item in removed:
            (directory / item["id"]).unlink(missing_ok=True)
        return 200,{"ok":True}

    def upload_media(self, raw_query, length):
        if self.headers.get_content_type() != "application/octet-stream" or not 0 < length <= MEDIA_FILE_LIMIT:
            raise ValueError("Envoie un fichier non vide de 256 Mio maximum.")
        query = parse_qs(raw_query, max_num_fields=3)
        key, name = query["id"][0], media_name(query["name"][0])
        folder_id = query.get("folder", [MEDIA_DEFAULT_FOLDER])[0]
        with LOCK:
            row = SCREENS.get(key)
            if not row or not row.get("token"):
                raise ValueError("Cet écran doit être associé.")
            folder = media_folders(row).get(folder_id)
            if folder is None:
                raise ValueError("Ce dossier n’existe plus.")
            media_quota(row, folder, length)
            generation = row.get("media_generation", 0)
            folder_generation = folder.get("generation", 0)
        MEDIA_ROOT.mkdir(mode=0o700, exist_ok=True)
        if length > media_available(media_disk(MEDIA_ROOT)):
            raise ValueError(f"Ce fichier ferait dépasser le seuil de {MEDIA_DISK_PERCENT} % sur la régie.")
        with NamedTemporaryFile(dir=MEDIA_ROOT, delete=False, buffering=0) as handle:
            staged = Path(handle.name)
            checksum = sha256()
            try:
                remaining = length
                while remaining:
                    data = self.rfile.read(min(remaining, MEDIA_CHUNK))
                    if not data:
                        raise ValueError("Le transfert du fichier a été interrompu.")
                    with MEDIA_WRITE_LOCK:
                        if len(data) > media_available(media_disk(MEDIA_ROOT)):
                            raise ValueError(f"Le disque de la régie a atteint le seuil de {MEDIA_DISK_PERCENT} %.")
                        handle.write(data)
                    checksum.update(data)
                    remaining -= len(data)
                handle.close()
                with LOCK:
                    if SCREENS.get(key) is not row:
                        raise ValueError("Cet écran a été retiré pendant le transfert.")
                    if row.get("media_generation", 0) != generation:
                        raise ValueError("L’envoi a été annulé parce que les dossiers ont été vidés.")
                    folders = media_folders(row)
                    folder = folders.get(folder_id)
                    if folder is None:
                        raise ValueError("Ce dossier a été supprimé pendant le transfert.")
                    if folder.get("generation", 0) != folder_generation:
                        raise ValueError("L’envoi a été annulé parce que le dossier a été vidé.")
                    media_quota(row, folder, length)
                    identifier = secrets.token_hex(16)
                    directory = MEDIA_ROOT / key
                    directory.mkdir(mode=0o700, exist_ok=True)
                    destination = directory / identifier
                    staged.replace(destination)
                    item = dict(id=identifier, name=name, size=length, sha256=checksum.hexdigest())
                    try:
                        folders = {**folders, folder_id: {**folder, "items": [*folder["items"], item]}}
                        save_media(row, folders, row.get("media_folder", MEDIA_DEFAULT_FOLDER), screen_media(row)["enabled"])
                    except (OSError, ValueError):
                        destination.unlink(missing_ok=True)
                        raise
            finally:
                handle.close()
                staged.unlink(missing_ok=True)
        self.reply(201, item)

    def send_media(self, raw_query, *, preview):
        if not preview and getattr(self.server, "remote_dashboard", False):
            self.reply(404, {"error": "Utilise le port d’association du récepteur."})
            return
        try:
            query = parse_qs(raw_query, max_num_fields=3)
            key, identifier = query["id"][0], query["file"][0]
        except (KeyError, ValueError):
            self.reply(400, {"error": "Fichier média invalide."})
            return
        with LOCK:
            row = SCREENS.get(key)
            token = row and row.get("token")
            allowed = token and (preview or token_matches(self.headers.get("Authorization"), "Bearer " + token))
            item = next((item for item in catalog_items(row) if item["id"] == identifier), None) if allowed else None
            try:
                stream = (MEDIA_ROOT / key / identifier).open("rb") if item else None
            except OSError:
                stream = None
        if not stream:
            self.reply(404 if allowed else 403, {"error": "Fichier média indisponible."})
            return
        with media_stream(stream, key, identifier):
            byte_range = self.headers.get("Range") if preview else None
            try:
                start, end = media_bytes_range(byte_range, item["size"])
            except ValueError:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{item['size']}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if byte_range else 200)
            self.send_header("Content-Type", MEDIA_TYPES[Path(item["name"]).suffix.lower()] if preview else "application/octet-stream")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if preview:
                self.send_header("Accept-Ranges", "bytes")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{item['size']}")
            self.end_headers()
            try:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(MEDIA_CHUNK, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except OSError:
                pass

    def prepare_media_export(self, data, now):
        files = data.get("files")
        if not isinstance(files, list) or not files:
            raise ValueError("Sélectionne les fichiers à télécharger.")
        catalogs = {}
        selected = {}
        for entry in files:
            key, identifier = entry["id"], entry["file"]
            if not isinstance(key, str) or not isinstance(identifier, str):
                raise ValueError("Fichier média invalide.")
            if key not in catalogs:
                row = SCREENS.get(key)
                catalogs[key] = {item["id"]: item for item in catalog_items(row)} if row and row.get("token") else {}
            item = catalogs[key].get(identifier)
            if item is None or not (MEDIA_ROOT / key / identifier).is_file():
                return 404, {"error": "Un fichier sélectionné est indisponible."}
            selected[key, identifier] = item["name"]
        for token, (deadline, _) in tuple(MEDIA_EXPORTS.items()):
            if deadline <= now:
                del MEDIA_EXPORTS[token]
        token = secrets.token_urlsafe(24)
        MEDIA_EXPORTS[token] = now + 60, selected
        return 200, {"url": "/api/media-download?token=" + token}

    def send_media_archive(self, raw_query):
        token = raw_query.removeprefix("token=")
        with LOCK:
            deadline, files = MEDIA_EXPORTS.pop(token, (0, {}))
        if time.time() >= deadline:
            self.reply(404, {"error": "Ce téléchargement a expiré. Relance-le depuis la médiathèque."})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", 'attachment; filename="SoundLive-medias.zip"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        names = set()
        missing = []
        try:
            with ZipFile(self.wfile, "w") as archive:
                for (key, identifier), original in files.items():
                    try:
                        stream = (MEDIA_ROOT / key / identifier).open("rb")
                    except OSError:
                        missing.append(original)
                        continue
                    name = re.sub(r'[<>:"|?*]', "_", original)
                    stem, suffix = Path(name).stem, Path(name).suffix
                    serial = 2
                    while name.casefold() in names:
                        name = f"{stem} ({serial}){suffix}"
                        serial += 1
                    names.add(name.casefold())
                    with media_stream(stream, key, identifier), archive.open(name, "w") as target:
                        shutil.copyfileobj(stream, target, MEDIA_CHUNK)
                if missing:
                    archive.writestr("Fichiers indisponibles.txt", "\n".join(missing) + "\n")
        except OSError:
            pass

    def change_media(self, key, row, data):
        folders = media_folders(row).copy()
        active = row.get("media_folder", MEDIA_DEFAULT_FOLDER)
        enabled = screen_media(row)["enabled"]
        action = data.get("action", "play" if "enabled" in data else "remove-file")
        folder_id = data.get("folder", active)
        if not isinstance(folder_id, str) or folder_id not in folders:
            raise ValueError("Ce dossier n’existe plus.")
        folder = folders[folder_id]
        removed = []
        extra_media, extra_outputs = extra_media_folders(row, folders), None
        resume_primary = False
        if action in ("create", "rename"):
            name = safe_text(data["name"], 64)
            if not name.isprintable():
                raise ValueError("Nom de dossier invalide.")
            if any(entry["name"].casefold() == name.casefold() for identifier, entry in folders.items()
                   if action == "create" or identifier != folder_id):
                raise ValueError("Un dossier porte déjà ce nom.")
            if action == "create":
                if len(folders) >= MEDIA_FOLDER_LIMIT:
                    raise ValueError("Ce lecteur a atteint la limite de 32 dossiers.")
                folder_id = secrets.token_hex(8)
                folder = dict(items=[])
            folders[folder_id] = {**folder, "name": name}
        elif action == "empty-all":
            removed = catalog_items(row)
            folders = {identifier: {**entry, "items": []} for identifier, entry in folders.items()}
            enabled = False
        elif action == "empty-folder":
            removed = folder["items"]
            folders[folder_id] = {**folder, "items": [], "generation": folder.get("generation", 0) + 1}
        elif action == "remove-folder":
            if folder_id == MEDIA_DEFAULT_FOLDER:
                raise ValueError("Le dossier par défaut peut être renommé.")
            removed = folder["items"]
            del folders[folder_id]
            if folder_id == active:
                active, enabled = MEDIA_DEFAULT_FOLDER, False
        elif action == "timing":
            timing = media_timing(dict(image_seconds=data["image_seconds"], video_seconds=data["video_seconds"]))
            folders[folder_id] = {**folder, "timing": timing}
        elif action == "play":
            playing = data["enabled"]
            if type(playing) is not bool:
                raise ValueError("Mode de lecture locale invalide.")
            targets = data.get("outputs", ["0"])
            if (not isinstance(targets, list) or not targets or len(targets) > 8
                    or any(index not in tuple(map(str, range(8))) for index in targets)):
                raise ValueError("Sélection de sorties vidéo invalide.")
            targets = set(targets)
            if playing and not folder["items"]:
                raise ValueError("Ajoute d’abord une image ou une vidéo à ce dossier.")
            secondary = targets - {"0"}
            if secondary:
                report = row.get("report", {})
                available = {o["id"] for o in report.get("outputs", [])}
                if playing and (not secondary <= available or release_number(report.get("client_release")) < (1, 4, 118)):
                    raise ValueError("Mets à jour le lecteur et vérifie que les écrans sélectionnés sont connectés.")
                extra_outputs = dict(row.get("extra_outputs") or {})
                for index in secondary:
                    settings = dict(extra_outputs.get(index) or {})
                    if playing:
                        extra_media[index] = folder_id
                        settings.setdefault("mode", "independent")
                        settings.setdefault("source", "")
                        settings["enabled"] = True
                    else:
                        extra_media.pop(index, None)
                        if settings.get("mode") != "same" and not settings.get("source"):
                            settings["enabled"] = False
                    extra_outputs[index] = settings
            if "0" in targets:
                enabled = playing
                if playing:
                    active = folder_id
                    resume_primary = True
        elif action == "remove-files":
            files = data.get("files")
            if not isinstance(files, list) or not files or any(not isinstance(value, str) for value in files):
                raise ValueError("Sélection de fichiers invalide.")
            identifiers = set(files)
            removed = [item for item in folder["items"] if item["id"] in identifiers]
            if len(removed) != len(identifiers):
                return 409, {"error": "Le contenu du dossier a changé. Réessaie avec la liste à jour."}
            folders[folder_id] = {**folder, "items": [item for item in folder["items"] if item["id"] not in identifiers]}
        elif action == "reorder":
            source, _, item = media_location(folders, data["file"])
            destination, _, anchor = media_location(folders, data["anchor"])
            after = data.get("after")
            if source != folder_id or destination != folder_id or item == anchor or type(after) is not bool:
                raise ValueError("Déplacement invalide dans ce dossier.")
            items = [current for current in folder["items"] if current["id"] != item["id"]]
            items.insert(items.index(anchor) + after, item)
            folders[folder_id] = {**folder, "items": items}
        elif action in ("remove-file", "move", "order"):
            source, index, item = media_location(folders, data["file"])
            if action == "move" and data.get("source_folder", source) != source:
                return 409, {"error": "Le fichier a changé de dossier. Réessaie avec la liste à jour."}
            original = folders[source]
            items = list(original["items"])
            if action == "order":
                delta = data["delta"]
                if type(delta) is not int or delta not in (-1, 1) or not 0 <= index + delta < len(items):
                    raise ValueError("Ce fichier est déjà au bord de la liste.")
                items[index], items[index + delta] = items[index + delta], items[index]
            elif action == "remove-file":
                removed = [items.pop(index)]
            elif source != folder_id:
                candidate = [*folder["items"], items.pop(index)]
                media_config(dict(enabled=False, items=candidate))
                folders[folder_id] = {**folder, "items": candidate}
            folders[source] = {**original, "items": items}
        else:
            raise ValueError("Action média inconnue.")
        save_media(row, folders, active, enabled, resume=resume_primary,
                   empty=action == "empty-all", extra_media=extra_media, extra_outputs=extra_outputs)
        for item in removed:
            try:
                (MEDIA_ROOT / key / item["id"]).unlink(missing_ok=True)
            except OSError as error:
                print(error, flush=True)
        return 200, {"media": screen_media(row), "folder": folder_id}

    def apply(self, path, data):
        now = time.time()
        if path == "/api/identify":
            enabled = data.get("enabled")
            if type(enabled) is not bool:
                raise ValueError("Indique si l’identification doit être affichée.")
            if "ids" in data:
                ids = data["ids"]
                if not isinstance(ids, list) or len(ids) > 500 or any(
                        not isinstance(key, str) or key not in SCREENS or
                        not SCREENS[key].get("token") for key in ids):
                    raise ValueError("La sélection doit contenir uniquement des écrans associés.")
                targets = [SCREENS[key] for key in dict.fromkeys(ids)]
            elif "id" in data:
                key = data["id"]
                row = SCREENS.get(key) if isinstance(key, str) else None
                if not row or not row.get("token"):
                    return 404, {"error": "Écran associé introuvable."}
                targets = [row]
            else:
                targets = [row for row in SCREENS.values() if row.get("token")]
            for row in targets:
                if enabled:
                    row["identify"] = True
                else:
                    row.pop("identify", None)
            if targets:
                save()
            return 200, {"count": len(targets)}
        if path == "/api/screen-order":
            return order_screens(data)
        if path == "/api/media-export":
            return self.prepare_media_export(data, now)
        if path == "/api/preview":
            return request_preview(data, now)
        if path in ("/api/group", "/api/client-updates", "/api/os-updates", "/api/screen-groups"):
            return batch(path, data, now)
        if path == "/api/pair":
            key = secrets.token_hex(8)
            code = secrets.token_urlsafe(9)
            # The installers can interpret positional values beginning with "-" as options.
            while code.startswith("-"):
                code = secrets.token_urlsafe(9)
            kind = data.get("kind")
            if kind is not None and kind not in DEVICE_KINDS:
                raise ValueError("Choisis Raspberry Pi, Ubuntu ou Windows.")
            fingerprint = data.get("installation_fingerprint")
            exchange_ssh = bool(fingerprint and kind in ("pi", "ubuntu") and data.get("exchange_ssh") is True)
            if exchange_ssh:
                code = SSH_PAIRING_PREFIX + code
            SCREENS[key] = dict(name=safe_text(data["name"]), kind=kind, pairing=code,
                                pairing_until=now + 900, exchange_ssh=exchange_ssh)
            network = data.get("installation_network")
            if isinstance(network, dict):
                fields = ("installation", "regie", "hostname", "address", "ssid", "ethernet", "wifi_interface", "gateway", "dns", "dns2", "wifi")
                settings = {field: network[field] for field in fields
                            if isinstance(network.get(field), str) and len(network[field]) <= 128
                            and not any(ord(char) < 32 for char in network[field])}
                if settings.get("wifi") == "inline":
                    settings["wifi"] = "prompt"
                if settings.get("installation") in ("static", "basic"):
                    SCREENS[key]["installation_network"] = settings
            if fingerprint:
                SCREENS[key]["installation_fingerprint"] = fingerprint
            save()
            return 200, {"code": code, "installation_fingerprint": fingerprint}
        if path == "/api/enroll":
            return enroll_reader(data, now)

        key = data.get("id")
        if not isinstance(key, str) or key not in SCREENS:
            return 404, {"error": "Écran inconnu.", "code": "screen_unknown"}
        row = SCREENS[key]
        if path in ("/api/poll", "/api/client-package", "/api/preview-result"):
            token = row.get("token")
            if not token or not token_matches(self.headers.get("Authorization"), "Bearer " + token):
                return 403, {"error": "Réassocie le récepteur depuis le tableau.", "code": "association_invalid"}
            if path == "/api/preview-result":
                return finish_preview(key, data, now)
            if path == "/api/client-package":
                job = CLIENT_UPDATES.get(key)
                nonce = data.get("nonce")
                if not isinstance(nonce, str) or not SAFE_ID.fullmatch(nonce):
                    raise ValueError("Identifiant de vérification invalide.")
                if not job or data.get("job") != job["id"] or now >= job["until"]:
                    return 409, {"error": "Cette mise à jour n’est plus disponible."}
                return 200, dict(device=key, job=job["id"], nonce=nonce,
                                 files=job["files"], version=job["version"])
            sources = data.get("sources", [])
            if not isinstance(sources, list):
                raise ValueError("Liste de sources invalide.")
            boot = safe_text(data["boot"])
            state = data.get("state")
            if state not in ("Lecture", "En attente", "Arrêté", "Démarrage"):
                raise ValueError("État invalide.")
            if data.get("check") is True:
                return 200, dict(options=row["options"])
            diagnostics = diagnostic_report(data.get("diagnostic"))
            kind = data.get("kind")
            if kind not in DEVICE_KINDS:
                serial = row.get("serial", "")
                kind = "ubuntu" if serial.startswith("pc-") else "pi"
            row["kind"] = row.get("kind") or kind
            test = LATENCY_TESTS.get(key)
            if test and now >= test["until"]:
                LATENCY_TESTS.pop(key)
                test = None
            answered = data.get("latency_test")
            test_result = None
            if test and isinstance(answered, dict) and answered.get("id") == test["id"]:
                test_result = dict(id=test["id"], ms=bounded_number(answered.get("ms"), 10000))
                LATENCY_TESTS.pop(key)
                test = None
            previous = row.get("report", {})
            if test_result is None and not test and previous.get("boot") == boot:
                test_result = previous.get("latency_test")
            job = CLIENT_UPDATES.get(key)
            result = data.get("update_result")
            if (isinstance(result, dict) and job and result.get("id") == job["id"]
                    and result.get("state") in ("installing", "updated", "failed")):
                result = dict(id=job["id"], state=result["state"], message=str(result.get("message", ""))[:240])
            else:
                result = None
            version = data.get("client_version")
            version = version if isinstance(version, str) and SAFE_ID.fullmatch(version) else None
            release = data.get("client_release")
            release = release if release_number(release) else None
            can_update = data.get("can_update") is True and version is not None
            update_issue = data.get("update_issue")
            update_issue = update_issue if not can_update and update_issue in CLIENT_UPDATE_ISSUES else None
            update_target = data.get("update_target")
            update_target = update_target if isinstance(update_target, str) and update_target in ENGINE_ASSETS else None
            active_source = data.get("active_source")
            active_source = active_source[:256] if state == "Lecture" and isinstance(active_source, str) else None
            active_media = active_source if data.get("local_video") is True else None
            if active_media:
                active_source = None
            orientation_status = data.get("orientation_status")
            if isinstance(orientation_status, dict) and orientation_status.get("value") in ORIENTATIONS:
                orientation_status = dict(value=orientation_status["value"],
                                          error=str(orientation_status.get("error") or "")[:240])
            else:
                orientation_status = None
            cached_media = data.get("media_status")
            if isinstance(cached_media, dict):
                revision = cached_media.get("revision")
                disk = cached_media.get("disk")
                if (not isinstance(disk, dict) or any(type(disk.get(k)) is not int or disk[k] < 0
                        for k in ("total", "used", "free")) or disk["total"] <= 0 or disk["used"] + disk["free"] > disk["total"]):
                    disk = None
                cached = cached_media.get("cached")
                cached = [i for i in cached[:MEDIA_LIBRARY_LIMIT] if isinstance(i, str)
                          and re.fullmatch(r"[a-f0-9]{32}", i)] if isinstance(cached, list) else []
                transfer = cached_media.get("transfer")
                if (not isinstance(transfer, dict) or not isinstance(transfer.get("id"), str)
                        or not re.fullmatch(r"[a-f0-9]{32}", transfer["id"])
                        or type(transfer.get("received")) is not int or not 0 <= transfer["received"] <= MEDIA_FILE_LIMIT):
                    transfer = None
                else:
                    transfer = dict(id=transfer["id"], received=transfer["received"])
                cached_media = dict(
                    revision=revision if isinstance(revision, str) and SAFE_ID.fullmatch(revision) else None,
                    error=str(cached_media.get("error") or "")[:240],
                    local=cached_media.get("local") is True,
                    disk=disk, cached=cached, transfer=transfer,
                )
            else:
                cached_media = None
            identification = data.get("identification")
            if isinstance(identification, dict):
                identification = dict(visible=identification.get("visible") is True,
                                      error=str(identification.get("error") or "")[:240])
            else:
                identification = None
            os_capability = data.get("os_capability")
            if os_capability not in ("ready", "setup_required", "key_required") or row["kind"] == "windows":
                os_capability = "unsupported"
            outputs = []
            if row.get("kind") in ("windows", "ubuntu", "pi") and isinstance(data.get("outputs"), list):
                for output in data["outputs"][:8]:
                    if (isinstance(output, dict) and output.get("id") in tuple(map(str, range(8)))
                            and all(type(output.get(k)) is int and 0 < output[k] <= 16384 for k in ("width", "height"))
                            and not any(o["id"] == output["id"] for o in outputs)):
                        outputs.append(dict(id=output["id"], width=output["width"], height=output["height"],
                                            playing=output.get("playing") is True, local=output.get("local") is True,
                                            media_error=str(output.get("media_error") or "")[:240],
                                            active_media=str(output.get("active_media") or "")[:256] if output.get("local") is True else "",
                                            model=output["model"][:160] if isinstance(output.get("model"), str) else "",
                                            connector=output["connector"][:128] if isinstance(output.get("connector"), str) else "",
                                            **{k: output[k] for k in ("resolution_width", "resolution_height")
                                               if type(output.get(k)) is int and 0 < output[k] <= 32768}))
            audio_devices = data.get("audio_devices")
            audio_devices = [dict(id=d["id"], label=str(d.get("label", d["id"]))[:200]) for d in audio_devices[:32]
                             if isinstance(d, dict) and isinstance(d.get("id"), str) and 0 < len(d["id"]) <= 256
                             and not any(ord(c) < 32 for c in d["id"])] if isinstance(audio_devices, list) else []
            row["report"] = dict(audio_devices=audio_devices, outputs=outputs, seen=now, ip=self.client_address[0], state=state, boot=boot,
                                 os_capability=os_capability,
                                 os_health=os_update_report(data.get("os_health")),
                                 identification=identification,
                                 active_source=active_source,
                                 active_media=active_media,
                                 sources=[s[:256] for s in sources[:32] if isinstance(s, str)],
                                 error=str(data.get("error", ""))[:240],
                                 can_poweroff=data.get("can_poweroff") is True,
                                 can_reboot=data.get("can_reboot") is not False,
                                 diagnostic=diagnostics, latency_test=test_result,
                                 display=display_report(data.get("display")),
                                 orientation_status=orientation_status,
                                 media_status=cached_media,
                                 display_capture=data.get("display_capture") is True
                                 if "display_capture" in data else None,
                                 capture_needs_consent=data.get("capture_needs_consent") is True,
                                 display_preview_kind="player" if data.get("display_preview_kind") == "player" else "monitor",
                                 display_preview_error=str(data.get("display_preview_error") or "")[:240],
                                 can_update=can_update, client_version=version, client_release=release,
                                 update_issue=update_issue,
                                 update_target=update_target, update_has_engine=data.get("update_has_engine") is True,
                                 update_result=result,
                                 latency_ms=bounded_number(data.get("latency_ms"), 10000))
            os_job = os_poll(row, data, boot, now) if os_capability != "unsupported" else None
            maintaining = os_busy(row, now)
            power = row.get("power", {})
            pending = not maintaining and power.get("boot") == boot and now - power.get("time", 0) < POWER_REQUEST_AGE
            operation = power.get("operation") if pending else None
            availability = update_availability(row["report"], now, UPDATE_KEY.is_file())
            recommendation = dict(version=CLIENT_VERSION, state=availability)
            preview_prune(now)
            preview = next((dict(id=item["id"], source=item["source"], screen=item["screen"],
                                 automatic=item["automatic"], output=item.get("output", "0")) for item in PREVIEWS.values()
                            if item["reader"] == key and item["state"] == "pending"), None)
            return 200, dict(options=row["options"], audio_routes=row.get("audio_routes", {}), extra_outputs=row.get("extra_outputs", {}), identify=row.get("identify") is True,
                             media=screen_media(row), extra_media=extra_media_configs(row),
                             media_catalog=catalog_items(row),
                             preview_request=preview,
                             output_groups=row.get("output_groups", {}),
                             waiting=dict(name=row["name"], group=row.get("group"), output_names=row.get("output_names", {}),
                                          qr=getattr(self.server, "waiting_qr", None)),
                             reader_update=recommendation, os_job=os_job,
                             reboot=power["id"] if operation == "reboot" else None,
                             poweroff=power["id"] if operation == "poweroff" else None,
                             latency_test=test["id"] if test else None,
                             client_update=job["id"] if can_update and not maintaining and job and result is None
                             and now < job["until"] else None)
        if path == "/api/rename":
            name = safe_text(data.get("name"))
            names = data.get("output_names", {})
            if not isinstance(names, dict) or any(index not in physical_outputs(row) for index in names):
                raise ValueError("Écran inconnu.")
            updated = dict(row.get("output_names", {}))
            for index, value in names.items():
                if not isinstance(value, str) or len(value) > 80:
                    raise ValueError("Nom d’écran invalide.")
                if value.strip():
                    updated[index] = safe_text(value)
                else:
                    updated.pop(index, None)
            previous = row["name"], row.get("output_names")
            row["name"], row["output_names"] = name, updated
            try:
                save()
            except OSError:
                row["name"] = previous[0]
                if previous[1] is None:
                    row.pop("output_names", None)
                else:
                    row["output_names"] = previous[1]
                raise
            return 200, {"name": name, "output_names": updated}
        if path == "/api/remove":
            remove_screens({key: row})
            return 200, {"ok": True}
        elif path == "/api/audio-route" and row.get("token"):
            index, target = data.get("output"), data.get("target")
            report = row.get("report", {})
            if index not in ("0", *(o["id"] for o in report.get("outputs", []))):
                raise ValueError("Sortie écran inconnue.")
            if not isinstance(target, str) or (target and not any(d["id"] == target for d in report.get("audio_devices", []))):
                raise ValueError("Sortie audio indisponible.")
            previous = row.get("audio_routes", {})
            row["audio_routes"] = {**previous, index: target}
            try:
                save()
            except OSError:
                row["audio_routes"] = previous
                raise
            return 200, {"ok": True}
        elif path == "/api/extra-output" and row.get("token"):
            report = row.get("report", {})
            outputs = report.get("outputs", [])
            index = data.get("output")
            if (now - report.get("seen", 0) >= ONLINE_AGE or len(outputs) < 2
                    or index == "0" or not any(o["id"] == index for o in outputs)):
                return 409, {"error": "Sortie secondaire indisponible."}
            enabled, mode, source = data.get("enabled"), data.get("mode"), data.get("source", "")
            if type(enabled) is not bool or mode not in ("same", "independent"):
                raise ValueError("Mode de sortie invalide.")
            if not isinstance(source, str) or len(source) > 256 or any(ord(c) < 32 for c in source):
                raise ValueError("Source NDI invalide.")
            if enabled and mode == "independent" and not source.strip() and index not in extra_media_folders(row):
                raise ValueError("Sélectionne une source NDI.")
            bandwidth = data.get("bandwidth", row["options"]["bandwidth"])
            volume, muted = data.get("volume", 0), data.get("muted", True)
            if bandwidth not in ("0", "1") or type(volume) is not int or not 0 <= volume <= 100 or type(muted) is not bool:
                raise ValueError("Réglages de qualité ou de son invalides.")
            orientation = data.get("orientation", "landscape")
            if orientation not in ORIENTATIONS:
                raise ValueError("Orientation invalide.")
            previous = row.get("extra_outputs", {})
            row["extra_outputs"] = {**previous, index: dict(enabled=enabled, mode=mode, source=source.strip(),
                                                            bandwidth=bandwidth, volume=volume, muted=muted, orientation=orientation)}
            try:
                save()
            except OSError:
                row["extra_outputs"] = previous
                raise
            return 200, {"ok": True}
        elif path == "/api/media-settings" and row.get("token"):
            return self.change_media(key, row, data)
        elif path == "/api/latency":
            report = row.get("report", {})
            if now - report.get("seen", 0) >= ONLINE_AGE:
                return 409, {"error": "Le récepteur est hors ligne."}
            if report.get("diagnostic") is None:
                return 409, {"error": "Mets à jour le récepteur pour tester sa connexion."}
            test = LATENCY_TESTS.get(key)
            if not test or now >= test["until"]:
                test = dict(id=secrets.token_hex(8), until=now + LATENCY_TEST_AGE)
                LATENCY_TESTS[key] = test
            return 200, {"id": test["id"]}
        elif path == "/api/resolution-limit" and "options" in row:
            if not RESOLUTION_CONTROLS_ENABLED:
                return 409, {"error": "Les réglages de résolution sont temporairement désactivés."}
            limit = data["limit"]
            if limit is not None and limit not in RESOLUTIONS:
                raise ValueError("Résolution maximale invalide.")
            previous = row.get("resolution_limit")
            row["resolution_limit"] = limit
            try:
                save()
            except OSError:
                row["resolution_limit"] = previous
                raise
            return 200, {"ok": True}
        elif path == "/api/update" and "options" in row:
            if data.get("version") != row["version"]:
                return 409, {"error": "Réglages modifiés entre-temps. Recharge la page."}
            incoming = data["options"]
            settings = options({"orientation": row["options"].get("orientation", "landscape"), **incoming})
            error = resolution_rejection(row, settings, now, data.get("resolution"))
            if error:
                return 409, {"error": error}
            previous = row["options"], row["version"]
            row.update(options=settings, version=row["version"] + 1)
            try:
                save()
            except OSError:
                row["options"], row["version"] = previous
                raise
            return 200, {"ok": True}
        elif path in ("/api/reboot", "/api/poweroff"):
            operation = "poweroff" if path == "/api/poweroff" else "reboot"
            state = queue_power(row, operation, now)
            if state == "offline":
                return 409, {"error": "Le récepteur est hors ligne."}
            if state == "unsupported":
                return 409, {"error": "Cette commande n’est pas prise en charge par le lecteur."}
            if state == "maintenance":
                return 409, {"error": "Une mise à jour système est en cours sur le lecteur."}
        else:
            return 400, {"error": "Action indisponible."}
        save()
        return 200, {"ok": True}

class Regie:
    def __init__(self, port=8765, publication_status=None, network_control=None):
        self.remote_url = None
        self.connections = None
        self.server = ThreadingHTTPServer(("0.0.0.0", port), Request)
        self.server.receiver_port = self.server.server_port
        self.server.accounts_path = REMOTE_ACCESS / "utilisateurs.json"
        self.server.auth_lock = threading.Lock()
        self.server.accounts = RemoteDashboard.accounts.__get__(self.server)
        self.server.public_accounts = RemoteDashboard.public_accounts.__get__(self.server)
        self.server.edit_accounts = RemoteDashboard.edit_accounts.__get__(self.server)
        self.server.terminals = Terminals()
        self.server.os_update_check = OsUpdateCheck()
        self.server.network_control = network_control
        self.server.interface_traffic = InterfaceTraffic()
        self.server.publication_status = publication_status or PublicationStatus(
            ROOT / "source-publications.json", SOFTWARE_VERSION)
        self.local_url = f"http://127.0.0.1:{self.server.server_port}"
        self.remote = None
        self.acme_renewal = None

    def start(self, remote_enabled=False):
        try:
            with LOCK:
                screens = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else {}
                for row in screens.values():
                    for field in ("report", "reboot", "power"):
                        row.pop(field, None)
                    if "options" in row:
                        row["options"].setdefault("orientation", "landscape")
                SCREENS.clear()
                SCREENS.update(screens)
                SCREEN_ORDER.update(load_screen_order())
                MEDIA_EXPORTS.clear()
                CLIENT_UPDATES.clear()
                LATENCY_TESTS.clear()
                PREVIEWS.clear()
            if remote_enabled:
                self.connections = RemoteConnections(REMOTE_ACCESS / "adresses-bloquees.json")
                self.remote = RemoteDashboard(REMOTE_ACCESS, Request, self.server.server_port)
                self.remote.interface_traffic = self.server.interface_traffic
                self.remote.terminals = self.server.terminals
                self.remote.os_update_check = self.server.os_update_check
                self.remote.network_control = self.server.network_control
                public_address = self.remote.certificate_name or self.remote.server_address[0]
                self.server.waiting_qr = waiting_access(public_address, self.remote.server_port)
                formatted = f"[{public_address}]" if ":" in public_address else public_address
                self.remote_url = f"https://{formatted}:{self.remote.server_port}"
                self.server.remote_connections = self.remote.remote_connections = self.connections
                self.server.auth_lock = self.remote.auth_lock
                self.remote.publication_status = self.server.publication_status
        except BaseException:
            self.server.terminals.close()
            if self.remote is not None:
                self.remote.server_close()
            self.server.server_close()
            raise
        if self.remote is not None:
            threading.Thread(target=self.remote.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()
            if get_config(REMOTE_ACCESS):
                self.acme_renewal = RenewalService(REMOTE_ACCESS)
                self.acme_renewal.start()
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True).start()

    def close(self):
        if self.acme_renewal is not None:
            self.acme_renewal.stop()
        self.server.terminals.close()
        for server in (self.remote, self.server):
            if server is not None:
                server.shutdown()
                server.server_close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    setup = parser.add_mutually_exclusive_group()
    setup.add_argument("--preparer-mises-a-jour", action="store_true",
                       help="créer ou réexporter la clé publique des mises à jour")
    setup.add_argument("--tester-mises-a-jour", action="store_true",
                       help="tester la signature de la régie avec sa clé privée existante")
    setup.add_argument("--preparer-installations", action="store_true",
                       help="préparer les clés publiques récupérées par les installateurs lecteurs")
    setup.add_argument("--activer-acces-distant", metavar="IP",
                       help="préparer l’accès HTTPS sur l’IPv4 du PC de régie")
    setup.add_argument("--desactiver-acces-distant", action="store_true",
                       help="désactiver l’accès HTTPS au prochain démarrage")
    setup.add_argument("--configurer-certificat-acme", nargs=3,
                       metavar=("DNS_OU_IP_PUBLIQUE", "FULLCHAIN_PEM", "CLE_PRIVEE_PEM"),
                       help="utiliser des fichiers PEM renouvelés par un client ACME externe")
    setup.add_argument("--retirer-certificat-acme", action="store_true",
                       help="revenir au certificat HTTPS local à la prochaine ouverture")
    args = parser.parse_args()
    if args.configurer_certificat_acme:
        try:
            identifier = configure_certificate(REMOTE_ACCESS, *args.configurer_certificat_acme)
            disable_automatic(REMOTE_ACCESS)
        except (OSError, ValueError) as error:
            sys.exit(f"Certificat ACME non configuré : {error}")
        print(f"Certificat externe configuré pour {identifier}. Le port HTTPS reste {REMOTE_PORT}.")
        print("Redémarre la régie une fois ; les renouvellements PEM seront ensuite chargés automatiquement.")
        return
    if args.retirer_certificat_acme:
        disable_certificate(REMOTE_ACCESS)
        disable_automatic(REMOTE_ACCESS)
        print("Retour au certificat local au prochain démarrage de la régie.")
        return
    if args.activer_acces_distant:
        try:
            password, certificate = prepare_access(REMOTE_ACCESS, args.activer_acces_distant, renew=True)
        except (OSError, ValueError) as error:
            sys.exit(str(error))
        print(f"Adresse du tableau : https://{args.activer_acces_distant}:{REMOTE_PORT}")
        print(f"Identifiant : regie\nMot de passe : {password}")
        print(f"Certificat à installer sur les appareils de commande : {certificate}")
        print("Garde le mot de passe et relance le tableau.")
        return
    if args.desactiver_acces_distant:
        (REMOTE_ACCESS / "acces.json").unlink(missing_ok=True)
        print("L’accès distant sera désactivé au prochain démarrage du tableau.")
        return
    if args.preparer_mises_a_jour:
        try:
            public = prepare_key(ROOT)
        except (OSError, RuntimeError) as error:
            sys.exit(str(error))
        print(f"Clé publique à copier sur les récepteurs : {public}")
        return
    if args.preparer_installations:
        try:
            fingerprint = prepare_installations(ROOT)
        except (OSError, RuntimeError, ValueError) as error:
            sys.exit(str(error))
        print(f"Clés publiques prêtes. Empreinte : {fingerprint}")
        return
    if args.tester_mises_a_jour:
        if not UPDATE_KEY.is_file():
            sys.exit("Clé de mise à jour absente. Prépare-la avec tableau.py --preparer-mises-a-jour.")
        try:
            package(UPDATE_KEY, {}, "a" * 16, "b" * 32, "c" * 64, "d" * 64)
        except (OSError, RuntimeError) as error:
            sys.exit(f"Signature impossible : {error}")
        print("Signature OK. La régie peut signer les mises à jour.")
        return
    regie = Regie(args.port)
    try:
        regie.start((REMOTE_ACCESS / "acces.json").exists())
    except (OSError, ValueError, KeyError, TypeError) as error:
        if not (REMOTE_ACCESS / "acces.json").exists():
            raise
        print(f"Impossible d’ouvrir l’accès HTTPS : {error}", flush=True)
        regie = Regie(args.port)
        regie.start()
    print(f"{APP_NAME} — {regie.local_url}", flush=True)
    print(f"Fichier des écrans : {DATA}", flush=True)
    if not DATA.exists():
        print("Aucun ecrans.json dans ce dossier. Pour une mise à jour, reprends le fichier du dossier précédent.",
              flush=True)
    if regie.remote_url:
        print(f"Accès distant — {regie.remote_url}", flush=True)
    try:
        threading.Event().wait()
    finally:
        regie.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
