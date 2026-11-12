#!/usr/bin/env bash
# Runs the ticketing service (github.com/niteshnankani-svg/Multi_agent_ticketing_system) on this machine.
# This is the "Backend" department's service: POST /ticket.
#
#   ./scripts/run_ticketing_local.sh
#
# First run: clones the repo next to this one, creates a virtualenv, installs only what the API imports (not its
# training libraries), and creates its .env. If TYPESAFE_API_KEY and ANTHROPIC_API_KEY are already set in this
# project's .env they are copied across; otherwise put them in <that folder>/.env and run this again.
# It never overwrites an existing .env. Stop with Ctrl-C.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TICKETING_DIR="${TICKETING_DIR:-$ROOT/../Multi_agent_ticketing_system}"
TICKETING_PORT="${TICKETING_PORT:-8103}"

PY=""
for c in python3.12 python3.11 python3.13; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Ticketing needs Python 3.11 or newer (found only: $(python3 --version 2>&1)). On a Mac: brew install python@3.12" >&2; exit 1; }

port_busy() { python3 -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError: sys.exit(0)
sys.exit(1)' "$1"; }
if port_busy "$TICKETING_PORT"; then
  echo "Port $TICKETING_PORT is already in use. Use another one, for example:  TICKETING_PORT=8104 ./scripts/run_ticketing_local.sh" >&2
  exit 1
fi

[ -d "$TICKETING_DIR/.git" ] || git clone https://github.com/niteshnankani-svg/Multi_agent_ticketing_system "$TICKETING_DIR"
cd "$TICKETING_DIR"

# Its requirements.txt pins "torch==2.12.0+cpu" with a PyTorch index, which exists only for Linux: on a Mac it cannot install.
# The API only needs the packages below, so install just those (sentence-transformers brings the right torch for this machine).
# numpy is deliberately not pinned: its exact pin (2.4.6) clashes with the newer scipy that sentence-transformers pulls in, and pip
# then backtracks for many minutes without finishing.
RUNTIME="$(grep -E '^(fastapi|uvicorn|langgraph|langgraph-checkpoint|langgraph-checkpoint-sqlite|chromadb|sentence-transformers|transformers|anthropic|typesafe-sdk|python-dotenv)==' requirements.txt)"
[ -d .venv ] || "$PY" -m venv .venv
STAMP=".venv/.requirements.sha"
WANT="$(printf '%s' "$RUNTIME" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ]; then
  echo "Installing the ticketing service's requirements (large: PyTorch; several minutes)..."
  printf '%s\n' "$RUNTIME" > .venv/runtime-requirements.txt
  .venv/bin/pip install -q -r .venv/runtime-requirements.txt
  echo "$WANT" > "$STAMP"
fi

val() { sed -n "s/^$2=\(.*\)/\1/p" "$1" 2>/dev/null | tail -1; }
if [ ! -f .env ]; then
  ( umask 077; printf 'TYPESAFE_API_KEY=%s\nANTHROPIC_API_KEY=%s\n' "$(val "$ROOT/.env" TYPESAFE_API_KEY)" "$(val "$ROOT/.env" ANTHROPIC_API_KEY)" > .env )
  echo "Created $TICKETING_DIR/.env (keys copied from $ROOT/.env where they were set)."
fi
for k in TYPESAFE_API_KEY ANTHROPIC_API_KEY; do
  if [ -z "$(val .env "$k")" ]; then
    echo "Put your key in $TICKETING_DIR/.env  (the line $k=...), then run this script again." >&2
    MISSING=1
  fi
done
[ -z "${MISSING:-}" ] || exit 1

echo
echo "Ticketing:  http://localhost:$TICKETING_PORT/health"
echo "In the Agent Company .env set:  TICKETING_URL=http://localhost:$TICKETING_PORT/ticket   (then restart that script)"
echo "The first start downloads a small embedding model (all-MiniLM-L6-v2). Ctrl-C stops it."
exec .venv/bin/uvicorn api:app --host 127.0.0.1 --port "$TICKETING_PORT"
