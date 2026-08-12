"""
Serveur web de l'assistant vocal.

Lance une petite API + sert l'interface (static/index.html).

Dépendances :
    pip install -r requirements.txt

Lancement :
    uvicorn server:app --host 127.0.0.1 --port 8500
    # puis ouvre http://127.0.0.1:8500

Variables d'environnement :
    VOXTRIA_DEBUG=1        expose les traces d'exception dans les réponses
    VOXTRIA_API_KEY=...    clé API du LLM (prioritaire sur config.json)
    VOXTRIA_ALLOW_ORIGINS  origines autorisées supplémentaires (séparées par ,)
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import time
import traceback
import uuid
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Body, FastAPI, File, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from starlette.concurrency import run_in_threadpool

from config_schema import (
    PRESET_VOICES,
    SAFE_VOICE_NAME,
    Config,
    normalize_base_url,
    safe_voice_path,
)
from pipeline import Assistant, load_config, save_config

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("voxtria.server")

BASE = Path(__file__).parent
OUT_DIR = BASE / "_out"
OUT_DIR.mkdir(exist_ok=True)
VOICES_DIR = BASE / "voices"
VOICES_DIR.mkdir(exist_ok=True)

DEBUG = os.getenv("VOXTRIA_DEBUG") == "1"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024        # JSON de voix
MAX_AUDIO_BYTES = 25 * 1024 * 1024        # audio micro
AUDIO_TTL_SECONDS = 30 * 60               # purge des wav générés

app = FastAPI(title="VoxTria — Assistant Vocal FR")
assistant = Assistant()


# -------------------------------------------------------------------------
# Utilitaires
# -------------------------------------------------------------------------
def fail(message: str, status: int = 500, exc: BaseException | None = None) -> JSONResponse:
    """Réponse d'erreur : la trace n'est exposée qu'en mode debug explicite."""
    body = {"error": message}
    if DEBUG and exc is not None:
        body["trace"] = traceback.format_exc()
    if exc is not None:
        log.error("%s: %s", message, exc)
    return JSONResponse(body, status)


def purge_old_audio() -> None:
    """Supprime les wav générés expirés (sinon `_out/` croît sans fin)."""
    now = time.time()
    for p in OUT_DIR.glob("*.wav"):
        try:
            if now - p.stat().st_mtime > AUDIO_TTL_SECONDS:
                p.unlink()
        except OSError:
            pass


def new_audio_path(prefix: str) -> tuple[Path, str]:
    """Chemin de sortie unique + URL associée.

    Un nom fixe (reply.wav) faisait s'écraser les requêtes concurrentes et
    empêchait le rafraîchissement côté navigateur (cache sur URL identique).
    """
    name = f"{prefix}_{uuid.uuid4().hex}.wav"
    return OUT_DIR / name, f"/api/audio/{name}"


# -------------------------------------------------------------------------
# Anti-CSRF : le serveur est sans authentification et écoute en local.
# Sans ce garde-fou, n'importe quelle page web ouverte dans le navigateur
# de l'utilisateur peut déclencher un POST (multipart ne provoque aucun
# préflight CORS) et écrire des fichiers ou modifier la configuration.
# -------------------------------------------------------------------------
def _allowed_origins() -> set[str]:
    origins = set()
    for host in ("127.0.0.1", "localhost", "[::1]"):
        for port in ("8500", "8501"):
            origins.add(f"http://{host}:{port}")
            origins.add(f"https://{host}:{port}")
    extra = os.getenv("VOXTRIA_ALLOW_ORIGINS", "")
    for o in extra.split(","):
        if o.strip():
            origins.add(o.strip().rstrip("/"))
    return origins


ALLOWED_ORIGINS = _allowed_origins()


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin:
            netloc = urlparse(origin).netloc.split(":")[0]
            # On accepte l'origine si elle est explicitement autorisée ou si
            # elle correspond à l'hôte servant l'application (proxy, LAN…).
            same_host = netloc == urlparse(str(request.base_url)).netloc.split(":")[0]
            if origin.rstrip("/") not in ALLOWED_ORIGINS and not same_host:
                log.warning("Requête refusée depuis une origine externe : %s", origin)
                return JSONResponse(
                    {"error": "Origine non autorisée (protection CSRF)."}, 403
                )
    return await call_next(request)


# -------------------------------------------------------------------------
# Interface
# -------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def index():
    p = BASE / "static" / "index.html"
    if not p.exists():
        # Dégradation explicite : un 500 opaque ne disait pas quoi faire.
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'>"
            "<title>VoxTria</title>"
            "<style>body{font-family:system-ui;max-width:40rem;margin:4rem auto;"
            "padding:0 1rem;line-height:1.6}code{background:#eee;padding:.1em .3em}</style>"
            "<h1>🎙️ VoxTria</h1>"
            "<p><strong>L'interface web est absente</strong> "
            "(<code>static/index.html</code> introuvable).</p>"
            "<p>L'API fonctionne : voir <a href='/docs'>/docs</a> "
            "ou <a href='/api/health'>/api/health</a>.</p>",
            status_code=503,
        )
    return p.read_text(encoding="utf-8")


# -------------------------------------------------------------------------
# Configuration
# -------------------------------------------------------------------------
@app.get("/api/config")
def get_config():
    cfg = load_config()
    # On n'expose jamais la clé API au navigateur : seulement sa présence.
    cfg = _json.loads(_json.dumps(cfg))
    if cfg.get("llm", {}).get("api_key"):
        cfg["llm"]["api_key"] = ""
        cfg["llm"]["api_key_set"] = True
    return cfg


@app.post("/api/config")
def update_config(cfg: dict = Body(...)):
    """Met à jour la configuration après validation stricte du schéma."""
    try:
        # Fusion superficielle : l'UI peut n'envoyer qu'une section.
        current = load_config()
        merged = {**current}
        for section, values in (cfg or {}).items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section] = {**merged[section], **values}
            else:
                merged[section] = values
        # Une clé vide envoyée par l'UI ne doit pas effacer la clé existante.
        if not (cfg.get("llm") or {}).get("api_key"):
            merged.setdefault("llm", {})["api_key"] = current["llm"].get("api_key", "")
        validated = save_config(merged)
        assistant.reload_config()
        return {"ok": True, "config": {**validated, "llm": {**validated["llm"], "api_key": ""}}}
    except ValueError as e:
        return fail(f"Configuration invalide : {e}", 400, e)
    except Exception as e:
        return fail("Impossible d'enregistrer la configuration.", 500, e)


@app.get("/api/config/schema")
def config_schema():
    """Schéma JSON de la config : permet à l'UI de générer ses contrôles."""
    return Config.model_json_schema()


# -------------------------------------------------------------------------
# Voix
# -------------------------------------------------------------------------
def list_custom_voices() -> list[str]:
    """Voix clonées disponibles dans ./voices/ (fichiers .json)."""
    return sorted(p.stem for p in VOICES_DIR.glob("*.json") if SAFE_VOICE_NAME.match(p.stem))


@app.get("/api/voices")
def voices(probe: bool = False):
    """Liste des voix.

    Par défaut on renvoie les presets sans instancier Supertonic : le simple
    affichage du panneau de réglages déclenchait sinon le téléchargement de
    plusieurs centaines de Mo. `?probe=true` interroge réellement le moteur.
    """
    customs = ["custom:" + n for n in list_custom_voices()]
    if not probe:
        return {"voices": list(PRESET_VOICES), "custom_voices": customs, "probed": False}
    try:
        v = assistant.tts.list_voices() or list(PRESET_VOICES)
        return {"voices": v, "custom_voices": customs, "probed": True}
    except Exception as e:
        log.warning("Sonde des voix impossible : %s", e)
        return {
            "voices": list(PRESET_VOICES), "custom_voices": customs,
            "probed": False, "warning": str(e),
        }


@app.post("/api/upload_voice")
async def upload_voice(file: UploadFile = File(...)):
    """Upload d'un JSON de voix clonée (créé via le Voice Builder de Supertone).

    Le nom fourni par le client est ignoré au profit d'un nom assaini, et le
    contenu est validé AVANT d'être écrit sur disque.
    """
    try:
        raw_name = file.filename or ""
        # Refus explicite de tout séparateur de chemin : le nom est confiné à
        # ./voices de toute façon, mais un rejet franc vaut mieux qu'une
        # réécriture silencieuse de "../../x.json" en "x.json".
        if any(sep in raw_name for sep in ("/", "\\")) or ".." in raw_name:
            return JSONResponse(
                {"error": "Le nom de fichier ne doit pas contenir de chemin."}, 400
            )
        if not raw_name.lower().endswith(".json"):
            return JSONResponse({"error": "Fichier .json attendu (export Voice Builder)."}, 400)
        stem = Path(raw_name).stem
        if not SAFE_VOICE_NAME.match(stem):
            return JSONResponse(
                {"error": "Nom de voix invalide : lettres, chiffres, '_' et '-' "
                          "uniquement (64 caractères max)."}, 400,
            )

        raw = await file.read()
        if len(raw) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": "Fichier trop volumineux (5 Mo max)."}, 413)
        try:
            data = _json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, _json.JSONDecodeError):
            return JSONResponse({"error": "Le fichier n'est pas un JSON valide."}, 400)
        if not isinstance(data, dict):
            return JSONResponse({"error": "Le JSON de voix doit être un objet."}, 400)

        dest = safe_voice_path(stem)          # confiné à ./voices
        dest.write_text(_json.dumps(data), encoding="utf-8")

        cfg = load_config()
        cfg["tts"]["custom_style_path"] = str(dest)
        cfg["tts"]["voice"] = f"custom:{stem}"
        save_config(cfg)
        assistant.reload_config()
        return {"ok": True, "name": stem, "voice": f"custom:{stem}"}
    except ValueError as e:
        return fail(str(e), 400, e)
    except Exception as e:
        return fail("Échec de l'upload de la voix.", 500, e)


@app.delete("/api/voices/{name}")
def delete_voice(name: str):
    try:
        p = safe_voice_path(name)
    except ValueError as e:
        return fail(str(e), 400, e)
    if not p.exists():
        return JSONResponse({"error": "Voix introuvable."}, 404)
    p.unlink()
    cfg = load_config()
    if cfg["tts"].get("voice") == f"custom:{name}":
        cfg["tts"]["voice"] = "M1"
        cfg["tts"]["custom_style_path"] = ""
        save_config(cfg)
        assistant.reload_config()
    return {"ok": True}


# -------------------------------------------------------------------------
# LLM
# -------------------------------------------------------------------------
@app.get("/api/models")
def discover_models():
    """Découverte auto des modèles via l'endpoint /v1/models (OpenAI-compatible).

    Ne lève jamais : renvoie une liste vide + un avertissement si la source ne
    supporte pas /v1/models (l'utilisateur saisit alors le nom à la main).
    """
    try:
        import requests
        cfg = load_config()
        url = normalize_base_url(cfg["llm"]["base_url"]) + "/models"
        headers = {}
        key = assistant.llm.api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
        ids = [m.get("id") for m in data.get("data", []) if m.get("id")]
        return {"models": ids}
    except Exception as e:
        return {"models": [], "warning": str(e)}


# -------------------------------------------------------------------------
# Synthèse et conversation
# -------------------------------------------------------------------------
@app.post("/api/tts_test")
async def tts_test(payload: dict = Body(...)):
    """Test rapide d'une voix : texte court -> wav, avec les réglages live."""
    text = str(payload.get("text") or "Bonjour, ceci est un test de voix.")[:1000]
    out, url = new_audio_path("voice_test")
    try:
        await run_in_threadpool(
            assistant.tts.synthesize,
            text, str(out), payload.get("voice"),
            payload.get("total_steps"), payload.get("speed"),
            payload.get("silence_duration"),
        )
        purge_old_audio()
        return FileResponse(out, media_type="audio/wav", filename="voice_test.wav",
                            headers={"X-Audio-Url": url})
    except Exception as e:
        return fail(f"Échec de la synthèse : {e}", 500, e)


@app.post("/api/chat_text")
async def chat_text(payload: dict = Body(...)):
    """Pipeline depuis un texte (sans micro) : LLM -> TTS."""
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Message vide."}, 400)
    if len(text) > 10000:
        return JSONResponse({"error": "Message trop long (10 000 caractères max)."}, 413)
    out, url = new_audio_path("reply")
    try:
        res = await run_in_threadpool(assistant.handle_text, text, str(out))
        if res.get("audio"):
            res["audio_url"] = url
        purge_old_audio()
        return res
    except Exception as e:
        return fail(str(e), 502, e)


@app.post("/api/chat_stream")
async def chat_stream(payload: dict = Body(...)):
    """Pipeline en streaming (SSE) : une phrase synthétisée à la fois.

    Le client reçoit le texte au fil de l'eau et peut jouer le premier audio
    sans attendre la fin de la génération.
    """
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Message vide."}, 400)
    if len(text) > 10000:
        return JSONResponse({"error": "Message trop long (10 000 caractères max)."}, 413)

    async def events():
        q: asyncio.Queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def produce():
            # Le pipeline est synchrone (HTTP + ONNX) : il tourne dans un
            # thread et pousse ses événements vers la boucle asyncio.
            try:
                for ev in assistant.respond_stream(text, OUT_DIR):
                    if ev.get("audio"):
                        ev["audio_url"] = f"/api/audio/{ev['audio']}"
                    loop.call_soon_threadsafe(q.put_nowait, ev)
            except Exception as e:  # pragma: no cover - filet de sécurité
                log.error("stream: %s", e)
                loop.call_soon_threadsafe(q.put_nowait, {"type": "error", "error": str(e)})
            finally:
                loop.call_soon_threadsafe(q.put_nowait, None)

        loop.run_in_executor(None, produce)
        while True:
            ev = await q.get()
            if ev is None:
                break
            yield f"data: {_json.dumps(ev, ensure_ascii=False)}\n\n"
        purge_old_audio()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/chat_audio")
async def chat_audio(file: UploadFile = File(...)):
    """Pipeline complet depuis un audio micro : ASR -> LLM -> TTS."""
    raw = await file.read()
    if not raw:
        return JSONResponse({"error": "Audio vide."}, 400)
    if len(raw) > MAX_AUDIO_BYTES:
        return JSONResponse({"error": "Audio trop volumineux (25 Mo max)."}, 413)

    in_path = OUT_DIR / f"input_{uuid.uuid4().hex}.wav"
    out, url = new_audio_path("reply")
    try:
        in_path.write_bytes(raw)
        # run_in_threadpool : le pipeline est synchrone et lent (Whisper +
        # HTTP + TTS). Dans une coroutine il gelait toute la boucle
        # d'événements, donc l'ensemble du serveur, pendant chaque tour.
        res = await run_in_threadpool(assistant.handle_audio, str(in_path), str(out))
        if res.get("audio"):
            res["audio_url"] = url
        return res
    except Exception as e:
        return fail(str(e), 502, e)
    finally:
        # Le fichier temporaire n'était jamais supprimé : `_out/` accumulait
        # un wav par tour de parole indéfiniment.
        in_path.unlink(missing_ok=True)
        purge_old_audio()


@app.get("/api/audio/{name}")
def get_audio(name: str):
    p = (OUT_DIR / name).resolve()
    # Validation explicite plutôt que de s'en remettre au routage du framework.
    if p.parent != OUT_DIR.resolve() or not p.is_file() or p.suffix != ".wav":
        return JSONResponse({"error": "not found"}, 404)
    return FileResponse(p, media_type="audio/wav")


@app.post("/api/clear")
def clear():
    assistant.clear_history()
    return {"ok": True}


@app.get("/api/history")
def history():
    return {"history": assistant.history}


@app.get("/api/health")
def health():
    cfg = load_config()
    return {
        "ok": True,
        "ui": (BASE / "static" / "index.html").exists(),
        "llm_url": normalize_base_url(cfg["llm"]["base_url"]),
        "voice": cfg["tts"]["voice"],
        "turns": len(assistant.history) // 2,
    }
