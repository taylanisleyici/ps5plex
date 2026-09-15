#!/bin/bash
# Double-click this in Finder to start the server. Ctrl+C (or closing this
# window) stops it. Nothing is left running afterwards.
cd "$(dirname "$0")" || exit 1

if [ ! -f .env ]; then
  echo "No .env yet. Copy .env.example to .env and fill in your tokens:"
  echo "  cp .env.example .env"
  read -r -p "Press return to close."
  exit 1
fi

if ! docker info >/dev/null 2>&1; then
  echo "Docker isn't running. Start Docker Desktop, then try again."
  read -r -p "Press return to close."
  exit 1
fi

echo "Plex will be at http://localhost:32400/web once it boots."
echo "Press Ctrl+C to stop."
echo
docker compose up
