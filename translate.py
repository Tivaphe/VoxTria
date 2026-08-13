"""
Module de traduction live — branche VoxTria spécialisée.

Trois sources supportées, sélectionnables indépendamment :
  - "youtube_sub" : sous-titres officiels YouTube (timedtext, JSON3)
    → quasi instantané, pas d'ASR, le + fiable quand la vidéo a des subs
  - "tab_audio"   : audio d'onglet capturé côté navigateur (MediaRecorder
    sur un getDisplayMedia audio) et POSTé à /api/translate/audio
    → universel (YouTube/Twitch/Zoom/podcast), ~1-2 s de retard
  - "url_stream"  : worker Python qui télécharge via yt-dlp + ffmpeg et
    passe à faster-whisper par segments → indépendant du navigateur

Pour les 3 sources, le pipeline aval est identique :

  segment texte source (≈ 1 phrase)
        ↓
  Translator (LLM OpenAI-compatible, ex. tencent/Hy-MT2-1.8B-GGUF
               via llama.cpp, prompt système "translate the {src} into {tgt}")
        ↓
  TTS Supertonic (voix/langue déjà câblés dans pipeline.TTS)
        ↓
  URL /api/audio/... renvoyée au navigateur → file audio côté client

Conception :
  - Le Translator réutilise le client `LLM` du pipeline (requests, pas de
    dépendance ajoutée). Pour Hy-MT2 c'est trivial : on remplace juste
    system_prompt + temperature + max_tokens au moment de l'appel, sans
    modifier l'historique de conversation (`Assistant` n'est pas engagé).
  - Les sessions sont conservées en mémoire (LRU borné). Pas de persistence
    disque : un redémarrage les efface, c'est documenté et volontaire (les
    traductions sont éphémères).
  - Tout est thread-safe : un `threading.Lock` par session + un verrou de
    classe pour la table des sessions.

Dépendances supplémentaires (toutes déjà présentes sauf yt-dlp) :
    pip install yt-dlp
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import requests

from config_schema import normalize_base_url

log = logging.getLogger("voxtria.translate")

# Code langue BCP-47 simple (2 lettres), whitelist pour Hy-MT2 et Supertonic.
# Étendre si besoin — Hy-MT2 couvre une bonne partie des langues majeures.
SUPPORTED_LANGS = (
    "en", "fr", "es", "de", "it", "pt", "nl", "ru", "zh", "ja",
    "ko", "ar", "hi", "tr", "pl", "uk", "vi", "th", "id",
)

# Borne dure de la file de sessions (LRU).
MAX_SESSIONS = 16
# Nombre max de segments gardés en mémoire par session (tronque les vieilles
# lignes, garde les plus récentes — c'est ce que l'utilisateur vient d'entendre).
MAX_SEGMENTS_PER_SESSION = 200


# ------------------------------------------------------------------ segments
@dataclass
class Segment:
    """Un segment de traduction, affiché dans l'onglet Sessions live."""
    idx: int
    src_text: str            # texte source (langue d'origine)
    tgt_text: str            # traduction (langue cible)
    src_lang: str
    tgt_lang: str
    t0: float                # timestamp d'arrivée (time.time)
    elapsed_ms: int          # latence totale traduction+synthèse
    audio_url: Optional[str] = None
    tts_error: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "idx": self.idx,
            "src_text": self.src_text,
            "tgt_text": self.tgt_text,
            "src_lang": self.src_lang,
            "tgt_lang": self.tgt_lang,
            "t0": self.t0,
            "elapsed_ms": self.elapsed_ms,
            "audio_url": self.audio_url,
            "tts_error": self.tts_error,
        }


@dataclass
class Session:
    """Une session de traduction live.

    Les sessions sont identifiées par un UUID court et conservées en mémoire
    (LRU borné par MAX_SESSIONS). Chaque session est strictement mono-producteur
    (un seul onglet / worker écrit dedans à la fois) ; la lecture (UI) est
    libre et protégée par `_lock`.
    """
    id: str
    source: str                       # "youtube_sub" | "tab_audio" | "url_stream"
    src_lang: str
    tgt_lang: str
    title: str = ""                   # titre de la vidéo (best effort)
    started_at: float = field(default_factory=time.time)
    segments: deque = field(default_factory=lambda: deque(maxlen=MAX_SEGMENTS_PER_SESSION))
    _lock: threading.Lock = field(default_factory=threading.Lock)
    active: bool = True
    _idx_counter: int = 0

    def add_segment(self, seg: Segment) -> None:
        with self._lock:
            self.segments.append(seg)

    def snapshot(self, since_idx: int = 0, limit: int = 100) -> list[dict]:
        """Renvoie les segments à partir de `since_idx` (exclu), au plus `limit`."""
        with self._lock:
            out = [s.to_dict() for s in self.segments if s.idx > since_idx]
        return out[-limit:]

    def next_idx(self) -> int:
        with self._lock:
            self._idx_counter += 1
            return self._idx_counter

    def close(self) -> None:
        with self._lock:
            self.active = False


# ---------------------------------------------------------------- Translator
class Translator:
    """Façade d'invocation du LLM de traduction.

    Hy-MT2 (Tencent) est un *vrai* modèle de traduction : il faut
      - temperature = 0 (aucune créativité)
      - max_tokens court (une phrase ne dépasse pas ~80 tokens FR)
      - system_prompt = "translate the {src} into {tgt}"
      - PAS d'historique de conversation (chaque segment est indépendant)

    On réutilise la classe `LLM` du pipeline pour la négociation HTTP (clé API,
    normalisation d'URL, timeouts) — mais on n'engage pas `Assistant`, donc
    l'historique du chat vocal n'est pas affecté.
    """

    # Le rôle du system prompt chez Hy-MT2 / NLLB : forcer le format
    # "translate X into Y" plutôt qu'une consigne conversationnelle. On laisse
    # l'utilisateur le surcharger via la config s'il veut pointer vers un autre
    # modèle de traduction (NLLB, M2M-100, MADLAD, etc.).
    DEFAULT_PROMPT = "translate the {src} into {tgt}"

    def __init__(self, llm_cfg: dict, translate_cfg: dict):
        self.llm_cfg = dict(llm_cfg)            # copie : ne pas muter la globale
        self.translate_cfg = translate_cfg

    def _system_prompt(self, src: str, tgt: str) -> str:
        tmpl = (self.translate_cfg.get("system_prompt") or self.DEFAULT_PROMPT)
        return tmpl.format(src=src, tgt=tgt)

    def _sampling_params(self) -> dict:
        """Paramètres de sampling lus depuis `translate.*`.

        Recommandation officielle Tencent pour Hy-MT2 (1.8B & 7B) :
          temperature=0.7, top_p=0.6, top_k=20, repetition_penalty=1.05.

        Ces valeurs sont des défauts modifiables depuis l'UI. On les
        passe tels quels dans la requête OpenAI — llama.cpp et la plupart
        des serveurs compatibles honorent ces champs.
        """
        t = self.translate_cfg
        params = {
            "temperature": float(t.get("temperature", 0.7)),
            "top_p": float(t.get("top_p", 0.6)),
            "top_k": int(t.get("top_k", 20)),
            "repetition_penalty": float(t.get("repetition_penalty", 1.05)),
        }
        # On n'envoie pas une valeur si elle n'est pas comprise par le serveur :
        # llama.cpp récent les supporte toutes, mais prudence.
        return {k: v for k, v in params.items() if v is not None}

    def translate(self, text: str, src: str, tgt: str, timeout: float = 30.0) -> str:
        """Traduit un segment de texte. Lève RuntimeError en cas d'échec."""
        if not text or not text.strip():
            return ""
        if src not in SUPPORTED_LANGS or tgt not in SUPPORTED_LANGS:
            raise ValueError(
                f"Langues non supportées : src={src!r}, tgt={tgt!r} "
                f"(autorisées : {', '.join(SUPPORTED_LANGS)})"
            )
        url = normalize_base_url(self.llm_cfg.get("base_url", "")) + "/chat/completions"
        system = self._system_prompt(src, tgt)
        # max_tokens grossit avec la longueur de l'entrée (≈ 2× pour FR),
        # borné par la valeur configurée.
        cfg_max = int(self.translate_cfg.get("max_tokens", 4096))
        max_tokens = max(64, min(int(len(text) * 4) + 32, cfg_max))
        payload = {
            "model": self.llm_cfg.get("model") or "hy-mt2",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "max_tokens": max_tokens,
            "stream": False,
        }
        # Sampling Hy-MT2 : on fusionne avec les défauts recommandés.
        payload.update(self._sampling_params())
        headers = {"Content-Type": "application/json"}
        key = (self.llm_cfg.get("api_key") or "").strip()
        if key and key != "not-needed":
            headers["Authorization"] = f"Bearer {key}"

        try:
            r = requests.post(url, json=payload, headers=headers, timeout=timeout)
        except requests.exceptions.ConnectionError as e:
            raise RuntimeError(
                f"LLM de traduction injoignable sur {url} ({e.__class__.__name__})."
            ) from e
        except requests.exceptions.Timeout as e:
            raise RuntimeError(
                f"LLM de traduction n'a pas répondu à temps ({timeout}s)."
            ) from e
        if r.status_code != 200:
            raise RuntimeError(f"LLM HTTP {r.status_code} : {r.text[:200]}")
        try:
            data = r.json()
            out = (data["choices"][0]["message"]["content"] or "").strip()
        except (ValueError, KeyError, IndexError) as e:
            raise RuntimeError(f"Réponse LLM inattendue : {str(e)[:200]}") from e
        if not out:
            raise RuntimeError("Le traducteur a renvoyé une réponse vide.")
        log.info("[TR] %s→%s %r -> %r", src, tgt, text[:60], out[:60])
        return out


# ------------------------------------------------------------- source: YouTube
# L'endpoint timedtext est public (pas de cookie nécessaire pour les vidéos
# qui exposent leurs sous-titres). Format JSON3 = segments horodatés.
_TIMEDTEXT_URL = "https://www.youtube.com/api/timedtext"


def _extract_video_id(url: str) -> Optional[str]:
    """Récupère l'ID vidéo depuis une URL YouTube variée (watch, youtu.be, shorts, embed, live)."""
    if not url:
        return None
    u = url.strip()
    # youtu.be/<id>
    m = re.match(r"https?://youtu\.be/([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    # youtube.com/... ?v=<id>  ou  &v=<id>
    m = re.search(r"[?&]v=([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    # /shorts/<id>  /embed/<id>  /live/<id>
    m = re.search(r"youtube\.com/(?:shorts|embed|live)/([A-Za-z0-9_-]{6,})", u)
    if m:
        return m.group(1)
    return None


def _get_video_title(video_id: str, timeout: float = 8.0) -> str:
    """Best-effort : récupère le <title> de la page watch. Tolère tout échec."""
    try:
        r = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=timeout,
        )
        if r.ok:
            m = re.search(r"<title>([^<]+)</title>", r.text)
            if m:
                # YouTube ajoute " - YouTube" en suffixe.
                return m.group(1).replace(" - YouTube", "").strip()
    except requests.RequestException:
        pass
    return video_id


def fetch_youtube_subtitle_segment(
    video_id: str, lang: str, last_t_ms: int, timeout: float = 8.0
) -> tuple[Optional[str], int]:
    """Interroge timedtext et renvoie le prochain segment après `last_t_ms`.

    Renvoie (texte, nouveau_t_ms). (None, last_t_ms) si rien de neuf
    (live : segments pas encore publiés par YouTube).
    """
    params = {
        "v": video_id,
        "lang": lang,
        "fmt": "json3",
        "3p": "1",                # format 3p = list of {tStartMs, durMs, segs:[{utf8}]}
    }
    try:
        r = requests.get(_TIMEDTEXT_URL, params=params, timeout=timeout)
    except requests.RequestException as e:
        log.warning("[YT-sub] %s", e)
        return None, last_t_ms
    if r.status_code != 200 or not r.text.strip():
        return None, last_t_ms
    try:
        data = r.json()
    except ValueError:
        return None, last_t_ms
    # json3 : {"events": [{"tStartMs": 1234, "dDurationMs": 2500,
    #                       "segs": [{"utf8": "Hello"}]}, ...]}
    best_t, best_text = last_t_ms, None
    for ev in data.get("events") or []:
        t = int(ev.get("tStartMs") or 0)
        if t <= last_t_ms:
            continue
        segs = ev.get("segs") or []
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        # On prend le segment le plus récent publié (les subs live peuvent en
        # pousser plusieurs d'un coup si le worker était en pause).
        if t > best_t:
            best_t, best_text = t, text
    return best_text, best_t


# ------------------------------------------------------------- source: yt-dlp
# Import paresseux : la majorité des utilisateurs n'activent pas ce mode.
def _ytdlp_available() -> bool:
    try:
        import yt_dlp  # noqa: F401
        return True
    except ImportError:
        return False


def stream_url_to_wav(url: str, out_path: Path, on_chunk: Optional[callable] = None,
                      chunk_seconds: float = 4.0) -> None:
    """Télécharge un flux audio (YouTube live, podcast, HLS…) et le segmente
    en WAV courts écrits dans `out_path` (un fichier par chunk).

    Nécessite : pip install yt-dlp  +  ffmpeg installé sur le système.

    `on_chunk(path, dur_s)` est appelé pour chaque WAV produit — c'est le
    hook utilisé par le worker pour le passer à faster-whisper.

    Cette fonction est *synchrone* et bloquante : à lancer dans un thread
    dédié (géré par `URLStreamWorker`).
    """
    import yt_dlp
    import subprocess
    import tempfile

    out_path = Path(out_path)
    out_path.mkdir(parents=True, exist_ok=True)

    # yt-dlp sort du bestaudio. -o - streame sur stdout, ffmpeg segmente.
    # On préfère un fichier intermedio + boucle ffmpeg : plus stable pour
    # les flux live qui restent ouverts.
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src.%(ext)s"
        ydl_opts = {
            "format": "bestaudio/best",
            "outtmpl": str(src),
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
            # yt-dlp nomme avec l'extension effective.
            downloaded = next(Path(tmp).glob("src.*"), None)
            if not downloaded:
                raise RuntimeError("yt-dlp n'a rien téléchargé.")
        # Segmentation en WAV de `chunk_seconds` via ffmpeg.
        idx = 0
        proc = subprocess.Popen(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error",
                "-i", str(downloaded),
                "-f", "segment", "-segment_time", str(chunk_seconds),
                "-ac", "1", "-ar", "16000",            # mono 16kHz = format Whisper
                "-acodec", "pcm_s16le",
                str(out_path / "chunk_%05d.wav"),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        )
        proc.wait()
        if proc.returncode != 0:
            err = (proc.stderr.read() or b"").decode("utf-8", "replace")[:300]
            raise RuntimeError(f"ffmpeg a échoué : {err}")
        if on_chunk:
            for f in sorted(out_path.glob("chunk_*.wav")):
                on_chunk(f, chunk_seconds)
                f.unlink(missing_ok=True)


# ------------------------------------------------------------- file de segts
# Segments qui arrivent sur le réseau (utilisés par les 3 sources) : on
# normalise en `Segment` puis on appelle `session.add_segment(...)`. La
# traduction + TTS est faite par l'orchestrateur (voir server.py).
# Pas de logique métier ici : c'est juste un dataclass + la table de sessions.
class SessionStore:
    """Table de sessions en mémoire, LRU borné par MAX_SESSIONS."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._order: deque = deque()             # ordre d'accès
        self._lock = threading.Lock()

    def get(self, sid: str) -> Optional[Session]:
        with self._lock:
            s = self._sessions.get(sid)
            if s is not None:
                self._order.remove(sid) if sid in self._order else None
                self._order.append(sid)
            return s

    def create(self, source: str, src_lang: str, tgt_lang: str, title: str = "") -> Session:
        sid = uuid.uuid4().hex[:12]
        s = Session(id=sid, source=source, src_lang=src_lang, tgt_lang=tgt_lang, title=title)
        with self._lock:
            self._sessions[sid] = s
            self._order.append(sid)
            # LRU : on coupe la plus ancienne si on dépasse la borne.
            while len(self._order) > MAX_SESSIONS:
                old = self._order.popleft()
                self._sessions.pop(old, None)
        return s

    def list_sessions(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": s.id,
                    "source": s.source,
                    "src_lang": s.src_lang,
                    "tgt_lang": s.tgt_lang,
                    "title": s.title,
                    "active": s.active,
                    "started_at": s.started_at,
                    "segments": len(s.segments),
                }
                for s in (self._sessions.get(sid) for sid in self._order)
                if s is not None
            ]

    def close(self, sid: str) -> bool:
        s = self.get(sid)
        if s is None:
            return False
        s.close()
        return True


# Singleton partagé par les endpoints.
SESSIONS = SessionStore()


# =========================================================== téléchargement
# Téléchargement / listing des quantizations Hy-MT2 depuis Hugging Face.
#
# Pourquoi dans translate.py ? C'est le seul endroit du projet qui a besoin
# de connaître l'ID du repo Hy-MT2 sur HF. Si tu changes de modèle de
# traduction, c'est ici que ça se passe.
#
# Utilisation en CLI :
#     python -c "from translate import list_hymt2_quants; list_hymt2_quants()"
#     python -c "from translate import download_hymt2; download_hymt2('Q4_K_M')"
# ou via le script dédié `download_hymt2.py` à la racine (voir README).

# Repo par défaut. Le repo 7B existe aussi (tencent/Hy-MT2-7B-GGUF) ; on le
# propose en option dans la CLI mais on n'encombre pas l'UI.
HYMT2_REPOS = {
    "1.8B": "tencent/Hy-MT2-1.8B-GGUF",
    "7B":   "tencent/Hy-MT2-7B-GGUF",
}


def _hf_list_files(repo_id: str) -> list[dict]:
    """Liste les fichiers d'un repo HF via l'API publique (pas de token requis
    pour les fichiers publics).

    Renvoie [] en cas d'échec (réseau, repo inexistant) — l'appelant décide
    d'afficher un message d'erreur convivial plutôt qu'un stack trace.
    """
    try:
        r = requests.get(
            f"https://huggingface.co/api/models/{repo_id}",
            headers={"Accept": "application/json"},
            timeout=15,
        )
        if r.status_code != 200:
            return []
        data = r.json()
        # Format HF : {"siblings": [{"rfilename": "..."}, ...]}
        return data.get("siblings", [])
    except requests.RequestException:
        return []


def list_hymt2_quants(size: str = "1.8B") -> list[dict]:
    """Liste les quantizations GGUF disponibles pour Hy-MT2.

    Renvoie [{"name": "Hy-MT2-1.8B-Q4_K_M.gguf",
              "quant": "Q4_K_M", "size_bytes": 1234567890}, ...].
    Les quantizations sont filtrées pour ne garder que les fichiers
    *.gguf de premier niveau (le repo peut contenir des .md, .json, etc.).
    """
    repo = HYMT2_REPOS.get(size, HYMT2_REPOS["1.8B"])
    files = _hf_list_files(repo)
    out: list[dict] = []
    # Les quantizations GGUF courantes : Q2_K, Q3_K_S/M/L, Q4_0, Q4_1,
    # Q4_K_S/M, Q5_0, Q5_1, Q5_K_S/M, Q6_K, Q8_0, F16, F32.
    quant_re = re.compile(r"-([A-Za-z0-9_]+)\.gguf$", re.IGNORECASE)
    for f in files:
        name = f.get("rfilename", "")
        if not name.lower().endswith(".gguf"):
            continue
        m = quant_re.search(name)
        if not m:
            continue
        out.append({
            "name": name,
            "quant": m.group(1),
            "size_bytes": None,                # l'API ne donne pas la taille ici
        })
    out.sort(key=lambda x: x["quant"])
    return out


def _hf_get_size(repo_id: str, filename: str) -> Optional[int]:
    """Récupère la taille en octets d'un fichier via l'API HF (HEAD)."""
    try:
        r = requests.head(
            f"https://huggingface.co/{repo_id}/resolve/main/{filename}",
            allow_redirects=True, timeout=10,
        )
        if r.status_code == 200 and r.headers.get("Content-Length"):
            return int(r.headers["Content-Length"])
    except requests.RequestException:
        pass
    return None


def download_hymt2(
    quant: str = "Q4_K_M",
    out_dir: str | os.PathLike = "./models",
    size: str = "1.8B",
    progress: bool = True,
) -> str:
    """Télécharge une quantization Hy-MT2 depuis Hugging Face.

    Args:
        quant: suffixe de quantization (ex. "Q4_K_M", "Q5_K_M", "Q8_0").
        out_dir: dossier de destination (créé si absent).
        size: "1.8B" ou "7B".
        progress: affiche une barre de progression dans le terminal.

    Returns:
        Chemin absolu du fichier .gguf téléchargé.
    """
    # Import paresseux : huggingface_hub est lourd (~50 Mo de deps) et
    # n'est utile qu'au téléchargement initial. On évite de l'importer
    # pour les utilisateurs qui ne font que traduire.
    from huggingface_hub import hf_hub_download

    repo = HYMT2_REPOS.get(size, HYMT2_REPOS["1.8B"])
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    quants = list_hymt2_quants(size)
    match = [q for q in quants if q["quant"].upper() == quant.upper()]
    if not match:
        available = ", ".join(q["quant"] for q in quants) or "(aucune)"
        raise ValueError(
            f"Quantization {quant!r} introuvable pour {repo}.\n"
            f"Disponibles : {available}"
        )
    filename = match[0]["name"]
    log.info("[HF] téléchargement %s/%s -> %s", repo, filename, out)
    if progress:
        print(f"Téléchargement de {repo}/{filename}…")
    path = hf_hub_download(
        repo_id=repo,
        filename=filename,
        local_dir=str(out),
        local_dir_use_symlinks=False,
    )
    if progress:
        size_bytes = Path(path).stat().st_size
        size_mb = size_bytes / (1024 * 1024)
        print(f"✓ Téléchargé : {path}  ({size_mb:.1f} Mo)")
    return str(Path(path).resolve())


# Petit utilitaire CLI pratique : lance depuis un terminal.
def _cli() -> None:  # pragma: no cover — point d'entrée manuel
    import argparse
    p = argparse.ArgumentParser(
        description="Télécharge une quantization Hy-MT2 depuis Hugging Face.",
    )
    p.add_argument("--list", action="store_true", help="liste les quants dispo")
    p.add_argument("--size", choices=list(HYMT2_REPOS), default="1.8B")
    p.add_argument("--quant", default="Q4_K_M",
                   help="quantization (Q4_K_M, Q5_K_M, Q8_0, F16…)")
    p.add_argument("--out-dir", default="./models",
                   help="dossier de destination")
    args = p.parse_args()
    if args.list:
        print(f"Repo : {HYMT2_REPOS[args.size]}")
        for q in list_hymt2_quants(args.size):
            print(f"  - {q['quant']:<10}  {q['name']}")
    else:
        download_hymt2(args.quant, args.out_dir, args.size)


if __name__ == "__main__":  # pragma: no cover
    _cli()
