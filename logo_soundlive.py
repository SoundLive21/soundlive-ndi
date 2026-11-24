"""Ressources graphiques SoundLive, à partir du logo fourni."""
from base64 import b64decode
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parent

def png_bytes(root=ROOT):
    return b64decode((Path(root) / "logo-soundlive.txt").read_text().strip(), validate=True)

def install_icons(root=ROOT):
    root = Path(root)
    png = png_bytes(root)
    (root / "soundlive.png").write_bytes(png)
    width, height = struct.unpack_from(">II", png, 16)
    ico = struct.pack("<HHHBBBBHHII", 0, 1, 1, width, height, 0, 0, 1, 32, len(png), 22) + png
    (root / "soundlive.ico").write_bytes(ico)

if __name__ == "__main__":
    install_icons()
