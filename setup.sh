#!/usr/bin/env bash
set -euo pipefail

# Check if Docker and docker-compose are installed
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker is not installed. Please install Docker Desktop from https://www.docker.com/products/docker-desktop"
  exit 1
fi

if ! command -v docker-compose >/dev/null 2>&1; then
  echo "ERROR: docker-compose is not installed. Please ensure Docker Desktop includes compose or install it separately."
  exit 1
fi

# If an .env file is not present, create it from the example
if [ ! -f .env ]; then
  if [ -f .env.example ]; then
    cp .env.example .env
    echo "✓ Created .env from .env.example"
  fi

  # Ensure POSTGRES_* variables exist in .env so docker-compose can pick them up
  echo "✓ Setting up environment variables for docker-compose..."
  if ! grep -q '^POSTGRES_USER=' .env; then
    echo "POSTGRES_USER=user" >> .env
  fi
  if ! grep -q '^POSTGRES_PASSWORD=' .env; then
    echo "POSTGRES_PASSWORD=password" >> .env
  fi
  if ! grep -q '^POSTGRES_DB=' .env; then
    echo "POSTGRES_DB=forseti" >> .env
  fi
fi

echo ""
echo "✓ Setup complete!"
echo ""
echo "To start the application with Docker, run:"
echo "  docker-compose up"
echo ""
echo "The API will be available at http://localhost:8000"
echo "The database will be available at localhost:5432"
