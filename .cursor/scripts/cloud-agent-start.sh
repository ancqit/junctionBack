#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

if [[ ! -f .env ]]; then
  cp .cursor/dev.env .env
fi

MONGO_DATA_DIR="$ROOT_DIR/.mongodb-data"
MONGO_LOG="$MONGO_DATA_DIR/mongod.log"
mkdir -p "$MONGO_DATA_DIR"

mongo_ready() {
  mongosh --quiet --eval 'db.runCommand({ ping: 1 })' mongodb://127.0.0.1:27017 >/dev/null 2>&1
}

if ! mongo_ready; then
  mongod \
    --dbpath "$MONGO_DATA_DIR" \
    --bind_ip 127.0.0.1 \
    --port 27017 \
    --nounixsocket \
    --fork \
    --logpath "$MONGO_LOG"
fi

for _ in $(seq 1 30); do
  if mongo_ready; then
    exit 0
  fi
  sleep 1
done

echo "MongoDB failed to become ready within 30 seconds" >&2
exit 1
