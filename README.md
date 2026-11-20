# SoundLive-NDI

**Auteur : DUVERNOY Matthieu & ChatGPT**<br>
contact@soundlive.fr<br>
Société SoundLive

SoundLive-NDI pilote des lecteurs NDI sur un réseau local depuis une régie
Windows ou Ubuntu Desktop. Chaque écran reçoit la vidéo et le son directement
du PC qui publie la source. La régie choisit ce que chaque écran affiche.

Ce kit contient la régie **1.4.67** et le client **1.4.32**.

## Installer et lancer la régie

Décompresse tout le ZIP dans un dossier accessible en écriture sur le PC de
régie. Branche-le au réseau des écrans.

- **Windows x64** — Double-clique sur `installer-regie-windows.cmd`.
  Il installe la régie dans `%LOCALAPPDATA%\Programs\soundlive-ndi`, installe
  Python et Tcl/Tk si nécessaire, crée les raccourcis et prépare les accès
  distants après validation administrateur. Si TightVNC est nouveau, saisis
  et confirme le mot de passe VNC dans le terminal d’installation. Pour
  reprendre une régie portable, exécute l’installateur depuis un terminal avec
  `installer-regie-windows.cmd -Depuis "C:\chemin\ancienne-regie"`.
- **Ubuntu Desktop ou Raspberry Pi OS 64 bits** — Ouvre un terminal dans le
  dossier extrait depuis ta session graphique, puis lance :

```bash
bash installer-regie-linux.sh
```

Sous Linux, le menu **SoundLive-NDI Régie** démarre le lanceur installé dans
`~/.local/lib/soundlive-ndi`. Pour reprendre une régie portable, ajoute
`--depuis /chemin/vers/ancienne-regie` à la commande d’installation. Les
associations, réglages et clés sont copiés si leur destination est vide.
Ajoute `--cle-ssh /chemin/vers/cle.pub` pour autoriser une clé entrante
sur la régie Linux. `demarrer-regie-ubuntu.sh` et `SoundLive-NDI.exe` restent
les points d’entrée du lanceur pour un kit déjà préparé.

Dans le lanceur, clique sur **Démarrer la régie**, puis **Ouvrir la console**.
Sur ce PC, le tableau est aussi disponible à `http://127.0.0.1:8765`.
Les lecteurs utilisent l’IPv4 locale de la régie sur le port TCP 8765. Si le
pare-feu filtre ce port, autorise-le sur le réseau des écrans.

Le lecteur s’installe dans `~/.local/share/soundlive-ndi` sous Linux et
`%LOCALAPPDATA%\soundlive-ndi` sous Windows. Une mise à jour locale reprend
l’ancien dossier si nécessaire et actualise le raccourci de démarrage. Les
anciens fichiers sont conservés pour permettre un retour arrière.

Le dossier de la régie contient les associations et les clés. Garde
`ecrans.json`, `.soundlive-update` et `.soundlive-acces` dans ce dossier pour une
mise à jour ou un transfert vers un autre PC de régie.

### Piloter depuis un autre PC, une tablette ou un téléphone

Dans le lanceur, coche **Activer l’accès distant** et choisis l’interface
reliée au réseau des écrans. Tu peux saisir ton mot de passe avant de
démarrer la régie. À la première activation, si le champ est vide, le
lanceur génère un mot de passe.

Sur l’appareil de commande, installe l’autorité
`.soundlive-acces/autorite-soundlive.cer` fournie par ta régie dans les
certificats de confiance. Depuis le même réseau, ouvre l’adresse HTTPS
affichée par le lanceur ou scanne le code dans **QR code · Gestion des écrans**.
Connecte-toi avec le compte
`regie` et le mot de passe affiché ou choisi. Le QR contient uniquement
l’URL du tableau sur le port TCP 8766. Le même QR apparaît sur les écrans
d’attente après réception de la configuration.

La session distante dure **30 minutes** à partir de la connexion. Deux minutes
avant son expiration, le tableau affiche le temps restant et propose
**Prolonger la session**, ce qui redonne 30 minutes. **Me déconnecter**
ouvre une confirmation. Une fois validée, la session se ferme et le
navigateur revient à la page de connexion.

Le lanceur affiche les sessions distantes actives. Tu peux y sélectionner
une connexion, cliquer sur **Déconnecter** ou **Bloquer l’IP** et confirmer. Le bouton
**Adresses bloquées** permet de lever un blocage. Le guide fourni dans le kit
détaille l’installation du certificat sur Windows, Android et iOS.
[LISEZ-MOI.txt](LISEZ-MOI.txt)

## Installer un lecteur

Branche l’écran au récepteur en HDMI, sélectionne cette sortie pour l’image
et le son, puis relie le récepteur au réseau de la régie. Décompresse le kit
sur chaque récepteur et ouvre une session avec le compte qui affichera la
vidéo.

Dans le tableau, choisis **Ajouter un écran**, nomme-le, puis sélectionne
son type et l’adresse IPv4 de la régie sur le réseau des écrans. Lance
la commande affichée depuis le dossier extrait, pendant que la régie est
ouverte. Le code d’association est valable 15 minutes. Remplace l’adresse
et le code si tu pars des exemples ci-dessous.

```bash
bash installer-client.sh --plateforme pi http://192.168.10.20:8765 CODE
bash installer-client.sh --plateforme ubuntu http://192.168.10.20:8765 CODE
```

```powershell
.\installer-client.cmd http://192.168.10.20:8765 CODE
```

- Raspberry Pi OS 64 bits et Ubuntu Desktop 64 bits utilisent une session
  graphique. L’installateur Pi réutilise FFplay NDI s’il est déjà présent.
  Sur un Pi neuf, l’installateur compile le lecteur SDL2 avec le SDK NDI Linux
  pour ARM64. L’installateur Ubuntu et celui d’un Pi neuf téléchargent le SDK
  officiel. Si ce téléchargement échoue, place l’archive téléchargée depuis
  le site NDI dans le dossier du kit et préfixe la commande d’association avec
  `NDI_SDK_ARCHIVE=./Install_NDI_SDK_v6_Linux.tar.gz`.
  Rouvre la session graphique pour démarrer le lecteur. Pour qu’il démarre dès
  l’allumage, active l’ouverture automatique de cette session.
- Windows x64 utilise Python 3 x64 avec Tcl/Tk, le runtime NDI 6, SDL2 et le
  binaire `lecteur-ndi-windows.exe`. Place ce binaire et `SDL2.dll` x64 à côté
  du kit avant de lancer l’installateur. Le client démarre à la prochaine
  ouverture de session. La compilation et la lecture Windows restent à valider
  sur un poste Windows équipé du SDK et d’une source NDI.

Pour produire le binaire Windows, installe le SDK NDI 6 sur le poste de
compilation et décompresse le paquet de développement SDL2 pour Visual C++.
Dans **x64 Native Tools Command Prompt for Visual Studio**, depuis le dossier du kit,
exécute cette commande avec le chemin de ton SDK SDL2.

```bat
powershell -NoProfile -File .\compiler-lecteur-windows.ps1 -SdlSdk C:\SDK\SDL2
```

SDK et runtime NDI sur [NDI](https://ndi.video/for-developers/ndi-sdk/download/).
Paquets de développement [SDL2](https://www.libsdl.org/release/).

### Préparer un lecteur avec une IPv4 fixe

Dans le tableau, utilise **Ajouter un écran**, choisis Raspberry Pi ou PC
Ubuntu et remplis **Préparer une IPv4 fixe et associer**. Après confirmation,
le bouton **Copier** récupère la commande avec le code d’association.

Lance la préparation depuis la session locale du lecteur. Branche l'Ethernet
sur un réseau qui fournit un bail DHCP et donne accès à Internet. Le script
essaie d'obtenir ce bail pour télécharger les paquets, dont FFmpeg, et préparer
le moteur NDI. Les profils fixes `SOUNDLIVE-ETHERNET` et `SOUNDLIVE-WIFI` ne sont
mis en service qu'après cette phase. Si le réseau d'installation est différent
du réseau de diffusion, ajoute `--attendre-reseau-ecrans` ou
`-AttendreReseauEcrans`. Le script attend alors que tu déplaces le câble
avant d'appliquer l'IP fixe. Si les téléchargements passent déjà par une autre interface,
utilise `--sans-dhcp-install` sous Linux ou `-SansDhcpInstallation` sous Windows.

Les deux interfaces d'un lecteur partagent sa propre IP fixe sur le même
sous-réseau, hors de la plage DHCP. Ethernet est prioritaire. Dans les exemples,
`10.10.2.99` est la passerelle du réseau écrans et l'adresse de la régie. Choisis
ta passerelle et tes DNS selon ton installation. Le deuxième DNS est facultatif.

Après validation du réseau et de l’association, la préparation nettoie
les anciens profils SoundLive inutilisés. Sous Linux, cela inclut les
profils temporaires laissés par des installations précédentes. Sous
Windows, le profil Wi-Fi SoundLive est remplacé et l’ancienne configuration
SoundLive est retirée des cartes qui ne servent plus au lecteur. Les
profils personnels et les profils actifs du lecteur sont conservés.

La commande créée par **Ajouter un écran** inclut `--empreinte-regie`.
Si plusieurs interfaces sont détectées, renseigne **Interface Ethernet**
ou **Interface Wi-Fi** avec les noms affichés sur le lecteur par
`nmcli device status`. La commande ajoute ces options après `--preparer`.
Elle récupère les deux clés publiques après la bascule réseau, vérifie
leur empreinte et installe la clé SSH dans le compte du lecteur et la
clé de vérification des mises à jour dans son dossier applicatif.
Les installateurs déclenchent aussi la préparation du bureau distant.
Sous Windows, lance la commande en administrateur sous le compte du lecteur.

Pour préparer séparément une régie Windows déjà installée, lance depuis
un PowerShell administrateur sous le compte qui utilisera le lanceur :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer-bureau-distant-windows.ps1 -Regie
```

Les installateurs de régie préparent les clés sur le poste de régie. Une ancienne identité
`~/.ssh/id_soundlive` déjà configurée continue d’être utilisée. Une nouvelle
identité SSH est créée dans `.soundlive-update/ssh`.

Pour une installation manuelle avec ta propre clé, crée depuis la régie
une clé SSH dédiée si nécessaire et copie le fichier
`id_soundlive.pub` à côté du kit sur le lecteur. Sous Linux, exécute
`ssh-keygen -t ed25519 -f ~/.ssh/id_soundlive`. Sous PowerShell, exécute
`ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_soundlive"`. Garde
la clé privée dans le profil de la régie. Le script installe le serveur SSH
si tu lui fournis la clé publique. Il affiche ensuite l'empreinte SSH du
lecteur. Compare-la à celle proposée lors de ta première connexion.
Depuis une régie Linux, utilise par exemple
`ssh -i ~/.ssh/id_soundlive soundlive@10.10.2.103`. Dans PowerShell sur
la régie, utilise `ssh -i "$env:USERPROFILE\.ssh\id_soundlive" soundlive@10.10.2.103`.

```bash
bash installer-client.sh --plateforme pi --preparer \
  --hote ndi-player-03 --ip 10.10.2.103/24 --wifi "Ecrans" --saisir-cle-wifi \
  --passerelle 10.10.2.99 --dns 1.1.1.1 --dns2 9.9.9.9 \
  --cle-ssh ./id_soundlive.pub --regie http://10.10.2.99:8765 --code CODE
```

Pour Ubuntu Desktop 64 bits, remplace `--plateforme pi` par
`--plateforme ubuntu` et adapte le hostname et l'IP. NetworkManager doit
gérer les deux cartes. Le SSID désigne le réseau Wi-Fi réel. Sans
`--saisir-cle-wifi`, le script réutilise un profil enregistré du même SSID
si possible ou demande la clé WPA2. Pour la fournir depuis un fichier privé
contenant la clé sur la première ligne, remplace `--saisir-cle-wifi` par
`--cle-wifi-fichier /chemin/prive/wifi.txt`. Utilise `--wifi-ouvert` pour un
SSID sans mot de passe. `--verifier` vérifie les paramètres et la détection
des cartes. `--ethernet` et `--interface-wifi` sélectionnent les cartes si
plusieurs sont présentes. Pour diagnostiquer la bascule, utilise
`journalctl -u soundlive-ndi-network.service -b`.

```powershell
# Windows 10 ou 11 x64, PowerShell en administrateur sous le compte du lecteur
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\preparer-lecteur-windows.ps1 `
  -Hote ndi-player-05 -Ip 10.10.2.105/24 -Wifi "Ecrans" -SaisirCleWifi `
  -Passerelle 10.10.2.99 -Dns 1.1.1.1 -Dns2 9.9.9.9 `
  -CleSsh .\id_soundlive.pub -Regie http://10.10.2.99:8765 -Code CODE
```

Sous Windows, la clé SSH publique est ajoutée au fichier OpenSSH des comptes
administrateurs avec les permissions requises. Le script réutilise le profil
Wi-Fi du SSID ou demande la clé WPA2. `-SaisirCleWifi` redemande la clé.
`-CleWifiFichier` prend un fichier privé contenant la clé sur la première
ligne, `-WifiOuvert` sélectionne un réseau ouvert et `-ProfilWifiXml`
importe un profil pour le même SSID. Les deux cartes doivent être dédiées
au lecteur. Le script renomme les cartes et remplace leur configuration IPv4.
Il installe Python avec Tcl/Tk et FFmpeg avec WinGet si nécessaire. Le moteur
NDI Windows peut être déjà installé ou fourni avec `-Lecteur` et son
`SDL2.dll` x64. Pour le compiler, fournis `-SdlSdk C:\SDK\SDL2` et dispose
du SDK et du runtime NDI 6, ainsi que de Visual Studio Build Tools C++ x64.
`-NdiSdk` définit le chemin du SDK NDI. `-Ethernet` et `-InterfaceWifi`
sélectionnent les cartes. `-Ffplay` accepte un binaire autonome existant.
`-Verifier` vérifie les paramètres et la détection des cartes. Le journal
réseau se trouve dans `C:\Program Files\SoundLive-NDI\Network\reseau.log`.

Un lecteur déjà associé conserve son association avec la commande sans code.
Pour que le lecteur démarre dès l'allumage, active l'ouverture automatique
de la session graphique. Après redémarrage, débranche et rebranche le câble
pour vérifier que la régie retrouve le lecteur avec la même IPv4. Teste ensuite
un JPG et un MP4 via **Images et vidéos** en mode média local.

### Partager Internet depuis la régie

Dans le lanceur graphique, ouvre **Partage Internet**, sélectionne l'IPv4 de
la carte dédiée aux écrans et clique sur **Activer** ou **Désactiver**. La
régie prend comme sortie l'interface de sa route IPv4 vers Internet. Ses deux
interfaces doivent être sur des sous-réseaux différents. Sur Ubuntu, installe
`nftables` et assure-toi que `pkexec` est disponible pour l'élévation
graphique. Sur Windows, cette option utilise WinNAT et demande une validation
administrateur. WinNAT doit être disponible sur le poste et libre de toute
autre configuration NAT. Pour une régie Windows reliée à un réseau physique,
valide le passage Internet avec un lecteur avant de l'utiliser en prestation.
Un pare-feu existant doit autoriser le routage entre les deux interfaces.

Sous Windows, sélectionne la carte des écrans et clique sur **Diagnostic**
pour vérifier WinNAT, les NAT existants et les deux interfaces. Si
l’activation échoue, la fenêtre indique également l’étape, le message et
le code d’erreur PowerShell. **Copier le diagnostic** permet de récupérer le
rapport entier. Le même diagnostic peut être exécuté depuis PowerShell
en administrateur, dans le dossier de la régie. Cette commande affiche
directement le résultat si le lanceur ne peut pas récupérer son rapport :

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\regie-internet-windows.ps1 -Action diagnostic -Gateway 10.10.2.99
```

Renseigne l'IPv4 dédiée de la régie comme passerelle des lecteurs, et renseigne
un ou deux DNS. Le partage route l'IPv4 et le DHCP reste à la charge du réseau
d'installation. Le réglage Ubuntu est valable jusqu'au redémarrage de la régie.
Sous Windows, utilise **Désactiver** pour retirer le NAT SoundLive et restaurer
les réglages de routage des deux interfaces.

### Serveur DHCP du réseau écrans

Clique sur **Serveur DHCP** à côté de **Partage Internet** dans le lanceur
Windows ou Ubuntu. La fenêtre est accessible même quand la régie est arrêtée.
Sélectionne l’interface dédiée aux écrans, configurée avec une IPv4 fixe.
Indique une plage libre hors des adresses fixes, la durée des baux et, si le
réseau en utilise, une passerelle et jusqu’à deux DNS. Pour un réseau
`10.10.2.0/24`, tu peux par exemple utiliser `10.10.2.150` à
`10.10.2.199` si ces adresses sont libres.

Confirme que ce réseau n’a pas d’autre DHCP, puis clique sur **Démarrer
le DHCP**. Le panneau affiche l’IP, le nom éventuel, la MAC et l’expiration
des baux. Le serveur utilise UDP 67 sur l’interface sélectionnée. La
configuration et les baux sont conservés dans `.soundlive-dhcp/`. Fermer
la fenêtre DHCP ou arrêter la régie laisse le DHCP actif. Quitter le lanceur
coupe le DHCP. Il est réactivé au prochain lancement si la configuration
est toujours valide. Le bouton **Arrêter le DHCP** enregistre l’arrêt.

Sous Windows, si le pare-feu bloque les requêtes, ouvre PowerShell en
administrateur et ajoute une règle pour UDP 67 sur la seule carte des
écrans. Remplace `Ethernet` par le nom exact affiché dans le panneau :

```powershell
New-NetFirewallRule -Name 'SoundLive-NDI-DHCP' -DisplayName 'SoundLive-NDI DHCP' -Direction Inbound -Action Allow -Protocol UDP -LocalPort 67 -InterfaceAlias 'Ethernet'
```

Sous Ubuntu, si elle est nécessaire, l’autorisation d’ouvrir le port UDP 67
sur cette interface est demandée via `pkexec` (paquet `pkexec`). Le lanceur
et le serveur DHCP restent sous ton compte. Pour répartir l’accès à
Internet, configure aussi le partage Internet et renseigne dans le DHCP
l’IPv4 de la passerelle réellement joignable sur ce réseau.

### Gérer le partage Internet et le DHCP depuis la console web

Sur le PC régie, clique une fois sur **Autoriser la gestion réseau web**
dans le lanceur et accepte la demande administrateur de Windows ou Ubuntu.
Sur Ubuntu Desktop, l’autorisation graphique utilise `pkexec`. Démarre
la régie avec **Activer l’accès distant** pour utiliser la console HTTPS
depuis un autre poste. Après connexion, ouvre **Gestion réseau** dans
la section régie. Tu peux régler le partage Internet, lancer son diagnostic,
démarrer ou arrêter le serveur DHCP et consulter les baux avec **Actualiser**.

Les commandes modifient les mêmes réglages que le lanceur. Tu peux retirer
l’autorisation depuis le lanceur. Elle expire à sa fermeture. L’arrêt de la régie coupe
l’accès à la console web. Le DHCP déjà démarré continue de servir les écrans
tant que le lanceur reste ouvert.

### Bureau distant et terminal SSH

La fenêtre **Informations** propose **SSH** et **TigerVNC** pour un Pi ou un
poste Ubuntu, et **MSRDP** et **TigerVNC** pour un poste Windows.

Pour un lecteur, la console locale lance TigerVNC Viewer ou le client
Microsoft `mstsc.exe` quand l’application est disponible. Depuis l’interface
HTTPS ou la fiche de la régie, télécharge le fichier `.tigervnc` ou `.rdp`
et ouvre-le dans le client correspondant sur ton poste de commande.
Les premières installations Pi et Ubuntu mettent en place OpenSSH et
`tigervnc-viewer`. Les commandes générées par la régie ajoutent aussi sa
clé publique SSH au compte du lecteur.

- Sur une machine Linux déjà installée, exécute
  `bash installer-bureau-distant-linux.sh` depuis sa session graphique.
  Raspberry Pi OS Wayland utilise WayVNC. Sur une session X11, choisis un
  mot de passe VNC puis rouvre la session pour démarrer le serveur TigerVNC.
- Sous Windows, l’installation configure **TightVNC Server** en service et
  **MSRDP** si l’édition le permet. Le téléchargement WinGet accepte les
  conditions de la source et du paquet. Le mot de passe VNC est saisi sans
  affichage et transmis à Windows Installer en mémoire. Pour configurer
  un poste existant, exécute en administrateur
  `powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\installer-bureau-distant-windows.ps1 -ActiverRdp`.
  Pour utiliser un MSI officiel déjà téléchargé, indique `-VncMsi C:\chemin\tightvnc.msi`.
- Sous Ubuntu, le bouton TigerVNC suppose qu’un serveur VNC écoute sur le
  port 5900. Le partage intégré à GNOME Wayland utilise RDP. Pour accéder
  à la session affichée dans TigerVNC, utilise une session X11 ou un
  environnement Wayland avec un w0vncserver compatible.

Les scripts d’installation du bureau distant fonctionnent aussi sur le PC
de régie. Les postes doivent être joignables depuis le client VNC ou RDP
sur le LAN de diffusion, ou via un VPN autorisé. Dans le cas de WayVNC, le
client doit prendre en charge l’authentification configurée sur le Pi.
Utilise TigerVNC Viewer pour une connexion WayVNC.

Pour une préparation Windows avec IP fixe, ajoute
`-EmpreinteRegie EMPREINTE`, disponible dans la commande produite par
le tableau. Le script installe TigerVNC Viewer et active le serveur
TightVNC compatible après configuration de son mot de passe. Il active
aussi MSRDP sur les éditions Windows compatibles. Windows Home accepte
VNC et peut utiliser le client MSRDP vers un autre poste. Sous Linux, le
partage de l’écran physique utilise WayVNC sur Pi, TigerVNC sur X11 ou un
`w0vncserver` compatible sur Wayland lorsque disponible.

### Déplacer la régie sur un autre PC

Arrête l’ancienne régie et transfère les données suivantes par un moyen
privé avant le premier démarrage sur le nouveau PC :

- Le dossier `.soundlive-update` et le fichier `regie-update.pub` pour
  conserver la signature des mises à jour et les clés SSH portables.
- Le fichier `ecrans.json` pour conserver les associations. Copie aussi
  `disposition-ecrans.json` et `.soundlive-medias` si tu utilises ces données.
- Si SSH utilisait `~/.ssh/id_soundlive` ou `SOUNDLIVE_SSH_KEY`, transfère
  cette identité et son fichier `.pub` vers l’emplacement correspondant
  sur le nouveau PC. Garde ces clés privées hors des archives des lecteurs
  et des publications GitHub.
- `source-publications.json` et `publication.pub` si tu utilises les
  mises à jour GitHub de la régie.
- `.soundlive-dhcp/` si tu utilises le serveur DHCP. Sur le nouveau PC,
  sélectionne sa carte réseau dans **Serveur DHCP** et vérifie la plage
  avant de le redémarrer.

Réattribue au nouveau PC la même IPv4 de diffusion et le même port. Si
l’adresse change, utilise le mode `--mettre-a-jour` des lecteurs avec
la nouvelle URL pour conserver leurs associations. Sous Ubuntu, utilise
`--mettre-a-jour-client` pour mettre à jour le client et son URL en
conservant le moteur installé.

Pour le terminal SSH, installe les dépendances avec le Python du lanceur
sur le **PC de régie** :

```bash
sudo apt install python3-paramiko python3-pyte
```

```powershell
py -3 -m pip install paramiko pyte
```

Dans le terminal, sélectionne **Clé SSH** ou **Mot de passe**. Pour la
connexion par mot de passe, le serveur SSH du poste cible doit accepter
cette méthode. En mode clé, le terminal utilise `~/.ssh/id_soundlive`
ou le chemin défini dans `SOUNDLIVE_SSH_KEY` au lancement de la régie. Le lecteur doit autoriser
la clé publique correspondante. Renseigne sa phrase secrète si la clé en
utilise une. Pour préparer un lecteur Linux, utilise le serveur
`openssh-server` et autorise le compte SSH à utiliser.

Le terminal reconnaît les clés d’hôte déjà approuvées dans `known_hosts`.
S’il affiche une empreinte inconnue ou différente, vérifie depuis la console
du poste cible :

```bash
for cle in /etc/ssh/ssh_host_*.pub; do ssh-keygen -lf "$cle"; done
```

Saisis l’empreinte correspondante dans **Empreinte SSH vérifiée** puis clique
sur **Connecter**. Cette empreinte est utilisée pour la connexion en cours,
y compris quand la clé d’hôte a changé depuis son enregistrement dans
`known_hosts`.

Clique dans le terminal pour coller avec **Ctrl+V**. Le bouton **Coller des
commandes** ouvre une zone où tu peux relire le texte avant de l’envoyer.

### Durée de rotation des médias

Dans **Images et vidéos**, sélectionne un dossier, règle **Durée des images**
et **Durée max. des vidéos**, puis clique sur **Enregistrer les durées**.
Par défaut, les images tournent toutes les 10 secondes et les vidéos vont à
leur fin. Une durée maximale positive fait passer au média suivant au plus
tard à la durée indiquée. Une image seule reste affichée. Une vidéo seule
boucle. Mets à jour les lecteurs en version 1.4.27 pour appliquer ces durées.

## Publier une source NDI sur le réseau local

Le PC émetteur peut être la régie, un autre PC Windows ou un PC Ubuntu. Un
portable peut par exemple publier un diaporama pendant que la régie publie
le programme OBS. Relie les émetteurs et les récepteurs au même réseau local,
sur le même sous-réseau pour la découverte automatique. Donne un nom distinct
à chaque source.

### Publier une vidéo, une scène OBS ou un diaporama

1. Sur le PC qui doit émettre, installe OBS Studio, une version compatible
   du plugin **DistroAV** et le runtime NDI indiqué par le plugin. Suis la
   procédure d’installation adaptée à Windows ou Ubuntu.
   [DistroAV](https://github.com/DistroAV/DistroAV)
2. Dans OBS, prépare une scène. Utilise **Source média** pour une vidéo,
   **Capture de fenêtre** pour un diaporama ou **Capture d’écran** pour
   partager le bureau. Vérifie le son dans le mélangeur OBS.
3. Si tu veux reprendre le réglage de la prestation, ouvre **Paramètres →
   Vidéo**, choisis une résolution de sortie de **1280 × 720** et
   **25 images/s**.
4. Dans **Outils → DistroAV NDI Settings**, active **Main Output**, donne-lui
   un nom comme `Programme` ou `Diaporama`, puis valide. Cette sortie publie
   ce qui est affiché dans **Programme** dans OBS. En mode Studio, passe
   la scène voulue de l’aperçu au programme. La sortie NDI démarre à cette
   étape. Laisse OBS ouvert pendant la diffusion sur le réseau local.
   [Options DistroAV](https://github.com/DistroAV/DistroAV/blob/master/data/locale/en-US.ini)
5. Dans le tableau SoundLive-NDI, ouvre **Commandes et réglages** sur un écran,
   sélectionne le flux dans **Sources NDI détectées**, puis clique sur
   **Appliquer**. Les lecteurs peuvent afficher un nom complet comme
   `PC-SALLE (Diaporama)`. Après le test sur un écran, utilise **Actions de
   groupe → Changer la source NDI** pour appliquer le flux aux écrans voulus.

Un second PC peut reprendre ces mêmes étapes avec son propre OBS et son
propre nom de source. Pour publier une scène en parallèle depuis un seul OBS,
ajoute le filtre **Dedicated Output** de DistroAV à cette scène et attribue-lui
un nom NDI distinct. [DistroAV](https://github.com/DistroAV/DistroAV)

### Diffuser le même programme en 360p, 720p, 1080p et 4K

Depuis la régie 1.4.13, les commandes de résolution sont temporairement
masquées. Pour les retrouver après les essais, passe
`RESOLUTION_CONTROLS_ENABLED` à `True` dans `tableau.py`, puis relance la régie.
Le menu de source et la qualité High/Low restent disponibles.

1. Crée une scène **Programme** qui contient ton contenu. Pour obtenir un
   rendu 4K avec les détails d’origine, règle le canevas de base OBS à
   **3840 × 2160** et utilise du contenu 4K.
2. Crée quatre scènes de sortie. Dans chacune, ajoute **Programme** en tant
   que source de type **Scène**. Ajoute à chaque scène de sortie le filtre
   **Mise à l’échelle / rapport hauteur-largeur**, puis **NDI Dedicated
   Output** en dessous dans la liste des filtres d’effet. Renseigne la
   résolution dans le premier filtre et le nom NDI dans le second.

   | Résolution | Dimensions | Nom NDI |
   | --- | --- | --- |
   | 360p | 640 × 360 | `Programme [360p]` |
   | 720p | 1280 × 720 | `Programme [720p]` |
   | 1080p | 1920 × 1080 | `Programme [1080p]` |
   | 4K | 3840 × 2160 | `Programme [4K]` |

3. Avec DistroAV 6.2, garde les quatre scènes rendues simultanément. Par
   exemple, ajoute-les comme sources visibles dans une scène **Contrôle NDI**
   en mosaïque, puis ouvre un projecteur OBS de cette scène ou garde-la en
   aperçu en mode Studio. La scène **Programme** reste le contenu commun.
4. Vérifie sur le réseau de diffusion que chaque nom fournit une image et que
   les dimensions reçues correspondent au nom. Dans **Commandes et réglages**
   du tableau, renseigne **Résolution maximale validée** pour chaque lecteur
   après un essai sur son matériel et son réseau. Tant que cette limite est
   inconnue, les quatre choix restent grisés. Le suffixe du nom indique la
   résolution attendue. Vérifie les dimensions côté réception avant de valider
   la limite. Un changement de programme ou de fréquence d’image peut justifier
   un nouvel essai.
5. Choisis **Résolution du flux**, puis **Appliquer**. La sélection passe le
   lecteur en **High**. Si plusieurs sources compatibles annoncent la même
   résolution, choisis laquelle dans **Sources NDI détectées**. Pour une
   action de groupe, **Choisir une résolution NDI** propose les sources
   communes détectées et autorisées pour tous les écrans sélectionnés.

[Filtre de mise à l’échelle OBS](https://obsproject.com/kb/scaling-aspect-ratio-filter) ·
[Rendu des sorties dédiées avec DistroAV 6.2](https://github.com/DistroAV/DistroAV/discussions/1437)

### Partager directement un bureau Windows

Depuis le PC Windows à partager, ouvre **NDI Screen Capture** dans NDI Tools.
L’application se place dans la zone de notification. Configure l’écran et le
son de la capture depuis son icône, puis sélectionne la source correspondante
dans le tableau SoundLive-NDI. [NDI Screen Capture](https://docs.ndi.video/all/using-ndi/ndi-tools/ndi-tools-for-windows/screen-capture)

### Si une source est absente ou saccade

- Vérifie que la sortie NDI est active sur le PC émetteur et que le récepteur
  est **En ligne**. La liste du tableau vient des sources détectées par les
  récepteurs. Dans **Actions de groupe**, le tableau affiche combien d’écrans
  sélectionnés et en ligne voient chaque source.
- Si le lecteur voit la régie mais que la source reste absente, vérifie que
  le PC émetteur et le lecteur sont sur le même sous-réseau, que le Wi-Fi
  permet aux appareils de communiquer et que le pare-feu autorise NDI et la
  découverte mDNS sur le réseau privé. Depuis un PC Windows de ce même
  réseau, NDI Studio Monitor permet de tester la réception de la source.
  [Découverte mDNS](https://docs.ndi.video/all/getting-started/white-paper/discovery-and-registration/mdns) ·
  [NDI Studio Monitor](https://docs.ndi.video/all/using-ndi/ndi-tools/ndi-tools-for-windows/studio-monitor)
- Si l’image saccade, teste avec un seul écran, de préférence en Ethernet.
  Essaie d’abord le réglage 720p à 25 images/s sur l’émetteur puis, si
  nécessaire, **Low · Aperçu NDI** sur le lecteur. **High · Résolution de la
  source** reçoit le flux principal à la résolution émise. Pour proposer
  plusieurs résolutions précises, configure les sorties dédiées ci-dessus.

### Priorité Ethernet sur un lecteur

Configure d’abord un réseau Wi-Fi enregistré avec connexion automatique.
Installe ensuite la bascule depuis le lecteur. La fonction s’exécute au
démarrage et vérifie le lien Ethernet toutes les quelques secondes, même en
l’absence de la régie. Elle coupe le Wi-Fi quand une interface Ethernet
physique a un lien et une IPv4 utilisable. Elle réactive le Wi-Fi quand
l’Ethernet perd sa connexion. L’adresse de la régie enregistrée sur le lecteur
doit être joignable depuis les deux réseaux.

À partir du client **1.4.19**, une réponse HTTP interrompue pendant la
bascule est traitée comme une coupure. Le lecteur reprend ses échanges à
la même adresse dès qu’elle répond. Si l’adresse de la régie a changé,
relance la commande `ecran.py verifier` du client avec sa nouvelle URL
pour vérifier et enregistrer l’adresse en conservant l’association.

- **Raspberry Pi OS ou Ubuntu avec NetworkManager actif**. Depuis le dossier
  décompressé, lance `bash installer-client.sh --reseau-auto`. Pour retirer
  la bascule, lance `sudo systemctl disable --now soundlive-ndi-network.timer`
  puis `sudo nmcli radio wifi on`.
- **Windows x64**. Dans une invite de commandes ouverte en administrateur,
  lance `installer-client.cmd --reseau-auto`. Pour retirer la bascule, arrête
  puis supprime la tâche **SoundLive-NDI Network** dans le Planificateur de
  tâches et réactive l’adaptateur Wi-Fi.

La mise à jour envoyée depuis le tableau conserve les réglages du système.
Exécute cette commande une fois sur chaque lecteur où tu veux activer la
bascule.

## Utiliser le tableau de régie

### Organiser et lire des images et des vidéos

Avec la régie **1.4.36** et le client **1.4.25**, ouvre **Images et vidéos**
dans les commandes d’un écran. Pour la même opération sur plusieurs
lecteurs, sélectionne-les dans **Actions de groupe → Gérer les images et
vidéos autonomes**. La fenêtre propose les dossiers communs au groupe.
Créer un dossier depuis cette fenêtre le rend disponible sur chaque écran
sélectionné. Les médias des versions précédentes se retrouvent dans
**Général**.

Utilise la fenêtre pour créer ou renommer un dossier, envoyer, déplacer ou
supprimer des fichiers, modifier leur ordre et ouvrir l’aperçu d’une image
ou d’une vidéo. Pour une vidéo, l’aperçu dépend des formats décodés par ton
navigateur. Tu peux télécharger le fichier original depuis l’aperçu. Coche
plusieurs fichiers pour les supprimer ensemble ou les télécharger dans un
ZIP. Glisse une ligne par sa poignée ou utilise les flèches au clavier pour
modifier sa position. En groupe, un déplacement s’applique aux lecteurs
qui possèdent les deux fichiers. La progression indique l’envoi à la régie
puis la copie sur les lecteurs. La copie atteint 100 % après vérification.
**Lancer ce dossier** lance la liste choisie en boucle, y compris sur un
lecteur en ligne ou précédemment arrêté. Les lecteurs téléchargent leurs
fichiers en arrière-plan. Attends **Lecture locale en cours** avant de
couper le réseau.

Le lecteur lit le dossier dans l’ordre affiché. Avec plusieurs
médias, chaque image reste dix secondes et chaque vidéo est lue jusqu’à sa
fin. Une image seule reste affichée. Après un redémarrage, le lecteur reprend
la liste depuis le début avec les fichiers déjà présents. Le bouton
**Revenir à la diffusion NDI** rétablit la source NDI enregistrée.

**Vider tous les dossiers** efface les fichiers de tous les dossiers des
lecteurs sélectionnés, y compris les dossiers propres à un seul écran. La
confirmation liste les écrans et le nombre de fichiers. Les dossiers sont
conservés et la lecture locale s’arrête. Les copies des lecteurs hors ligne
sont supprimées à leur reconnexion.

La fenêtre indique l’espace libre sur la régie et sur chaque lecteur.
La tuile **Disque** sur chaque fiche affiche l’occupation et l’espace libre,
en orange à partir de 80 % et en rouge à partir de 90 %.
L’ajout est bloqué si les fichiers feraient dépasser **90 % d’occupation**
sur la régie ou le lecteur. Le lecteur vérifie aussi son espace réel lors
de la copie. Après un redémarrage de la régie, connecte les lecteurs pour
qu’ils lui remontent leur espace disque.

Formats acceptés : JPG, PNG, MP4, M4V, MOV, MKV et WebM. Limites : 32
dossiers et 128 fichiers par écran, 20 fichiers et 1 Gio par dossier,
256 Mio par fichier. Pour un Raspberry Pi
3 B+, un MP4 H.264 en 720p à 25 i/s permet de commencer avec un fichier
adapté à ses capacités. À partir du lecteur **1.4.21**, Raspberry Pi OS et
Ubuntu affichent les JPG et PNG avec GTK, en respectant l’orientation EXIF
du JPEG. La lecture des vidéos nécessite `ffplay` (`sudo apt install ffmpeg`).
Sur un Pi avec l’ancien client NDI, le `ffplay` installé dans
`/opt/ndi-pi/bin` peut servir à la lecture locale. Sous Windows, rends
`ffplay.exe` accessible dans le PATH ou dans le dossier du client, puis
relance le lecteur.

Pour déplacer la régie avec ses listes et fichiers, copie aussi le dossier
`.soundlive-medias` à côté de `ecrans.json`.

- **Fiche d’écran** — La vue compacte montre la latence, le CPU, la RAM,
  la version du lecteur et le flux signalé. Déplie **Commandes et réglages**
  pour choisir une source, une qualité, le volume ou l’état muet, puis
  clique sur **Appliquer**.
- **Diffusion et alimentation** — **Stop** affiche l’écran d’attente en
  laissant le récepteur allumé. **Reprendre** relance la lecture.
  **Redémarrer** et **Éteindre** agissent sur le récepteur lui-même.
  **Retirer** enlève son association au tableau. Quand le flux est absent
  ou arrêté, l’écran affiche son nom, sa version et l’état de mise à jour
  dès que cette information est connue.
- **Groupes** — **Organiser les écrans** permet de créer des groupes pour
  ranger les fiches. Chaque groupe, y compris **Sans groupe**, s’affiche
  ouvert et peut être replié en cliquant sur son titre. Le nom du groupe
  s’affiche sous le nom du lecteur sur son écran d’attente dès qu’il reçoit
  la modification.
  **Actions de groupe** permet de sélectionner des
  écrans et de leur appliquer une commande, par exemple une source, le
  volume, le mode muet, l’arrêt de la diffusion ou le redémarrage.
  Les actions d’alimentation ciblent les récepteurs en ligne.
- **Diagnostic** — Clique sur **Réseau** ou **Informations** pour voir l’OS,
  le processeur, la RAM, le disque et les interfaces réseau du récepteur.
  Depuis le client **1.4.9**, le nom du Wi-Fi connecté (SSID) figure dans
  les interfaces réseau et sous les débits du lecteur si cette interface
  est sélectionnée. Le système doit rendre le SSID accessible au lecteur.
  **Tester la latence** mesure l’aller-retour entre ce lecteur et la régie.

L’écran d’attente indique aussi l’état du rattachement. **Lecteur rattaché
à la régie** confirme une réponse de la régie. **Régie indisponible ·
association enregistrée** indique que le lecteur garde son association
locale et réessaie de joindre la régie. **Réassociation à la régie
nécessaire** apparaît quand la régie refuse l’association existante.

### Suivre les débits réseau

Dans **Débit réseau de la régie**, choisis l’interface qui transporte le
flux. Si OBS diffuse sur la régie, regarde surtout **Émission**. Si le
flux vient d’un autre PC, regarde **Réception** sur les fiches des lecteurs.

Chaque fiche associée affiche les tuiles **Réception** et **Émission** en
Mbit/s, avec le nom de l’interface. Par défaut, la fiche privilégie
l’interface utilisée pour joindre la régie. Si le flux NDI arrive par une
autre carte, clique sur l’une des tuiles et choisis cette interface dans la
fenêtre de diagnostic. Cette fenêtre affiche aussi les mesures des autres
interfaces connectées.

Les chiffres cumulent tout le trafic de l’interface. Un autre transfert
simultané s’ajoute au trafic NDI. Le lecteur **1.4.3** est nécessaire pour
remonter les mesures par écran.

### Afficher un aperçu NDI

Ouvre **Aperçu des sources NDI disponibles** pour retrouver les flux signalés par les
lecteurs en ligne. La galerie capture la source NDI à bande passante réduite.
Chaque fiche propose sa propre **Capture de l’écran**, prise sur le moniteur
du lecteur, en lecture, en attente ou à l’arrêt. Ouvre **Informations**
pour voir la résolution du moniteur, son modèle et sa sortie d’affichage
lorsqu’ils sont signalés par le système.

Le tableau rafraîchit les aperçus visibles avec un délai d’au moins
**30 secondes par source ou par écran**. Une image d’attente réserve la
même surface que la capture.
Les captures automatiques se font l’une après l’autre. Elles se mettent en
pause quand l’onglet passe en arrière-plan. L’option **Actualiser
automatiquement les aperçus visibles** permet de désactiver ce mode.
**Capturer un aperçu** et **Actualiser l’aperçu** restent disponibles.

La galerie nécessite un lecteur **1.4.7** ou ultérieur qui détecte le flux.
La capture de l’écran et ses informations nécessitent le client **1.4.8**.
La capture reflète ce qui est visible sur la sortie, y compris les fenêtres
qui recouvrent le lecteur. Elle est disponible sous Windows et sous Linux
en session X11. Pour les bureaux Wayland compatibles avec `grim`, installe
`sudo apt install grim` et utilise un seul moniteur en lecture. Sur
Raspberry Pi, la disponibilité de la capture est vérifiée indépendamment
des informations du moniteur. Le
lecteur Ubuntu SDL en session Wayland utilise le rendu décrit ci-dessous.
Une résolution précédée de **≈** est une estimation.

#### Orientation du bureau du lecteur

Dans **Gestion → Commandes et réglages**, choisis **Paysage**, **Portrait
gauche** ou **Portrait droite** sous **Orientation de l’écran**, puis clique
sur **Appliquer**. Le client **1.4.15** modifie la sortie du système
graphique. Le bureau, l’écran d’attente et le flux suivent la rotation.
Le paysage est la valeur par défaut. Le client réapplique la valeur
enregistrée à l’ouverture de sa session et reçoit les changements à la
reconnexion. **Actions de groupe** permet d’appliquer le même réglage à
plusieurs lecteurs. La fiche indique si le système a accepté la demande.
À partir du client **1.4.16**, le lecteur contacte la régie avant de lancer
la rotation et traite les opérations d’affichage en arrière-plan. Sous
Linux, le journal `~/.local/state/soundlive-ndi/lancement.log` indique si
le lanceur graphique a démarré et consigne les erreurs de démarrage.
Si un ancien lecteur Linux journalise `cannot import name 'client_lock'`
au redémarrage, copie le fichier `lancer-client.py` du kit **1.4.27** ou
ultérieur dans `~/.local/share/soundlive-ndi/` avec le compte du
lecteur, puis redémarre. Le lanceur corrigé fonctionne avec les anciens
modules présents dans ce dossier et reprend la version active du client.

Sur Raspberry Pi OS, le bureau Wayland doit prendre en charge
`wlr-randr` (`sudo apt install wlr-randr` si absent). En X11, utilise
`xrandr` (`sudo apt install x11-xserver-utils` si absent). Ubuntu GNOME
sous Wayland utilise le service d’affichage de GNOME. Sous Windows,
la rotation dépend du pilote graphique. Avec plusieurs moniteurs, le client
signale une erreur s’il ne peut pas identifier la sortie de lecture ou si
le système refuse la disposition demandée.

#### Aperçu du lecteur Ubuntu sous Wayland

Sur un lecteur Ubuntu dédié, le client **1.4.14** et son moteur SDL
mis à jour peuvent afficher dans la régie le **rendu SoundLive‑NDI**.
L’image vient de la vidéo en cours de lecture ou de l’écran d’attente
dessiné par le lecteur. Elle n’inclut que le contenu de SoundLive‑NDI.
Aucune confirmation locale n’est nécessaire, y compris après redémarrage.

Pour un lecteur déjà installé, copie le nouveau ZIP, décompresse-le
dans un nouveau dossier et exécute cette commande depuis le dossier
`SoundLive-NDI` extrait, avec le compte `soundlive`, par exemple via SSH.
Pousse ensuite le client **1.4.14** depuis la régie pour charger le moteur mis à jour. Si le
client est déjà à jour, redémarre le lecteur après la commande.

```bash
bash installer-ubuntu.sh --mettre-a-jour-rendu
```

La fiche indique le prérequis manquant si le moteur SDL est trop ancien
ou si `python3-gi-cairo` manque. Le script installe cette dépendance si
nécessaire. Les clients antérieurs peuvent encore proposer **Autoriser la
capture**, qui déclenche la demande de partage de GNOME.

## Mettre à jour les lecteurs depuis la régie

Depuis le dossier de la régie, crée ou réexporte la clé avec
`py -3 tableau.py --preparer-mises-a-jour` sous Windows ou
`python3 tableau.py --preparer-mises-a-jour` sous Linux. Place uniquement
`regie-update.pub` dans le kit utilisé pour installer les lecteurs. Si un
lecteur a déjà été installé sans la clé, relance une installation locale
avec cette clé et l’option `--mettre-a-jour` avant le premier push. Le
client OpenSSH doit être présent sur la régie et les lecteurs.

La clé publique des mises à jour est distincte de la clé utilisée pour
ouvrir une session SSH. Sur un lecteur déjà installé et associé, tu peux
copier `regie-update.pub` dans `~/.local/share/soundlive-ndi` sous Linux
ou dans `%LOCALAPPDATA%\SoundLive\client` sous Windows, puis rouvrir la
session du lecteur pour qu’il la charge.

Si la régie refuse de signer, teste la clé existante dans le dossier de
la régie avec `py -3 tableau.py --tester-mises-a-jour` sous Windows ou
`python3 tableau.py --tester-mises-a-jour` sous Linux. Sous Windows, si
OpenSSH refuse les permissions de la clé privée, la régie tente de réserver
son accès au compte qui la lance et réessaie la signature une fois.

```bash
bash installer-client.sh --plateforme pi --mettre-a-jour
bash installer-client.sh --plateforme ubuntu --mettre-a-jour-client
```

```powershell
.\installer-client.cmd --mettre-a-jour
```

Quand le tableau propose une version, clique sur **Mettre à jour le lecteur**
sur sa fiche. Vérifie qu’il revient en ligne avec sa nouvelle version et la
mention verte **Dernière version**. Pour déployer sur plusieurs écrans,
choisis **Actions de groupe → Pousser la mise à jour client**.

Le push client envoie un paquet signé contenant tous les fichiers de
l’application, dont le lanceur, ainsi que le moteur disponible pour le
lecteur. L’association, la configuration et la clé de confiance restent
sur le lecteur. Le runtime NDI, SDL et les autres dépendances système sont
fournis par l’installation locale. Sur les Pi avec `/opt/ndi-pi/bin/ffplay`,
ce moteur système reste installé à part.

1. Pour un lecteur antérieur à **1.4.14**, lance une première mise à jour
   pour installer le nouveau mécanisme. Dès qu’il est revenu en ligne,
   clique à nouveau sur **Mettre à jour le lecteur** pour envoyer le paquet
   complet.
2. Pour distribuer aussi le moteur, place dans le dossier de la régie
   le binaire compilé pour les lecteurs concernés, sous le nom approprié.
   Pour Linux, compile depuis ce kit sur un poste de l’architecture cible,
   avec `bash installer-ubuntu.sh --mettre-a-jour-rendu` sur un lecteur de
   préparation déjà associé, puis copie son fichier
   `~/.local/share/soundlive-ndi/lecteur-ndi-ubuntu` vers la régie.
   Sous Windows, utilise la procédure de compilation Windows de ce guide.

   | Lecteur | Fichier à placer dans le dossier de la régie |
   | --- | --- |
   | Linux SDL x86-64 | `lecteur-ndi-ubuntu-amd64` |
   | Linux SDL ARM64 | `lecteur-ndi-ubuntu-arm64` |
   | Windows x64 | `lecteur-ndi-windows.exe` |

3. Relance le **lanceur** de la régie après avoir ajouté ou remplacé un
   binaire. La régie sélectionne le paquet approprié à chaque lecteur.

Le ZIP de sources ne fournit pas les moteurs précompilés. Si le binaire
correspondant est absent, la régie distribue le dossier Python complet
avec le lanceur et le lecteur utilise son moteur installé localement. Si
un moteur a déjà été distribué depuis la régie, celle-ci signale l’absence
du binaire au lieu de faire revenir le lecteur à son moteur de base. Le
lecteur teste le démarrage d’un moteur livré avant son activation et
conserve sa version précédente en cas d’échec.

## Publier une version du logiciel sur GitHub

Le ZIP livré peut servir de base au premier dépôt. Prépare le dépôt depuis un
dossier extrait propre, sans les données d’une régie en production. Vérifie
la liste des fichiers à publier et choisis la licence de ton code avant de
diffuser les sources. Le ZIP exclut le SDK NDI et les bibliothèques tierces
binaires. Respecte leurs licences si tu les redistribues séparément.

```bash
gh auth login
git init -b main
git add -A
git status --short
git commit -m "Import initial de SoundLive-NDI"
gh repo create soundlive-ndi --public --source . --remote origin --push
```

Crée une clé Ed25519 dédiée aux publications, conservée hors du dépôt. La clé
privée utilisée par la régie pour signer le push des lecteurs reste distincte.

```bash
ssh-keygen -t ed25519 -f /chemin/prive/soundlive-publication
```

Depuis le dépôt GitHub déjà configuré, sur sa branche `main`, publie avec
ta clé habituelle.

```bash
python3 publier-github.py --cle /chemin/prive/soundlive-publication --notes "Mise à jour de SoundLive-NDI"
```

Le script lit le tag dans `tableau.py`, signe les archives et les ajoute à
la Release GitHub. La version des lecteurs est définie dans `commun.py`.
Une release peut inclure les moteurs compilés sous les noms indiqués
ci-dessus. La régie vérifie la signature avant d’importer le ZIP.
Ne publie jamais les clés privées, le certificat privé de la régie, son fichier
d’accès ou les associations des récepteurs.

La publication automatisée inclut les archives de compatibilité pour les
régies **1.4.7 et antérieures**. Pour générer localement les mêmes fichiers
signés, lance `publier-version.py --compatibilite` avec ta clé habituelle.
Tu peux également installer le nouveau ZIP manuellement dans le dossier
de régie existant.

## Mettre la régie à jour

Arrête la régie dans le lanceur. Dans **Versions publiées**, saisis le dépôt
sous la forme `propriétaire/dépôt` et importe la clé publique de publication
obtenue auprès de l’éditeur. Le lanceur vérifie le dépôt mémorisé à l’ouverture,
puis toutes les cinq minutes, même pendant la diffusion. Clique sur **Vérifier**, puis **Installer** si
une version est proposée. Dès la version 1.4.11, le lanceur se rouvre
automatiquement après l’installation d’une mise à jour. Si tu as lancé
la mise à jour depuis une version antérieure, rouvre le lanceur une fois.
Clique ensuite sur **Démarrer la régie**. Le tableau permet d’envoyer le
paquet client signé aux récepteurs quand une version client est proposée.

Une régie antérieure à la 1.4.50 peut refuser un nouveau fichier avec le
message « Entrée de publication inattendue ». Pour passer à la 1.4.52,
ferme le lanceur et copie les fichiers du dossier `SoundLive-NDI` du ZIP
dans le dossier existant de la régie. Conserve les fichiers de configuration
locaux, dont `ecrans.json` et `source-publications.json`, puis relance
`SoundLive-NDI.exe` sous Windows.

À partir de la 1.4.52, le lanceur vérifie la signature de la publication
avant de lire sa liste de fichiers. Il peut ainsi installer les nouveaux
fichiers du logiciel explicitement déclarés dans la publication signée.
