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
set --
if [ -n "${VISN_INTERPRETATION_API_URL:-}" ]; then
  set -- --interpretation-api-url "$VISN_INTERPRETATION_API_URL"
fi
.venv-retrieval/bin/python tools/semantic_memory_server.py --data-dir "$VISN_DATA_DIR" \
  --interpretation-model "${VISN_INTERPRETATION_MODEL:-Qwen/Qwen3-VL-2B-Instruct}" \
  --interpretation-revision "${VISN_INTERPRETATION_REVISION:-main}" "$@" &
retrieval_pid=$!
trap 'kill "$retrieval_pid" 2>/dev/null || true' EXIT INT TERM
sleep 1
kill -0 "$retrieval_pid"
cargo run
