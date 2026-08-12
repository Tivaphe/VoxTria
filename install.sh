#!/usr/bin/env bash
# ============================================================
#  Installation de l'Assistant Vocal FR (Linux / macOS)
#  - cree un environnement virtuel .venv
#  - installe toutes les dependances
# ============================================================
set -e
cd "$(dirname "$0")"

echo
echo "=== Assistant Vocal FR - Installation ==="
echo

# --- Choisir python ---
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "[ERREUR] Python introuvable. Installe Python 3.10+."
    exit 1
fi

echo "[1/3] Creation de l'environnement virtuel (.venv)..."
if [ ! -d ".venv" ]; then
    "$PY" -m venv .venv
else
    echo "    .venv existe deja, on reutilise."
fi

echo "[2/3] Mise a jour de pip..."
./.venv/bin/python -m pip install --upgrade pip

echo "[3/3] Installation des dependances (peut prendre quelques minutes)..."
./.venv/bin/python -m pip install -r requirements.txt

# --- Config locale (non versionnee, peut contenir une cle API) ---
if [ ! -f "config.json" ]; then
    cp config.example.json config.json
    echo "    config.json cree depuis config.example.json"
fi

echo
echo "=== Installation terminee avec succes ! ==="
echo "Lance maintenant ./run.sh pour demarrer l'assistant."
echo
echo "NOTE: pour le micro, ffmpeg est recommande."
echo "      Ubuntu/Debian : sudo apt install ffmpeg"
echo "      macOS (brew)  : brew install ffmpeg"
echo
