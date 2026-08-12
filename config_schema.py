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


class Config(_Base):
    asr: ASRCfg = Field(default_factory=ASRCfg)
    llm: LLMCfg = Field(default_factory=LLMCfg)
    tts: TTSCfg = Field(default_factory=TTSCfg)
    vad: VADCfg = Field(default_factory=VADCfg)


def validate_config(raw: dict | None) -> dict:
    """Valide un dict brut et renvoie un dict complet (défauts appliqués)."""
    return Config.model_validate(raw or {}).model_dump()
