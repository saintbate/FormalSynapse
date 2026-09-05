#!/usr/bin/env bash
# Launch the Phase 3 generator: vLLM serving Qwen2.5-Coder-7B-Instruct with guided decoding.
#
#   scripts/run_vllm.sh
#   MODEL=Qwen/Qwen2.5-Coder-14B-Instruct scripts/run_vllm.sh
#
# Requires a CUDA box (or a vLLM build that supports your GPU). On this Mac the formal
# harness is local; point FSYN_LLM_BASE_URL at a remote vLLM if you do not have a GPU here.
set -euo pipefail

MODEL="${MODEL:-Qwen/Qwen2.5-Coder-7B-Instruct}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if ! command -v vllm >/dev/null 2>&1; then
  echo "error: vllm is not on PATH. Install on the GPU machine:" >&2
  echo "  pip install 'vllm>=0.6' outlines" >&2
  exit 1
fi

echo "==> vLLM ${MODEL}  http://${HOST}:${PORT}/v1  (guided decoding via outlines/xgrammar)"
exec vllm serve "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --guided-decoding-backend xgrammar \
  --trust-remote-code
