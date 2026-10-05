#!/usr/bin/env bash
# Lanza una corrida del crew. Todo queda en output/<fecha_hora>/ (ignorada por Git):
# los informes, _guardrail.log, corrida.json y el log completo de CrewAI (corrida.log).
# Uso, dentro de tmux:   bash correr.sh
set -uo pipefail
cd "$(dirname "$0")"
export VDI_RUN_DIR="output/$(date +%F_%H%M)"
mkdir -p "$VDI_RUN_DIR"
PYTHONUNBUFFERED=1 uv run kickoff 2>&1 | tee "$VDI_RUN_DIR/corrida.log"
