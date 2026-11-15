#!/usr/bin/env python3
"""Publie sur GitHub la version inscrite dans tableau.py."""

import argparse
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
from tempfile import TemporaryDirectory
from urllib.parse import urlencode


def run(*args, capture=False, cwd=None):
    process = subprocess.run(args, cwd=cwd, check=True, stdout=subprocess.PIPE if capture else None)
    return process.stdout.decode("utf-8").strip() if capture else None


def git(*args):
    return run("git", *args, capture=True)


def github_repository(url):
    match = re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)"
        r"([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+?)(?:\.git)?/?", url
    )
    if not match:
        raise ValueError("Le remote origin doit pointer vers un dépôt sur github.com.")
    return match[1]


def git_paths(*args):
    return set(filter(None, git(*args).split("\0")))


def github_api(path, *args):
    return json.loads(run("gh", "api", "--hostname", "github.com", path, *args, capture=True))


def find_release(repo, tag, title, draft_id):
    if draft_id:
        release = github_api(f"repos/{repo}/releases/{draft_id}")
        tag_name = release.get("tag_name") or ""
        if tag_name != tag and not (release["draft"] and release["name"] == title
                                    and (not tag_name or tag_name.startswith("untagged-"))):
            raise ValueError(f"La Release {draft_id} ne correspond pas à {tag}.")
        return release
    expression = (
        f".[] | select(.tag_name == {json.dumps(tag)} or "
        f"(.draft and .name == {json.dumps(title)} and "
        '((.tag_name // "") | . == "" or startswith("untagged-")))) | @json'
    )
    output = run("gh", "api", "--hostname", "github.com", "--paginate",
                 f"repos/{repo}/releases?per_page=100", "--jq", expression, capture=True)
    matches = [json.loads(line) for line in output.splitlines()]
    exact = [release for release in matches if release.get("tag_name") == tag]
    matches = exact or matches
    if len(matches) > 1:
        ids = ", ".join(str(release["id"]) for release in matches)
        raise ValueError(f"Plusieurs Releases correspondent à {tag} ({ids}). Utilise --brouillon avec l’ID voulu.")
    return matches[0] if matches else None


def generate_assets(commit, key, folder):
    source = folder / "source"
    source.mkdir()
    tracked = git_paths("ls-tree", "-r", "--name-only", "-z", commit)
    archive = subprocess.check_output(["git", "archive", "--format=tar", commit,
                                       "--", *sorted(tracked & PUBLICATION_FILES)])
    with tarfile.open(fileobj=io.BytesIO(archive)) as snapshot:
        for member in snapshot.getmembers():
            if member.name not in PUBLICATION_FILES or not member.isfile():
                continue
            target = source / member.name
            with snapshot.extractfile(member) as data, target.open("wb") as output:
                shutil.copyfileobj(data, output)
            target.chmod(member.mode & 0o777)
            os.utime(target, (member.mtime, member.mtime))
    output_dir = folder / "dist"
    run(sys.executable, str(source / "publier-version.py"), "--cle", str(key), "--sortie",
        str(output_dir), "--compatibilite", cwd=source)
    assets = [output_dir / name for name in ASSET_NAMES]
    if any(not asset.is_file() or asset.stat().st_size == 0 for asset in assets):
        raise ValueError("La génération des archives ou de leurs signatures est incomplète.")
    return assets


def publish_release(repo, tag, title, notes, release, assets, temporary):
    endpoint = f"repos/{repo}/releases"
    if release is None:
        release = github_api(endpoint, "--method", "POST",
                             "-f", f"tag_name={tag}", "-f", f"name={title}",
                             "-f", f"body={notes}", "-F", "draft=true")
    if not release["draft"]:
        raise ValueError(f"Impossible de reprendre le brouillon {tag}.")
    release_id = int(release["id"])
    endpoint = f"{endpoint}/{release_id}"
    upload_url = f"https://uploads.github.com/{endpoint}/assets"
    existing = {asset["name"]: asset for asset in release["assets"]}
    for asset in assets:
        remote_asset = existing.get(asset.name)
        if remote_asset is None:
            github_api(f"{upload_url}?{urlencode({'name': asset.name})}", "--method", "POST",
                       "-H", "Content-Type: application/octet-stream", "--input", str(asset))
            continue
        expected = "sha256:" + sha256(asset.read_bytes()).hexdigest()
        if remote_asset["state"] != "uploaded":
            raise ValueError(f"{asset.name} est incomplet dans le brouillon {release_id}. Retire le fichier incomplet sur GitHub, puis relance.")
        if remote_asset.get("digest"):
            actual = remote_asset["digest"]
        else:
            downloaded = temporary / asset.name
            with downloaded.open("wb") as file:
                subprocess.run(["gh", "api", "--hostname", "github.com", "-H", "Accept: application/octet-stream",
                                f"repos/{repo}/releases/assets/{int(remote_asset['id'])}"], stdout=file, check=True)
            actual = "sha256:" + sha256(downloaded.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"{asset.name} diffère dans le brouillon {tag}. Vérifie ce fichier sur GitHub.")
    release = github_api(endpoint)
    if not release["draft"] or not set(ASSET_NAMES) <= {
            asset["name"] for asset in release["assets"] if asset["state"] == "uploaded"}:
        raise ValueError("Les quatre fichiers doivent être présents avant de publier la Release.")
    release = github_api(endpoint, "--method", "PATCH", "-f", f"tag_name={tag}", "-F", "draft=false",
                         "-F", "prerelease=false")
    if release["draft"] or release["tag_name"] != tag:
        raise ValueError(f"GitHub n’a pas confirmé la publication de {tag}.")
    print(release["html_url"])


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--cle", type=Path, default=Path.home() / ".ssh" / "soundlive-publication",
                     help="clé privée de signature déjà utilisée pour les Releases")
    cli.add_argument("--notes", help="texte de la Release et du commit")
    cli.add_argument("--brouillon", type=int, help="ID GitHub d’un brouillon à reprendre en cas d’ambiguïté")
    args = cli.parse_args()
    key = args.cle.expanduser().resolve()

    for command in ("git", "gh", "ssh-keygen"):
        if shutil.which(command) is None:
            raise ValueError(f"Commande introuvable : {command}.")
    root = Path(git("rev-parse", "--show-toplevel")).resolve()
    os.chdir(root)
    if not (root / "tableau.py").is_file() or not (root / "publier-version.py").is_file():
        raise ValueError("Lance le script dans ton dépôt SoundLive-NDI.")
    if git("branch", "--show-current") != "main":
        raise ValueError("Place-toi sur la branche main pour publier.")
    version_match = re.search(r'^SOFTWARE_VERSION = "([0-9]+\.[0-9]+\.[0-9]+)"$',
                              (root / "tableau.py").read_text(encoding="utf-8"), re.MULTILINE)
    if version_match is None:
        raise ValueError("Version introuvable dans tableau.py.")
    version = version_match[1]
    tag = f"v{version}"
    ref = f"refs/tags/{tag}"
    title = f"SoundLive-NDI {version}"
    notes = args.notes or title
    repo = github_repository(git("remote", "get-url", "origin"))
    push_url = git("remote", "get-url", "--push", "--all", "origin")
    if github_repository(push_url).lower() != repo.lower():
        raise ValueError("Les adresses de lecture et d’envoi de origin visent des dépôts différents.")
    run("gh", "auth", "status", "--hostname", "github.com")
    release = find_release(repo, tag, title, args.brouillon)
    if release and not release["draft"]:
        print(f"{tag} est déjà publié. {release['html_url']}")
        return
    if not key.is_file():
        raise ValueError(f"Clé privée de publication introuvable : {key}")
    if key.is_relative_to(root):
        raise ValueError("Place la clé privée de publication en dehors du dépôt Git.")

    staged = git_paths("diff", "--cached", "--name-only", "--no-renames", "-z")
    outside = staged - SOURCE_FILES
    if outside:
        raise ValueError("Des fichiers hors du projet sont déjà préparés : " + ", ".join(sorted(outside)))
    if git("diff", "--name-only", "--diff-filter=U"):
        raise ValueError("Résous les conflits Git avant de publier.")
    tracked = git_paths("ls-files", "-z")
    local_tag = git("tag", "--list", tag)
    remote_tags = dict(line.split("\t")[::-1] for line in git("ls-remote", "origin", ref, ref + "^{}").splitlines())
    remote_commit = remote_tags.get(ref + "^{}", remote_tags.get(ref))
    head = git("rev-parse", "HEAD")
    if local_tag and git("rev-parse", ref + "^{commit}") != head:
        raise ValueError(f"Le tag local {tag} pointe vers un autre commit.")
    if remote_commit and remote_commit != head:
        raise ValueError(f"Le tag distant {tag} pointe vers un autre commit.")
    resuming = bool(local_tag or remote_commit or release)
    scope = PUBLICATION_FILES if resuming else SOURCE_FILES
    eligible = sorted(name for name in scope if name in tracked or (root / name).is_file())
    if any((root / name).is_symlink() or (root / name).resolve() == key for name in eligible):
        raise ValueError("Une clé de publication ou un lien symbolique figure parmi les fichiers à publier.")
    run("git", "add", "-A", "--", *eligible)
    changes = git_paths("diff", "--cached", "--name-only", "-z")
    if resuming and changes - {"publier-github.py"}:
        raise ValueError(f"Le tag ou le brouillon {tag} existe déjà pour un autre état des sources.")
    if changes and not resuming:
        run("git", "commit", "-m", notes)
        head = git("rev-parse", "HEAD")

    with TemporaryDirectory(prefix="soundlive-publication-") as directory:
        temporary = Path(directory)
        assets = generate_assets(head, key, temporary)
        if not local_tag:
            if remote_commit:
                run("git", "fetch", "origin", f"{ref}:{ref}")
            else:
                run("git", "tag", "-a", tag, head, "-m", title)
        run("git", "push", "--atomic", "origin", f"{head}:refs/heads/main", f"{ref}:{ref}")
        publish_release(repo, tag, title, notes, release, assets, temporary)


# Seuls les fichiers du projet entrent dans le commit et le ZIP de publication.
PUBLICATION_FILES = frozenset("""
.gitignore LICENSE LICENSE.txt COPYING README.md LISEZ-MOI.txt
SoundLive-NDI.c SoundLive-NDI.exe acces-refuse.html acces_regie.py commun.py
compiler-lecteur-windows.ps1 demarrer-regie-ubuntu.sh demarrer-windows.bat
diagnostic.py ecran.py installer-client-windows.py installer-client.cmd
installer-client.sh installer-pi.sh installer-ubuntu.sh lancer-client.py
lecteur-ndi-ubuntu.c lecteur-ndi-windows.exe mise_a_jour.py publier-version.py
qrencoder.py regie-windows.py tableau.html tableau.py versions_regie.py
""".split())
SOURCE_FILES = PUBLICATION_FILES | {"publier-github.py"}
ASSET_NAMES = ("SoundLive-NDI.zip", "SoundLive-NDI.zip.sig",
               "SoundLive-Ecrans.zip", "SoundLive-Ecrans.zip.sig")

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(f"Publication interrompue : {error}")
