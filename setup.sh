#!/usr/bin/env bash
set -euo pipefail

# If an .env file is not present, create it from the example so the user can edit it
if [ ! -f .env ] && [ -f .env.example ]; then
  cp .env.example .env
  echo "Created .env from .env.example — updating it with docker-compose-friendly values."

  # Ensure POSTGRES_* variables exist in .env so docker-compose can pick them up
  if ! grep -q '^POSTGRES_USER=' .env; then
    echo "POSTGRES_USER=user" >> .env
  fi
  if ! grep -q '^POSTGRES_PASSWORD=' .env; then
    echo "POSTGRES_PASSWORD=password" >> .env
  fi
  if ! grep -q '^POSTGRES_DB=' .env; then
    echo "POSTGRES_DB=forseti" >> .env
  fi

  # Load the file values and construct a DATABASE_URL that points at localhost:5432
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a

  NEW_DB_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5432/${POSTGRES_DB}"

  # Update or append DATABASE_URL in .env in a portable way
  if grep -q '^DATABASE_URL=' .env; then
    awk -v url="$NEW_DB_URL" 'BEGIN{FS=OFS="="} /^DATABASE_URL=/{print "DATABASE_URL=" url; next} {print}' .env > .env.tmp && mv .env.tmp .env
  else
    echo "DATABASE_URL=${NEW_DB_URL}" >> .env
  fi
fi

# Load environment variables from .env (simple KEY=VALUE pairs)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# Warn if DATABASE_URL is not configured or still contains placeholders
if [ -z "${DATABASE_URL:-}" ] || [[ "${DATABASE_URL}" == *"user:password"* ]]; then
  echo "WARNING: DATABASE_URL is not set or uses placeholder credentials."
  echo "Edit .env before proceeding if you intend to connect to a PostgreSQL database."
fi

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
