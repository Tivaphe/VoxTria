#!/usr/bin/env python3
"""
Clonage de voix 100% LOCAL pour Supertonic, via supertonic.embed.

⚠️ NÉCESSITE UN GPU NVIDIA (4 Go+ VRAM, CUDA). Ne fonctionne pas sur CPU seul.
   -> à lancer sur une machine avec GPU, ou sur Google Colab (GPU T4 gratuit).

Ce script :
  1. clone le repo supertonic.embed (s'il n'est pas déjà là)
  2. installe ses dépendances
  3. télécharge les modèles ONNX + voice_styles depuis Supertone/supertonic-2
  4. lance l'optimisation à partir de ton WAV (3-10 s, un seul locuteur)
  5. copie le JSON de voix résultant dans ./voices/ (utilisable par l'assistant)

Usage :
  python clone_voice.py --wav mon_echantillon.wav --name ma_voix
  python clone_voice.py --wav mon_echantillon.wav --name ma_voix --steps 3000

Puis dans l'assistant : Paramètres -> la voix "ma_voix" sera disponible
(ou charge ./voices/ma_voix.json via le bouton d'upload).
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import wave
from pathlib import Path

BASE = Path(__file__).parent
EMBED_DIR = BASE / "_supertonic_embed"
VOICES_DIR = BASE / "voices"
REPO_URL = "https://github.com/kdrkdrkdr/supertonic.embed.git"
# Révision épinglée (commit complet) : ce script EXÉCUTE du code tiers
# (optimize_style.py). Sans épinglage, ce code peut changer à tout moment
# côté amont. Pour mettre à jour : vérifier les changements amont, puis
# remplacer le SHA ci-dessous sciemment (ou passer --ref en connaissance).
REPO_REF = "ec5325e9e2a7d4cc51b0160428a2c299db7d5725"


SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def check_wav(path: Path) -> None:
    """Contrôle basique de l'échantillon (la doc exige 3-10 s, un locuteur)."""
    try:
        with wave.open(str(path), "rb") as w:
            frames, rate, channels = w.getnframes(), w.getframerate(), w.getnchannels()
    except wave.Error as e:
        print(f"[ERREUR] Fichier WAV illisible ({e}). Convertis-le en PCM 16 bits :")
        print(f"    ffmpeg -i {path} -ac 1 -ar 44100 -c:a pcm_s16le sortie.wav")
        sys.exit(1)
    duration = frames / float(rate or 1)
    print(f"[wav] {duration:.1f}s, {rate} Hz, {channels} canal/canaux")
    if duration < 2:
        print("[ERREUR] Échantillon trop court (< 2 s). Vise 3 à 10 secondes.")
        sys.exit(1)
    if duration > 30:
        print("[!] Échantillon long (> 30 s) : seul le début est réellement utile.")
    if channels > 1:
        print("[!] Audio non mono : un enregistrement mono donne de meilleurs résultats.")


def run(cmd, cwd=None):
    print(f"$ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=cwd)


def check_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def ensure_repo(ref: str = REPO_REF):
    if not EMBED_DIR.exists():
        run(["git", "clone", REPO_URL, str(EMBED_DIR)])
    # Positionne la copie locale sur la révision épinglée (idempotent : un
    # clone existant mais sur un autre état est ramené sur `ref`).
    run(["git", "fetch", "origin"], cwd=str(EMBED_DIR))
    run(["git", "checkout", ref], cwd=str(EMBED_DIR))


def ensure_deps(skip: bool = False):
    """Installe les dépendances de supertonic.embed.

    ⚠️ Ces paquets (torch, onnxruntime…) peuvent écraser les versions du venv
    de VoxTria. Utilise --skip-deps si tu gères l'environnement toi-même, ou
    lance ce script dans un venv dédié.
    """
    if skip:
        print("[skip] installation des dépendances ignorée (--skip-deps)")
        return
    req = EMBED_DIR / "requirements.txt"
    if req.exists():
        run([sys.executable, "-m", "pip", "install", "-r", str(req)])
    # utilitaires nécessaires au téléchargement des modèles
    run([sys.executable, "-m", "pip", "install", "huggingface_hub"])


def ensure_models():
    """Télécharge onnx/ et voice_styles/ depuis Supertone/supertonic-2."""
    onnx_dir = EMBED_DIR / "onnx"
    styles_dir = EMBED_DIR / "voice_styles"
    if onnx_dir.exists() and styles_dir.exists():
        print("[ok] modèles déjà téléchargés")
        return
    from huggingface_hub import snapshot_download
    print("[..] téléchargement des modèles Supertone/supertonic-2 (onnx + voice_styles)")
    local = snapshot_download(
        repo_id="Supertone/supertonic-2",
        allow_patterns=["onnx/*", "voice_styles/*"],
    )
    local = Path(local)
    if (local / "onnx").exists():
        shutil.copytree(local / "onnx", onnx_dir, dirs_exist_ok=True)
    if (local / "voice_styles").exists():
        shutil.copytree(local / "voice_styles", styles_dir, dirs_exist_ok=True)


def make_config(wav_path: Path, name: str, steps: int) -> Path:
    """Crée configs/<name>.json attendu par optimize_style.py."""
    cfg_dir = EMBED_DIR / "configs"
    wav_dir = EMBED_DIR / "wavs"
    cfg_dir.mkdir(exist_ok=True)
    wav_dir.mkdir(exist_ok=True)
    # copie le wav dans wavs/
    dest_wav = wav_dir / f"{name}.wav"
    shutil.copy(wav_path, dest_wav)
    cfg = {
        "name": name,
        "target_wav": f"wavs/{name}.wav",
        "reference_style": "auto",
        "seed": 42,
        "lr": 2e-4,
        "num_steps": steps,
        "total_step": 5,
        "speed": 1.05,
        "save_every": 100,
    }
    cfg_path = cfg_dir / f"{name}.json"
    cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg_path


def find_result_json(name: str) -> Path | None:
    """Cherche le JSON de style optimisé (dernier checkpoint)."""
    # voice_styles/<name>.json (final) ou logs/<name>/<name>_XXXXXXXX.json
    candidates = []
    final = EMBED_DIR / "voice_styles" / f"{name}.json"
    if final.exists():
        candidates.append(final)
    logs = EMBED_DIR / "logs" / name
    if logs.exists():
        # Tri par date de modification : un tri lexicographique classait
        # "_900" après "_1000" et sélectionnait le mauvais checkpoint.
        ckpts = sorted(logs.glob(f"{name}_*.json"), key=lambda p: p.stat().st_mtime)
        candidates.extend(ckpts)
    return candidates[-1] if candidates else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True, help="échantillon vocal WAV (3-10 s, un locuteur)")
    ap.add_argument("--name", default="ma_voix", help="nom de la voix clonée")
    ap.add_argument("--steps", type=int, default=3000, help="étapes max d'optimisation")
    ap.add_argument("--ref", default=REPO_REF, help="révision git de supertonic.embed")
    ap.add_argument("--skip-deps", action="store_true",
                    help="ne pas installer les dépendances (venv géré à la main)")
    ap.add_argument("-y", "--yes", action="store_true",
                    help="ne pas poser de question (mode non interactif / Colab)")
    args = ap.parse_args()

    if not SAFE_NAME.match(args.name):
        print(f"[ERREUR] Nom de voix invalide : {args.name!r} "
              "(lettres, chiffres, '_' et '-' uniquement)")
        sys.exit(1)

    wav = Path(args.wav)
    if not wav.exists():
        print(f"[ERREUR] WAV introuvable : {wav}")
        sys.exit(1)
    check_wav(wav)

    if not check_gpu():
        print("\n" + "="*60)
        print("⚠️  AUCUN GPU CUDA détecté.")
        print("supertonic.embed exige un GPU NVIDIA (4 Go+ VRAM).")
        print("Options :")
        print("  - lance ce script sur une machine avec GPU NVIDIA")
        print("  - ou utilise Google Colab (runtime GPU T4 gratuit)")
        print("="*60)
        # On laisse l'utilisateur forcer s'il veut tenter quand même
        if not args.yes:
            if input("Continuer quand même (très lent / risque d'échec) ? [o/N] ").lower() != "o":
                sys.exit(1)

    VOICES_DIR.mkdir(exist_ok=True)

    print("\n=== 1/5 Récupération du repo supertonic.embed ===")
    ensure_repo(args.ref)
    print("\n=== 2/5 Installation des dépendances ===")
    ensure_deps(args.skip_deps)
    print("\n=== 3/5 Téléchargement des modèles ===")
    ensure_models()
    print("\n=== 4/5 Préparation de la config ===")
    make_config(wav, args.name, args.steps)
    print("\n=== 5/5 Optimisation (clonage)… ça peut prendre plusieurs minutes ===")
    run([sys.executable, "optimize_style.py", args.name], cwd=str(EMBED_DIR))

    result = find_result_json(args.name)
    if not result:
        print("[ERREUR] JSON de voix non trouvé après optimisation.")
        sys.exit(1)
    dest = VOICES_DIR / f"{args.name}.json"
    shutil.copy(result, dest)
    print("\n" + "="*60)
    print(f"✅ Voix clonée prête : {dest}")
    print("Dans l'assistant : Paramètres -> Voix clonée -> charge ce .json,")
    print("ou sélectionne la voix dans la liste après rechargement.")
    print("="*60)


if __name__ == "__main__":
    main()
