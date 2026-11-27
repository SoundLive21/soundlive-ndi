#!/usr/bin/env python3
"""Tests des destinations physiques, sans réseau ni matériel vidéo."""
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tableau as t

def item(number, name):
    return dict(id=f"{number:032x}", sha256="a"*64, size=10, name=name)

first, second = item(1, "premier.png"), item(2, "second.mp4")
row = dict(
    options=dict(source="OBS (Programme)", bandwidth="0", volume=50,
                 muted=False, orientation="landscape", enabled=True),
    version=0, kind="ubuntu", token="fictif",
    media=dict(enabled=False, items=[]), media_folder="general",
    media_folders={"general": {"name":"Général", "items":[first]},
                   "other": {"name":"Autre", "items":[second]}},
    report=dict(seen=time.time(), client_release="1.4.118", display_capture=True,
                outputs=[{"id":"0","width":1920,"height":1080},
                         {"id":"1","width":1280,"height":720}]))
def play(folder, outputs, enabled=True):
    status, response = t.Request.change_media(None, "test", row,
        dict(action="play", folder=folder, outputs=outputs, enabled=enabled))
    assert status == 200, response

with patch.object(t, "save"):
    play("other", ["1"])
    assert not t.screen_media(row)["enabled"]
    assert row["extra_media"] == {"1":"other"}
    assert t.extra_media_configs(row)["1"]["items"] == [second]
    assert row["extra_outputs"]["1"]["enabled"]
    play("general", ["0"])
    assert t.screen_media(row)["enabled"]
    assert t.screen_media(row)["items"] == [first]
    assert t.extra_media_configs(row)["1"]["items"] == [second]
    play("other", ["1"], False)
    assert t.screen_media(row)["enabled"]
    assert not t.extra_media_configs(row)
    play("other", ["0","1"])
    assert t.screen_media(row)["items"] == [second]
    assert t.extra_media_configs(row)["1"]["items"] == [second]
    play("general", ["0"], False)
    assert not t.screen_media(row)["enabled"]
    assert t.extra_media_configs(row)["1"]["items"] == [second]

with patch.dict(t.SCREENS, {"test":row}, clear=True), patch.dict(t.PREVIEWS, {}, clear=True):
    now = time.time()
    status, primary = t.request_preview({"screen":"test"}, now)
    assert status == 200, primary
    assert ("screen","test") in t.PREVIEWS
    # The same host serializes captures; completed requests use distinct keys.
    t.PREVIEWS[("screen","test")]["state"] = "ready"
    t.PREVIEWS[("screen","test")]["captured"] = now
    status, secondary = t.request_preview({"screen":"test","output":"1"}, now)
    assert status == 200, secondary
    assert ("screen","test","1") in t.PREVIEWS
    assert primary["id"] != secondary["id"]
    assert t.PREVIEWS[("screen","test","1")]["output"] == "1"

print("Tests destinations médias et aperçus : OK.")

with patch.dict(t.SCREENS, {"test":row}, clear=True), patch.object(t,"save"):
    now = time.time()
    assert t.batch("/api/screen-groups", {"ids":["test:0"],"group":"Salle"},now)[0] == 200
    assert t.batch("/api/screen-groups", {"ids":["test:1"],"group":"Hall"},now)[0] == 200
    assert t.physical_group(row,"0") == "Salle"
    assert t.physical_group(row,"1") == "Hall"
    primary = dict(row["options"])
    assert t.batch("/api/group", {"ids":["test:1"],"changes":{"enabled":False,"volume":25}},now)[0] == 200
    assert row["options"] == primary
    assert row["extra_outputs"]["1"]["enabled"] is False
    assert row["extra_outputs"]["1"]["volume"] == 25
    secondary = dict(row["extra_outputs"]["1"])
    assert t.batch("/api/group", {"ids":["test:0"],"changes":{"volume":65}},now)[0] == 200
    assert row["options"]["volume"] == 65
    assert row["extra_outputs"]["1"] == secondary
print("Tests groupes physiques : OK.")
