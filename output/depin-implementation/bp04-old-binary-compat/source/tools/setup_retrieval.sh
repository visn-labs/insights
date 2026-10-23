#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
python3 -m venv .venv-retrieval
.venv-retrieval/bin/python -m pip install -r tools/requirements-retrieval.txt
mkdir -p .retrieval
if [ ! -d .retrieval/qwen/.git ]; then
  git clone https://github.com/QwenLM/Qwen3-VL-Embedding.git .retrieval/qwen
fi
git -C .retrieval/qwen fetch origin 393e2978d27852b0d0230d6994f37f9c15bed73c
git -C .retrieval/qwen checkout --detach 393e2978d27852b0d0230d6994f37f9c15bed73c
