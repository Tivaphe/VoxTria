"""
Serveur web de l'assistant vocal.

Lance une petite API + sert l'interface (static/index.html).

Dépendances :
    pip install fastapi uvicorn python-multipart faster-whisper supertonic requests soundfile numpy

Lancement :
    uvicorn server:app --host 127.0.0.1 --port 8500
    # puis ouvre http://127.0.0.1:8500
"""
from __future__ import annotations

import tempfile
import traceback
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, Body
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from pipeline import Assistant, load_config, save_config

BASE = Path(__file__).parent
OUT_DIR = BASE / "_out"
OUT_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Assistant Vocal FR")
assistant = Assistant()


@app.get("/", response_class=HTMLResponse)
def index():
    return (BASE / "static" / "index.html").read_text(encoding="utf-8")


@app.get("/api/config")
def get_config():
    return load_config()


@app.post("/api/config")
def update_config(cfg: dict = Body(...)):
    save_config(cfg)
    assistant.reload_config()
    return {"ok": True}


ALL_SUPERTONIC_VOICES = ["M1", "M2", "M3", "M4", "M5", "F1", "F2", "F3", "F4", "F5"]
VOICES_DIR = BASE / "voices"
VOICES_DIR.mkdir(exist_ok=True)


def list_custom_voices() -> list[str]:
    """Voix clonées disponibles dans ./voices/ (fichiers .json)."""
    return sorted(p.stem for p in VOICES_DIR.glob("*.json"))


@app.get("/api/voices")
def voices():
    customs = ["custom:" + n for n in list_custom_voices()]
    try:
        v = assistant.tts.list_voices()
        if not v or len(v) < 2:
            v = ALL_SUPERTONIC_VOICES
        return {"voices": v, "custom_voices": customs}
    except Exception as e:
        return {"voices": ALL_SUPERTONIC_VOICES, "custom_voices": customs, "warning": str(e)}


@app.get("/api/models")
def discover_models():
    """Découverte auto des modèles via l'endpoint /v1/models (OpenAI-compatible).

    Marche avec LM Studio, llama.cpp server, OpenAI, etc. Si la source ne
    supporte pas /v1/models, on renvoie une liste vide + un avertissement
    (l'utilisateur peut alors taper le nom du modèle à la main).
    """
    import requests
    cfg = load_config()
    base = cfg["llm"]["base_url"].rstrip("/")
    if not base.endswith("/v1"):
        base = base + "/v1"
    url = base + "/models"
    headers = {}
    key = cfg["llm"].get("api_key")
    if key and key != "not-needed":
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        # format OpenAI : {"data": [{"id": "..."}]}
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return {"models": ids}
    except Exception as e:
        return {"models": [], "warning": str(e)}


@app.post("/api/tts_test")
def tts_test(payload: dict = Body(...)):
    """Test rapide d'une voix : texte court -> wav, avec les réglages live."""
    text = payload.get("text", "Bonjour, ceci est un test de voix.")
    voice = payload.get("voice")
    out = OUT_DIR / "voice_test.wav"
    try:
        assistant.tts.synthesize(
            text, str(out), voice=voice,
            total_steps=payload.get("total_steps"),
            speed=payload.get("speed"),
            silence_duration=payload.get("silence_duration"),
        )
        return FileResponse(out, media_type="audio/wav", filename="voice_test.wav")
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, 500)


@app.post("/api/upload_voice")
async def upload_voice(file: UploadFile = File(...)):
    """Upload d'un JSON de voix clonée (créé via le Voice Builder de Supertone).

    Le fichier est enregistré dans ./voices/ et défini comme voix custom active.
    """
    try:
        if not file.filename.lower().endswith(".json"):
            return JSONResponse({"error": "Fichier .json attendu (export Voice Builder)."}, 400)
        dest = VOICES_DIR / file.filename
        dest.write_bytes(await file.read())
        # validation basique : c'est bien du JSON
        import json as _json
        _json.loads(dest.read_text(encoding="utf-8"))
        # on l'active dans la config
        cfg = load_config()
        cfg["tts"]["custom_style_path"] = str(dest)
        cfg["tts"]["voice"] = "custom"
        save_config(cfg)
        assistant.reload_config()
        return {"ok": True, "path": str(dest), "name": file.filename}
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, 500)


@app.post("/api/chat_text")
def chat_text(payload: dict = Body(...)):
    """Pipeline depuis un texte (sans micro) : LLM -> TTS."""
    text = payload.get("text", "")
    out = OUT_DIR / "reply.wav"
    try:
        res = assistant.handle_text(text, str(out))
        res["audio_url"] = "/api/audio/reply.wav"
        return res
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, 500)


@app.post("/api/chat_audio")
async def chat_audio(file: UploadFile = File(...)):
    """Pipeline complet depuis un audio micro : ASR -> LLM -> TTS."""
    out = OUT_DIR / "reply.wav"
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir=OUT_DIR) as tmp:
            tmp.write(await file.read())
            in_path = tmp.name
        res = assistant.handle_audio(in_path, str(out))
        res["audio_url"] = "/api/audio/reply.wav"
        return res
    except Exception as e:
        return JSONResponse({"error": str(e), "trace": traceback.format_exc()}, 500)


@app.get("/api/audio/{name}")
def get_audio(name: str):
    p = OUT_DIR / name
    if not p.exists():
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(p, media_type="audio/wav")


@app.post("/api/clear")
def clear():
    assistant.clear_history()
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}
