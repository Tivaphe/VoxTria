"""Tests des briques du pipeline (sans charger le moindre modèle)."""
from __future__ import annotations

import pytest


# --- Nettoyage des balises d'expression -----------------------------------
@pytest.mark.parametrize("brut, attendu", [
    ("Salut <laugh> ça va ?", "Salut ça va ?"),
    ("<sigh>Bon...", "Bon..."),
    ("Ah <chuckle> oui <breath> bien sûr.", "Ah oui bien sûr."),
    ("Fin de phrase <laugh>.", "Fin de phrase."),
    ("<LAUGH> majuscules", "majuscules"),
    ("</laugh> fermante", "fermante"),
    ("<laugh/> auto-fermante", "auto-fermante"),
    ("Rien à retirer.", "Rien à retirer."),
    ("Maths : 3 < 5 et 7 > 2", "Maths : 3 < 5 et 7 > 2"),
    ("<inconnu> conservé", "<inconnu> conservé"),
])
def test_strip_expression_tags(tmp_home, brut, attendu):
    from pipeline import strip_expression_tags
    assert strip_expression_tags(brut) == attendu


# --- Normalisation d'URL ---------------------------------------------------
@pytest.mark.parametrize("entree, attendu", [
    ("http://localhost:8080", "http://localhost:8080/v1"),
    ("http://localhost:8080/", "http://localhost:8080/v1"),
    ("http://localhost:8080/v1", "http://localhost:8080/v1"),
    ("http://localhost:8080/v1/", "http://localhost:8080/v1"),
    ("  http://x:1/v1  ", "http://x:1/v1"),
])
def test_normalize_base_url(tmp_home, entree, attendu):
    from config_schema import normalize_base_url
    assert normalize_base_url(entree) == attendu


# --- Invalidation des moteurs au rechargement ------------------------------
def test_changer_le_modele_asr_decharge_le_modele(tmp_home):
    """Bug d'origine : le modèle restait chargé, le changement sans effet."""
    from pipeline import Assistant, load_config, save_config
    a = Assistant()
    a.asr._model = object()                       # simule un modèle chargé
    a.asr._signature = a.asr._sig(a.cfg)

    a.reload_config()
    assert a.asr._model is not None, "config inchangée : pas de rechargement"

    cfg = load_config()
    cfg["asr"]["model_size"] = "medium"
    save_config(cfg)
    a.reload_config()
    assert a.asr._model is None, "config changée : le modèle doit être déchargé"


# --- Fenêtre glissante d'historique ---------------------------------------
def test_historique_borne(tmp_home, monkeypatch):
    from pipeline import LLM, load_config
    cfg = load_config()
    cfg["llm"]["max_history_turns"] = 3
    llm = LLM(cfg)

    captured = {}

    class FakeResp:
        status_code = 200
        def json(self):
            return {"choices": [{"message": {"content": "ok"}}]}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["messages"] = json["messages"]
        return FakeResp()

    import requests
    monkeypatch.setattr(requests, "post", fake_post)

    history = [{"role": "user", "content": f"m{i}"} for i in range(50)]
    llm.chat(history)
    # 1 système + 3 tours * 2 messages
    assert len(captured["messages"]) == 1 + 6
    assert captured["messages"][0]["role"] == "system"
    assert captured["messages"][-1]["content"] == "m49"


def test_erreur_connexion_message_lisible(tmp_home, monkeypatch):
    import requests

    from pipeline import LLM, load_config

    def boom(*a, **k):
        raise requests.exceptions.ConnectionError("refused")

    monkeypatch.setattr(requests, "post", boom)
    with pytest.raises(RuntimeError, match="Impossible de joindre le serveur LLM"):
        LLM(load_config()).chat([{"role": "user", "content": "x"}])


def test_rollback_historique_si_le_llm_echoue(tmp_home, monkeypatch):
    from pipeline import Assistant
    a = Assistant()
    monkeypatch.setattr(a.llm, "chat", lambda h: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError):
        a.handle_text("bonjour", "/tmp/x.wav")
    assert a.history == [], "le tour utilisateur doit être annulé"


def test_echec_tts_conserve_la_reponse_texte(tmp_home, monkeypatch):
    """Avant : une erreur TTS renvoyait un 500 et perdait le texte du LLM."""
    from pipeline import Assistant
    a = Assistant()
    monkeypatch.setattr(a.llm, "chat", lambda h: "Voici ma réponse.")
    monkeypatch.setattr(a.tts, "synthesize",
                        lambda *args, **kw: (_ for _ in ()).throw(RuntimeError("pas de modèle")))
    res = a.handle_text("salut", "/tmp/x.wav")
    assert res["assistant"] == "Voici ma réponse."
    assert res["audio"] is None
    assert "tts_error" in res
    assert len(a.history) == 2


def test_cle_api_env_prioritaire(tmp_home, monkeypatch):
    from pipeline import LLM, load_config
    cfg = load_config()
    cfg["llm"]["api_key"] = "depuis-fichier"
    monkeypatch.setenv("VOXTRIA_API_KEY", "depuis-env")
    assert LLM(cfg).api_key() == "depuis-env"
    monkeypatch.delenv("VOXTRIA_API_KEY")
    assert LLM(cfg).api_key() == "depuis-fichier"


def test_not_needed_traite_comme_absence_de_cle(tmp_home):
    from pipeline import LLM, load_config
    cfg = load_config()
    cfg["llm"]["api_key"] = "not-needed"
    assert LLM(cfg).api_key() == ""
    assert "Authorization" not in LLM(cfg).headers()


def test_resolution_voix_clonee_refuse_la_traversee(tmp_home):
    from config_schema import safe_voice_path
    with pytest.raises(ValueError):
        safe_voice_path("../../etc/passwd")
    with pytest.raises(ValueError):
        safe_voice_path("a/b")
    assert safe_voice_path("ma_voix").name == "ma_voix.json"
