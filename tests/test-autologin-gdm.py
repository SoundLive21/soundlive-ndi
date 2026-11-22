#!/usr/bin/env python3
"""Vérifie que l'autologin GDM modifie seulement les clés attendues."""
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory

script = Path("configurer-session-lecteur-linux.sh").read_text(encoding="utf-8")
block = re.search(
    r"sudo python3 - \"\$conf\" \"\$account\" <<'PY'\n(?P<code>.*?)\nPY",
    script, re.S,
)
assert block, "La configuration GDM doit être présente."
with TemporaryDirectory() as folder:
    file = Path(folder) / "custom.conf"
    file.write_text(
        "[daemon]\n# AutomaticLoginEnable=false\nAutomaticLogin=olduser\n"
        "WaylandEnable=false\n\n[security]\nDisallowTCP=true\n", encoding="utf-8"
    )
    saved = file.read_text(encoding="utf-8")
    for attempt in range(2):
        subprocess.run([sys.executable, "-c", block.group("code"), str(file), "soundlive"],
                       check=True, capture_output=True, text=True)
        output = file.read_text(encoding="utf-8")
        assert output.count("AutomaticLoginEnable=true") == 1
        assert output.count("AutomaticLogin=soundlive") == 1
        assert "WaylandEnable=false" in output
        assert "[security]\nDisallowTCP=true" in output
        assert file.with_suffix(".conf.soundlive-avant").read_text(encoding="utf-8") == saved
        print("OK: GDM autologin, exécution", attempt + 1, "conserve les autres sections.")
    missing = Path(folder) / "empty" / "custom.conf"
    subprocess.run([sys.executable, "-c", block.group("code"), str(missing), "soundlive"],
                   check=True, capture_output=True, text=True)
    assert "[daemon]" in missing.read_text(encoding="utf-8")
    assert "AutomaticLogin=soundlive" in missing.read_text(encoding="utf-8")
    print("OK: GDM sans fichier préexistant.")
