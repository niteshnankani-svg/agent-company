#!/usr/bin/env bash
# Runs the Legal RAG service (github.com/niteshnankani-svg/legal-rag-chatbot) on this machine, API only
# (no Gradio page, no Redis: the service treats Redis as optional).
#
#   ./scripts/run_legal_local.sh
#
# First run: clones the repo next to this one, creates a virtualenv, installs its requirements (large: PyTorch),
# creates its .env, and downloads its index files from the Hugging Face dataset. Then put your OpenAI key in
# <that folder>/.env (OPENAI_API_KEY=...) and run this again. It never overwrites an existing .env.
# Stop with Ctrl-C.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LEGAL_DIR="${LEGAL_DIR:-$ROOT/../legal-rag-chatbot}"
LEGAL_PORT="${LEGAL_PORT:-8102}"

# Its pins (numpy 1.26.4, torch 2.9.0) have no wheels for Python 3.13, so it needs 3.11 or 3.12.
PY=""
for c in python3.12 python3.11; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
[ -n "$PY" ] || { echo "Legal needs Python 3.11 or 3.12 (found only: $(python3 --version 2>&1)). On a Mac: brew install python@3.12" >&2; exit 1; }

port_busy() { python3 -c 'import socket,sys; s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try: s.bind(("127.0.0.1", int(sys.argv[1])))
except OSError: sys.exit(0)
sys.exit(1)' "$1"; }
if port_busy "$LEGAL_PORT"; then
  echo "Port $LEGAL_PORT is already in use. Use another one, for example:  LEGAL_PORT=8103 ./scripts/run_legal_local.sh" >&2
  exit 1
fi

[ -d "$LEGAL_DIR/.git" ] || git clone https://github.com/niteshnankani-svg/legal-rag-chatbot "$LEGAL_DIR"
cd "$LEGAL_DIR"

[ -d .venv ] || "$PY" -m venv .venv
STAMP=".venv/.requirements.sha"
WANT="$(python3 -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
if [ ! -f "$STAMP" ] || [ "$(cat "$STAMP")" != "$WANT" ]; then
  echo "Installing Legal's requirements (this is large and takes several minutes)..."
  .venv/bin/pip install -q -r requirements.txt
  echo "$WANT" > "$STAMP"
fi

if [ ! -f .env ]; then
  printf 'OPENAI_API_KEY=\n' > .env
  echo "Created $LEGAL_DIR/.env"
fi
KEY="$(sed -n 's/^OPENAI_API_KEY=\(.*\)/\1/p' .env | tail -1)"
if [ -z "$KEY" ]; then
  echo "Put your OpenAI key in $LEGAL_DIR/.env  (the line OPENAI_API_KEY=...), then run this script again." >&2
  exit 1
fi

# The index: the section lists ship with the repo, the embeddings come from the Hugging Face dataset (as its app.py does).
mkdir -p data/index
if [ ! -f data/index/BNS_embeddings.pkl ]; then
  echo "Downloading the index files from Hugging Face..."
  if ! .venv/bin/python - <<'PY'
from huggingface_hub import hf_hub_download
for act in ("BNS", "BNSS", "BSA", "DPDP"):
    for suffix in ("embeddings.pkl", "index.json", "pages.json"):
        name = f"{act}_{suffix}"
        hf_hub_download(repo_id="nitz0219/legal-rag-index-v2", repo_type="dataset", filename=name, local_dir="data/index")
        print("  ", name)
PY
  then
    echo "Could not download the index from huggingface.co/datasets/nitz0219/legal-rag-index-v2." >&2
    echo "Check your internet connection. If that dataset is private, log in first:  $LEGAL_DIR/.venv/bin/huggingface-cli login" >&2
    exit 1
  fi
fi

echo
echo "Legal RAG:  http://localhost:$LEGAL_PORT/health"
echo "In the Agent Company .env set:  LEGAL_QUERY_URL=http://localhost:$LEGAL_PORT/query   (then restart that script)"
echo "Ctrl-C stops it."
exec .venv/bin/uvicorn app.api.main:app --host 127.0.0.1 --port "$LEGAL_PORT"
