#!/usr/bin/env bash
# Run THIS on the rented CUDA box (not on the Mac). Serves CodeV-SVA-14B on :8000.
# Grammar is off at the FormalSynapse client. Then on the Mac:
#
#   ssh -L 8000:127.0.0.1:8000 root@<pod-ip> -p <pod-ssh-port>
#   export FSYN_LLM_BASE_URL=http://127.0.0.1:8000/v1
#   export FSYN_LLM_MODEL=wyt2000/CodeV-SVA-14B
#   export FSYN_LLM_API_KEY=EMPTY
#   fsyn doctor && fsyn baseline && fsyn cegar
#
# Needs ~24 GB VRAM (4090 in 8-bit, or 48 GB in bf16).
set -euo pipefail

MODEL="${MODEL:-wyt2000/CodeV-SVA-14B}"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"

if ! command -v nvidia-smi >/dev/null 2>&1; then
  echo "error: nvidia-smi not found; this is not a CUDA box" >&2
  exit 1
fi
nvidia-smi -L

python3 -m pip install -U 'vllm>=0.6' outlines

echo "==> vLLM ${MODEL}  http://${HOST}:${PORT}/v1"
# vLLM 0.8+ dropped --guided-decoding-backend; grammar is requested per-call
# via guided_grammar / structured outputs. xgrammar is still a dependency.
exec python3 -m vllm.entrypoints.openai.api_server \
  --model "$MODEL" \
  --host "$HOST" \
  --port "$PORT" \
  --trust-remote-code \
  --max-model-len "${MAX_MODEL_LEN:-8192}"
