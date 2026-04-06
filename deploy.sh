#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_ROOT"

log() {
  printf '[AIProxy] %s\n' "$1"
}

find_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  echo "Python 3 is not installed or not available in PATH." >&2
  exit 1
}

PYTHON_CMD="$(find_python)"
VENV_PATH="$PROJECT_ROOT/.venv"

if [ ! -d "$VENV_PATH" ]; then
  log "Creating virtual environment..."
  "$PYTHON_CMD" -m venv "$VENV_PATH"
fi

log "Activating virtual environment..."
source "$VENV_PATH/bin/activate"

log "Upgrading pip..."
python -m pip install --upgrade pip

log "Installing dependencies..."
python -m pip install -r requirements.txt

if [ ! -f ".env" ]; then
  log "Creating .env from .env.example..."
  cp .env.example .env
fi

log "Starting AIProxy services..."
python main.py
