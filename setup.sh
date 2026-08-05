#!/usr/bin/env bash
set -euo pipefail

# On macOS, install Python if needed using Homebrew.
if [[ "$(uname)" == "Darwin" ]]; then
  if ! command -v python3 >/dev/null 2>&1 && ! command -v python >/dev/null 2>&1; then
    echo "Python not found. Installing Python via Homebrew..."
    if ! command -v brew >/dev/null 2>&1; then
      echo "Homebrew not found. Installing Homebrew first..."
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
      echo 'eval "$('/opt/homebrew/bin/brew' shellenv)"' >> ~/.zprofile
      eval "$('/opt/homebrew/bin/brew' shellenv)"
    fi
    brew install python
  fi
fi

# Prefer python3 when available.
if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD=python3
else
  PYTHON_CMD=python
fi

# Create a virtual environment in .venv
$PYTHON_CMD -m venv .venv

# Activate the virtual environment
# For bash/zsh:
source .venv/bin/activate

# Install the project dependencies
pip install -r requirements.txt

# Start the FastAPI application
uvicorn app.main:app --reload
