"""Tests de non-régression sur les failles identifiées lors de la revue."""
from __future__ import annotations

import json

import pytest


def test_upload_rejette_la_traversee_de_repertoire(client, tmp_home):
    """La faille d'origine : ../../pwned.json était écrit hors de ./voices."""
    r = client.post(
        "/api/upload_voice",
        files={"file": ("../../pwned.json", b'{"a": 1}', "application/json")},
    )
    assert r.status_code == 400
    # Rien écrit nulle part : ni hors du dossier, ni dedans sous un nom réécrit.
    assert not (tmp_home / "pwned.json").exists()
    assert not (tmp_home.parent / "pwned.json").exists()
    assert list((tmp_home / "voices").glob("*")) == []


@pytest.mark.parametrize("name", [
    "../../etc/passwd.json",
    "..%2F..%2Fx.json",
    "/absolute/path.json",
    "nom avec espaces.json",
    "trop" + "long" * 40 + ".json",
    ".json",
    "voix.txt",
])
def test_upload_noms_dangereux_refuses(client, name):
    r = client.post("/api/upload_voice", files={"file": (name, b"{}", "application/json")})
    assert r.status_code == 400, f"{name!r} aurait dû être refusé"


def test_upload_confine_toujours_dans_voices(client, tmp_home):
    """Propriété centrale : quoi qu'envoie le client, rien n'est écrit ailleurs.

    Certains noms (chemin Windows) sont normalisés en amont par le parseur
    multipart ; on vérifie donc le confinement, pas seulement le code HTTP.
    """
    before = set(p.name for p in tmp_home.parent.rglob("*") if p.is_file())
    for name in ["C:\\windows\\x.json", "....//....//y.json", "%2e%2e/z.json"]:
        client.post("/api/upload_voice", files={"file": (name, b"{}", "application/json")})
    voices = tmp_home / "voices"
    for p in voices.glob("*"):
        assert p.parent == voices
    # aucun fichier créé hors du sandbox du test
    after = set(p.name for p in tmp_home.parent.rglob("*") if p.is_file())
    assert after - before <= {"x.json", "config.json", "config.json.tmp"}


def test_upload_nom_valide_accepte(client, tmp_home):
    r = client.post(
        "/api/upload_voice",
        files={"file": ("ma_voix.json", b'{"style": [1, 2, 3]}', "application/json")},
    )
    assert r.status_code == 200
    assert r.json()["voice"] == "custom:ma_voix"
    assert (tmp_home / "voices" / "ma_voix.json").exists()


def test_json_invalide_nest_pas_ecrit(client, tmp_home):
    """Le contenu était écrit AVANT validation : un fichier invalide restait."""
    r = client.post(
        "/api/upload_voice",
        files={"file": ("pasjson.json", b"<html>nope</html>", "application/json")},
    )
    assert r.status_code == 400
    assert not (tmp_home / "voices" / "pasjson.json").exists()


def test_upload_trop_volumineux(client):
    r = client.post(
        "/api/upload_voice",
        files={"file": ("gros.json", b"x" * (6 * 1024 * 1024), "application/json")},
    )
    assert r.status_code == 413


def test_csrf_origine_externe_refusee(client):
    r = client.post(
        "/api/clear", headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403


def test_csrf_upload_depuis_site_externe_refuse(client, tmp_home):
    r = client.post(
        "/api/upload_voice",
        files={"file": ("ok.json", b"{}", "application/json")},
        headers={"Origin": "https://evil.example.com"},
    )
    assert r.status_code == 403
    assert not (tmp_home / "voices" / "ok.json").exists()


def test_csrf_origine_locale_acceptee(client):
    r = client.post("/api/clear", headers={"Origin": "http://127.0.0.1:8500"})
    assert r.status_code == 200


def test_lecture_audio_hors_dossier_refusee(client):
    for path in ["/api/audio/..%2F..%2Fconfig.json", "/api/audio/config.json",
                 "/api/audio/%2Fetc%2Fpasswd"]:
        assert client.get(path).status_code == 404


def test_config_custom_style_path_confine(client):
    """Un chemin absolu arbitraire permettait de faire lire n'importe quel fichier."""
    r = client.post("/api/config", json={"tts": {"custom_style_path": "/etc/passwd"}})
    assert r.status_code == 400


def test_cle_api_non_exposee(client, tmp_home):
    client.post("/api/config", json={"llm": {"api_key": "sk-secret-123"}})
    body = client.get("/api/config").json()
    assert body["llm"]["api_key"] == ""
    assert body["llm"]["api_key_set"] is True
    # la clé est bien persistée côté serveur
    saved = json.loads((tmp_home / "config.json").read_text())
    assert saved["llm"]["api_key"] == "sk-secret-123"
