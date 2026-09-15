#!/bin/bash
# Is Real-Debrid's API talking to us again?
# The website staying up while the API is refused means we are rate-limited,
# not blocked — the fix is to wait, not to change anything.
site=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 12 https://real-debrid.com/ 2>/dev/null)
api=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 12 https://api.real-debrid.com/rest/1.0/time 2>/dev/null)
echo "website: ${site:-000}   api: ${api:-000}"
if [ "$api" = "200" ]; then
  echo "API is back — 'docker compose up' is safe."
elif [ "$site" = "200" ]; then
  echo "Still rate-limited. Wait and run this again; don't restart the stack yet."
else
  echo "Both down — that's a network problem, not a rate limit."
fi
