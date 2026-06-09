"""
Pipeline de l'assistant vocal — briques modulaires ASR / LLM / TTS.

Chaque brique est isolée : tu peux remplacer le LLM (local/cloud/API) sans
toucher au reste, changer la voix TTS, etc.

Dépendances :
    pip install faster-whisper supertonic requests soundfile numpy
"""
from __future__ import annotations

import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

CONFIG_PATH = Path(__file__).parent / "config.json"


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict) -> None:
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


# =========================================================================
# 1) ASR — Whisper (faster-whisper), voix -> texte, optimisé CPU
# =========================================================================
class ASR:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._model = None

    def _lazy(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            a = self.cfg["asr"]
            print(f"[ASR] chargement Whisper '{a['model_size']}' ({a['compute_type']})...")
            self._model = WhisperModel(
                a["model_size"], device=a["device"], compute_type=a["compute_type"]
            )
        return self._model

    def transcribe(self, wav_path: str) -> str:
        model = self._lazy()
        lang = self.cfg["asr"].get("language") or None
        segments, _ = model.transcribe(wav_path, language=lang, vad_filter=True)
        text = "".join(seg.text for seg in segments).strip()
        print(f"[ASR] -> {text!r}")
        return text


# =========================================================================
# 2) LLM — client OpenAI-compatible (llama.cpp server, LM Studio, cloud...)
# =========================================================================
class LLM:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def chat(self, history: list[dict]) -> str:
        import requests
        c = self.cfg["llm"]
        messages = [{"role": "system", "content": c["system_prompt"]}] + history
        payload = {
            "model": c["model"],
            "messages": messages,
            "temperature": c.get("temperature", 0.7),
            "max_tokens": c.get("max_tokens", 512),
            "stream": False,
        }
        headers = {"Content-Type": "application/json"}
        if c.get("api_key") and c["api_key"] != "not-needed":
            headers["Authorization"] = f"Bearer {c['api_key']}"

        # Tolérance : on s'assure que l'URL se termine par /v1
        base = c["base_url"].rstrip("/")
        if not base.endswith("/v1"):
            base = base + "/v1"
        url = base + "/chat/completions"

        r = requests.post(url, json=payload, headers=headers, timeout=120)
        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code} : {r.text[:300]}")

        data = r.json()
        if "choices" not in data or not data["choices"]:
            # message d'erreur lisible au lieu d'un KeyError 'choices'
            raise RuntimeError(f"Réponse LLM inattendue (pas de 'choices') : {str(data)[:300]}")

        out = data["choices"][0]["message"]["content"].strip()
        print(f"[LLM] -> {out!r}")
        return out


# =========================================================================
# 3) TTS — Supertonic, texte -> wav (rapide CPU, voix FR natives)
# =========================================================================
class TTS:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._tts = None

    def _lazy(self):
        if self._tts is None:
            from supertonic import TTS as STTS
            print("[TTS] initialisation Supertonic...")
            self._tts = STTS(auto_download=True)
        return self._tts

    def list_voices(self) -> list[str]:
        tts = self._lazy()
        for attr in ("list_voice_styles", "list_voices", "get_voice_names", "voices"):
            if hasattr(tts, attr):
                val = getattr(tts, attr)
                v = val() if callable(val) else val
                try:
                    return list(v)
                except TypeError:
                    return [str(v)]
        # Supertonic 3 : 10 voix preset (M1-M5 masculines, F1-F5 féminines)
        return ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]

    def _get_style(self, tts, voice: str | None):
        """Retourne un voice_style :
        - 'custom:<nom>'  -> ./voices/<nom>.json (voix clonée locale)
        - 'custom'        -> chemin custom_style_path de la config
        - 'M1'..'F5'      -> voix preset Supertonic
        """
        from pathlib import Path
        voices_dir = Path(__file__).parent / "voices"
        voice = voice if voice is not None else self.cfg["tts"]["voice"]

        # Voix clonée nommée : "custom:ma_voix"
        if isinstance(voice, str) and voice.startswith("custom:"):
            name = voice.split(":", 1)[1]
            p = voices_dir / f"{name}.json"
            if p.exists():
                return tts.get_voice_style_from_path(p)
            raise RuntimeError(f"Voix clonée introuvable : {p}")

        # Voix custom générique via custom_style_path
        custom = self.cfg["tts"].get("custom_style_path", "")
        if voice == "custom":
            if custom and Path(custom).exists():
                return tts.get_voice_style_from_path(Path(custom))
            raise RuntimeError("Voix clonée demandée mais 'custom_style_path' invalide.")

        return tts.get_voice_style(voice_name=voice)

    def synthesize(self, text: str, out_path: str, voice: str | None = None,
                   total_steps: int | None = None, speed: float | None = None,
                   silence_duration: float | None = None) -> str:
        tts = self._lazy()
        t = self.cfg["tts"]
        lang = t.get("lang", "fr")
        style = self._get_style(tts, voice)
        kwargs = {
            "lang": lang,
            "total_steps": int(total_steps if total_steps is not None else t.get("total_steps", 8)),
            "speed": float(speed if speed is not None else t.get("speed", 1.05)),
            "silence_duration": float(
                silence_duration if silence_duration is not None else t.get("silence_duration", 0.3)
            ),
        }
        try:
            result = tts.synthesize(text, voice_style=style, **kwargs)
        except TypeError:
            # SDK plus ancien : on retire les kwargs non supportés
            result = tts.synthesize(text, voice_style=style, lang=lang)
        wav = result[0] if isinstance(result, tuple) else result
        tts.save_audio(wav, out_path)
        print(f"[TTS] -> {out_path} (voix={voice or t['voice']}, steps={kwargs['total_steps']}, speed={kwargs['speed']})")
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

    def reload_config(self):
        self.cfg = load_config()
        self.asr.cfg = self.llm.cfg = self.tts.cfg = self.cfg

    def handle_audio(self, wav_in: str, wav_out: str) -> dict:
        """Pipeline complet : audio -> texte -> LLM -> texte -> audio."""
        t0 = time.time()
        user_text = self.asr.transcribe(wav_in)
        if not user_text:
            return {"user": "", "assistant": "", "error": "Aucune parole détectée."}
        return self._respond(user_text, wav_out, t0)

    def handle_text(self, user_text: str, wav_out: str) -> dict:
        """Entrée texte directe (sans micro)."""
        return self._respond(user_text, wav_out, time.time())

    def _respond(self, user_text: str, wav_out: str, t0: float) -> dict:
        # On ajoute le tour utilisateur, mais on l'annule si le LLM échoue
        # (évite d'accumuler des messages sans réponse dans l'historique).
        self.history.append({"role": "user", "content": user_text})
        try:
            reply = self.llm.chat(self.history)
        except Exception:
            self.history.pop()  # rollback du tour utilisateur
            raise
        self.history.append({"role": "assistant", "content": reply})
        self.tts.synthesize(reply, wav_out)
        return {
            "user": user_text,
            "assistant": reply,
            "audio": wav_out,
            "elapsed": round(time.time() - t0, 2),
        }

    def clear_history(self):
        self.history = []


if __name__ == "__main__":
    # Test rapide en CLI (texte -> réponse -> wav), sans micro
    a = Assistant()
    res = a.handle_text("Bonjour, présente-toi en une phrase.", "reponse.wav")
    print(json.dumps(res, ensure_ascii=False, indent=2))
