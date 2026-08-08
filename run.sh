#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ ! -f .env ]]; then
  echo "Missing .env. Copy .env.example to .env and add your Atlas connection string."
  exit 1
fi
docker compose up --build
