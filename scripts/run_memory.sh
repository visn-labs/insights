#!/bin/sh
# Start the app and its local visual-retrieval worker together.
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv-retrieval/bin/python ]; then
  echo 'Run sh tools/setup_retrieval.sh first.' >&2
  exit 1
fi
export VISN_RETRIEVAL_URL=http://127.0.0.1:8186
export VISN_DATA_DIR="${VISN_DATA_DIR:-$PWD/data}"
.venv-retrieval/bin/python tools/semantic_memory_server.py --data-dir "$VISN_DATA_DIR" &
retrieval_pid=$!
trap 'kill "$retrieval_pid" 2>/dev/null || true' EXIT INT TERM
sleep 1
kill -0 "$retrieval_pid"
cargo run
