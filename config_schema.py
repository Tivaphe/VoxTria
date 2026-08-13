"""Schéma de configuration validé (Pydantic v2).

Centralise la validation de `config.json` pour que :
  - une config partielle ou corrompue ne fasse plus planter l'application
    (les valeurs manquantes retombent sur des défauts) ;
  - `POST /api/config` ne puisse plus écrire n'importe quoi (bornes sur les
    nombres, whitelist sur les enums, chemins confinés à ./voices) ;
  - la normalisation de l'URL LLM soit écrite à un seul endroit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator

BASE = Path(__file__).parent
VOICES_DIR = BASE / "voices"

# Noms de voix acceptés : presets M1-M5 / F1-F5, "custom", ou "custom:<nom>".
VOICE_RE = re.compile(r"^(?:[MF][1-5]|custom(?::[A-Za-z0-9_-]{1,64})?)$")

# Nom de fichier de voix clonée autorisé (pas de séparateur, pas de "..").
SAFE_VOICE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

PRESET_VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]


def normalize_base_url(url: str) -> str:
    """Garantit une base d'API OpenAI-compatible se terminant par /v1.

    Utilisé par le client LLM et par la découverte de modèles : la logique
    était dupliquée aux deux endroits.
    """
    base = (url or "").strip().rstrip("/")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


def safe_voice_path(name: str) -> Path:
    """Résout ./voices/<name>.json en refusant toute sortie du dossier."""
    if not SAFE_VOICE_NAME.match(name or ""):
        raise ValueError(f"Nom de voix invalide : {name!r}")
    dest = (VOICES_DIR / f"{name}.json").resolve()
    if dest.parent != VOICES_DIR.resolve():
        raise ValueError("Chemin de voix refusé (hors du dossier ./voices).")
    return dest


class _Base(BaseModel):
    # extra="ignore" : une clé inconnue héritée d'une ancienne version
    # (ex. la section "agent" jamais implémentée) n'invalide pas la config.
    model_config = ConfigDict(extra="ignore", protected_namespaces=())


class ASRCfg(_Base):
    model_size: str = "small"
    device: Literal["cpu", "cuda", "auto"] = "cpu"
    compute_type: Literal["int8", "int8_float16", "float16", "float32"] = "int8"
    language: str = "fr"

    @field_validator("model_size")
    @classmethod
    def _known_size(cls, v: str) -> str:
        allowed = {
            "tiny", "tiny.en", "base", "base.en", "small", "small.en",
            "medium", "medium.en", "large-v1", "large-v2", "large-v3",
            "large-v3-turbo", "turbo", "distil-small.en", "distil-large-v3",
        }
        if v not in allowed:
            raise ValueError(f"Modèle Whisper inconnu : {v!r}")
        return v

    @field_validator("language")
    @classmethod
    def _lang(cls, v: str) -> str:
        v = (v or "").strip().lower()
        # "" ou "auto" => détection automatique par Whisper
        if v in ("", "auto"):
            return ""
        if not re.match(r"^[a-z]{2}$", v):
            raise ValueError("Code langue ASR attendu sur 2 lettres (ex. 'fr').")
        return v


class LLMCfg(_Base):
    provider: str = "openai_compatible"
    base_url: str = "http://localhost:8080/v1"
    api_key: str = ""
    model: str = ""
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(512, ge=1, le=32768)
    max_history_turns: int = Field(20, ge=1, le=200)
    timeout: float = Field(120.0, gt=0, le=600)
    system_prompt: str = Field("", max_length=20000)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        parsed = urlparse((v or "").strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("base_url doit être une URL http(s) valide.")
        return normalize_base_url(v)


class TTSCfg(_Base):
    engine: Literal["supertonic"] = "supertonic"
    voice: str = "M1"
    lang: str = "fr"
    total_steps: int = Field(8, ge=1, le=64)
    speed: float = Field(1.05, gt=0.1, le=3.0)
    silence_duration: float = Field(0.3, ge=0.0, le=5.0)
    strip_expression_tags: bool = True
    custom_style_path: str = ""

    @field_validator("voice")
    @classmethod
    def _voice(cls, v: str) -> str:
        if not VOICE_RE.match(v or ""):
            raise ValueError(f"Voix invalide : {v!r} (attendu M1-M5, F1-F5 ou custom[:nom]).")
        return v

    @field_validator("custom_style_path")
    @classmethod
    def _style_path(cls, v: str) -> str:
        """Confine le style custom au dossier ./voices.

        Sans ce contrôle, `POST /api/config` permettait de faire lire un
        fichier arbitraire du disque au moteur TTS.
        """
        v = (v or "").strip()
        if not v:
            return ""
        p = Path(v).resolve()
        if p.parent != VOICES_DIR.resolve():
            raise ValueError("custom_style_path doit pointer dans ./voices.")
        return str(p)


class VADCfg(_Base):
    threshold: float = Field(0.02, ge=0.0, le=1.0)
    silence_ms: int = Field(1000, ge=100, le=10000)
    max_utterance_ms: int = Field(30000, ge=1000, le=300000)


class TranslateCfg(_Base):
    """Configuration du mode traduction live.

    Ces réglages sont indépendants du chat vocal (qui continue d'utiliser
    `llm` et `tts`). Le LLM ciblé peut être différent (ex. Hy-MT2 servi
    par llama.cpp sur un autre port) — il suffit de renseigner `base_url`
    et `model` ; sinon on hérite de la section `llm` du chat.

    Les paramètres d'inférence (temperature/top_p/top_k/repetition_penalty)
    sont **spécifiques au modèle de traduction** et ne sont pas partagés
    avec le LLM du chat : un LLM généraliste veut temperature ~0.7,
    Hy-MT2 veut la même chose, mais d'autres traducteurs (NLLB, MADLAD)
    peuvent exiger d'autres valeurs. Tout est modifiable depuis l'UI.
    """
    # 'auto' = réutilise llm.base_url / llm.model ; sinon URL dédiée.
    base_url: str = "auto"
    model: str = ""                           # vide = réutilise llm.model
    source: Literal["youtube_sub", "tab_audio", "url_stream"] = "youtube_sub"
    source_lang: str = "en"
    target_lang: str = "fr"
    # Prompt système injecté au LLM de traduction. Hy-MT2 attend
    # "translate the {src} into {tgt}" — on garde ce défaut.
    system_prompt: str = "translate the {src} into {tgt}"
    # YouTube : poll interval (secondes) — plus bas = plus réactif, plus de requêtes.
    poll_interval_s: float = Field(1.0, ge=0.2, le=10.0)
    # yt-dlp : durée d'un segment audio avant transcription.
    chunk_seconds: float = Field(4.0, ge=1.0, le=20.0)
    # Lecture auto des wav côté client (sinon juste log texte).
    auto_play: bool = True
    # On coupe l'historique de conversation du chat si une session live
    # tourne, pour éviter que les segments soient injectés au LLM principal.
    # (Le LLM de traduction n'est de toute façon pas `Assistant.llm` —
    #  ce booléen est juste un garde-fou documentaire.)
    isolate_from_chat: bool = True

    # ---------- paramètres d'inférence (recommandations Tencent pour Hy-MT2) -
    # Doc : "For 1.8B and 7B, we recommend temperature 0.7, top_p 0.6,
    # top_k 20, repetition_penalty 1.05". On les expose en defaults
    # modifiables depuis l'UI.
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    top_p: float = Field(0.6, ge=0.0, le=1.0)
    top_k: int = Field(20, ge=0, le=200)
    repetition_penalty: float = Field(1.05, ge=0.5, le=2.0)
    max_tokens: int = Field(4096, ge=1, le=32768)

    @field_validator("base_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = (v or "").strip()
        if not v or v.lower() == "auto":
            return "auto"
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("translate.base_url doit être 'auto' ou une URL http(s).")
        return normalize_base_url(v)

    @field_validator("source_lang", "target_lang")
    @classmethod
    def _lang(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if len(v) != 2 or not v.isalpha():
            raise ValueError("Code langue attendu sur 2 lettres (ex. 'fr', 'en').")
        return v


class Config(_Base):
    asr: ASRCfg = Field(default_factory=ASRCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    vad: VADCfg = Field(default_factory=VADCfg)
    translate: TranslateCfg = Field(default_factory=TranslateCfg)


def validate_config(raw: dict | None) -> dict:
    """Valide un dict brut et renvoie un dict complet (défauts appliqués)."""
    return Config.model_validate(raw or {}).model_dump()
