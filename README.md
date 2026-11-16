# SoundLive-NDI

**Auteur : DUVERNOY Matthieu & ChatGPT**<br>
contact@soundlive.fr<br>
Société SoundLive

SoundLive-NDI pilote des lecteurs NDI sur un réseau local depuis une régie
Windows ou Ubuntu Desktop. Chaque écran reçoit la vidéo et le son directement
du PC qui publie la source. La régie choisit ce que chaque écran affiche.

Ce kit contient la régie **1.4.19** et le client **1.4.9**.

## Installer et lancer la régie

Décompresse tout le ZIP dans un dossier accessible en écriture sur le PC de
régie. Branche-le au réseau des écrans.

- **Windows x64** — Installe Python 3 x64 avec Tcl/Tk et ouvre
  `SoundLive-NDI.exe`. Pour l’accès distant, si `cryptography` manque,
  lance `py -3 -m pip install cryptography` dans PowerShell. Pour les mises
  à jour signées, installe le client OpenSSH si `ssh-keygen` est absent.
- **Ubuntu Desktop** — Ouvre un terminal dans le dossier extrait depuis ta
  session graphique et exécute les commandes suivantes.

```bash
sudo apt update
sudo apt install python3-tk python3-cryptography openssh-client
bash demarrer-regie-ubuntu.sh
```

Dans le lanceur, clique sur **Démarrer la régie**, puis **Ouvrir le tableau**.
Sur ce PC, le tableau est aussi disponible à `http://127.0.0.1:8765`.
Les lecteurs utilisent l’IPv4 locale de la régie sur le port TCP 8765. Si le
pare-feu filtre ce port, autorise-le sur le réseau des écrans.

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
**20 secondes par source ou par écran**.
Les captures automatiques se font l’une après l’autre. Elles se mettent en
pause quand l’onglet passe en arrière-plan. L’option **Actualiser
automatiquement les aperçus visibles** permet de désactiver ce mode.
**Capturer un aperçu** et **Actualiser l’aperçu** restent disponibles.

La galerie nécessite un lecteur **1.4.7** ou ultérieur qui détecte le flux.
La capture de l’écran et ses informations nécessitent le client **1.4.8**.
La capture reflète ce qui est visible sur la sortie, y compris les fenêtres
qui recouvrent le lecteur. Elle est disponible sous Windows et sous Linux
en session X11. Sous Wayland, elle nécessite un seul moniteur en lecture
et un compositeur compatible avec `grim`, installé avec
`sudo apt install grim`. Avec GNOME Wayland, les informations du moniteur
restent disponibles quand le système les fournit et la capture apparaît
indisponible. Une résolution précédée de **≈** est une estimation.

## Mettre à jour les lecteurs depuis la régie

Depuis le dossier de la régie, crée ou réexporte la clé avec
`py -3 tableau.py --preparer-mises-a-jour` sous Windows ou
`python3 tableau.py --preparer-mises-a-jour` sous Linux. Place uniquement
`regie-update.pub` dans le kit utilisé pour installer les lecteurs. Si un
lecteur a déjà été installé sans la clé, relance une installation locale
avec cette clé et l’option `--mettre-a-jour` avant le premier push. Le
client OpenSSH doit être présent sur la régie et les lecteurs.

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

Le push client met à jour les composants Python des récepteurs déjà installés.
Un changement de binaire NDI, de bibliothèques ou du lanceur client passe par
l’installateur local.

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
python3 publier-version.py --cle /chemin/prive/soundlive-publication
gh release create v1.4.19 dist/SoundLive-NDI.zip dist/SoundLive-NDI.zip.sig --title 'SoundLive-NDI 1.4.19' --generate-notes
```

Adapte le tag au numéro de `SOFTWARE_VERSION` dans `tableau.py`. La version
des lecteurs est définie dans `commun.py`. Une release
Windows peut inclure le lecteur si le binaire a été compilé avant la création
du ZIP. Le ZIP est importé par la régie après vérification de sa signature.
Ne publie jamais les clés privées, le certificat privé de la régie, son fichier
d’accès ou les associations des récepteurs.

Pour proposer la mise à jour automatique aux régies **1.4.7 et
antérieures**, ajoute `--compatibilite` à la commande `publier-version.py`.
Joins aussi `dist/SoundLive-Ecrans.zip` et `dist/SoundLive-Ecrans.zip.sig`
à la même publication. Ces deux fichiers permettent aux anciennes régies de
passer à la nouvelle version. Tu peux également installer le nouveau ZIP
manuellement en conservant le dossier de régie existant.

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
