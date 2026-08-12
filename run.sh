#!/usr/bin/env bash
# ============================================================
#  Lancement de l'Assistant Vocal FR (Linux / macOS)
#  - demarre le serveur
#  - ouvre la page web dans le navigateur
# ============================================================
set -e
cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
    echo "[ERREUR] Environnement non trouve. Lance d'abord ./install.sh"
    exit 1
fi

HOST=127.0.0.1
PORT=8500
URL="http://$HOST:$PORT"

echo
echo "=== Assistant Vocal FR ==="
echo "Serveur : $URL"
echo
echo "RAPPEL : demarre ton serveur LLM (LM Studio ou llama.cpp) avant d'utiliser le chat."
echo "Pour arreter : Ctrl+C."
echo

# --- Ouvrir le navigateur apres un court delai (en arriere-plan) ---
(
  sleep 3
  if command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"
  elif command -v open >/dev/null 2>&1; then open "$URL"
  fi
) >/dev/null 2>&1 &

# --- Demarrer le serveur (bloquant) ---
exec ./.venv/bin/python -m uvicorn server:app --host "$HOST" --port "$PORT"
