#!/usr/bin/env python3
"""Vérifie le pilotage groupé de l'identification sans écran physique."""
from pathlib import Path
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import tableau

html = Path("tableau.html").read_text(encoding="utf-8")
for marker in ("identify-manager-open", "identify-manager-list",
               "identify-selected-show", "identify-selected-hide", "identify-hide-all"):
    assert marker in html, f"Bouton ou fenêtre absent : {marker}"

snapshot = dict(tableau.SCREENS)
try:
    tableau.SCREENS.clear()
    tableau.SCREENS.update({
        "a": {"token": "one", "name": "Écran A"},
        "b": {"token": "two", "name": "Écran B"},
        "c": {"name": "Écran en attente"},
    })
    with patch.object(tableau, "save", return_value=None):
        code, outcome = tableau.Request.apply(None, "/api/identify",
                                               {"enabled": True, "ids": ["a", "b", "a"]})
        assert code == 200 and outcome["count"] == 2
        assert tableau.SCREENS["a"]["identify"] and tableau.SCREENS["b"]["identify"]
        assert "identify" not in tableau.SCREENS["c"]
        code, outcome = tableau.Request.apply(None, "/api/identify",
                                               {"enabled": False, "ids": ["a"]})
        assert code == 200 and outcome["count"] == 1
        assert "identify" not in tableau.SCREENS["a"]
        assert tableau.SCREENS["b"]["identify"]
        try:
            tableau.Request.apply(None, "/api/identify", {"enabled": True, "ids": ["a", "c"]})
        except ValueError:
            pass
        else:
            raise AssertionError("Écran en attente inclus dans la sélection.")
        assert "identify" not in tableau.SCREENS["a"]
        code, outcome = tableau.Request.apply(None, "/api/identify",
                                               {"enabled": False, "ids": ["b"]})
        assert code == 200 and "identify" not in tableau.SCREENS["b"]
    print("OK: popup d'identification, sélection multiple, désactivation et sécurité.")
finally:
    tableau.SCREENS.clear()
    tableau.SCREENS.update(snapshot)
