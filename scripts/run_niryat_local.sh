#!/usr/bin/env bash
# Runs the NiryatAI backend (github.com/niteshnankani-svg/niryat-ai, folder backend/) on this machine.
# This is the "Export" department's service: POST /chat (HS-code proposal) and POST /tradedata (TradeStat lookup).
#
#   ./scripts/run_niryat_local.sh
#
# First run: clones the repo next to this one, creates a virtualenv, installs only what the API imports (not its
# document-ingestion libraries or Whisper), creates backend/.env, and loads the 12 committed video summaries into
# its knowledge base. If ANTHROPIC_API_KEY is already set in this project's .env it is copied across; otherwise put it
# in <that folder>/backend/.env and run this again. It never overwrites an existing .env. Stop with Ctrl-C.
#
# Notes on what this does NOT give you:
#  - The study-material knowledge base (PDF/DOCX/XLSX/PPTX documents) is built by the repo's own ingest.py from source
#    files that are not in the repository, so it starts empty. /chat still works: it just has less to quote from.
#  - /tradedata queries tradestat.commerce.gov.in live, so it needs internet access.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NIRYAT_DIR="${NIRYAT_DIR:-$ROOT/../niryat-ai}"
NIRYAT_PORT="${NIRYAT_PORT:-8104}"

PY=""
for c in python3.12 python3.11 python3.13; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "NiryatAI needs Python 3.11 or newer (found only: $(python3 --version 2>&1)). On a Mac: brew install python@3.12" >&2; exit 1; }

port_busy() { python3 -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError: sys.exit(0)
sys.exit(1)' "$1"; }
if port_busy "$NIRYAT_PORT"; then
  echo "Port $NIRYAT_PORT is already in use. Use another one, for example:  NIRYAT_PORT=8105 ./scripts/run_niryat_local.sh" >&2
  exit 1
fi

[ -d "$NIRYAT_DIR/.git" ] || git clone https://github.com/niteshnankani-svg/niryat-ai "$NIRYAT_DIR"
cd "$NIRYAT_DIR/backend"

# The API imports only these. The rest of its requirements.txt (pandas, pdfplumber, python-docx, python-pptx, openpyxl,
# openai-whisper) are for building the knowledge base and are large.
RUNTIME='fastapi
uvicorn[standard]
anthropic
chromadb
sentence-transformers
requests
beautifulsoup4
python-dotenv'
[ -d .venv ] || "$PY" -m venv .venv
STAMP=".venv/.requirements.sha"
WANT="$(printf '%s' "$RUNTIME" | python3 -c 'import hashlib,sys; print(hashlib.sha256(sys.stdin.buffer.read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ]; then
  echo "Installing the NiryatAI backend's requirements (large: PyTorch; several minutes)..."
  printf '%s\n' "$RUNTIME" > .venv/runtime-requirements.txt
  .venv/bin/pip install -q -r .venv/runtime-requirements.txt
  echo "$WANT" > "$STAMP"
fi

# main.py loads backend/.env with override=True, so this file is the one that counts.
val() { sed -n "s/^$2=\(.*\)/\1/p" "$1" 2>/dev/null | tail -1; }
if [ ! -f .env ]; then
  ( umask 077; printf 'ANTHROPIC_API_KEY=%s\n' "$(val "$ROOT/.env" ANTHROPIC_API_KEY)" > .env )
  echo "Created $NIRYAT_DIR/backend/.env (key copied from $ROOT/.env if it was set)."
fi
if [ -z "$(val .env ANTHROPIC_API_KEY)" ]; then
  echo "Put your key in $NIRYAT_DIR/backend/.env  (the line ANTHROPIC_API_KEY=...), then run this script again." >&2
  exit 1
fi

# One-time: load the committed video summaries into the knowledge base (downloads the embedding model the first time).
if [ ! -d chroma_db ]; then
  echo "Loading the video summaries into the knowledge base (first run only)..."
  .venv/bin/python ingest_videos.py || echo "WARNING: could not load the video summaries; /chat will answer without them." >&2
fi

echo
echo "NiryatAI:   http://localhost:$NIRYAT_PORT/health"
echo "In the Agent Company .env set:"
echo "  NIRYAT_CHAT_URL=http://localhost:$NIRYAT_PORT/chat"
echo "  NIRYAT_TRADEDATA_URL=http://localhost:$NIRYAT_PORT/tradedata"
echo "(then restart that script). Ctrl-C stops it."
exec .venv/bin/uvicorn main:app --host 127.0.0.1 --port "$NIRYAT_PORT"
