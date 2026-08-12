"""Tests des endpoints HTTP : robustesse, concurrence, cycle de vie des fichiers."""
from __future__ import annotations

import concurrent.futures


def test_racine_degrade_proprement_sans_ui(client):
    """Avant : FileNotFoundError -> 500 opaque. Maintenant : 503 explicite."""
    r = client.get("/")
    assert r.status_code == 503
    assert "interface web est absente" in r.text.lower()


def test_health_repond_toujours(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["ui"] is False


def test_models_ne_leve_jamais(client, monkeypatch, server_mod):
    """Avant : l'import et l'accès config hors du try -> 500 non intercepté."""
    import requests

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "get", boom)
    r = client.get("/api/models")
    assert r.status_code == 200
    assert r.json()["models"] == []
    assert "warning" in r.json()


def test_voices_ninstancie_pas_le_moteur_par_defaut(client, server_mod, monkeypatch):
    """Le simple affichage des réglages ne doit pas télécharger le modèle."""
    appels = []
    monkeypatch.setattr(server_mod.assistant.tts, "_lazy",
                        lambda: appels.append(1))
    r = client.get("/api/voices")
    assert r.status_code == 200
    assert len(r.json()["voices"]) == 10
    assert appels == [], "aucune initialisation du moteur TTS attendue"


def test_voices_probe_tolere_un_moteur_absent(client):
    r = client.get("/api/voices?probe=true")
    assert r.status_code == 200
    assert r.json()["voices"]          # repli sur les presets


def test_chat_text_refuse_message_vide(client):
    assert client.post("/api/chat_text", json={"text": "   "}).status_code == 400


def test_chat_text_refuse_message_trop_long(client):
    r = client.post("/api/chat_text", json={"text": "a" * 20000})
    assert r.status_code == 413


def test_chat_audio_refuse_audio_vide(client):
    r = client.post("/api/chat_audio", files={"file": ("a.wav", b"", "audio/wav")})
    assert r.status_code == 400


def test_erreur_llm_renvoie_502_sans_trace(client, server_mod, monkeypatch):
    monkeypatch.setattr(server_mod.assistant, "handle_text",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("LLM injoignable")))
    r = client.post("/api/chat_text", json={"text": "salut"})
    assert r.status_code == 502
    assert "LLM injoignable" in r.json()["error"]
    assert "trace" not in r.json(), "pas de trace hors mode debug"


def test_noms_audio_uniques(client, server_mod, monkeypatch, tmp_home):
    """Avant : reply.wav fixe -> les requêtes concurrentes s'écrasaient."""
    def fake_handle(text, out):
        from pathlib import Path
        Path(out).write_bytes(b"RIFF")
        return {"user": text, "assistant": "ok", "audio": out, "elapsed": 0.1,
                "timings": {}}

    monkeypatch.setattr(server_mod.assistant, "handle_text", fake_handle)
    urls = set()
    for i in range(5):
        r = client.post("/api/chat_text", json={"text": f"msg {i}"})
        assert r.status_code == 200
        urls.add(r.json()["audio_url"])
    assert len(urls) == 5, "chaque réponse doit avoir sa propre URL audio"
    for u in urls:
        assert client.get(u).status_code == 200


def test_fichier_temporaire_supprime_apres_chat_audio(client, server_mod, monkeypatch, tmp_home):
    """Avant : un wav d'entrée par tour de parole restait à vie dans _out/."""
    def fake_handle(wav_in, wav_out):
        from pathlib import Path
        Path(wav_out).write_bytes(b"RIFF")
        return {"user": "x", "assistant": "y", "audio": wav_out, "elapsed": 0.1,
                "timings": {}}

    monkeypatch.setattr(server_mod.assistant, "handle_audio", fake_handle)
    r = client.post("/api/chat_audio", files={"file": ("a.wav", b"RIFFdata", "audio/wav")})
    assert r.status_code == 200
    restants = list((tmp_home / "_out").glob("input_*.wav"))
    assert restants == [], f"fichiers d'entrée non nettoyés : {restants}"


def test_purge_des_audios_expires(client, server_mod, tmp_home):
    import os
    import time
    vieux = tmp_home / "_out" / "reply_vieux.wav"
    vieux.write_bytes(b"RIFF")
    ancien = time.time() - (server_mod.AUDIO_TTL_SECONDS + 60)
    os.utime(vieux, (ancien, ancien))
    server_mod.purge_old_audio()
    assert not vieux.exists()


def test_historique_partage_serialise(client, server_mod, monkeypatch):
    """Le verrou empêche l'entrelacement de deux tours simultanés."""
    import time

    def slow_chat(history):
        time.sleep(0.05)
        return "réponse"

    monkeypatch.setattr(server_mod.assistant.llm, "chat", slow_chat)
    monkeypatch.setattr(server_mod.assistant.tts, "synthesize",
                        lambda *a, **k: None)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(client.post, "/api/chat_text", json={"text": f"m{i}"})
                   for i in range(4)]
        for f in futures:
            assert f.result().status_code == 200

    h = server_mod.assistant.history
    assert len(h) == 8
    # alternance stricte user/assistant : aucun entrelacement
    assert [m["role"] for m in h] == ["user", "assistant"] * 4


def test_clear_vide_lhistorique(client, server_mod):
    server_mod.assistant.history = [{"role": "user", "content": "x"}]
    assert client.post("/api/clear").status_code == 200
    assert server_mod.assistant.history == []


def test_suppression_de_voix(client, tmp_home):
    client.post("/api/upload_voice",
                files={"file": ("temp.json", b'{"a":1}', "application/json")})
    assert (tmp_home / "voices" / "temp.json").exists()
    assert client.delete("/api/voices/temp").status_code == 200
    assert not (tmp_home / "voices" / "temp.json").exists()
    # la config revient sur une voix valide
    assert client.get("/api/config").json()["tts"]["voice"] == "M1"


def test_suppression_voix_inexistante(client):
    assert client.delete("/api/voices/fantome").status_code == 404


def test_suppression_voix_nom_invalide(client):
    assert client.delete("/api/voices/..%2F..%2Fx").status_code in (400, 404)
