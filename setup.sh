#!/usr/bin/env bash
# ----------------------------------------------------------------------- #
# Chef development environment bootstrap.                                   #
# Creates a virtualenv, installs dependencies, and seeds the .env file.    #
#                                                                           #
# Usage:  source setup.sh    (sourcing keeps the venv active in your shell) #
#         ./setup.sh         (runs setup; activate manually afterwards)     #
# ----------------------------------------------------------------------- #
set -euo pipefail

VENV_DIR=".venv"
PYTHON_BIN="${PYTHON_BIN:-python3}"

echo "==> Checking Python version (3.11+ required)"
"$PYTHON_BIN" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || {
    echo "ERROR: Python 3.11+ is required (found: $($PYTHON_BIN --version 2>&1))" >&2
    exit 1
}

if [ ! -d "$VENV_DIR" ]; then
    echo "==> Creating virtual environment in $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
else
    echo "==> Reusing existing virtual environment in $VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

echo "==> Installing dependencies"
pip install --quiet --upgrade pip
pip install --quiet -r requirements-dev.txt

if [ ! -f ".env" ]; then
    echo "==> Seeding .env from .env.example (fill in your API keys!)"
    cp .env.example .env
else
    echo "==> .env already exists; leaving it untouched"
fi

mkdir -p dummy_workspace

echo ""
echo "Setup complete."
echo "  Activate the venv:   source $VENV_DIR/bin/activate"
echo "  Run the tests:       pytest"
echo "  Run sandboxed:       docker compose run --rm chef"
