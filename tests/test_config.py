"""Validation de la configuration et robustesse de sa persistance."""
from __future__ import annotations

import json

import pytest


def test_config_vide_ne_casse_pas_lapplication(client, tmp_home):
    """Avant : POST {} écrivait {} et tous les endpoints tombaient en KeyError."""
    (tmp_home / "config.json").write_text("{}", encoding="utf-8")
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["tts"]["voice"] == "M1"
    assert client.get("/api/health").status_code == 200


def test_config_corrompue_retombe_sur_les_defauts(client, tmp_home):
    (tmp_home / "config.json").write_text("{ pas du json", encoding="utf-8")
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["asr"]["model_size"] == "small"


@pytest.mark.parametrize("patch", [
    {"tts": {"speed": 99}},
    {"tts": {"speed": -1}},
    {"tts": {"total_steps": 0}},
    {"tts": {"voice": "XX9"}},
    {"tts": {"voice": "custom:../evil"}},
    {"llm": {"temperature": 5}},
    {"llm": {"max_tokens": -3}},
    {"llm": {"base_url": "file:///etc/passwd"}},
    {"llm": {"base_url": "pas-une-url"}},
    {"asr": {"model_size": "inexistant"}},
    {"asr": {"compute_type": "nawak"}},
    {"vad": {"threshold": 42}},
])
def test_valeurs_hors_bornes_refusees(client, patch):
    r = client.post("/api/config", json=patch)
    assert r.status_code == 400, f"{patch} aurait dû être refusé"


def test_mise_a_jour_partielle_preserve_le_reste(client, cfg_file):
    avant = cfg_file()
    r = client.post("/api/config", json={"tts": {"speed": 1.5}})
    assert r.status_code == 200
    apres = cfg_file()
    assert apres["tts"]["speed"] == 1.5
    assert apres["tts"]["voice"] == avant["tts"]["voice"]
    assert apres["llm"]["system_prompt"] == avant["llm"]["system_prompt"]


def test_base_url_normalisee_vers_v1(client, cfg_file):
    client.post("/api/config", json={"llm": {"base_url": "http://localhost:1234"}})
    assert cfg_file()["llm"]["base_url"] == "http://localhost:1234/v1"
    client.post("/api/config", json={"llm": {"base_url": "http://localhost:1234/v1/"}})
    assert cfg_file()["llm"]["base_url"] == "http://localhost:1234/v1"


def test_cle_api_vide_neffacce_pas_la_cle_existante(client, cfg_file):
    client.post("/api/config", json={"llm": {"api_key": "sk-abc"}})
    client.post("/api/config", json={"llm": {"temperature": 0.5}})
    assert cfg_file()["llm"]["api_key"] == "sk-abc"


def test_ecriture_atomique_pas_de_fichier_temporaire_residuel(client, tmp_home):
    client.post("/api/config", json={"tts": {"speed": 1.2}})
    assert not list(tmp_home.glob("*.tmp"))
    json.loads((tmp_home / "config.json").read_text(encoding="utf-8"))


def test_section_inconnue_ignoree_sans_erreur(client):
    """La section 'agent' n'a jamais été implémentée : ne doit pas bloquer."""
    r = client.post("/api/config", json={"agent": {"enabled": True}})
    assert r.status_code == 200


def test_schema_json_expose(client):
    r = client.get("/api/config/schema")
    assert r.status_code == 200
    assert "properties" in r.json()
