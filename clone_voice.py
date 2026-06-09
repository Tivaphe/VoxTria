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
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
EMBED_DIR = BASE / "_supertonic_embed"
VOICES_DIR = BASE / "voices"
REPO_URL = "https://github.com/kdrkdrkdr/supertonic.embed.git"


def run(cmd, cwd=None):
    print(f"$ {' '.join(cmd)}")
    subprocess.check_call(cmd, cwd=cwd)


def check_gpu() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def ensure_repo():
    if EMBED_DIR.exists():
        print(f"[ok] repo déjà présent : {EMBED_DIR}")
        return
    run(["git", "clone", REPO_URL, str(EMBED_DIR)])


def ensure_deps():
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
        ckpts = sorted(logs.glob(f"{name}_*.json"))
        candidates.extend(ckpts)
    return candidates[-1] if candidates else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", required=True, help="échantillon vocal WAV (3-10 s, un locuteur)")
    ap.add_argument("--name", default="ma_voix", help="nom de la voix clonée")
    ap.add_argument("--steps", type=int, default=3000, help="étapes max d'optimisation")
    args = ap.parse_args()

    wav = Path(args.wav)
    if not wav.exists():
        print(f"[ERREUR] WAV introuvable : {wav}")
        sys.exit(1)

    if not check_gpu():
        print("\n" + "="*60)
        print("⚠️  AUCUN GPU CUDA détecté.")
        print("supertonic.embed exige un GPU NVIDIA (4 Go+ VRAM).")
        print("Options :")
        print("  - lance ce script sur une machine avec GPU NVIDIA")
        print("  - ou utilise Google Colab (runtime GPU T4 gratuit)")
        print("="*60)
        # On laisse l'utilisateur forcer s'il veut tenter quand même
        if input("Continuer quand même (très lent / risque d'échec) ? [o/N] ").lower() != "o":
            sys.exit(1)

    VOICES_DIR.mkdir(exist_ok=True)

    print("\n=== 1/5 Récupération du repo supertonic.embed ===")
    ensure_repo()
    print("\n=== 2/5 Installation des dépendances ===")
    ensure_deps()
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
    print(f"ou sélectionne la voix dans la liste après rechargement.")
    print("="*60)


if __name__ == "__main__":
    main()
