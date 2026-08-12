"""
Pipeline de l'assistant vocal — briques modulaires ASR / LLM / TTS.

Chaque brique est isolée : tu peux remplacer le LLM (local/cloud/API) sans
toucher au reste, changer la voix TTS, etc.

Dépendances :
    pip install faster-whisper supertonic requests
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from config_schema import (
    PRESET_VOICES,
    normalize_base_url,
    safe_voice_path,
    validate_config,
)

log = logging.getLogger("voxtria")

CONFIG_PATH = Path(__file__).parent / "config.json"
VOICES_DIR = Path(__file__).parent / "voices"

# Verrou global des écritures de config (deux POST concurrents ne doivent pas
# s'entrelacer sur le même fichier).
_CONFIG_LOCK = threading.Lock()

# Balises d'expression que le prompt système invite le LLM à produire.
# Le SDK Supertonic ne les interprète pas : sans nettoyage elles sont
# prononcées littéralement. On les retire du texte envoyé au TTS, tout en
# les conservant dans le texte affiché à l'utilisateur.
EXPRESSION_TAGS = (
    "laugh", "chuckle", "sigh", "breath", "gasp",
    "cough", "sniff", "groan", "yawn", "clear_throat",
)
_EXPR_ALT = "|".join(EXPRESSION_TAGS)
_EXPR_RE = re.compile(rf"</?(?:{_EXPR_ALT})\s*/?>", re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]{2,}")
# En typographie française l'espace avant ; : ! ? est correct : on ne
# recolle que la virgule, le point et les parenthèses fermantes.
_TIGHT_PUNCT_RE = re.compile(r"[ \t]+([,.)\]])")


def strip_expression_tags(text: str) -> str:
    """Retire les balises <laugh>, <sigh>… et normalise les espaces résiduels."""
    cleaned = _EXPR_RE.sub(" ", text or "")
    cleaned = _WS_RE.sub(" ", cleaned)
    cleaned = _TIGHT_PUNCT_RE.sub(r"\1", cleaned)
    return cleaned.strip()


_LOGGED: set[str] = set()


def _log_once(msg: str) -> None:
    """Journalise un message d'état une seule fois (load_config est appelé
    à chaque requête : sans cela le log se répète indéfiniment)."""
    if msg not in _LOGGED:
        _LOGGED.add(msg)
        log.info(msg)


def load_config() -> dict:
    """Lit config.json et le valide (défauts appliqués si champs manquants).

    Ne lève jamais sur un fichier absent ou corrompu : on retombe sur la
    configuration par défaut plutôt que de rendre l'application inutilisable.
    """
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        # config.json n'est plus versionné (il peut contenir une clé API) :
        # au premier lancement on repart de l'exemple fourni.
        example = CONFIG_PATH.parent / "config.example.json"
        if example.exists():
            _log_once("config.json absent — valeurs de config.example.json.")
            raw = json.loads(example.read_text(encoding="utf-8"))
        else:
            _log_once("config.json absent — utilisation des valeurs par défaut.")
            raw = {}
    except (json.JSONDecodeError, OSError) as e:
        log.error("config.json illisible (%s) — valeurs par défaut.", e)
        raw = {}
    try:
        return validate_config(raw)
    except Exception as e:
        log.error("config.json invalide (%s) — valeurs par défaut.", e)
        return validate_config({})


def save_config(cfg: dict) -> dict:
    """Valide puis écrit la config de façon atomique.

    L'écriture passe par un fichier temporaire + os.replace : une coupure en
    cours d'écriture ne peut plus laisser un config.json tronqué.
    """
    validated = validate_config(cfg)
    payload = json.dumps(validated, ensure_ascii=False, indent=2)
    with _CONFIG_LOCK:
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, CONFIG_PATH)
    return validated


# =========================================================================
# 1) ASR — Whisper (faster-whisper), voix -> texte, optimisé CPU
# =========================================================================
class ASR:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None
        self._signature: tuple | None = None

    @staticmethod
    def _sig(cfg: dict) -> tuple:
        a = cfg["asr"]
        return (a["model_size"], a["device"], a["compute_type"])

    def invalidate_if_changed(self, cfg: dict) -> bool:
        """Décharge le modèle si un paramètre de construction a changé.

        Sans cela, changer `model_size` depuis l'UI n'avait aucun effet tant
        que le serveur n'était pas redémarré.
        """
        if self._model is not None and self._signature != self._sig(cfg):
            log.info("[ASR] paramètres modifiés — rechargement du modèle.")
            self._model = None
            self._signature = None
            return True
        return False

    def _lazy(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            a = self.cfg["asr"]
            log.info("[ASR] chargement Whisper '%s' (%s)...", a["model_size"], a["compute_type"])
            self._model = WhisperModel(
                a["model_size"], device=a["device"], compute_type=a["compute_type"]
            )
            self._signature = self._sig(self.cfg)
        return self._model

    def transcribe(self, wav_path: str) -> str:
        model = self._lazy()
        lang = self.cfg["asr"].get("language") or None
        segments, _ = model.transcribe(wav_path, language=lang, vad_filter=True)
        text = "".join(seg.text for seg in segments).strip()
        log.info("[ASR] -> %r", text)
        return text


# =========================================================================
# 2) LLM — client OpenAI-compatible (llama.cpp server, LM Studio, cloud...)
# =========================================================================
class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def api_key(self) -> str:
        """Clé API : la variable d'environnement prime sur le fichier.

        Permet de ne pas stocker de secret dans config.json (versionné).
        """
        env = os.getenv("VOXTRIA_API_KEY")
        if env:
            return env
        key = (self.cfg["llm"].get("api_key") or "").strip()
        return "" if key == "not-needed" else key

    def headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        key = self.api_key()
        if key:
            h["Authorization"] = f"Bearer {key}"
        return h

    def chat(self, history: list[dict]) -> str:
        import requests
        c = self.cfg["llm"]

        # Fenêtre glissante : sans borne, l'historique finit par dépasser la
        # fenêtre de contexte du modèle (erreur HTTP ou troncature du prompt
        # système) et la latence croît linéairement.
        max_msgs = int(c.get("max_history_turns", 20)) * 2
        window = history[-max_msgs:] if max_msgs > 0 else history

        messages = [{"role": "system", "content": c["system_prompt"]}] + window
        payload = {
            "model": c["model"],
            "messages": messages,
            "temperature": c.get("temperature", 0.7),
            "max_tokens": c.get("max_tokens", 512),
            "stream": False,
        }
        url = normalize_base_url(c["base_url"]) + "/chat/completions"

        try:
            r = requests.post(
                url, json=payload, headers=self.headers(),
                timeout=float(c.get("timeout", 120)),
            )
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"Impossible de joindre le serveur LLM sur {url}. "
                f"Est-il démarré ? ({e.__class__.__name__})"
            ) from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(f"Le serveur LLM n'a pas répondu à temps ({url}).") from e

        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code} : {r.text[:300]}")

        try:
            data = r.json()
        except ValueError as e:
            raise RuntimeError(f"Réponse LLM non-JSON : {r.text[:300]}") from e

        if not data.get("choices"):
            raise RuntimeError(f"Réponse LLM inattendue (pas de 'choices') : {str(data)[:300]}")

        message = data["choices"][0].get("message") or {}
        out = (message.get("content") or "").strip()
        if not out:
            raise RuntimeError("Le LLM a renvoyé une réponse vide.")
        log.info("[LLM] -> %r", out)
        return out


# =========================================================================
# 3) TTS — Supertonic, texte -> wav (rapide CPU, voix FR natives)
# =========================================================================
class TTS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._tts = None
        self._synth_params: set[str] | None = None

    def invalidate(self) -> None:
        self._tts = None
        self._synth_params = None

    def _lazy(self):
        if self._tts is None:
            from supertonic import TTS as STTS
            log.info("[TTS] initialisation Supertonic...")
            self._tts = STTS(auto_download=True)
            try:
                self._synth_params = set(
                    inspect.signature(self._tts.synthesize).parameters
                )
            except (TypeError, ValueError):
                self._synth_params = None
        return self._tts

    def list_voices(self) -> list[str]:
        """Noms de voix exposés par le SDK, sinon les presets connus.

        `voice_style_names` est l'attribut réellement fourni par supertonic>=1.3
        (les noms tentés auparavant n'existaient dans aucune version).
        """
        tts = self._lazy()
        for attr in ("voice_style_names", "list_voice_styles", "list_voices"):
            if hasattr(tts, attr):
                val = getattr(tts, attr)
                v = val() if callable(val) else val
                try:
                    names = [str(x) for x in v]
                except TypeError:
                    continue
                if names:
                    return names
        return list(PRESET_VOICES)

    def _get_style(self, tts, voice: str | None):
        """Retourne un voice_style :
        - 'custom:<nom>'  -> ./voices/<nom>.json (voix clonée locale)
        - 'custom'        -> chemin custom_style_path de la config
        - 'M1'..'F5'      -> voix preset Supertonic
        """
        voice = voice if voice is not None else self.cfg["tts"]["voice"]

        # Voix clonée nommée : "custom:ma_voix" — le nom est validé pour
        # empêcher toute traversée de répertoire via l'API.
        if isinstance(voice, str) and voice.startswith("custom:"):
            name = voice.split(":", 1)[1]
            p = safe_voice_path(name)
            if p.exists():
                return tts.get_voice_style_from_path(p)
            raise RuntimeError(f"Voix clonée introuvable : {p.name}")

        if voice == "custom":
            custom = self.cfg["tts"].get("custom_style_path", "")
            if custom and Path(custom).exists():
                return tts.get_voice_style_from_path(Path(custom))
            raise RuntimeError("Voix clonée demandée mais 'custom_style_path' invalide.")

        return tts.get_voice_style(voice_name=voice)

    def synthesize(self, text: str, out_path: str, voice: str | None = None,
                   total_steps: int | None = None, speed: float | None = None,
                   silence_duration: float | None = None) -> str:
        tts = self._lazy()
        t = self.cfg["tts"]

        if t.get("strip_expression_tags", True):
            text = strip_expression_tags(text)
        if not text:
            raise RuntimeError("Rien à synthétiser (texte vide après nettoyage).")

        style = self._get_style(tts, voice)
        kwargs = {
            "lang": t.get("lang", "fr"),
            "total_steps": int(total_steps if total_steps is not None else t.get("total_steps", 8)),
            "speed": float(speed if speed is not None else t.get("speed", 1.05)),
            "silence_duration": float(
                silence_duration if silence_duration is not None else t.get("silence_duration", 0.3)
            ),
        }
        # On filtre sur la signature réelle du SDK plutôt que de rattraper un
        # TypeError : celui-ci masquait aussi les erreurs internes de synthèse.
        if self._synth_params is not None:
            kwargs = {k: v for k, v in kwargs.items() if k in self._synth_params}

        result = tts.synthesize(text, voice_style=style, **kwargs)
        wav = result[0] if isinstance(result, tuple) else result
        tts.save_audio(wav, out_path)
        log.info("[TTS] -> %s (voix=%s, steps=%s)", out_path,
                 voice or t["voice"], kwargs.get("total_steps"))
        return out_path


# =========================================================================
# Orchestrateur
# =========================================================================
@dataclass
class Assistant:
    cfg: dict = field(default_factory=load_config)

    def __post_init__(self):
        self.asr = ASR(self.cfg)
        self.llm = LLM(self.cfg)
        self.tts = TTS(self.cfg)
        self.history: list[dict] = []
        self._lock = threading.Lock()

    def reload_config(self):
        """Recharge la config ET invalide les moteurs dont les paramètres de
        construction ont changé (sinon le changement restait sans effet)."""
        old = self.cfg
        self.cfg = load_config()
        self.asr.cfg = self.llm.cfg = self.tts.cfg = self.cfg
        self.asr.invalidate_if_changed(self.cfg)
        if self.cfg.get("tts", {}).get("engine") != old.get("tts", {}).get("engine"):
            self.tts.invalidate()

    def handle_audio(self, wav_in: str, wav_out: str) -> dict:
        """Pipeline complet : audio -> texte -> LLM -> texte -> audio."""
        t0 = time.time()
        user_text = self.asr.transcribe(wav_in)
        asr_ms = int((time.time() - t0) * 1000)
        if not user_text:
            return {"user": "", "assistant": "", "error": "Aucune parole détectée."}
        res = self._respond(user_text, wav_out, t0)
        res["timings"]["asr_ms"] = asr_ms
        return res

    def handle_text(self, user_text: str, wav_out: str) -> dict:
        """Entrée texte directe (sans micro)."""
        return self._respond(user_text, wav_out, time.time())

    def _respond(self, user_text: str, wav_out: str, t0: float) -> dict:
        # Un seul tour de parole à la fois : l'historique est un état partagé.
        with self._lock:
            self.history.append({"role": "user", "content": user_text})
            t_llm = time.time()
            try:
                reply = self.llm.chat(self.history)
            except Exception:
                self.history.pop()  # rollback du tour utilisateur
                raise
            llm_ms = int((time.time() - t_llm) * 1000)
            self.history.append({"role": "assistant", "content": reply})

            # Le texte a de la valeur même si l'audio échoue : on renvoie un
            # succès partiel plutôt qu'un 500 qui perdrait la réponse.
            t_tts = time.time()
            tts_error = None
            audio_ok = True
            try:
                self.tts.synthesize(reply, wav_out)
            except Exception as e:
                audio_ok = False
                tts_error = str(e)
                log.error("[TTS] échec de synthèse : %s", e)
            tts_ms = int((time.time() - t_tts) * 1000)

        res = {
            "user": user_text,
            "assistant": reply,
            "audio": wav_out if audio_ok else None,
            "elapsed": round(time.time() - t0, 2),
            "timings": {"llm_ms": llm_ms, "tts_ms": tts_ms},
        }
        if tts_error:
            res["tts_error"] = tts_error
        return res

    def clear_history(self):
        with self._lock:
            self.history = []


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    # Test rapide en CLI (texte -> réponse -> wav), sans micro
    a = Assistant()
    res = a.handle_text("Bonjour, présente-toi en une phrase.", "reponse.wav")
    print(json.dumps(res, ensure_ascii=False, indent=2))
