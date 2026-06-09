#!/bin/bash
# Wrapper de lancement Stack #2/#3. Attend IB Gateway (port 4002) AVANT de démarrer le bot.
# Utilisé par le launchd. Logs → ~/Documents/AI-OS/Logs/stack2-orb.log
set -u
cd "$(dirname "$0")"
PORT=4002
echo "$(date) — attente IB Gateway port $PORT…"
for i in $(seq 1 60); do
  if nc -z -G 2 127.0.0.1 $PORT 2>/dev/null; then
    echo "$(date) — Gateway up, démarrage bot."
    exec ./venv/bin/python main.py
  fi
  sleep 10
done
echo "$(date) — Gateway pas disponible après 10 min, abandon."
exit 1
