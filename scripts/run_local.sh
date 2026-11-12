#!/usr/bin/env bash
# Runs the Python API (port 8080) and the console (port 3000) on this machine. No AWS, no Vercel.
#
#   ./scripts/run_local.sh
#
# First run: creates .venv, installs both sides, and writes .env and frontend/.env.local (both
# git-ignored) with a freshly generated API key shared between the two. It never overwrites an
# existing .env or .env.local, so your edits are safe. Stop with Ctrl-C.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
API_PORT="${API_PORT:-8080}"
CONSOLE_PORT="${CONSOLE_PORT:-3000}"

command -v python3 >/dev/null || { echo "python3 (3.11+) is required" >&2; exit 1; }
command -v node >/dev/null || { echo "Node 22+ is required" >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Python 3.11 or newer is required" >&2; exit 1; }

# --- Python side ---------------------------------------------------------------------------
[ -d .venv ] || python3 -m venv .venv
STAMP=".venv/.requirements.sha"
WANT="$(python3 -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ]; then
  .venv/bin/pip install -q -r requirements.txt
  echo "$WANT" > "$STAMP"
fi

# --- Settings (created once, never overwritten) -----------------------------------------------
if [ ! -f .env ]; then
  KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
  cp .env.example .env
  printf '\n# Added by scripts/run_local.sh: the key the console uses (role "client").\nAPI_KEYS=console:client:%s\n' "$KEY" >> .env
  echo "Created .env. Edit it to add your keys and service addresses."
fi
if [ ! -f frontend/.env.local ]; then
  KEY="$(sed -n 's/^API_KEYS=console:client:\([^,]*\).*/\1/p' .env | head -1)"
  if [ -z "$KEY" ]; then
    echo "Could not find the 'console:client:' key in .env's API_KEYS. Set AGENT_COMPANY_API_KEY in frontend/.env.local yourself." >&2
  fi
  printf 'AGENT_COMPANY_API_URL=http://localhost:%s\nAGENT_COMPANY_API_KEY=%s\n' "$API_PORT" "$KEY" > frontend/.env.local
  echo "Created frontend/.env.local."
fi

# Tell the person what is missing rather than letting every request fail quietly.
val() { sed -n "s/^$1=\(.*\)/\1/p" .env | tail -1; }
[ -n "$(val ANTHROPIC_API_KEY)" ] || echo "NOTE: ANTHROPIC_API_KEY is empty in .env: HR, Export and Legal answers will be withheld for a person (the verifier cannot run)."
[ -n "$(val TYPESAFE_API_KEY)" ]  || echo "NOTE: TYPESAFE_API_KEY is empty in .env: routing will fall back to Claude, or to a person."
grep -q 'your-.*\.example' .env && echo "NOTE: .env still has the example service addresses (your-*.example): those departments will report 'unreachable' until you set them."

# --- Console side ----------------------------------------------------------------------------
if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm install --no-audit --no-fund)
fi

# --- Run both, stop both ---------------------------------------------------------------------
PIDS=()
cleanup() { trap - INT TERM EXIT; kill "${PIDS[@]}" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup INT TERM EXIT

set -a; . ./.env; set +a
.venv/bin/uvicorn api:app --host 127.0.0.1 --port "$API_PORT" &
PIDS+=($!)
(cd frontend && exec npm run dev -- --port "$CONSOLE_PORT") &
PIDS+=($!)

echo
echo "API:     http://localhost:$API_PORT/ready"
echo "Console: http://localhost:$CONSOLE_PORT"
echo "Ctrl-C stops both."
wait -n "${PIDS[@]}"   # if either one dies, stop the other too
