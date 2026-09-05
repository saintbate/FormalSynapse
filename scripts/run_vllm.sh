#!/usr/bin/env bash
# Launch the generator: vLLM serving CodeV-SVA-14B (grammar off at the client).
#
#   scripts/run_vllm.sh
#   MODEL=Qwen/Qwen2.5-Coder-14B-Instruct scripts/run_vllm.sh
#
# Requires a CUDA box (or a vLLM build that supports your GPU). On this Mac the formal
# harness is local; point FSYN_LLM_BASE_URL at a remote vLLM if you do not have a GPU here.
set -euo pipefail

MODEL="${MODEL:-wyt2000/CodeV-SVA-14B}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if ! command -v vllm >/dev/null 2>&1; then
  echo "error: vllm is not on PATH. Install on the GPU machine:" >&2
  echo "  pip install 'vllm>=0.6' outlines" >&2
  exit 1
fi

echo "==> vLLM ${MODEL}  http://${HOST}:${PORT}/v1"
exec vllm serve "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --trust-remote-code \
  --max-model-len "${MAX_MODEL_LEN:-8192}"
