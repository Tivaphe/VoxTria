"""
Tests du module de traduction live (translate.py + endpoints server.py).

Stratégie : pas de réseau, pas de modèles. On :
  - mock `requests` pour le Translator (vérifier qu'il formate la requête
    correctement et parse la réponse),
  - mock `_get_video_title` et `fetch_youtube_subtitle_segment`,
  - passe par l'API FastAPI (TestClient) pour les flux complets.

But : empêcher toute régression sur la validation des langues, la
création/fermeture de session, l'isolation des sessions, le rendu JSON.
"""
from __future__ import annotations

import importlib
import json
import shutil
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# --------- fixture local : inclut translate.py dans la copie isolée --------
@pytest.fixture
def tmp_home_tr(tmp_path, monkeypatch):
    for f in ("config_schema.py", "pipeline.py", "server.py", "translate.py"):
        shutil.copy(REPO / f, tmp_path / f)
    src_cfg = REPO / "config.json"
    if not src_cfg.exists():
        src_cfg = REPO / "config.example.json"
    shutil.copy(src_cfg, tmp_path / "config.json")
    (tmp_path / "voices").mkdir()
    (tmp_path / "_out").mkdir()

    monkeypatch.syspath_prepend(str(tmp_path))
    for mod in ("config_schema", "pipeline", "server", "translate"):
        sys.modules.pop(mod, None)
    yield tmp_path
    for mod in ("config_schema", "pipeline", "server", "translate"):
        sys.modules.pop(mod, None)


@pytest.fixture
def server_mod_tr(tmp_home_tr):
    import config_schema  # noqa: F401
    return importlib.import_module("server")


@pytest.fixture
def client_tr(server_mod_tr):
    return TestClient(server_mod_tr.app, raise_server_exceptions=False)


@pytest.fixture
def translate_mod(tmp_home_tr):
    return importlib.import_module("translate")


# ============================================================ extraction ID
class TestExtractVideoId:
    @pytest.mark.parametrize("url,expected", [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abcDEF_123-", "abcDEF_123-"),
        ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/live/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s", "dQw4w9WgXcQ"),
    ])
    def test_formats_courants(self, translate_mod, url, expected):
        assert translate_mod._extract_video_id(url) == expected

    def test_url_invalide(self, translate_mod):
        assert translate_mod._extract_video_id("") is None
        assert translate_mod._extract_video_id("https://example.com") is None
        assert translate_mod._extract_video_id("pas une url") is None


# ============================================================ Translator
class TestTranslator:
    def _mock_response(self, status=200, content="Bonjour le monde"):
        m = MagicMock()
        m.status_code = status
        m.json.return_value = {
            "choices": [{"message": {"content": content}}]
        }
        if status != 200:
            m.text = "boom"
        return m

    def test_traduit_via_openai_compatible(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://localhost:8080/v1",
                     "model": "hy-mt2", "api_key": ""},
            translate_cfg={"system_prompt": "translate the {src} into {tgt}"},
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response()) as post:
            out = tr.translate("Hello world", "en", "fr")
        assert out == "Bonjour le monde"
        # Vérifie qu'on a bien envoyé un system prompt au format attendu
        # (et qu'on a PAS d'historique).
        args, kwargs = post.call_args
        body = kwargs["json"]
        # Les paramètres Hy-MT2 (recommandation Tencent) sont appliqués
        # par défaut — pas temperature=0 comme avant.
        assert body["temperature"] == 0.7
        assert body["top_p"] == 0.6
        assert body["top_k"] == 20
        assert body["repetition_penalty"] == 1.05
        assert body["messages"][0]["role"] == "system"
        assert body["messages"][0]["content"] == "translate the en into fr"
        assert body["messages"][1]["content"] == "Hello world"
        assert "history" not in body
        # Authorization absent (api_key vide)
        assert "Authorization" not in kwargs["headers"]

    def test_sampling_personnalise(self, translate_mod):
        """L'utilisateur peut override les paramètres d'inférence."""
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={
                "temperature": 0.3, "top_p": 0.9, "top_k": 50,
                "repetition_penalty": 1.1,
            },
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response()) as post:
            tr.translate("Hi", "en", "fr")
        body = post.call_args.kwargs["json"]
        assert body["temperature"] == 0.3
        assert body["top_p"] == 0.9
        assert body["top_k"] == 50
        assert body["repetition_penalty"] == 1.1

    def test_max_tokens_adapte_a_la_longueur(self, translate_mod):
        """max_tokens grossit avec l'input (sinon on tronque la sortie)."""
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={"max_tokens": 4096},
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response()) as post:
            tr.translate("A" * 1000, "en", "fr")
        body = post.call_args.kwargs["json"]
        # min(1000*4 + 32, 4096) = 4032
        assert body["max_tokens"] == 4032
        # ... et qu'on ne dépasse jamais le max configuré
        tr2 = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={"max_tokens": 100},
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response()) as post:
            tr2.translate("A" * 1000, "en", "fr")
        assert post.call_args.kwargs["json"]["max_tokens"] == 100

    def test_cle_api_envoyee(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2",
                     "api_key": "sk-test"},
            translate_cfg={},
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response()) as post:
            tr.translate("Hi", "en", "fr")
        assert post.call_args.kwargs["headers"]["Authorization"] == "Bearer sk-test"

    def test_langue_inconnue_leve(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={},
        )
        with pytest.raises(ValueError):
            tr.translate("Hi", "klingon", "fr")

    def test_texte_vide_retourne_vide(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={},
        )
        assert tr.translate("", "en", "fr") == ""
        assert tr.translate("   \n  ", "en", "fr") == ""

    def test_erreur_http_remontee(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={},
        )
        with patch("translate.requests.post",
                   return_value=self._mock_response(status=500)):
            with pytest.raises(RuntimeError, match="LLM HTTP 500"):
                tr.translate("Hi", "en", "fr")

    def test_choix_vide_remontee(self, translate_mod):
        tr = translate_mod.Translator(
            llm_cfg={"base_url": "http://x/v1", "model": "hy-mt2"},
            translate_cfg={},
        )
        m = self._mock_response()
        m.json.return_value = {"choices": []}
        with patch("translate.requests.post", return_value=m):
            with pytest.raises(RuntimeError):
                tr.translate("Hi", "en", "fr")


# ============================================================ SessionStore
class TestSessionStore:
    def test_create_et_get(self, translate_mod):
        store = translate_mod.SessionStore()
        s = store.create("youtube_sub", "en", "fr", title="Live News")
        assert s.id
        assert s.title == "Live News"
        assert s.src_lang == "en"
        assert store.get(s.id) is s

    def test_lru_evince(self, translate_mod):
        store = translate_mod.SessionStore()
        ids = [store.create("tab_audio", "en", "fr").id for _ in range(20)]
        # MAX_SESSIONS=16 → on doit avoir perdu les 4 plus anciennes.
        for old in ids[:4]:
            assert store.get(old) is None
        for kept in ids[4:]:
            assert store.get(kept) is not None

    def test_segments_isoles_par_session(self, translate_mod):
        store = translate_mod.SessionStore()
        a = store.create("tab_audio", "en", "fr")
        b = store.create("tab_audio", "es", "fr")
        from translate import Segment
        a.add_segment(Segment(idx=1, src_text="hi", tgt_text="salut",
                              src_lang="en", tgt_lang="fr",
                              t0=0, elapsed_ms=10))
        assert len(a.snapshot()) == 1
        assert len(b.snapshot()) == 0

    def test_close_marque_inactive(self, translate_mod):
        store = translate_mod.SessionStore()
        s = store.create("tab_audio", "en", "fr")
        assert s.active
        store.close(s.id)
        assert not s.active


# ============================================================ endpoints
class TestTranslateTextEndpoint:
    def test_validation_langue(self, client_tr):
        r = client_tr.post("/api/translate/text",
                           json={"text": "Hello", "src": "klingon", "tgt": "fr"})
        assert r.status_code == 400
        assert "klingon" in r.json()["error"]

    def test_texte_vide(self, client_tr):
        r = client_tr.post("/api/translate/text", json={"text": "  "})
        assert r.status_code == 400

    def test_traduit_avec_mock(self, client_tr, server_mod_tr):
        """Vérifie qu'avec tts=False on n'a pas besoin du moteur TTS."""
        with patch.object(server_mod_tr.Translator, "translate",
                          return_value="Bonjour le monde"):
            r = client_tr.post("/api/translate/text",
                               json={"text": "Hello world", "src": "en",
                                     "tgt": "fr", "tts": False})
        assert r.status_code == 200
        body = r.json()
        assert body["tgt_text"] == "Bonjour le monde"
        assert body["src_text"] == "Hello world"
        assert body["audio_url"] is None


class TestSessionEndpoints:
    def test_start_youtube_sub(self, client_tr, server_mod_tr, translate_mod):
        # server.py importe les helpers depuis translate : on patche des deux
        # côtés pour rester robuste à une ré-écriture future de l'import.
        with patch.object(server_mod_tr, "_get_video_title",
                          return_value="Test Stream"), \
             patch.object(translate_mod, "_get_video_title",
                          return_value="Test Stream"):
            r = client_tr.post("/api/translate/session/start", json={
                "source": "youtube_sub",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "src": "en", "tgt": "fr",
            })
        assert r.status_code == 200
        body = r.json()
        assert body["source"] == "youtube_sub"
        assert body["title"] == "Test Stream"
        assert body["id"]

    def test_start_url_invalide(self, client_tr):
        r = client_tr.post("/api/translate/session/start", json={
            "source": "youtube_sub",
            "url": "https://example.com", "src": "en", "tgt": "fr"})
        assert r.status_code == 400

    def test_start_source_inconnue(self, client_tr):
        r = client_tr.post("/api/translate/session/start", json={
            "source": "satellite", "src": "en", "tgt": "fr"})
        assert r.status_code == 400

    def test_start_tab_audio_sans_url(self, client_tr):
        r = client_tr.post("/api/translate/session/start", json={
            "source": "tab_audio", "src": "en", "tgt": "fr"})
        assert r.status_code == 200
        assert r.json()["title"] == "Capture audio onglet"

    def test_segment_session_introuvable(self, client_tr):
        r = client_tr.post("/api/translate/session/nope/segment",
                           json={"text": "Hello"})
        assert r.status_code == 404

    def test_stop_session(self, client_tr, server_mod_tr, translate_mod):
        with patch.object(server_mod_tr, "_get_video_title", return_value="X"), \
             patch.object(translate_mod, "_get_video_title", return_value="X"):
            r = client_tr.post("/api/translate/session/start", json={
                "source": "youtube_sub",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "src": "en", "tgt": "fr"})
        sid = r.json()["id"]
        r = client_tr.post(f"/api/translate/session/{sid}/stop")
        assert r.status_code == 200
        # Après stop : la session existe mais est marquée inactive → 410 Gone
        r = client_tr.post(f"/api/translate/session/{sid}/segment",
                           json={"text": "Hi", "src": "en", "tgt": "fr"})
        assert r.status_code == 410

    def test_list_sessions(self, client_tr, server_mod_tr, translate_mod):
        with patch.object(server_mod_tr, "_get_video_title", return_value="X"), \
             patch.object(translate_mod, "_get_video_title", return_value="X"):
            client_tr.post("/api/translate/session/start", json={
                "source": "youtube_sub",
                "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                "src": "en", "tgt": "fr"})
        r = client_tr.get("/api/translate/sessions")
        assert r.status_code == 200
        body = r.json()
        assert len(body["sessions"]) >= 1


class TestYoutubeNextEndpoint:
    def test_video_id_requis(self, client_tr):
        # FastAPI renvoie 422 (Pydantic) quand un paramètre requis manque :
        # c'est la sémantique correcte, on accepte les deux codes.
        r = client_tr.get("/api/translate/youtube/next")
        assert r.status_code in (400, 422)

    def test_poll_renvoie_text_ou_null(self, client_tr, server_mod_tr,
                                        translate_mod):
        with patch.object(server_mod_tr, "fetch_youtube_subtitle_segment",
                          return_value=(None, 12345)), \
             patch.object(translate_mod, "fetch_youtube_subtitle_segment",
                          return_value=(None, 12345)):
            r = client_tr.get("/api/translate/youtube/next",
                              params={"video_id": "dQw4w9WgXcQ",
                                      "lang": "en", "last_t_ms": 12345})
        assert r.status_code == 200
        assert r.json()["text"] is None
        assert r.json()["t_ms"] == 12345

    def test_poll_renvoie_nouveau_segment(self, client_tr, server_mod_tr,
                                           translate_mod):
        with patch.object(server_mod_tr, "fetch_youtube_subtitle_segment",
                          return_value=("Hello there", 20000)), \
             patch.object(translate_mod, "fetch_youtube_subtitle_segment",
                          return_value=("Hello there", 20000)):
            r = client_tr.get("/api/translate/youtube/next",
                              params={"video_id": "dQw4w9WgXcQ",
                                      "lang": "en", "last_t_ms": 10000})
        body = r.json()
        assert body["text"] == "Hello there"
        assert body["t_ms"] == 20000


# ============================================================ config
class TestHyMT2QuantListing:
    def test_filtre_les_gguf(self, translate_mod):
        # Mock de la réponse HF : un mélange de fichiers
        fake_siblings = [
            {"rfilename": "Hy-MT2-1.8B-Q4_K_M.gguf"},
            {"rfilename": "Hy-MT2-1.8B-Q8_0.gguf"},
            {"rfilename": "Hy-MT2-1.8B-F16.gguf"},
            {"rfilename": "README.md"},
            {"rfilename": "config.json"},
            {"rfilename": "tokenizer.model"},
        ]
        with patch.object(translate_mod, "_hf_list_files",
                          return_value=fake_siblings):
            quants = translate_mod.list_hymt2_quants("1.8B")
        assert len(quants) == 3
        assert {q["quant"] for q in quants} == {"Q4_K_M", "Q8_0", "F16"}
        # Triés par nom de quant
        assert [q["quant"] for q in quants] == ["F16", "Q4_K_M", "Q8_0"]

    def test_repo_inexistant(self, translate_mod):
        with patch.object(translate_mod, "_hf_list_files", return_value=[]):
            assert translate_mod.list_hymt2_quants("1.8B") == []

    def test_size_inconnu(self, translate_mod):
        with patch.object(translate_mod, "_hf_list_files",
                          return_value=[{"rfilename": "x.gguf"}]) as m:
            translate_mod.list_hymt2_quants("99B")
        # Avec un size inconnu on retombe sur 1.8B par défaut
        m.assert_called_once()

    def test_fichier_sans_quant_ignore(self, translate_mod):
        # Un .gguf sans suffixe de quant (rare) doit être ignoré
        with patch.object(translate_mod, "_hf_list_files",
                          return_value=[{"rfilename": "weights.gguf"}]):
            assert translate_mod.list_hymt2_quants("1.8B") == []


class TestHyMT2DownloadEndpoint:
    def test_list_endpoint(self, client_tr, server_mod_tr):
        with patch.object(server_mod_tr, "list_hymt2_quants",
                          return_value=[{"name": "x.gguf",
                                         "quant": "Q4_K_M", "size_bytes": None}]):
            r = client_tr.get("/api/translate/hymt2/list",
                              params={"size": "1.8B"})
        assert r.status_code == 200
        body = r.json()
        assert body["size"] == "1.8B"
        assert body["quants"][0]["quant"] == "Q4_K_M"

    def test_list_size_inconnu(self, client_tr):
        r = client_tr.get("/api/translate/hymt2/list", params={"size": "99B"})
        assert r.status_code == 400

    def test_list_repo_vide(self, client_tr, server_mod_tr):
        with patch.object(server_mod_tr, "list_hymt2_quants", return_value=[]):
            r = client_tr.get("/api/translate/hymt2/list")
        assert r.status_code == 200
        assert "warning" in r.json()

    def test_download_refuse_out_dir_absolu(self, client_tr):
        r = client_tr.post("/api/translate/hymt2/download",
                           json={"quant": "Q4_K_M", "out_dir": "/etc/passwd"})
        assert r.status_code == 400

    def test_download_quant_inexistante(self, client_tr, server_mod_tr, translate_mod):
        # On mocke la fonction interne `download_hymt2` pour qu'elle lève
        # ValueError comme dans le cas où la quantization demandée n'est pas
        # listée par HF. Le serveur doit renvoyer 400, pas 502.
        def boom(*a, **kw):
            raise ValueError("Quantization 'BOGUS' introuvable.")
        with patch.object(server_mod_tr, "download_hymt2", side_effect=boom), \
             patch.object(translate_mod, "download_hymt2", side_effect=boom):
            r = client_tr.post("/api/translate/hymt2/download",
                               json={"quant": "BOGUS"})
        assert r.status_code == 400
        assert "BOGUS" in r.json()["error"]


# ============================================================ config
class TestTranslateConfig:
    def test_defaults(self, tmp_home_tr):
        from config_schema import validate_config
        cfg = validate_config({})
        assert "translate" in cfg
        t = cfg["translate"]
        assert t["source"] == "youtube_sub"
        assert t["source_lang"] == "en"
        assert t["target_lang"] == "fr"
        assert t["system_prompt"] == "translate the {src} into {tgt}"
        # Paramètres Hy-MT2 par défaut (recommandation Tencent)
        assert t["temperature"] == 0.7
        assert t["top_p"] == 0.6
        assert t["top_k"] == 20
        assert t["repetition_penalty"] == 1.05
        assert t["max_tokens"] == 4096

    def test_bornes_sampling(self, tmp_home_tr):
        from config_schema import validate_config
        from pydantic import ValidationError
        # temperature hors borne
        with pytest.raises(ValidationError):
            validate_config({"translate": {"temperature": 3.0}})
        # top_k hors borne
        with pytest.raises(ValidationError):
            validate_config({"translate": {"top_k": 1000}})
        # repetition_penalty hors borne
        with pytest.raises(ValidationError):
            validate_config({"translate": {"repetition_penalty": 0.1}})

    def test_validation_langue(self, tmp_home_tr):
        from config_schema import validate_config
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            validate_config({"translate": {"source_lang": "francais"}})

    def test_validation_url(self, tmp_home_tr):
        from config_schema import validate_config
        from pydantic import ValidationError
        # 'auto' est accepté
        cfg = validate_config({"translate": {"base_url": "auto"}})
        assert cfg["translate"]["base_url"] == "auto"
        # URL http(s) valide est acceptée
        cfg = validate_config({"translate": {"base_url": "http://h:8080/v1"}})
        assert cfg["translate"]["base_url"].endswith("/v1")
        # Pas une URL : rejeté
        with pytest.raises(ValidationError):
            validate_config({"translate": {"base_url": "pas une url"}})
