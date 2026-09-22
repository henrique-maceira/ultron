#!/usr/bin/env bash
# Atualiza o Ultron na máquina (Linux / macOS / WSL).
# Puxa a branch de desenvolvimento e, só se houver mudanças, reconstrói o container.
set -euo pipefail

BRANCH="claude/virtual-task-assistant-mdop7k"

cd "$(dirname "$0")/.."

echo "[Ultron] Buscando atualizações em '$BRANCH'..."
git fetch origin
git checkout "$BRANCH"

before="$(git rev-parse HEAD)"
git pull --ff-only origin "$BRANCH"
after="$(git rev-parse HEAD)"

if [ "$before" != "$after" ]; then
  echo "[Ultron] Novidades detectadas ($before -> $after). Reconstruindo container..."
  docker compose up -d --build
  docker image prune -f
  echo "[Ultron] Atualizado e no ar."
else
  echo "[Ultron] Já está na versão mais recente. Nada a fazer."
fi
