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


# --- Découpage en phrases (streaming) ------------------------------------
@pytest.mark.parametrize("buf, attendu_phrases", [
    # Faux amis classiques : pas de coupure
    ("Le taux est 3.5, pas 4.", []),                 # décimal
    ("M. Dupont arrive demain.", []),                # abréviation M.
    ("On a des pommes, etc. Et rien.", []),          # abréviation etc.
    ("J. Dupont est là.", []),                       # initiale isolée
    # Listes numérotées : « 1. » n'ouvre jamais une coupure à lui tout seul
    ("1. Premier point.", []),
    ("1. Premier point. 2. Deuxième point. ", ["1. Premier point.", "2. Deuxième point."]),
    ("(1. Encadré.) ", ["(1. Encadré.)"]),
    # …mais un nombre qui TERMINE une phrase reste une coupure valide
    ("Tu as 20. Bravo. ", ["Tu as 20.", "Bravo."]),
    ("Il est né en 2024. Merci. ", ["Il est né en 2024.", "Merci."]),
    # Coupure réelle : ponctuation finale + espace
    ("Bonjour. Comment vas-tu ?", ["Bonjour."]),
    ("Un. Deux. ", ["Un.", "Deux."]),
    # Fermants collés (style anglais) : absorbés avant la coupure
    ('"Bonjour." dit-il. ', ['"Bonjour."', "dit-il."]),
    ("(Rires.) Suite. ", ["(Rires.)", "Suite."]),
    # Fermants espacés (typographie française) : idem — c'était le bug
    ("« Bonjour. » dit-il. ", ["« Bonjour. »", "dit-il."]),
    ('" Bonjour. " dit-il. ', ['" Bonjour. "', "dit-il."]),
    # Citation multi-phrases : jamais coupée tant qu'elle n'est pas refermée
    ("« Un. Deux. » Puis rien. ", ["« Un. Deux. »", "Puis rien."]),
    ("Un “charmant” garçon. Bref. ", ["Un “charmant” garçon.", "Bref."]),
    # Pas de ponctuation : rien ne sort tant que le flux continue
    ("encore du texte sans fin", []),
])
def test_split_sentences(tmp_home, buf, attendu_phrases):
    from pipeline import split_sentences
    phrases, reste = split_sentences(buf)
    assert phrases == attendu_phrases


def test_split_sentences_flux_decimal_recombine(tmp_home):
    """Le point d'un décimal arrivant seul ne doit pas couper : on attend la suite."""
    from pipeline import split_sentences
    phrases, reste = split_sentences("La note est 3.")
    assert phrases == [] and reste == "La note est 3."
    phrases, reste = split_sentences(reste + "5 sur 20. ")
    assert phrases == ["La note est 3.5 sur 20."]


def test_split_sentences_final_vide_le_tampon(tmp_home):
    from pipeline import split_sentences
    phrases, reste = split_sentences("Sans ponctuation finale", final=True)
    assert reste == "Sans ponctuation finale"  # fourni en reste, tranché par l'appelant
    phrases, _ = split_sentences("Fin normale. ", final=True)
    assert phrases == ["Fin normale."]


def test_split_sentences_garde_fou_longueur(tmp_home):
    """Un flux sans ponctuation (liste) ne bloque pas la synthèse : coupe de secours."""
    from pipeline import split_sentences
    buf = ", ".join(f"élément numéro {i}" for i in range(30))
    phrases, reste = split_sentences(buf, max_len=120)
    assert phrases, "la coupe de secours doit produire des fragments"
    assert all(len(p) <= 125 for p in phrases)
    # reconstruit le texte sans perte
    assert (" ".join(phrases) + " " + reste.strip()).replace("  ", " ").startswith("élément numéro 0")


def test_split_sentences_guillemets_en_cours_de_flux(tmp_home):
    """« Bonjour. » reçu en deux morceaux : on attend le » avant de couper."""
    from pipeline import split_sentences
    phrases, reste = split_sentences("« Bonjour. ")
    assert phrases == []                      # » pas encore arrivé
    phrases, _ = split_sentences(reste + "» il répond. ")
    assert phrases == ["« Bonjour. »", "il répond."]


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
